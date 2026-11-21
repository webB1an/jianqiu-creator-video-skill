/** Render a clean, frame-addressable 16:9 picture from the prepared project clock. */
'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const {spawn, spawnSync} = require('node:child_process');
const {once} = require('node:events');
const {parseArgs} = require('node:util');
const kit = path.resolve(__dirname, '../assets/explainer-kit');
const MIME = {'.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.png':'image/png', '.webp':'image/webp', '.svg':'image/svg+xml', '.gif':'image/gif', '.js':'text/javascript', '.css':'text/css'};
const help = 'node explainer_scene.cjs --project DIR --shot ID [--check | --moves | --frame N --png NEW.png]\nnode explainer_scene.cjs --list [--describe SCENE]';

function inside(root, filename) {
  root = fs.realpathSync(root);
  const absolute = path.resolve(root, filename);
  let parent = absolute;
  while (!fs.existsSync(parent)) {
    if (parent === path.dirname(parent)) throw new Error('Cannot resolve path: ' + filename);
    parent = path.dirname(parent);
  }
  const actual = path.join(fs.realpathSync(parent), path.relative(parent, absolute));
  if (actual !== root && !actual.startsWith(root + path.sep)) throw new Error('Path leaves project: ' + filename);
  return actual;
}
function run(bin, argv) {
  const proc = spawnSync(bin, argv, {encoding:'utf8', maxBuffer:12*1024*1024});
  if (proc.error || proc.status !== 0) throw proc.error || new Error(bin + ': ' + proc.stderr.trim());
  return proc.stdout;
}
function openBrowser() {
  let puppeteer;
  try { puppeteer = require('puppeteer-core'); }
  catch (error) {
    if (error.code !== 'MODULE_NOT_FOUND') throw error;
    puppeteer = require(path.join(os.homedir(),'.cache/creator-video-motion/hyperframes/node_modules/puppeteer-core'));
  }
  const executablePath = process.env.CREATOR_CHROME || ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome','/usr/bin/google-chrome','/usr/bin/chromium','/usr/bin/chromium-browser'].find(fs.existsSync);
  if (!executablePath) throw new Error('Set CREATOR_CHROME to the Chromium executable.');
  return puppeteer.launch({executablePath,headless:true});
}

function sha(bytes) { return crypto.createHash('sha256').update(bytes).digest('hex'); }
function fileHash(file) {
  const hash=crypto.createHash('sha256'), buf=Buffer.alloc(1024*1024), fd=fs.openSync(file,'r');
  try { let n; while((n=fs.readSync(fd,buf,0,buf.length,null)))hash.update(buf.subarray(0,n)); }
  finally { fs.closeSync(fd); }
  return hash.digest('hex');
}
function remember(table, name, checksum) {
  if (table[name] && table[name]!==checksum) throw new Error('Input changed during scene render: '+name);
  table[name]=checksum;
}

