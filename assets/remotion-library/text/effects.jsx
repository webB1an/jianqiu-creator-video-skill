import React from 'react';
import {AbsoluteFill, Easing, interpolate, spring, useCurrentFrame} from 'remotion';

const serif = '"Songti SC", "STSong", "Noto Serif CJK SC", serif';
const sans = '"Heiti SC", "PingFang SC", "Noto Sans CJK SC", sans-serif';
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)};

function theme(params, hasBackground = false) {
  const light = params.theme === 'light';
  if (params.theme !== undefined && !['dark', 'light'].includes(params.theme)) throw new Error('theme 取 dark 或 light');
  const transparent = Boolean(params.transparent || hasBackground);
  return {
    paper: transparent ? 'transparent' : (light ? '#F4EDDD' : '#131A17'),
    plate: light ? 'rgba(244,237,221,0.92)' : 'rgba(19,26,23,0.88)',
    text: light ? '#131A17' : '#F4EDDD',
    muted: light ? '#56605A' : '#A9AB9F',
    accent: params.accent ?? (light ? '#82501D' : '#EDA961'),
    shadow: transparent ? (light ? '0 2px 14px rgba(244,237,221,0.75)' : '0 4px 24px rgba(0,0,0,0.65)') : undefined,
    transparent,
    light,
  };
}

function requiredText(value, label) {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`${label} 需要非空文字`);
  return value;
}

function progress(frame, from, to) {
  if (to <= from) return Number(frame >= from);
  return interpolate(frame, [from, to], [0, 1], clamp);
}

function stage(width, height, colors, frame = 0, frames = 90, ambient = true) {
  const portrait = width < height;
  const frac = frames > 1 ? frame / (frames - 1) : 0;
  const glowX = 24 + 52 * frac;
  const glowY = 26 + 18 * Math.sin(frac * Math.PI);
  const bg = colors.transparent
    ? 'transparent'
    : (ambient
      ? (colors.light
        ? `radial-gradient(ellipse at ${glowX.toFixed(1)}% ${glowY.toFixed(1)}%, rgba(130,80,29,0.06) 0%, transparent 56%), ${colors.paper}`
        : `radial-gradient(ellipse at ${glowX.toFixed(1)}% ${glowY.toFixed(1)}%, rgba(237,169,97,0.08) 0%, transparent 54%), radial-gradient(ellipse at ${(100 - glowX).toFixed(1)}% 82%, rgba(138,215,205,0.07) 0%, transparent 52%), ${colors.paper}`)
      : colors.paper);
  const camScale = ambient ? 1 + 0.032 * frac : 1;
  return {
    unit: Math.min(width / 1080, height / 800),
    margin: width * (portrait ? 0.078 : 0.085),
    camStyle: {transform: `scale(${camScale.toFixed(4)})`, transformOrigin: '50% 48%', width: '100%'},
    style: {background: bg, color: colors.text, fontFamily: sans, overflow: 'hidden',
      overflowWrap: 'anywhere', whiteSpace: 'pre-wrap', boxSizing: 'border-box', textShadow: colors.shadow},
  };
}

function Reveal({value, amount, fontSize, family = sans, weight = 500, style = {}}) {
  return <div style={{overflow: 'hidden', ...style}}><div style={{fontFamily: family, fontSize,
    fontWeight: weight, lineHeight: 1.32, transform: `translateY(${(1 - amount) * 100}%)`, opacity: amount}}>{value}</div></div>;
}

function LeafMark({size, color}) {
  return <svg width={size} height={size} viewBox="0 0 100 100" aria-hidden="true" style={{display: 'block'}}>
    <path fill={color} d="M9,82 C8,38 35,12 89,8 C71,23 34,50 9,82 Z"/>
    <path fill={color} d="M18,85 C34,61 60,35 91,13 C90,59 65,86 18,85 Z"/>
  </svg>;
}

