/** Inspect a director's scene board using estimated line timings before speech exists. */
'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {parseArgs} = require('node:util');
const {openBrowser, openPage, prepareClips, capture} = require('./explainer_scene.cjs');

const help = 'node explainer_board.cjs --project DIR --shot ID [--check | --frame N --png NEW.png | --sheet NEW.png]';
function seconds(value, name) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) throw Error(name+' must be finite, nonnegative seconds');
  return value;
}
function lineBeat(shot, line, i, start) {
  const text = line.spoken ?? line.text, id = line.id ?? (shot+'.L'+(i+1));
  if (typeof text !== 'string' || !/[\p{L}\p{N}]/u.test(text) || typeof id !== 'string' || !id.trim() || /[@:#]/.test(id) || id === 'title')
    throw Error(shot+': supply speakable text and a valid line id');
  const chars = [];
  let natural = 0;
  // A printed Latin word contributes one spoken beat distributed over its letters. Chinese
  // glyphs and digits carry independent weight. Punctuation lets the next phrase arrive later.
  for (const m of text.matchAll(/[A-Za-z]+(?:['’][A-Za-z]+)*|[\s\S]/gu)) {
    const token = Array.from(m[0]);
    let weight;
    if (/^[a-z]/i.test(m[0])) weight = Math.max(.18,.11+.042*token.length)/token.length;
    else if (/\p{N}/u.test(m[0])) weight = .17;
    else if (/\p{L}/u.test(m[0])) weight = .23;
    else if (/[。！？.!?…]/u.test(m[0])) weight = .26;
    else if (/[，、；：,;:]/u.test(m[0])) weight = .15;
    else weight = /\s/u.test(m[0]) ? .04 : .07;
    for (const char of token) { chars.push([char,natural]); natural += weight; }
  }
  let duration = line.estimate_seconds ?? natural;
  if (line.source_audio) {
    const spec = line.source_audio;
    duration = seconds(spec.out, id+'.source_audio.out') - seconds(spec.in,id+'.source_audio.in');
  }
  if (seconds(duration, id+'.estimate_seconds') <= 0) throw Error(id+': use a positive estimated reading time');
  return {shot,id,text,start,end:start+duration,chars:chars.map(([c,t])=>[c,start+t*duration/natural]),method:'estimate'};
}
function plan(edit, id) {
  const fps = edit.fps ?? 30;
  const [width,height] = edit.size ?? [1920,1080];
  if (![width,height,fps].every(n=>Number.isInteger(n)&&n>0) || width*9!==height*16 || width%2 || height%2 || 48000%fps)
    throw Error('Use an even 16:9 picture and the project clock accepted by prepare.py');
  if (!Array.isArray(edit.shots) || !edit.shots.length) throw Error('Add project shots in edit.json');
  let startFrame = 0, selected;
  const ids = new Set(), lineIds = new Set();
  for (const shot of edit.shots) {
    if (typeof shot.id!=='string' || !shot.id || ids.has(shot.id) || !Array.isArray(shot.lines)) throw Error('Use unique shot ids and an array of lines per shot');
    ids.add(shot.id);
    let cursor = seconds(shot.lead ?? .08,shot.id+'.lead');
    const beats = shot.lines.map((line,index)=>{
      const beat = lineBeat(shot.id,line,index,cursor);
      if (lineIds.has(beat.id)) throw Error('Duplicate spoken line id: '+beat.id);
      lineIds.add(beat.id);
      cursor = beat.end + seconds(line.pause ?? .09,beat.id+'.pause');
      return beat;
    });
    const length = cursor + seconds(shot.tail ?? .18,shot.id+'.tail');
    const frames = Math.ceil(length*fps-1e-9);
    if (frames < 1) throw Error(shot.id+': specify a positive tail for a silent shot');
    if (edit.presenter?.recording && shot.start_frame !== undefined) {
      if (!Number.isInteger(shot.start_frame) || shot.start_frame<0) throw Error('Presenter start_frame must be a nonnegative frame');
      startFrame = shot.start_frame;
    }
    if (id===shot.id) {
      const spec=shot.explainer_scene;
      if (!Array.isArray(spec?.scenes) || !spec.scenes.length) throw Error(id+': add explainer_scene.scenes');
      const host=spec.presenter===undefined ? (shot.presenter===undefined ? edit.presenter : shot.presenter) : spec.presenter;
      if (host?.fps!==undefined && host.fps!==fps) throw Error('Presenter fps must equal the project fps');
      selected={...spec,width,height,fps,frames,startFrame,beats:spec.beats??beats,
                presenter:host ? {...host,fps,startFrame} : null};
    }
    startFrame+=frames;
  }
  if (!selected) throw Error('Unknown shot '+id);
  return selected;
}
function samples(shots, frames, fps) {
  function firstAt(t) {
    let n = Math.floor(t*fps);
    if (n/fps < t) n++;
    return Math.min(frames, n);
  }
  return shots.map(s=>{
    const first=firstAt(s.t0), last=firstAt(s.t1)-1;
    if (last<first) throw Error('An estimated scene occupies less than one displayed frame: '+s.scene);
    return [first,Math.floor((first+last)/2),last];
  });
}
async function contactSheet(browser, page, groups, names, assertReady) {
  const sheet=await browser.newPage();
  try {
    const cellW=320, cellH=180, rowH=210;
    await sheet.setViewport({width:3*cellW,height:rowH});
    await sheet.setContent('<body style="margin:0;background:#141a18;color:white;font:14px system-ui"><main></main></body>');
    for (let i=0;i<groups.length;i++) {
      const frames=groups[i];
      const tiles=[];
      for (const n of frames) {
        const image=await capture(page,n); assertReady();
        tiles.push({n,src:'data:image/png;base64,'+image.toString('base64')});
      }
      await sheet.evaluate(async ({tiles,name,w,h,row})=>{
        const line=document.createElement('section');line.style.cssText='display:flex;height:'+row+'px';
        document.querySelector('main').append(line);
        for (const tile of tiles) {
          const box=document.createElement('div'), image=new Image();
          image.src=tile.src; await image.decode(); image.width=w; image.height=h;
          const caption=document.createElement('div'); caption.style.padding='2px 8px';
          caption.textContent=name+'   F'+tile.n; box.append(image,caption);line.append(box);
        }
      },{tiles,name:names[i],w:cellW,h:cellH,row:rowH});
    }
    return Buffer.from(await sheet.screenshot({type:'png',fullPage:true,optimizeForSpeed:true}));
  } finally {await sheet.close();}
}
async function main() {
  const {values:a}=parseArgs({options:{project:{type:'string',default:'.'},shot:{type:'string'},check:{type:'boolean'},
    frame:{type:'string'},png:{type:'string'},sheet:{type:'string'},help:{type:'boolean'}}});
  if (a.help){console.log(help);return;}
  if (!a.shot || (a.frame!==undefined)!==(a.png!==undefined) || [a.check,a.frame!==undefined,a.sheet!==undefined].filter(Boolean).length>1) throw Error(help);
  const output=a.png??a.sheet;
  if (output!==undefined && (!/\.png$/i.test(output) || fs.existsSync(output))) throw Error('Choose a new PNG for the board');
  const project=fs.realpathSync(a.project), edit=JSON.parse(fs.readFileSync(path.join(project,'edit.json'),'utf8'));
  const config=plan(edit,a.shot);
  let frame;
  if (a.frame!==undefined) {
    frame=Number(a.frame);
    if (!a.frame.trim() || !Number.isInteger(frame) || frame<0 || frame>=config.frames) throw Error('Frame outside estimated shot 0..'+(config.frames-1));
  }
  if (a.sheet) {config.width=320; config.height=180;}
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'explainer-board-'));
  let browser;
  try {
    config.clips=prepareClips(project,config,temp);
    browser=await openBrowser();
    const {page,assertReady}=await openPage(browser,project,config,temp);
    const report=await page.evaluate(c=>window.buildScenes(c),config); assertReady();
    const scenes=report.shots;
    const groups=samples(scenes,config.frames,config.fps);
    if (output!==undefined) {
      const image=a.png ? await capture(page,frame) : await contactSheet(browser,page,groups,report.shots.map(s=>s.scene),assertReady);
      assertReady();fs.mkdirSync(path.dirname(output),{recursive:true});fs.writeFileSync(output,image,{flag:'wx'});
    } else {
      for (const n of new Set(groups.flat())) {await page.evaluate(f=>window.drawScene(f),n);assertReady();}
    }
    console.log(JSON.stringify({clock:'estimated',shot:a.shot,frames:config.frames,fps:config.fps,start_frame:config.startFrame,
      beats:config.beats,scenes,warnings:report.warns,output},null,2));
  } finally {if(browser)await browser.close();fs.rmSync(temp,{recursive:true,force:true});}
}
if(require.main===module) main().catch(err=>{console.error(err.message);process.exitCode=1;});
module.exports={plan};
