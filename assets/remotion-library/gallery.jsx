import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Player, Thumbnail} from '@remotion/player';
import {entries, views} from './gallery-registry.js';

window.remotion_staticBase = new URL('.', document.baseURI).pathname.replace(/\/$/, '');

const pretty = value => JSON.stringify(value, null, 2);
const categories = [...new Set(entries.map(entry => entry.category))];
const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const fileTypes = '.svg,.png,.jpg,.jpeg,.webp,.bmp,.mp4,.m4v,.mov,.webm,.mkv';
const fieldNames = {source:'原始素材', before:'对比前', after:'对比后', main:'主画面', insetSource:'小窗素材',
  back:'背景', front:'前景', first:'第一张原件', second:'第二张原件', outgoing:'前一镜', incoming:'后一镜'};
let uploadId = 0;

function clearUpload(item) {
  if (!item) return;
  window.remotion_staticFiles = (window.remotion_staticFiles ?? []).filter(file => file.name !== item.name);
  URL.revokeObjectURL(item.src);
}

function parseParams(value) {
  const parsed = JSON.parse(value);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('参数须为一个 JSON 对象。');
  return parsed;
}

// Show a readable value on data cards and the middle of the overlap on transition cards.
function thumbnailFrame(entry) {
  const {frames, fps, params} = entry.spec;
  if (entry.previewFrame !== undefined) return entry.previewFrame;
  if (entry.id === 'stat-counter') return frames - 1;
  if (entry.group === 'transitions' && entry.id !== 'focus-pair') {
    const overlap = params.overlap ?? Math.min(frames, Math.max(2, Math.round(fps * .5)));
    const start = params.atFrame ?? Math.floor((frames - overlap) / 2);
    return start + Math.floor(overlap / 2);
  }
  return Math.floor((frames - 1) * .57);
}

// The first row gives one example of each production use. Category views keep their catalog order.
const firstLook = ['image-comparison-slider', 'chapter-title', 'stat-counter', 'clock-wipe', 'logo-stroke-draw'];
const browseOrder = [...firstLook.map(id => entries.find(entry => entry.id === id)).filter(Boolean),
  ...entries.filter(entry => !firstLook.includes(entry.id))];

function playerOptions(entry, params, files) {
  const {width, height, fps, frames} = entry.spec;
  // Keep the portable @library paths in the recipe. Resolve only its media inputs for the browser.
  const browserParams = {...params};
  const resolve = value => {
    if (typeof value === 'string') return value.startsWith('@library/') ? value.slice(9) : value;
    if (Array.isArray(value)) return value.map(resolve);
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k,v]) => [k,resolve(v)]));
    return value;
  };
  for (const key of entry.media ?? []) {
    if (!(key in browserParams)) continue;
    const raw = browserParams[key];
    const file = files?.get(key);
    browserParams[key] = file && typeof raw === 'string' && !raw.startsWith('@library/') &&
      raw.split('/').pop() === file.filename ? file.name : resolve(raw);
  }
  return {component: views[entry.id], fps, durationInFrames: frames, compositionWidth: width, compositionHeight: height,
    inputProps: {width, height, fps, frames, params: browserParams}, style: {width: '100%', height: '100%'}};
}

function ErrorPanel({error, notify}) {
  const message = error?.message || '此组参数无法渲染';
  useEffect(() => {notify(message);}, [message, notify]);
  return <div className="rg-player-error"><strong>预览暂不可用</strong><span>{message}</span></div>;
}