export function ChapterTitle({params, width, height, fps, frames, hasBackground}) {
  const frame = useCurrentFrame();
  const colors = theme(params, hasBackground);
  const ambient = params.ambient !== false;
  const {unit, margin, style, camStyle} = stage(width, height, colors, frame, frames, ambient);
  const end = Math.min(frames - 1, Math.round(0.84 * fps));
  const rule = progress(frame, 0, end * 0.62);
  const title = progress(frame, end * 0.12, end);
  const barGlow = ambient ? 12 * Math.sin(frame * 0.09) : 0;
  return <AbsoluteFill style={{...style, justifyContent: 'center', padding: `0 ${margin}px ${height * 0.05}px`}}>
    <div style={camStyle}>
      {params.kicker && <Reveal value={params.kicker} amount={rule} fontSize={26 * unit} style={{color: colors.accent, marginBottom: 19 * unit}}/>}
      <div style={{background: colors.accent, width: (108 + barGlow) * unit * rule, height: 3 * unit, marginBottom: 24 * unit}}/>
      <Reveal value={requiredText(params.title, 'title')} amount={title} fontSize={73 * unit} weight={700} family={serif}/>
    </div>
  </AbsoluteFill>;
}

export function LowerThird({params, width, height, fps, frames}) {
  const frame = useCurrentFrame();
  const colors = theme(params);
  const {unit, margin, style} = stage(width, height, colors, frame, frames, false);
  const start = params.startFrame ?? 0;
  const stop = params.endFrame ?? frames;
  if (!Number.isInteger(start) || !Number.isInteger(stop) || start < 0 || start >= stop || stop > frames) {
    throw new Error('身份条需要有效的镜内区间 [startFrame, endFrame)');
  }
  const span = stop - start;
  const local = frame - start;
  const enterEnd = Math.min(span - 1, Math.round(fps * 0.56));
  const exitLen = span >= 16 ? Math.min(10, Math.floor(span * 0.22)) : 0;
  const exitStart = stop - exitLen;
  const enterAmount = local < 0 || frame >= stop ? 0 : enterEnd < 2 || local >= enterEnd ? 1 : spring({
    frame: local, fps, durationInFrames: enterEnd,
    config: {mass: 1, damping: 28, stiffness: 165, overshootClamping: true},
  });
  const exitAmount = exitLen > 0 && frame >= exitStart
    ? interpolate(frame, [exitStart, stop], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.in(Easing.cubic)})
    : 1;
  const amount = enterAmount * exitAmount;
  const name = requiredText(params.name, 'name');
  return <AbsoluteFill style={{...style, background: params.background ?? 'transparent'}}>
    <div style={{position: 'absolute', left: margin, bottom: height * 0.24, maxWidth: width - 2 * margin,
      boxSizing: 'border-box', backgroundColor: colors.plate, borderLeft: `${5 * unit}px solid ${colors.accent}`,
      borderRadius: `0 ${8 * unit}px ${8 * unit}px 0`,
      boxShadow: `0 ${10 * unit}px ${28 * unit}px rgba(0,0,0,${0.32 * amount})`,
      padding: `${15 * unit}px ${26 * unit}px`, opacity: amount, transform: `translateX(${(amount - 1) * 58 * unit}px)`}}>
      <div style={{fontSize: 47 * unit, fontWeight: 700, lineHeight: 1.18}}>{name}</div>
      {params.role && <div style={{marginTop: 7 * unit, color: colors.muted, fontSize: 24 * unit, lineHeight: 1.3,
        opacity: progress(local, enterEnd * 0.34, enterEnd) * exitAmount}}>{params.role}</div>}
    </div>
  </AbsoluteFill>;
}

export function TextHighlight({params, width, height, fps, frames, hasBackground}) {
  const frame = useCurrentFrame();
  const colors = theme(params, hasBackground);
  const ambient = params.ambient !== false;
  const {unit, margin, style, camStyle} = stage(width, height, colors, frame, frames, ambient);
  if (!Array.isArray(params.segments) || !params.segments.length) throw new Error('segments 需要按原句顺序填写文本片段');
  const segments = params.segments.map((part) => {
    if (typeof part === 'string') return {text: requiredText(part, 'segment')};
    if (!part || typeof part !== 'object') throw new Error('高亮片段需要 text 和 at');
    requiredText(part.text, 'segment.text');
    if (!Number.isInteger(part.at) || part.at < 0 || part.at >= frames) throw new Error('segment.at 需在本镜帧范围内');
    if (part.until !== undefined && (!Number.isInteger(part.until) || part.until < part.at || part.until >= frames)) {
      throw new Error('segment.until 须位于 at 和本镜末帧之间');
    }
    return part;
  });
  if (!segments.some(s => s.at !== undefined)) throw new Error('至少给一个原句片段设置高亮帧 at');
  const stroke = Math.max(1, Math.round(fps * 0.28));
  return <AbsoluteFill style={{...style, justifyContent: 'center', padding: `0 ${margin}px ${height * 0.06}px`}}>
    <div style={camStyle}>
      {params.source && <div style={{color: colors.muted, fontSize: 26 * unit, marginBottom: 22 * unit}}>{params.source}</div>}
      <div style={{fontSize: 61 * unit, lineHeight: 1.65, fontFamily: serif, fontWeight: 600}}>
        {segments.map(({text, at, until}, index) => at === undefined ? <React.Fragment key={index}>{text}</React.Fragment> :
          <span key={index} style={{backgroundImage: `linear-gradient(${colors.accent}, ${colors.accent})`,
            backgroundRepeat: 'no-repeat', backgroundPosition: 'left 94%', WebkitBoxDecorationBreak: 'clone',
            boxDecorationBreak: 'clone', backgroundSize: `${progress(frame, at, until ?? Math.min(frames - 1, at + stroke)) * 100}% 0.085em`}}>{text}</span>)}
      </div>
    </div>
  </AbsoluteFill>;
}

