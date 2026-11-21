/* Bind the MIT visual templates to the prepared Jianqiu shot clock. */
'use strict';
window.FB = {fps: 30, cur: {shot: 0, t: 0}};

window.buildScenes = async function (config) {
  const stage = document.querySelector('#stage');
  const {frames, fps} = config;
  const T = {fps, duration: frames / fps, beats: config.beats || [], inserts: config.inserts || [], title: config.title};
  const clips = config.clips || {};
  const shots = [];
  function sceneAt(n) {
    const t = n / fps;
    const i = shots.findIndex(s => s.t0 <= t && t < s.t1);
    if (i < 0) throw new Error('No scene at frame ' + n);
    return i;
  }
  function fr(name, tick) {
    const clip = clips[name];
    if (!clip) throw new Error('Unregistered clip: ' + name);
    const n = Math.max(1, Math.floor((Math.max(1, Math.round(tick)) - 1) * clip.fps / fps + 1e-7) + 1);
    if (n > clip.frames) throw new Error(name + ': source ends before requested frame ' + n);
    return clip.dir + '/' + String(n).padStart(4, '0') + '.' + clip.ext;
  }
  const F = {T, stage, shots, fr, CL: {}, shot(t0, t1, f) { shots.push({t0, t1, f}); },
    clipSrc(name, t, start, rate = 1) { return fr(name, 1 + Math.floor(Math.max(0, t-start) * fps * rate + 1e-7)); },
    go({frame}) {
      window.drawScene = async n => {
        if (!Number.isInteger(n) || n < 0 || n >= frames) throw new Error('Frame outside prepared shot');
        const t = n / fps;
        const i = sceneAt(n);
        FB.cur = {shot: i, t};
        stage.innerHTML = S25.DEFS + frame(shots[i].f(t));
        await Promise.all(Array.from(stage.querySelectorAll('img'), async img => {
          try { await img.decode(); } catch { throw new Error('Cannot decode ' + img.getAttribute('src')); }
        }));
        await document.fonts.ready;
        const media=new Set(Array.from(stage.querySelectorAll('img'),img=>img.getAttribute('src')));
        for (const el of stage.querySelectorAll('[style*="url("]')) {
          for (const match of el.getAttribute('style').matchAll(/url\(\s*["']?([^)"']+?)["']?\s*\)/g)) {
            if (!match[1].startsWith('#')) media.add(match[1]);
          }
        }
        return {frame:n,scene:i,media:[...media]};
      };
    }};
  for (const [name, c] of Object.entries(clips)) F.CL[name] = Math.ceil(c.frames * fps / c.fps);
  Object.assign(FB, F, {fps});
  stage.style.transform = 'scale(' + (innerWidth / 1920) + ')';
  await Promise.allSettled(KIT.FONTS.map(font => document.fonts.load(font)));
  const K = await KIT.use(F);
  if (config.scenes.some(s => s.scene === 'host' || s.host)) {
    if (!config.presenter) throw new Error('Presenter scene requires a presenter source in this project.');
    await KIT_HOST.use(K, config.presenter);
  }
  const list = config.scenes.map(s => {
    if (s.scene !== 'custom') return s;
    const f = window.sceneDrawings?.[s.render];
    if (typeof f !== 'function') throw new Error('No project drawing registered for ' + s.render);
    return {...s, render: f};
  });
  const planned = await K.film(list);
  if (Math.abs(planned.at(-1).t1 - T.duration) > 1e-7) throw new Error('Last scene must cover all prepared shot frames');
  await K.go();
  window.sceneMoves = () => {
    const presenterRuns = [];
    for (let n=0; n<frames; n++) {
      const t=n/fps;
      const i=sceneAt(n);
      FB.cur={shot:i,t};
      shots[i].f(t);
      const scene=planned[i];
      const state=scene.p.host || (scene.scene==='host' ? 'room' : 'off');
      const last=presenterRuns.at(-1);
      if (last && last.state===state) last.end_frame=n+1;
      else presenterRuns.push({state,start_frame:n,end_frame:n+1});
    }
    const {shots: spans}=window.KIT_REPORT;
    const moves=MV.LOG.map(move=>({...move,frame:Math.round(move.t*fps)}));
    const seq=spans.map((s,i)=>[s.scene,s.enter,...moves.filter(m=>m.shot===i).map(m=>m.move)].join('/'));
    return {shots:spans,moves,seq,presenter_runs:presenterRuns};
  };
  return window.KIT_REPORT;
};