function Preview({entry, params, files, notify}) {
  const ref = useRef(null);
  const options = useMemo(() => playerOptions(entry, params, files), [entry, params, files]);
  const [position, setPosition] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [broken, setBroken] = useState(false);
  const onError = useCallback(message => {setBroken(true); notify(message);}, [notify]);
  const errorView = useCallback(({error}) => <ErrorPanel error={error} notify={onError}/>, [onError]);
  const {frames, fps} = entry.spec;
  useEffect(() => {
    const player = ref.current;
    if (!player) return;
    const update = event => setPosition(event.detail.frame);
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    player.addEventListener('frameupdate', update);
    player.addEventListener('play', onPlay);
    player.addEventListener('pause', onPause);
    player.addEventListener('ended', onPause);
    setPosition(player.getCurrentFrame());
    setPlaying(player.isPlaying());
    return () => {
      player.removeEventListener('frameupdate', update);
      player.removeEventListener('play', onPlay);
      player.removeEventListener('pause', onPause);
      player.removeEventListener('ended', onPause);
    };
  }, []);
  function seek(value) {
    ref.current?.pause();
    ref.current?.seekTo(Math.max(0, Math.min(frames - 1, value)));
  }
  return <div className="rg-monitor">
    <div className={'rg-stage' + (entry.spec.width < entry.spec.height ? ' rg-stage-portrait' : '')}
      style={{aspectRatio: entry.spec.width + '/' + entry.spec.height}} onErrorCapture={event => {
        if (event.target.tagName === 'IMG' || event.target.tagName === 'VIDEO') {
          onError('浏览器未能打开素材，请检查文件路径，或选择浏览器支持的图片、视频编码。');
        }
      }}>
      <Player ref={ref} {...options} initialFrame={reducedMotion() ? Math.floor((frames - 1) * .57) : 0}
        autoPlay={!reducedMotion()} loop initiallyMuted numberOfSharedAudioTags={0}
        controls={false} clickToPlay={false} spaceKeyToPlayOrPause={false} errorFallback={errorView}/>
    </div>
    <div className="rg-transport" role="group" aria-label="播放控制">
      <button type="button" className="rg-play" disabled={broken} aria-label={playing ? '暂停' : '播放'}
        title={playing ? '暂停' : '播放'} onClick={event => ref.current?.toggle(event)}>
        <span aria-hidden="true" className={playing ? 'rg-icon-pause' : 'rg-icon-play'}/>
      </button>
      <output className="rg-time" aria-label="播放时刻">{(position / fps).toFixed(2)}s</output>
      <input className="rg-scrub" type="range" min="0" max={frames - 1} value={position} disabled={broken}
        aria-label="逐帧预览" aria-valuetext={`第 ${position} 帧，共 ${frames} 帧`}
        style={{'--rg-progress': `${100 * position / Math.max(1, frames - 1)}%`}}
        onChange={event => seek(Number(event.target.value))}/>
      <span className="rg-frame" title={frames + ' 帧'}>F{position}</span>
      <button className="rg-step" type="button" aria-label="上一帧" title="上一帧" disabled={broken || position === 0} onClick={() => seek(position - 1)}>‹</button>
      <button className="rg-step" type="button" aria-label="下一帧" title="下一帧" disabled={broken || position === frames - 1} onClick={() => seek(position + 1)}>›</button>
    </div>
  </div>;
}

