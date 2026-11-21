/** Verify real-time A/B playback and decoded audio using a local Chromium browser. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const {createHash} = require('node:crypto');

function loadDriver() {
  try { return require('puppeteer-core'); }
  catch (error) {
    if (error.code !== 'MODULE_NOT_FOUND') throw error;
    return require(path.join(os.homedir(), '.cache/creator-video-motion/hyperframes/node_modules/puppeteer-core'));
  }
}

async function fileHash(filename) {
  const sha = createHash('sha256');
  for await (const chunk of fs.createReadStream(filename)) sha.update(chunk);
  return sha.digest('hex');
}

function serveViewer(viewer) {
  // Serve only the anonymous playback page and the two anonymous media.
  const routes = {'/': ['index.html','text/html; charset=utf-8'], '/A.mp4': ['A.mp4','video/mp4'],
                  '/B.mp4': ['B.mp4','video/mp4']};
  return http.createServer((req, res) => {
    if (!Object.hasOwn(routes, req.url)) { res.writeHead(404).end(); return; }
    const [name, mime] = routes[req.url];
    const file = path.join(viewer, name);
    res.writeHead(200, {'Content-Type': mime, 'Content-Length': fs.statSync(file).size});
    const stream = fs.createReadStream(file);
    res.on('close', () => stream.destroy());
    stream.on('error', () => res.destroy()).pipe(res);
  });
}

async function playVersion(browser, origin, label) {
  const page = await browser.newPage();
  try {
    await page.goto(origin, {waitUntil:'domcontentloaded'});
    await page.evaluate(() => {
      const video = document.querySelector('video');
      const ctx = new AudioContext();
      // Read one branch per channel. The ordinary combined signal still reaches the audio output.
      const input = ctx.createMediaElementSource(video);
      input.connect(ctx.destination);
      const splitter = ctx.createChannelSplitter(2);
      input.connect(splitter);
      const analysers = [0, 1].map(channel => {
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 2048;
        splitter.connect(analyser, channel);
        return analyser;
      });
      const data = new Float32Array(2048);
      const trace = new Map();
      const states = {rate_changed:false, muted:false, sought:false};
      let start = null;
      let end = null;
      let frameCallbacks = 0;
      let firstPTS = null;
      let lastPTS = null;
      let maxGap = 0;
      const sample = () => {
        if (start === null || video.paused || video.ended) return;
        const slot = Math.floor(video.currentTime * 10);
        let peak = 0;
        let sum = 0;
        for (const analyser of analysers) {
          analyser.getFloatTimeDomainData(data);
          for (const v of data) { peak = Math.max(peak, Math.abs(v)); sum += v * v; }
        }
        const rms = Math.sqrt(sum / (data.length * analysers.length));
        const old = trace.get(slot) || {peak:0, rms:0};
        trace.set(slot,{peak:Math.max(old.peak,peak),rms:Math.max(old.rms,rms)});
        if (video.playbackRate !== 1) states.rate_changed = true;
        if (video.muted || video.volume === 0) states.muted = true;
      };
      document.addEventListener('click', () => { ctx.resume().catch(() => {}); }, {once:true,capture:true});
      video.addEventListener('playing', () => { start ??= performance.now(); }, {once:true});
      video.addEventListener('seeking', () => { if (start !== null) states.sought = true; });
      video.addEventListener('ratechange', () => { if (video.playbackRate !== 1) states.rate_changed = true; });
      video.addEventListener('volumechange', () => { if (video.muted || video.volume === 0) states.muted = true; });
      const onFrame = (now, meta) => {
        if (start !== null) {
          frameCallbacks++;
          firstPTS ??= meta.mediaTime;
          if (lastPTS !== null) maxGap = Math.max(maxGap, meta.mediaTime-lastPTS);
          lastPTS = meta.mediaTime;
        }
        if (!video.ended) video.requestVideoFrameCallback(onFrame);
      };
      video.requestVideoFrameCallback(onFrame);
      const timer = setInterval(sample, 25);
      video.addEventListener('ended', () => { end = performance.now(); clearInterval(timer); }, {once:true});
      window.readPlayback = () => {
        const quality = video.getVideoPlaybackQuality();
        return {source_file:new URL(video.currentSrc).pathname.slice(1), duration:video.duration,
          end_time:video.currentTime, ended:video.ended, rate:video.playbackRate,
          wall_seconds:(end - start)/1000, audio_context:ctx.state, sample_rate:ctx.sampleRate,
          state_changes:states, decoded_video_frames:quality.totalVideoFrames, dropped_video_frames:quality.droppedVideoFrames,
          compositor_callbacks:frameCallbacks, first_presented_pts:firstPTS, last_presented_pts:lastPTS,
          max_presented_gap:maxGap,
          played:Array.from({length:video.played.length}, (_,i) => [video.played.start(i),video.played.end(i)]),
          audio_bins:[...trace].map(([slot,v])=>[slot/10,+v.peak.toFixed(5),+v.rms.toFixed(5)])};
      };
    });
    await page.click('#start-' + label);
    await page.waitForFunction(() => document.querySelector('video').currentTime > 0, {timeout:30000});
    const duration = await page.$eval('video', v => v.duration);
    await page.waitForFunction(() => document.querySelector('video').ended, {timeout:(duration + 25) * 1000});
    const row = await page.evaluate(() => window.readPlayback());
    const played = row.played;
    const frameWindow = Math.max(.25, 3 * duration / Math.max(1, row.decoded_video_frames));
    row.passed = row.source_file === label+'.mp4' && row.ended && row.rate === 1 && !Object.values(row.state_changes).some(Boolean) &&
      row.audio_context === 'running' && row.audio_bins.some(bin => bin[1] > 0.001) &&
      row.compositor_callbacks > 0 && row.decoded_video_frames > 0 && row.first_presented_pts <= frameWindow &&
      row.last_presented_pts >= duration - frameWindow && row.max_presented_gap <= frameWindow &&
      played.length === 1 && played[0][0] < .10 && played[0][1] >= duration - .10 &&
      Math.abs(row.wall_seconds-duration) <= Math.max(.35,duration*.035);
    return row;
  } finally { await page.close(); }
}

async function main() {
  const [viewerArg, outputArg, ...requestedOrder] = process.argv.slice(2);
  const order = requestedOrder.length ? requestedOrder : ['A','B'];
  if (!viewerArg || !outputArg || order.slice().sort().join(',') !== 'A,B') {
    throw new Error('Usage: node verify_playback.cjs TRIAL/viewer NEW-report.json [A B | B A]');
  }
  const viewer = path.resolve(viewerArg);
  const output = path.resolve(outputArg);
  if (fs.existsSync(output)) throw new Error('Choose a new report file');
  const task = JSON.parse(fs.readFileSync(path.join(viewer,'task.json'), 'utf8'));
  const assertMedia = async () => {
    for (const label of order) {
      if (await fileHash(path.join(viewer,label+'.mp4')) !== task.files[label]) throw new Error('Changed file '+label);
    }
  };
  await assertMedia();
  const pup = loadDriver();
  const executablePath = process.env.CREATOR_CHROME || ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/usr/bin/google-chrome','/usr/bin/chromium','/usr/bin/chromium-browser'].find(fs.existsSync);
  if (!executablePath) throw new Error('Set CREATOR_CHROME to the installed Chromium executable');
  const server = serveViewer(viewer);
  await new Promise((resolve,reject) => {server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  let browser;
  try {
    browser = await pup.launch({headless:true,executablePath});
    const origin = 'http://127.0.0.1:'+server.address().port+'/';
    const runs = {};
    for (const label of order) runs[label] = await playVersion(browser, origin, label);
    await assertMedia();
    const report = {method:'Chromium 1x wall-clock playback of the muxed file. Web Audio analyser upstream of the output device. No listener vote.',
      audio_columns:['media_seconds_bin_start','maximum_sampled_absolute_peak','maximum_sampled_stereo_rms'],
      browser:await browser.version(), files:task.files, order, runs, passed:order.every(label=>runs[label].passed)};
    fs.writeFileSync(output,JSON.stringify(report,null,2)+'\n',{flag:'wx'});
    console.log(JSON.stringify({passed:report.passed, order, output}));
    if (!report.passed) process.exitCode = 1;
  } finally {
    try { if (browser) await browser.close(); } finally { await new Promise(resolve=>server.close(resolve)); }
  }
}
main().catch(error => { console.error(error.message); process.exitCode=1; });