export function QuoteCard({params, width, height, fps, frames, hasBackground}) {
  const frame = useCurrentFrame();
  const colors = theme(params, hasBackground);
  const ambient = params.ambient !== false;
  const {unit, margin, style, camStyle} = stage(width, height, colors, frame, frames, ambient);
  const end = Math.min(frames - 1, Math.round(fps * 0.95));
  const quote = requiredText(params.quote, 'quote');
  const ruleW = 72 * unit * progress(frame, end * 0.4, Math.min(frames - 1, end + Math.round(fps * 0.6)));
  return <AbsoluteFill style={{...style, justifyContent: 'center', padding: `0 ${margin}px ${height * 0.06}px`}}>
    <div style={camStyle}>
      <div style={{color: colors.accent, fontSize: 102 * unit, lineHeight: 0.85, fontFamily: serif,
        opacity: progress(frame, 0, end * 0.4)}} aria-hidden="true">“</div>
      <Reveal value={quote} amount={progress(frame, end * 0.08, end * 0.7)} fontSize={57 * unit} family={serif} weight={600}/>
      <div style={{width: ruleW, height: 3 * unit, background: colors.accent, opacity: 0.75, marginTop: 22 * unit}}/>
      <Reveal value={requiredText(params.credit, 'credit')} amount={progress(frame, end * 0.5, end)}
        fontSize={25 * unit} style={{color: colors.muted, marginTop: 16 * unit}}/>
    </div>
  </AbsoluteFill>;
}

export function EndCard({params, width, height, fps, frames, hasBackground}) {
  const frame = useCurrentFrame();
  const colors = theme(params, hasBackground);
  const ambient = params.ambient !== false;
  const {unit, margin, style, camStyle} = stage(width, height, colors, frame, frames, ambient);
  const end = Math.min(frames - 1, Math.round(fps * 0.86));
  const first = progress(frame, 0, end * 0.55);
  const last = progress(frame, end * 0.35, end);
  const tall = width < height;
  const items = Array.isArray(params.items) && params.items.length ? params.items : null;
  const itemCues = items
    ? (Array.isArray(params.cues) && params.cues.length === items.length
      ? params.cues
      : items.map((_, i) => Math.round(end * 0.35 + i * Math.min(fps * 0.45, (frames - end) / (items.length + 1)))))
    : null;
  return <AbsoluteFill style={{...style, justifyContent: 'center', padding: `0 ${margin}px ${height * 0.05}px`}}>
    <div style={camStyle}>
      <div style={{display: 'grid', alignItems: 'center', gridTemplateColumns: tall ? '1fr' : '0.9fr 1.3fr',
        gap: tall ? 30 * unit : 60 * unit}}>
        <div>
          {(params.credit || params.mark) && <div style={{display: 'flex', alignItems: 'center', gap: 12 * unit,
            marginBottom: 18 * unit, opacity: first}}>
            {params.mark && <LeafMark size={30 * unit} color={colors.accent}/>}
            {params.credit && <span style={{color: colors.muted, fontSize: 25 * unit}}>{params.credit}</span>}
          </div>}
          <Reveal value={requiredText(params.title, 'title')} amount={first} fontSize={65 * unit} family={serif} weight={700}/>
        </div>
        <div style={{position: 'relative', padding: tall ? `${25 * unit}px 0 0` : `0 0 0 ${36 * unit}px`}}>
          <div style={{background: colors.accent, position: 'absolute', top: 0, left: 0,
            width: tall ? `${first * 100}%` : 3 * unit, height: tall ? 3 * unit : `${first * 100}%`}}/>
          {items ? <div style={{display: 'flex', flexWrap: 'wrap', gap: 14 * unit}}>
            {items.map((label, i) => {
              const local = frame - itemCues[i];
              const pop = local < 0 ? 0 : spring({frame: local, fps, durationInFrames: Math.max(8, Math.round(fps * 0.4)),
                config: {damping: 22, stiffness: 175, overshootClamping: true}});
              return <div key={i} style={{
                opacity: pop, transform: `translateY(${(1 - pop) * 16 * unit}px)`,
                padding: `${10 * unit}px ${20 * unit}px`, borderRadius: 10 * unit,
                background: colors.plate, border: `${1.5 * unit}px solid ${colors.accent}`,
                fontSize: 32 * unit, fontWeight: 600, whiteSpace: 'nowrap',
              }}>{label}</div>;
            })}
          </div> : <Reveal value={requiredText(params.takeaway, 'takeaway')} amount={last} fontSize={40 * unit}/>}
        </div>
      </div>
    </div>
  </AbsoluteFill>;
}