const Card = React.memo(function Card({entry, active, choose}) {
  const [ready, setReady] = useState(false);
  const ref = useRef(null);
  const options = useMemo(() => playerOptions(entry, entry.spec.params), [entry]);
  useEffect(() => {
    if (!('IntersectionObserver' in window)) {setReady(true); return;}
    const observer = new IntersectionObserver(([item]) => {
      if (item.isIntersecting) {setReady(true); observer.disconnect();}
    }, {rootMargin:'240px'});
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return <button className={'rg-card' + (active ? ' rg-active' : '')} type="button" ref={ref}
    aria-label={'预览' + entry.name} aria-pressed={active} onClick={event => choose(entry, event.detail === 0)}>
    <div className="rg-tile">
      {ready && <Thumbnail {...options} frameToDisplay={thumbnailFrame(entry)}
        errorFallback={() => <span className="rg-thumb-error">缩略帧不可用</span>}/>}
    </div>
    <span className="rg-card-caption"><span><strong>{entry.name}</strong><small>{entry.category}</small></span>
      <span className="rg-card-sign" aria-hidden="true">{active ? '●' : '↗'}</span>
    </span>
  </button>;
});

async function copySpec(value) {
  if (navigator.clipboard) {
    try {await navigator.clipboard.writeText(value); return;} catch { /* file:// can lack clipboard permission */ }
  }
  const prior = document.activeElement;
  const box = document.createElement('textarea');
  box.value = value; box.style.position = 'fixed'; box.style.opacity = '0'; document.body.appendChild(box); box.select();
  let ok;
  try {ok = document.execCommand('copy');} finally {box.remove(); prior?.focus?.();}
  if (!ok) throw new Error('无法访问剪贴板，可以下载 JSON。');
}

function Editor({entry}) {
  const initial = pretty(entry.spec.params);
  const [draft, setDraft] = useState(initial);
  const [params, setParams] = useState(entry.spec.params);
  const [portrait, setPortrait] = useState(entry.spec.height > entry.spec.width);
  const files = useRef(new Map());
  useEffect(() => {
    const held = files.current;
    return () => {held.forEach(clearUpload); held.clear();};
  }, []);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [expanded, setExpanded] = useState(false);
  const [runtimeError, setRuntimeError] = useState('');
  const dirty = draft !== pretty(params);
  const short = Math.min(entry.spec.width, entry.spec.height);
  const long = Math.max(entry.spec.width, entry.spec.height);
  const layout = useMemo(() => ({...entry, spec: {...entry.spec, width: portrait ? short : long,
    height: portrait ? long : short}}), [entry, portrait, short, long]);
  const spec = {...layout.spec, params};
  function apply(value) {
    try {
      const parsed = parseParams(value);
      for (const field of entry.media ?? []) {
        const path = parsed[field];
        if (typeof path === 'string' && !path.startsWith('@library/') && files.current.get(field)?.filename !== path.split('/').pop()) {
          throw new Error('请先为“' + (fieldNames[field] ?? field) + '”选择对应的本地文件。');
        }
      }
      setParams(parsed); setDraft(pretty(parsed)); setRevision(value => value + 1);
      setError(''); setRuntimeError(''); setStatus('已应用参数');
    } catch (issue) {setError(issue.message); setStatus('');}
  }
  function reset() {
    files.current.forEach(clearUpload); files.current.clear();
    apply(initial);
    setStatus('已恢复示例');
  }
  function selectFile(field, file) {
    if (!file) return;
    try {
      const parsed = parseParams(draft);
      const ext = file.name.split('.').pop().toLowerCase();
      if (!fileTypes.split(',').includes('.' + ext)) throw new Error('请选择支持的图片或视频格式。');
      const collision = [...files.current.entries()].some(([key, other]) => key !== field && other.filename === file.name);
      const prior = files.current.get(field);
      const name = 'gallery-local/' + (++uploadId) + '.' + ext;
      const item = {name, src:URL.createObjectURL(file), filename:file.name, sizeInBytes:file.size,
        lastModified:file.lastModified};
      window.remotion_staticFiles = [...(window.remotion_staticFiles ?? []), item];
      files.current.set(field, item);
      clearUpload(prior);
      parsed[field] = collision ? field + '/' + file.name : file.name;
      const value = pretty(parsed);
      setDraft(value);
      apply(value);
    } catch (issue) {setError(issue.message); setStatus('');}
  }
  const onRuntimeError = useCallback(message => {
    setRuntimeError(message); setStatus(''); setExpanded(true);
  }, []);
  async function copy() {
    try {await copySpec(pretty(spec)); setStatus('已复制完整配置');}
    catch (issue) {setStatus(issue.message);}
  }
  function download() {
    const url = URL.createObjectURL(new Blob([pretty(spec) + '\n'], {type:'application/json'}));
    const a = document.createElement('a');
    a.href = url; a.download = entry.id + '.json'; document.body.appendChild(a); a.click(); a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000); setStatus('已下载完整配置');
  }
  return <section className="rg-workbench" id="rg-preview" aria-label="当前动效">
    <Preview key={revision} entry={layout} params={params} files={files.current} notify={onRuntimeError}/>
    <div className="rg-inspector">
      <span className="rg-category">{entry.category}</span>
      <h2 className="rg-effect-name" tabIndex="-1">{entry.name}</h2>
      <p className="rg-description">{entry.description}</p>
      <dl className="rg-facts"><div><dt><label htmlFor="rg-format">画幅</label></dt><dd>
        <select id="rg-format" value={portrait ? 'portrait' : 'landscape'} onChange={event => setPortrait(event.target.value === 'portrait')}>
          <option value="landscape">横屏 · {long} × {short}</option><option value="portrait">竖屏 · {short} × {long}</option>
        </select></dd></div>
        <div><dt>时长</dt><dd>{(entry.spec.frames / entry.spec.fps).toFixed(2)} s</dd></div><div><dt>帧率</dt><dd>{entry.spec.fps} fps</dd></div></dl>
      <details className="rg-details" open={expanded} onToggle={event => setExpanded(event.currentTarget.open)}>
        <summary>编辑参数 <span aria-hidden="true">{expanded ? '−' : '+'}</span></summary>
        {!!entry.media?.length && <div className="rg-media-fields">
          {entry.media.map(field => <label key={field}><span>{fieldNames[field] ?? field}</span>
            <span className="rg-file-picker">{files.current.get(field)?.filename ?? '选择本地文件'}
              <input type="file" accept={fileTypes} aria-label={'选择' + (fieldNames[field] ?? field)}
                onChange={event => {selectFile(field, event.target.files[0]); event.target.value = '';}}/>
            </span></label>)}
          <small>导出后，把所选素材按配置中的相对路径放在 JSON 旁。</small>
        </div>}
        <label className="rg-parameter-help" htmlFor="rg-json">{entry.parameters}</label>
        <textarea id="rg-json" className="rg-json" rows="6" value={draft} spellCheck={false} aria-label="动效参数 JSON"
          aria-invalid={Boolean(error || runtimeError)} onChange={event => {setDraft(event.target.value); setError(''); setStatus('');}}
          onKeyDown={event => {if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {event.preventDefault(); apply(draft);}}}/>
        {(error || runtimeError) && <p className="rg-error" role="alert">{error || runtimeError}</p>}
        <div className="rg-edit-actions"><button type="button" onClick={() => apply(draft)}>应用预览</button>
          <button type="button" onClick={reset}>恢复示例</button></div>
        <div className="rg-provenance"><p><strong>实现</strong> {entry.implementation}</p>
          <a href={entry.reference} target="_blank" rel="noopener noreferrer">参考示例 <span aria-hidden="true">↗</span></a></div>
        {entry.exampleCredit && <small className="rg-parameter-help">{entry.exampleCredit}</small>}
      </details>
      <div className="rg-actions"><button className="rg-primary" type="button" disabled={dirty || !!runtimeError} onClick={copy}>复制配置</button>
        <button className="rg-secondary" type="button" disabled={dirty || !!runtimeError} onClick={download}>下载 JSON <span aria-hidden="true">↓</span></button></div>
      <span className="rg-status" role="status">{status || (dirty ? '应用预览后可导出配置' : '')}</span>
    </div>
  </section>;
}

function Gallery() {
  const [selected, setSelected] = useState(entries.find(x => x.id === new URLSearchParams(location.search).get('effect')) ??
    entries.find(x => x.id === 'image-comparison-slider') ?? entries[0]);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('');
  const search = useRef(null);
  useEffect(() => {
    const shortcut = event => {
      if (event.key !== '/' || event.ctrlKey || event.altKey || event.metaKey || /INPUT|TEXTAREA|SELECT/.test(event.target.tagName)) return;
      event.preventDefault(); search.current?.focus();
    };
    document.addEventListener('keydown', shortcut);
    return () => document.removeEventListener('keydown', shortcut);
  }, []);
  const choose = useCallback((entry, keyboard) => {
    setSelected(entry);
    document.getElementById('rg-preview')?.scrollIntoView({block:'start', behavior: reducedMotion() ? 'instant' : 'smooth'});
    if (keyboard) requestAnimationFrame(() => document.querySelector('.rg-effect-name')?.focus({preventScroll:true}));
  }, []);
  const shown = useMemo(() => {
    const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    return (category || words.length ? entries : browseOrder).filter(entry => {
      const all = [entry.id, entry.name, entry.category, entry.description, ...entry.tags].join(' ').toLocaleLowerCase();
      return (!category || entry.category === category) && words.every(word => all.includes(word));
    });
  }, [category, query]);
  return <div className="rg-shell">
    <header className="rg-header"><div className="rg-brand"><span className="rg-mark" aria-hidden="true">见</span>
      <span className="rg-brand-word">见秋</span><h1>Remotion 动效库</h1></div>
      <a className="rg-nav" href="#rg-browse">浏览素材 <span aria-hidden="true">↘</span></a></header>
    <main>{selected && <Editor key={selected.id} entry={selected}/>}
      <section className="rg-browse" id="rg-browse" aria-label="浏览本地动效">
        <div className="rg-browse-head"><div className="rg-list-title"><h2>素材目录</h2>
          <span className="rg-count" aria-live="polite">{shown.length} 项</span></div>
          <label className="rg-search"><i aria-hidden="true"/><input type="search" ref={search} value={query} onChange={event => setQuery(event.target.value)}
            placeholder="搜索名称、用途或关键词" aria-label="搜索动效"/><kbd aria-hidden="true">/</kbd></label></div>
        <div className="rg-filters" role="group" aria-label="动效分类">
          {['', ...categories].map(item => <button type="button" key={item || 'all'} aria-pressed={category === item}
            onClick={() => setCategory(item)}>{item || '全部'}</button>)}</div>
        <div className="rg-grid">{shown.map(entry => <Card key={entry.id} entry={entry} active={selected?.id === entry.id} choose={choose}/>)}</div>
        {!shown.length && <div className="rg-empty"><h3>没有匹配的动效</h3><button type="button" onClick={() => {setQuery(''); setCategory('');}}>清除筛选</button></div>}
      </section>
    </main>
  </div>;
}

createRoot(document.getElementById('root')).render(<Gallery/>);
