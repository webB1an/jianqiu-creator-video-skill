import React from 'react';
import {AbsoluteFill, Easing, Img, OffthreadVideo, interpolate, interpolateColors, staticFile, useCurrentFrame} from 'remotion';
import {evolvePath} from '@remotion/paths';

const INK = '#131A17';
const PAPER = '#F4EDDD';
const AQUA = '#9BC8BE';
const FONT = '"Avenir Next", "PingFang SC", "Heiti SC", sans-serif';
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'};

function bounded(value, label, min, max) {
  if (!Number.isFinite(value) || value < min || value > max) throw new Error(`${label} must be ${min}–${max}`);
  return value;
}

function progress(frame, frames, params, fraction, easing = Easing.inOut(Easing.cubic)) {
  const start = params.startFrame ?? 0;
  const end = params.settleFrame ?? Math.max(start, Math.round((frames - 1) * fraction));
  if (![start, end].every(Number.isInteger) || start < 0 || end < start || end >= frames) {
    throw new Error('Expected 0 ≤ startFrame ≤ settleFrame < frames');
  }
  if (start === end) return frame >= end ? 1 : 0;
  return interpolate(frame, [start, end], [0, 1], {...clamp, easing});
}

function backdrop(params) {
  return params.transparent ? 'transparent' : (params.background ?? INK);
}

export function GradientShift({params, frames}) {
  const t = progress(useCurrentFrame(), frames, params, .78);
  const from = params.from ?? ['#123C3B', '#171B18'];
  const to = params.to ?? ['#172C34', '#403124'];
  if (![from, to].every(pair => Array.isArray(pair) && pair.length === 2)) throw new Error('from and to require two colors each');
  const colors = from.map((color, i) => interpolateColors(t, [0, 1], [color, to[i]]));
  const degrees = interpolate(t, [0, 1], [params.fromAngle ?? 117, params.toAngle ?? 153]);
  const style = {backgroundImage: `radial-gradient(ellipse at ${18 + t * 18}% ${30 + t * 10}%, ${params.light ?? '#9BC8BE20'} 0%, transparent 68%), linear-gradient(${degrees}deg, ${colors[0]}, ${colors[1]})`};
  return <AbsoluteFill style={style}/>;
}

export function GridPulse({params, width, height, frames}) {
  const t = progress(useCurrentFrame(), frames, params, .8, Easing.linear);
  const columns = bounded(params.columns ?? 18, 'columns', 4, 48);
  if (!Number.isInteger(columns)) throw new Error('columns must be an integer');
  const [x, y] = params.origin ?? [.31, .52];
  const cx = bounded(x, 'origin x', 0, 1) * width, cy = bounded(y, 'origin y', 0, 1) * height;
  const gap = width / columns, rows = Math.ceil(height / gap);
  const edge = Math.hypot(Math.max(cx, width - cx), Math.max(cy, height - cy));
  const band = gap * 2.4, radius = (edge + 2 * band) * t - band;
  const strength = bounded(params.strength ?? .75, 'strength', 0, 1);
  return <AbsoluteFill style={{backgroundColor: backdrop(params)}}>
    <svg width="100%" height="100%" viewBox={`0 0 ${width} ${height}`} fill={params.color ?? AQUA}>
      {Array.from({length: (columns + 1) * (rows + 1)}, (_, i) => {
        const px = gap * (i % (columns + 1)), py = gap * Math.floor(i / (columns + 1));
        const glow = Math.max(0, 1 - Math.abs(Math.hypot(px - cx, py - cy) - radius) / band);
        return <circle key={i} cx={px} cy={py} r={gap * (.027 + .044 * glow)} opacity={.15 + .85 * glow * strength}/>;
      })}
    </svg>
  </AbsoluteFill>;
}