export function KineticHook({params, width, height, fps, frames, hasBackground}) {
  const frame = useCurrentFrame();
  const colors = theme(params, hasBackground);
  const ambient = params.ambient !== false;
  const {unit, margin, style, camStyle} = stage(width, height, colors, frame, frames, ambient);
  const lines = params.lines;
  if (!Array.isArray(lines) || lines.length < 1 || lines.length > 3 || lines.some(l => typeof l !== 'string' || !l.trim())) {
    throw new Error('lines 需要 1–3 行非空标题文字');
  }
  const cues = params.cues ?? lines.map((_, i) => Math.round(i * fps * 0.24));
  const bg = colors.transparent ? 'transparent' : style.background;
  const hiPhrase = typeof params.highlight === 'string' && params.highlight.trim() ? params.highlight.trim() : null;
  const hiAt = params.highlightFrame ?? Math.min(frames - 1, (cues.at(-1) ?? 0) + Math.round(fps * 0.22));
  const hiSweep = progress(frame, hiAt, Math.min(frames - 1, hiAt + Math.round(fps * 0.28)));
  const renderLine = (text) => {
    if (!hiPhrase || !text.includes(hiPhrase)) return text;
    const idx = text.indexOf(hiPhrase);
    return <>{text.slice(0, idx)}<span style={{
      color: hiSweep > 0.35 ? (colors.light ? '#F4EDDD' : '#131A17') : colors.text,
      padding: `0 ${10 * unit}px`, borderRadius: 6 * unit, textShadow: 'none',
      backgroundImage: `linear-gradient(${colors.accent}, ${colors.accent})`,
      backgroundRepeat: 'no-repeat', backgroundSize: `${hiSweep * 100}% 100%`,
    }}>{hiPhrase}</span>{text.slice(idx + hiPhrase.length)}</>;
  };
  return <AbsoluteFill style={{...style, background: bg, justifyContent: 'center', padding: `0 ${margin}px ${height * 0.06}px`}}>
    <div style={camStyle}>
      {params.kicker && <div style={{display: 'flex', alignItems: 'center', gap: 14 * unit, marginBottom: 22 * unit,
        opacity: progress(frame, 0, Math.round(fps * 0.3))}}>
        <div style={{width: 44 * unit, height: 4 * unit, borderRadius: 2 * unit, background: colors.accent}}/>
        <span style={{color: colors.accent, fontSize: 27 * unit, fontWeight: 600, letterSpacing: '0.06em'}}>{params.kicker}</span>
      </div>}
      {lines.map((line, i) => {
        const local = frame - (cues[i] ?? 0);
        const pop = local < 0 ? 0 : spring({frame: local, fps, durationInFrames: Math.max(8, Math.round(fps * 0.48)),
          config: {damping: 26, stiffness: 170, overshootClamping: true}});
        return <div key={i} style={{overflow: 'hidden', marginBottom: 12 * unit}}>
          <div style={{fontFamily: serif, fontSize: 78 * unit, fontWeight: 700, lineHeight: 1.22,
            transform: `translateY(${(1 - pop) * 95}%)`, opacity: pop}}>
            {renderLine(line)}
          </div>
        </div>;
      })}
    </div>
  </AbsoluteFill>;
}

