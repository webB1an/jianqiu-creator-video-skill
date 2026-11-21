import React from 'react';
import {AbsoluteFill, Easing, interpolate, spring, useCurrentFrame} from 'remotion';

const serif = '"Songti SC", "STSong", "Noto Serif CJK SC", serif';
const sans = '"Heiti SC", "PingFang SC", "Noto Sans CJK SC", sans-serif';
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)};

function theme(params) {
  const light = params.theme === 'light';
  if (params.theme !== undefined && !['dark', 'light'].includes(params.theme)) throw new Error('theme 取 dark 或 light');
  return {
    paper: light ? '#F4EDDD' : '#131A17', text: light ? '#131A17' : '#F4EDDD',
    muted: light ? '#56605A' : '#A9AB9F', accent: params.accent ?? (light ? '#82501D' : '#EDA961'),
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

function stage(width, height, colors) {
  const portrait = width < height;
  return {
    unit: Math.min(width / 1080, height / 800),
    margin: width * (portrait ? 0.078 : 0.085),
    style: {background: colors.paper, color: colors.text, fontFamily: sans, overflow: 'hidden',
      overflowWrap: 'anywhere', whiteSpace: 'pre-wrap', boxSizing: 'border-box'},
  };
}

// Reserve space for every line from the first frame. Each reveal moves inside its own crop.
function Reveal({value, amount, fontSize, family = sans, weight = 500, style = {}}) {
  return <div style={{overflow: 'hidden', ...style}}><div style={{fontFamily: family, fontSize,
    fontWeight: weight, lineHeight: 1.32, transform: `translateY(${(1 - amount) * 100}%)`, opacity: amount}}>{value}</div></div>;
}

export function ChapterTitle({params, width, height, fps, frames}) {
  const frame = useCurrentFrame();
  const colors = theme(params);
  const {unit, margin, style} = stage(width, height, colors);
  const end = Math.min(frames - 1, Math.round(0.84 * fps));
  const rule = progress(frame, 0, end * 0.62);
  const title = progress(frame, end * 0.12, end);
  return <AbsoluteFill style={{...style, justifyContent: 'center', padding: `0 ${margin}px ${height * 0.05}px`}}>
    {params.kicker && <Reveal value={params.kicker} amount={rule} fontSize={26 * unit} style={{color: colors.accent, marginBottom: 19 * unit}}/>}
    <div style={{background: colors.accent, width: 108 * unit * rule, height: 3 * unit, marginBottom: 24 * unit}}/>
    <Reveal value={requiredText(params.title, 'title')} amount={title} fontSize={73 * unit} weight={700} family={serif}/>
  </AbsoluteFill>;
}

export function LowerThird({params, width, height, fps, frames}) {
  const frame = useCurrentFrame();
  const colors = theme(params);
  const {unit, margin, style} = stage(width, height, colors);
  const end = Math.min(frames - 1, Math.round(fps * 0.62));
  const amount = end < 2 || frame >= end ? 1 : spring({frame, fps, durationInFrames: end,
    config: {mass: 1, damping: 28, stiffness: 160, overshootClamping: true}});
  const name = requiredText(params.name, 'name');
  return <AbsoluteFill style={{...style, background: params.background ?? 'transparent'}}>
    <div style={{position: 'absolute', left: margin, bottom: height * 0.24, maxWidth: width - 2 * margin,
      boxSizing: 'border-box', backgroundColor: colors.paper, borderLeft: `${5 * unit}px solid ${colors.accent}`,
      padding: `${15 * unit}px ${26 * unit}px`, opacity: amount, transform: `translateX(${(amount - 1) * 62 * unit}px)`}}>
      <div style={{fontSize: 47 * unit, fontWeight: 700, lineHeight: 1.18}}>{name}</div>
      {params.role && <div style={{marginTop: 7 * unit, color: colors.muted, fontSize: 24 * unit, lineHeight: 1.3,
        opacity: progress(frame, end * 0.34, end)}}>{params.role}</div>}
    </div>
  </AbsoluteFill>;
}

export function TextHighlight({params, width, height, fps, frames}) {
  const frame = useCurrentFrame();
  const colors = theme(params);
  const {unit, margin, style} = stage(width, height, colors);
  if (!Array.isArray(params.segments) || !params.segments.length) throw new Error('segments 需要按原句顺序填写文本片段');
  const segments = params.segments.map((part) => {
    if (typeof part === 'string') return {text: requiredText(part, 'segment')};
    if (!part || typeof part !== 'object') throw new Error('高亮片段需要 text 和 at');
    requiredText(part.text, 'segment.text');
    if (!Number.isInteger(part.at) || part.at < 0 || part.at >= frames) throw new Error('segment.at 需在本镜帧范围内');
    return part;
  });
  if (!segments.some(s => s.at !== undefined)) throw new Error('至少给一个原句片段设置高亮帧 at');
  const stroke = Math.max(1, Math.round(fps * 0.28));
  return <AbsoluteFill style={{...style, justifyContent: 'center', padding: `0 ${margin}px ${height * 0.06}px`}}>
    {params.source && <div style={{color: colors.muted, fontSize: 26 * unit, marginBottom: 22 * unit}}>{params.source}</div>}
    <div style={{fontSize: 61 * unit, lineHeight: 1.65, fontFamily: serif, fontWeight: 600}}>
      {segments.map(({text, at}, index) => at === undefined ? <React.Fragment key={index}>{text}</React.Fragment> :
        <span key={index} style={{backgroundImage: `linear-gradient(${colors.accent}, ${colors.accent})`,
          backgroundRepeat: 'no-repeat', backgroundPosition: 'left 94%', WebkitBoxDecorationBreak: 'clone',
          boxDecorationBreak: 'clone', backgroundSize: `${progress(frame, at, Math.min(frames - 1, at + stroke)) * 100}% 0.085em`}}>{text}</span>)}
    </div>
  </AbsoluteFill>;
}

export function QuoteCard({params, width, height, fps, frames}) {
  const frame = useCurrentFrame();
  const colors = theme(params);
  const {unit, margin, style} = stage(width, height, colors);
  const end = Math.min(frames - 1, Math.round(fps * 0.95));
  const quote = requiredText(params.quote, 'quote');
  return <AbsoluteFill style={{...style, justifyContent: 'center', padding: `0 ${margin}px ${height * 0.06}px`}}>
    <div style={{color: colors.accent, fontSize: 102 * unit, lineHeight: 0.85, fontFamily: serif,
      opacity: progress(frame, 0, end * 0.4)}} aria-hidden="true">“</div>
    <Reveal value={quote} amount={progress(frame, end * 0.08, end * 0.7)} fontSize={57 * unit} family={serif} weight={600}/>
    <Reveal value={requiredText(params.credit, 'credit')} amount={progress(frame, end * 0.5, end)}
      fontSize={25 * unit} style={{color: colors.muted, marginTop: 26 * unit}}/>
  </AbsoluteFill>;
}

export function EndCard({params, width, height, fps, frames}) {
  const frame = useCurrentFrame();
  const colors = theme(params);
  const {unit, margin, style} = stage(width, height, colors);
  const end = Math.min(frames - 1, Math.round(fps * 0.86));
  const first = progress(frame, 0, end * 0.55);
  const last = progress(frame, end * 0.35, end);
  const tall = width < height;
  return <AbsoluteFill style={{...style, justifyContent: 'center', padding: `0 ${margin}px ${height * 0.05}px`}}>
    <div style={{display: 'grid', alignItems: 'center', gridTemplateColumns: tall ? '1fr' : '0.9fr 1.3fr',
      gap: tall ? 30 * unit : 60 * unit}}>
      <div>
        {params.credit && <div style={{color: colors.muted, fontSize: 25 * unit, marginBottom: 18 * unit,
          opacity: first}}>{params.credit}</div>}
        <Reveal value={requiredText(params.title, 'title')} amount={first} fontSize={65 * unit} family={serif} weight={700}/>
      </div>
      <div style={{position: 'relative', padding: tall ? `${25 * unit}px 0 0` : `0 0 0 ${36 * unit}px`}}>
        <div style={{background: colors.accent, position: 'absolute', top: 0, left: 0,
          width: tall ? `${first * 100}%` : 3 * unit, height: tall ? 3 * unit : `${first * 100}%`}}/>
        <Reveal value={requiredText(params.takeaway, 'takeaway')} amount={last} fontSize={40 * unit}/>
      </div>
    </div>
  </AbsoluteFill>;
}