export function LetterboxReveal({params, height, frames}) {
  const t = progress(useCurrentFrame(), frames, params, .60);
  const start = bounded(params.from ?? .5, 'from', 0, .5), end = bounded(params.to ?? .065, 'to', 0, .5);
  const size = height * interpolate(t, [0, 1], [start, end]);
  if (typeof params.source !== 'string' || !params.source) throw new Error('source requires a local media file');
  const offset = params.sourceStart ?? 0, fit = params.fit ?? 'cover';
  if (!Number.isInteger(offset) || offset < 0) throw new Error('sourceStart must be a nonnegative integer');
  if (!['cover', 'contain'].includes(fit)) throw new Error('fit must be cover or contain');
  const media = {src: staticFile(params.source), style: {width: '100%', height: '100%', objectFit: fit}};
  const bar = {position: 'absolute', left: 0, right: 0, height: size, backgroundColor: params.color ?? INK};
  return <AbsoluteFill style={{backgroundColor: params.background ?? INK, overflow: 'hidden'}}>
    {/\.(mp4|webm|mov|m4v|mkv)$/i.test(params.source) ? <OffthreadVideo {...media} muted trimBefore={offset}/> : <Img {...media}/>}
    <div style={{...bar, top: 0}}/><div style={{...bar, bottom: 0}}/>
  </AbsoluteFill>;
}

export function LogoStrokeDraw({params, width, height, frames}) {
  const t = progress(useCurrentFrame(), frames, params, .60);
  const paths = params.paths;
  if (!Array.isArray(paths) || !paths.length || paths.some(d => typeof d !== 'string' || !d.trim())) {
    throw new Error('paths requires SVG path data for each stroke');
  }
  const box = params.viewBox ?? [0, 0, 100, 100];
  if (!Array.isArray(box) || box.length !== 4 || !box.every(Number.isFinite) || box[2] <= 0 || box[3] <= 0) throw new Error('Invalid SVG viewBox');
  const size = bounded(params.size ?? .4, 'size', .05, .9) * Math.min(width, height);
  const weight = bounded(params.strokeWidth ?? 3, 'strokeWidth', .01, Math.max(box[2], box[3]));
  return <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', backgroundColor: backdrop(params)}}>
    <svg width={size} height={size} viewBox={box.join(' ')} fill="none" stroke={params.color ?? PAPER} strokeWidth={weight} strokeLinecap="round" strokeLinejoin="round">
      {paths.map((d, i) => {
        const part = Math.max(0, Math.min(1, t * paths.length - i));
        return <path key={i} d={d} opacity={part > 0 ? 1 : 0} {...evolvePath(part, d)}/>;
      })}
    </svg>
  </AbsoluteFill>;
}

export function CreditsRoll({params, width, height, frames}) {
  const t = progress(useCurrentFrame(), frames, params, .85, Easing.linear);
  const rows = params.items;
  if (!Array.isArray(rows) || !rows.length || rows.some(row => !row || ![row.role, row.name].every(s => typeof s === 'string' && s.trim()))) {
    throw new Error('items requires a role and name per credit');
  }
  const rowHeight = height * bounded(params.spacing ?? .135, 'spacing', .09, .24);
  const text = Math.min(height * .054, width * .053, rowHeight * .46);
  const top = interpolate(t, [0, 1], [height * .87, height * .5 - rowHeight * (rows.length - 1)]);
  const fit = (s, available, max) => Math.min(max, available / Math.max(1, [...s].reduce((sum, c) => sum + (c.charCodeAt(0) < 256 ? .65 : 1.05), 0)));
  return <AbsoluteFill style={{backgroundColor: backdrop(params), overflow: 'hidden'}}>
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{fontFamily: FONT}}>
      {rows.map(({role, name}, i) => {
        const y = top + i * rowHeight;
        const opacity = interpolate(y / height, [.13, .26, .72, .89], [0, 1, 1, 0], clamp);
        return <g key={i} opacity={opacity} dominantBaseline="middle">
          <text x={width * .45} y={y} textAnchor="end" fontSize={fit(role, width * .37, text * .74)} fill={params.roleColor ?? AQUA}>{role}</text>
          <text x={width * .49} y={y} fontSize={fit(name, width * .43, text)} fill={params.color ?? PAPER}>{name}</text>
        </g>;
      })}
    </svg>
  </AbsoluteFill>;
}