export function CalloutAnnotation({params, width, height, fps, frames, hasBackground}) {
  const frame = useCurrentFrame();
  const colors = theme(params, hasBackground);
  const {unit, style} = stage(width, height, colors, frame, frames, false);
  const target = params.target;
  if (!Array.isArray(target) || target.length !== 2 || !target.every(v => Number.isFinite(v) && v >= 0 && v <= 1)) {
    throw new Error('target 需要归一化坐标 [x, y]');
  }
  const label = requiredText(params.label, 'label');
  const start = params.startFrame ?? 0;
  const stop = params.endFrame ?? frames;
  if (!Number.isInteger(start) || !Number.isInteger(stop) || start < 0 || start >= stop || stop > frames) {
    throw new Error('CalloutAnnotation 需要有效的 [startFrame, endFrame)');
  }
  const span = stop - start;
  const exitLen = span >= 16 ? Math.min(8, Math.floor(span * 0.2)) : 0;
  const exitFade = exitLen > 0 && frame >= stop - exitLen
    ? interpolate(frame, [stop - exitLen, stop], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})
    : 1;
  const local = frame - start;
  if (local < 0 || frame >= stop) return <AbsoluteFill style={{...style, background: colors.transparent ? 'transparent' : colors.paper}}/>;
  const tx = target[0] * width, ty = target[1] * height;
  const boxPos = params.labelAt ?? [target[0] > 0.55 ? target[0] - 0.24 : target[0] + 0.16, Math.max(0.14, target[1] - 0.14)];
  const lx = boxPos[0] * width, ly = boxPos[1] * height;
  const elbowX = (tx + lx) / 2;
  const lineP = progress(local, 0, Math.min(span - 1, Math.round(fps * 0.32)));
  const cardP = progress(local, Math.round(fps * 0.18), Math.min(span - 1, Math.round(fps * 0.45)));
  const pulse = 0.5 + 0.5 * Math.sin(local * 0.22);
  const seg1 = Math.hypot(elbowX - tx, ly - ty);
  const seg2 = Math.abs(lx - elbowX);
  const totalLen = Math.max(1, seg1 + seg2);
  const drawn = totalLen * lineP;
  const p1 = Math.min(1, drawn / Math.max(1, seg1));
  const p2 = drawn > seg1 ? Math.min(1, (drawn - seg1) / Math.max(1, seg2)) : 0;
  const midX = tx + (elbowX - tx) * p1;
  const midY = ty + (ly - ty) * p1;
  const endX = elbowX + (lx - elbowX) * p2;
  const alignRight = lx < tx;
  return <AbsoluteFill style={{...style, background: colors.transparent ? 'transparent' : colors.paper, opacity: exitFade}}>
    <svg width={width} height={height} style={{position: 'absolute', inset: 0}}>
      <circle cx={tx} cy={ty} r={(10 + 7 * pulse) * unit} fill={colors.accent} opacity={0.28 * lineP}/>
      <circle cx={tx} cy={ty} r={6.5 * unit} fill={colors.accent} stroke={colors.paper} strokeWidth={2.5 * unit}/>
      <polyline points={p2 > 0 ? `${tx},${ty} ${elbowX},${ly} ${endX},${ly}` : `${tx},${ty} ${midX},${midY}`}
        fill="none" stroke={colors.accent} strokeWidth={3 * unit} strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
    <div style={{position: 'absolute', left: alignRight ? undefined : lx, right: alignRight ? width - lx : undefined,
      top: ly - 32 * unit, backgroundColor: colors.plate, border: `${2 * unit}px solid ${colors.accent}`,
      borderRadius: 10 * unit, padding: `${12 * unit}px ${20 * unit}px`,
      boxShadow: `0 ${10 * unit}px ${26 * unit}px rgba(0,0,0,0.34)`,
      opacity: cardP, transform: `translateY(${(1 - cardP) * 10 * unit}px)`}}>
      <div style={{fontSize: 30 * unit, fontWeight: 700, lineHeight: 1.2, whiteSpace: 'nowrap'}}>{label}</div>
      {params.detail && <div style={{marginTop: 5 * unit, fontSize: 20 * unit, color: colors.muted, whiteSpace: 'nowrap'}}>{params.detail}</div>}
    </div>
  </AbsoluteFill>;
}