function projectConfig(project, id) {
  const editBytes=fs.readFileSync(path.join(project,'edit.json')), timelineBytes=fs.readFileSync(path.join(project,'timeline.json'));
  const edit=JSON.parse(editBytes), timeline=JSON.parse(timelineBytes);
  const original={'edit.json':sha(editBytes),'timeline.json':sha(timelineBytes)};
  if (timeline.edit_sha256 !== original['edit.json']) throw new Error('Run prepare.py after editing edit.json.');
  const shot = edit.shots.find(s => s.id === id), timing = timeline.shots.find(s => s.id === id);
  if (!shot?.explainer_scene || !timing) throw new Error('No prepared explainer_scene for shot ' + id);
  if (shot.in) throw new Error('Generated scene visuals start at source frame zero.');
  const spec = shot.explainer_scene, {fps} = timeline, {frames,start_frame} = timing;
  const [width,height] = edit.size || [1920,1080];
  if (![width,height,frames,fps].every(n => Number.isInteger(n) && n > 0) || width%2 || height%2 || width*9 !== height*16)
    throw new Error('Prepared scene requires an even 16:9 picture and positive integer timing.');
  if (!Array.isArray(spec.scenes) || !spec.scenes.length) throw new Error('Provide an explainer_scene.scenes list.');
  const seconds = start_frame/fps;
  const beats = spec.beats ?? (timeline.beats || []).filter(b => b.shot === id).map(b => ({...b,start:b.start-seconds,end:b.end-seconds,chars:b.chars.map(c => [c[0],c[1]-seconds])}));
  const presenter = spec.presenter === undefined ? (shot.presenter === undefined ? edit.presenter : shot.presenter) : spec.presenter;
  if (presenter && presenter.fps !== undefined && presenter.fps !== fps) throw new Error('Presenter fps must equal the prepared project fps.');
  const config = {...spec, width, height, fps, frames, startFrame:start_frame, beats,
    presenter:presenter ? {startFrame:start_frame,fps,...(presenter === edit.presenter && timeline.presenter_source?.sha256 ? {source_sha256:timeline.presenter_source.sha256}:{}),...presenter} : null};
  return {config, output:inside(project,shot.visual), edit,original};
}
function prepareClips(project, config, scratch, inputs={}, engine={}, decoded={}) {
  const prepared = {};
  for (const [name, clip] of Object.entries(config.clips || {})) {
    if (!/^[\p{L}\p{N}_-]+$/u.test(name)) throw new Error('Use a simple clip identifier: ' + name);
    if (clip.file) {
      const src = inside(project,clip.file), start = clip.in ?? 0, seconds = clip.seconds;
      if (!Number.isFinite(start) || start < 0 || !Number.isFinite(seconds) || seconds <= 0) throw new Error(name + ': file clip requires in >= 0 and seconds > 0');
      const sourceHash=fileHash(src);
      const envPython=path.join(os.homedir(),'.cache/creator-video-env/bin/python');
      const python=process.env.CREATOR_PYTHON || (fs.existsSync(envPython)?envPython:'python3');
      const helper=path.join(__dirname,'explainer_clip.py');
      const decodedClip=JSON.parse(run(python,[helper,src,path.join(scratch,name),String(start),
        String(seconds),String(config.fps),JSON.stringify(clip.input_color ?? null)]));
      const {engine:decoder, ...report}=decodedClip;
      for(const [file,hash] of Object.entries(decoder)) remember(engine,file,hash);
      decoded[name]={file:clip.file,...report};
      const logical=path.relative(project,path.resolve(project,clip.file));
      remember(inputs,logical,sourceHash);
      if(fileHash(src)!==sourceHash)throw new Error('Clip changed during decoding: '+clip.file);
      prepared[name]={dir:'/_decoded/'+name,frames:report.frames,fps:config.fps,ext:'jpg',source:logical,sourceIn:start};
    } else {
      const ext = clip.ext ?? 'jpg', fps = clip.fps ?? config.fps;
      if (!Number.isFinite(fps) || fps<=0 || !Number.isInteger(clip.frames) || clip.frames<=0 || !['jpg','jpeg','png','webp'].includes(ext))
        throw new Error(name + ': invalid frame sequence.');
      inside(project,clip.dir);
      const dir=path.relative(project,path.resolve(project,clip.dir)).split(path.sep).map(encodeURIComponent).join('/');
      prepared[name]={dir:'/'+dir, frames:clip.frames, fps, ext};
    }
  }
  return prepared;
}
const ORIGIN = 'http://scene.invalid';
async function openPage(browser, project, config, scratch, inputs={}, engine={}) {
  const page = await browser.newPage();
  await page.setViewport({width:config.width,height:config.height,deviceScaleFactor:1});
  const files = ['stage25d.js','moves.js','kit.js','scenes.js','scenes_plus.js'];
  if (config.presenter || fs.existsSync(path.join(kit,'host.js'))) files.push('host.js');
  files.push('runtime.js');
  const custom = config.drawing ? inside(project, config.drawing) : null;
  if (custom && !/\.js$/i.test(custom)) throw new Error('drawing must be a project-authored .js file.');
  const scripts = files.map(f => '/kit/'+f).concat(custom ? ['/_drawing.js'] : []);
  const html = '<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\';script-src \'self\';style-src \'self\' \'unsafe-inline\';font-src \'self\';img-src \'self\' data:;connect-src \'self\'"><link rel="stylesheet" href="/kit/kit.css"><link rel="stylesheet" href="/kit/style.css"><main id="stage"></main>'+scripts.map(s=>'<script src="'+s+'"></script>').join('');
  const failures = [];
  await page.setRequestInterception(true);
  page.on('request', async req => {
    try {
      const url = new URL(req.url());
      if (url.protocol === 'data:') { await req.continue(); return; }
      if (url.origin !== ORIGIN) throw new Error('Scene requested a non-project asset: '+url.href);
      const name=decodeURIComponent(url.pathname).slice(1), ext=path.extname(name).toLowerCase();
      if (!name) { await req.respond({status:200,contentType:'text/html;charset=utf-8',body:html});return; }
      if (name==='favicon.ico') { await req.respond({status:204});return; }
      let source;
      if (name==='_drawing.js' && custom) source=custom;
      else if (name.startsWith('kit/')) source=inside(kit,name.slice(4));
      else {
        if (!['.jpg','.jpeg','.png','.svg','.webp','.gif','.json'].includes(ext)) throw new Error('Unsupported project image or data file: '+name);
        source=name.startsWith('_decoded/')?inside(scratch,name.slice(9)):inside(project,name);
      }
      const body=await fs.promises.readFile(source);
      if (name.startsWith('kit/')) remember(engine,'assets/explainer-kit/'+name.slice(4),sha(body));
      else if (!name.startsWith('_decoded/')) remember(inputs, name==='_drawing.js'?config.drawing:name,sha(body));
      await req.respond({status:200,contentType:MIME[ext] || 'application/json',body});
    } catch(error) {
      failures.push(String(error.message));
      if(!req.isInterceptResolutionHandled()) await req.respond({status:404,body:'Missing scene asset'}).catch(()=>{});
    }
  });
  page.on('pageerror',error=>failures.push(String(error.message)));
  await page.goto(ORIGIN,{waitUntil:'load'});
  function assertReady() { if(failures.length) throw new Error(failures[0]); }
  assertReady();
  return {page,assertReady};
}
async function capture(page, n, observe) {
  const state=await page.evaluate(index=>window.drawScene(index), n);
  if(observe)observe(n,state.media);
  return Buffer.from(await page.screenshot({type:'png',optimizeForSpeed:true}));
}
function mediaUsage(config) {
  const used=new Map();
  function record(n,urls) {
    const observed=new Map();
    for(const raw of urls) {
      if(typeof raw!=='string')continue;
      const u=new URL(raw,ORIGIN),name=decodeURIComponent(u.pathname).slice(1);
      if(u.origin!==ORIGIN||name.startsWith('kit/'))continue;
      if(name.startsWith('_decoded/')){
        const [,key,frame]=name.split('/'),clip=config.clips[key];
        if(!clip?.source)continue;
        const time=clip.sourceIn+(parseInt(frame,10)-1)/clip.fps;
        const span=observed.get(clip.source);
        observed.set(clip.source,[Math.min(span?.[0]??time,time),Math.max(span?.[1]??time,time+1/clip.fps)]);
      } else observed.set(name,null);
    }
    for(const [file,range] of observed) {
      if(!used.has(file))used.set(file,[]);
      const spans=used.get(file),last=spans.at(-1);
      if(last?.frames[1]===n && (!range || (last.range && Math.abs(last.range[1]-range[0])<1e-5))){
        last.frames[1]=n+1;if(range)last.range[1]=range[1];
      } else spans.push({file,frames:[n,n+1],range});
    }
  }
  return {record,rows:()=>[...used.values()].flat()};
}
async function video(page, config, target, marker, assertReady, observe) {
  const args=['-nostdin','-hide_banner','-v','error','-f','image2pipe','-framerate',String(config.fps),'-vcodec','png','-i','pipe:0',
    '-an','-vf','scale=out_color_matrix=bt709:out_range=tv,format=yuv444p10le,colorspace=ispace=bt709:iprimaries=bt709:itrc=iec61966-2-1:irange=tv:all=bt709:range=tv:format=yuv420p',
    '-frames:v',String(config.frames),'-c:v','libx264','-crf','15','-preset','veryfast','-colorspace','bt709','-color_primaries','bt709','-color_trc','bt709','-color_range','tv','-metadata','comment='+marker,...(target ? [target] : ['-f','null','-'])];
  const stream = spawn('ffmpeg',args,{stdio:['pipe','ignore','pipe']});
  let diagnostic='';
  stream.stderr.on('data',buf=>{diagnostic=(diagnostic+buf).slice(-6000);});
  stream.stdin.on('error',()=>{});
  const ended = once(stream,'close');
  try {
    for (let n=0;n<config.frames;n++) {
      const pixels=await capture(page,n,observe);
      assertReady();
      await new Promise((res,rej)=>stream.stdin.write(pixels,err=>err?rej(err):res()));
    }
    stream.stdin.end();
    const [code]=await ended;
    if(code!==0) throw new Error(diagnostic || 'FFmpeg failed');
  } finally { stream.stdin.destroy(); if(stream.exitCode===null)stream.kill(); }
}
async function main() {
  const {values:args}=parseArgs({options:{project:{type:'string',default:'.'},shot:{type:'string'},check:{type:'boolean'},moves:{type:'boolean'},frame:{type:'string'},png:{type:'string'},list:{type:'boolean'},describe:{type:'string'},help:{type:'boolean'}}});
  if(args.help){console.log(help);return;}
  const listing=args.list || args.describe, project=fs.realpathSync(args.project);
  if(!listing && (!args.shot || Boolean(args.frame!==undefined)!==Boolean(args.png) || ([args.check,args.moves,args.png].filter(Boolean).length>1)))throw new Error(help);
  const {config,output,edit,original}=listing ? {config:{width:960,height:540}} : projectConfig(project,args.shot);
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'explainer-source-'));
  let browser;
  const inputs={}, decoded={}, engine={'scripts/explainer_scene.cjs':fileHash(__filename)};
  try {
    if (!listing) {
      Object.assign(inputs,original);
      config.clips=prepareClips(project,config,temp,inputs,engine,decoded);
    }
    browser=await openBrowser();
    const {page,assertReady}=await openPage(browser,project,config,temp,inputs,engine);
    if(listing){
      const inventory=await page.evaluate(()=>Object.fromEntries(Object.entries(KIT.SCENES).map(([key,s])=>[key,{says:s.says,use:s.use,avoid:s.avoid,need:s.need||[],help:s.help||{}}])));
      if(args.describe && !inventory[args.describe])throw new Error('Unknown scene: '+args.describe);
      console.log(JSON.stringify(args.describe?inventory[args.describe]:Object.keys(inventory),null,2));return;
    }
    const report=await page.evaluate(config=>window.buildScenes(config),config);
    assertReady();
    if(args.moves){console.log(JSON.stringify(await page.evaluate(()=>window.sceneMoves()),null,2));return;}
    if(args.check){
      const media=mediaUsage(config);
      const observed=await page.evaluate(async total=>{
        const result=[];
        for(let n=0;n<total;n++)result.push((await window.drawScene(n)).media);
        return result;
      },config.frames);
      observed.forEach((urls,n)=>media.record(n,urls));
      assertReady();console.log(JSON.stringify({...report,frames_checked:config.frames,decoded_clips:decoded,used:media.rows()},null,2));return;
    }
    if(args.png){
      if(!/\.png$/i.test(args.png)||args.frame.trim()==='')throw new Error('Choose a new .png path and integer frame.');
      const pixels=await capture(page,Number(args.frame));assertReady();
      fs.writeFileSync(args.png,pixels,{flag:'wx'}); console.log(args.png);return;
    }
    if (Object.keys(inputs).some(name=>inside(project,name)===output))throw new Error('Generated visual also appears as a source: '+output);
    if (!output.endsWith('.mp4') || edit.shots.some(s=>s.id!==args.shot && s.visual && inside(project,s.visual)===output))
      throw new Error('Reserve a distinct project MP4 for this generated shot.visual.');
    const marker='creator-video/explainer-scene/'+args.shot;
    if(fs.existsSync(output)){
      const probe=JSON.parse(run('ffprobe',['-v','error','-show_entries','format_tags=comment','-of','json',output]));
      if(probe.format?.tags?.comment!==marker) throw new Error('Choose a new generated output; existing visual belongs to another source.');
    }
    fs.mkdirSync(path.dirname(output),{recursive:true});
    const staged=fs.mkdtempSync(path.join(path.dirname(output),'.scene-'));
    try {
      const media=mediaUsage(config);
      const file=path.join(staged,'picture.mp4');
      await video(page,config,file,marker,assertReady,media.record);
      for(const [name,hash] of Object.entries(inputs))if(fileHash(inside(project,name))!==hash)throw new Error('Source changed while rendering: '+name);
      for(const [name,hash] of Object.entries(engine))if(fileHash(inside(path.resolve(__dirname,'..'),name))!==hash)throw new Error('Renderer changed while rendering: '+name);
      const receipt={schema:1,shot:args.shot,start_frame:config.startFrame,frames:config.frames,fps:config.fps,output_sha256:fileHash(file),inputs,engine,decoded_clips:decoded,used:media.rows()};
      const receiptFile=path.join(staged,'receipt.json');
      fs.writeFileSync(receiptFile,JSON.stringify(receipt,null,2)+'\n');
      fs.renameSync(file,output);
      fs.renameSync(receiptFile,output+'.scene.json');
    }
    finally { fs.rmSync(staged,{recursive:true,force:true}); }
    console.log(output);
  } finally { if(browser)await browser.close();fs.rmSync(temp,{recursive:true,force:true}); }
}
if(require.main===module)main().catch(err=>{console.error(err.message);process.exitCode=1;});
module.exports={openBrowser,openPage,projectConfig,prepareClips,capture,video,mediaUsage};