export function StickerCallout({params, width, height, fps, frames, hasBackground}) {
  const rawFrame = useCurrentFrame();
  // Vox signature 12fps deliberate stepped frame cadence over smooth 30fps live footage.
  const posterize = Number.isFinite(params.posterizeFps) ? Math.max(4, Math.min(fps, params.posterizeFps)) : 12;
  const stride = Math.max(1, Math.round(fps / posterize));
  const frame = Math.floor(rawFrame / stride) * stride;
  const colors = theme(params, hasBackground);
  const {unit, style} = stage(width, height, colors, frame, frames, false);
  const start = params.startFrame ?? 0;
  const stop = params.endFrame ?? frames;
  if (!Number.isInteger(start) || !Number.isInteger(stop) || start < 0 || start >= stop || stop > frames) {
    throw new Error('StickerCallout 需要有效的 [startFrame, endFrame)');
  }
  const local = frame - start;
  if (local < 0 || rawFrame >= stop) {
    return <AbsoluteFill style={{...style, background: colors.transparent ? 'transparent' : colors.paper}}/>;
  }
  const label = requiredText(params.label, 'label');
  const pos = Array.isArray(params.at) && params.at.length === 2 ? params.at : [0.5, 0.44];
  const x = pos[0] * width, y = pos[1] * height;
  const pop = spring({frame: local, fps, durationInFrames: Math.max(6, Math.round(fps * 0.34)),
    config: {damping: 14, stiffness: 210}});
  const baseTilt = Number.isFinite(params.tilt) ? params.tilt : -2.6;
  const wobble = Math.sin(Math.floor(rawFrame / stride) * 1.9) * 0.95;
  const jitterY = Math.cos(Math.floor(rawFrame / stride) * 2.3) * 1.8 * unit;
  const target = Array.isArray(params.target) && params.target.length === 2 ? params.target : null;
  const tx = target ? target[0] * width : null;
  const ty = target ? target[1] * height : null;
  const ringR = (params.ringRadius ?? 0.085) * Math.min(width, height);
  return <AbsoluteFill style={{...style, background: colors.transparent ? 'transparent' : colors.paper}}>
    {target && <svg width={width} height={height} style={{position: 'absolute', inset: 0}}>
      <ellipse cx={tx} cy={ty} rx={ringR * pop} ry={ringR * 0.72 * pop}
        fill="none" stroke={colors.accent} strokeWidth={4.5 * unit} strokeDasharray="16 8"
        transform={`rotate(${(-8 + wobble * 3).toFixed(1)} ${tx} ${ty})`}/>
      <path d={`M ${x} ${y} Q ${(x + tx) * 0.5} ${Math.min(y, ty) - 48 * unit} ${tx} ${ty - ringR * 0.75}`}
        fill="none" stroke={colors.accent} strokeWidth={3.5 * unit} strokeLinecap="round" opacity={pop}/>
    </svg>}
    <div style={{
      position: 'absolute', left: x, top: y,
      transform: `translate(-50%, -50%) translateY(${jitterY.toFixed(1)}px) rotate(${(baseTilt + wobble).toFixed(2)}deg) scale(${(0.7 + 0.3 * pop).toFixed(3)})`,
      background: colors.accent, color: colors.light ? '#F4EDDD' : '#131A17',
      padding: `${14 * unit}px ${26 * unit}px`, borderRadius: 10 * unit,
      border: `${3 * unit}px solid ${colors.light ? '#131A17' : '#F4EDDD'}`,
      boxShadow: `${6 * unit}px ${8 * unit}px 0 rgba(0,0,0,0.42)`,
      opacity: Math.min(1, pop * 1.3),
    }}>
      <div style={{fontSize: 38 * unit, fontWeight: 800, lineHeight: 1.15, whiteSpace: 'nowrap', textShadow: 'none'}}>{label}</div>
      {params.detail && <div style={{marginTop: 4 * unit, fontSize: 20 * unit, fontWeight: 600, opacity: 0.88, whiteSpace: 'nowrap', textShadow: 'none'}}>{params.detail}</div>}
    </div>
  </AbsoluteFill>;
}
