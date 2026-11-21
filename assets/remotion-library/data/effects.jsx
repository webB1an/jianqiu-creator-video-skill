import React from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from 'remotion';
import {evolvePath} from '@remotion/paths';
import {makePie} from '@remotion/shapes';

const PAPER = '#F4EDDD';
const INK = '#131A17';
const MUTED = '#56605A';
const RULE = '#C9C4B5';
const COLORS = ['#176663', '#82501D', '#4F687B', '#836653', '#5C6651', '#83667A'];
const LABEL = '"Avenir Next", "PingFang SC", "Heiti SC", sans-serif';
const TITLE = '"Songti SC", "STSong", serif';
const bounds = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'};
const ease = {...bounds, easing: Easing.out(Easing.cubic)};

function number(value, field) {
  if (!Number.isFinite(value)) throw new Error(field + ' requires a finite number');
  return value;
}

function format(value, decimals) {
  if (decimals !== undefined && (!Number.isInteger(decimals) || decimals < 0 || decimals > 12)) {
    throw new Error('decimals must be an integer from 0 to 12');
  }
  return new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: decimals ?? 0, maximumFractionDigits: decimals ?? 12,
  }).format(value);
}

function series(params, min, max) {
  const rows = params.items;
  if (!Array.isArray(rows) || rows.length < min || rows.length > max) {
    throw new Error('items requires ' + min + '–' + max + ' entries');
  }
  rows.forEach(row => {
    if (typeof row?.label !== 'string' || !row.label.trim()) throw new Error('Each item needs a label');
    number(row.value, row.label);
  });
  return rows;
}

function cueRange(params, frames) {
  const start = params.startFrame ?? 0;
  const settle = params.settleFrame ?? Math.max(start, Math.round((frames - 1) * 0.69));
  if (![start, settle].every(Number.isInteger) || start < 0 || settle < start || settle >= frames) {
    throw new Error('Expected 0 ≤ startFrame ≤ settleFrame < composition frames');
  }
  return [start, settle];
}

function reveal(frame, start, end) {
  if (frame >= end) return 1;
  if (frame <= start) return 0;
  return interpolate(frame, [start, end], [0, 1], ease);
}

// A shorter label stays at the design size; wide CJK glyphs get their full em.
function fitted(text, available, preferred) {
  const ems = Array.from(String(text)).reduce((sum, c) => sum + (c.charCodeAt(0) > 255 ? 1.06 : .66), 0);
  return Math.min(preferred, available / Math.max(1, ems));
}

function Board({width, height, params, children}) {
  const h = Math.max(445, Math.min(1100, height / width * 1000));
  const top = h * .13, bottom = h * .79;
  return <AbsoluteFill style={{backgroundColor: PAPER}}>
    <svg width="100%" height="100%" viewBox={'0 0 1000 ' + h} style={{fontFamily: LABEL, color: INK, fontVariantNumeric: 'tabular-nums'}}>
      {params.title && <text x="70" y={top} fontFamily={TITLE} fontWeight="700" fill={INK}
        fontSize={fitted(params.title, 860, 47)}>{params.title}</text>}
      {children({top: params.title ? top + 53 : h * .19, bottom, h})}
    </svg>
  </AbsoluteFill>;
}

export function StatCounter({params, width, height, frames}) {
  const frame = useCurrentFrame();
  const from = number(params.from ?? 0, 'from'), to = number(params.to, 'to');
  const [start, settle] = cueRange(params, frames);
  const p = reveal(frame, start, settle);
  const value = p === 1 ? to : interpolate(p, [0, 1], [from, to]);
  // By default the rolling number uses the precision of its supplied endpoints.
  const decimals = params.decimals ?? Math.max(...[from, to].map(n => (format(n).split('.')[1] || '').length));
  const unit = params.unit ?? '';
  const endLabels = [format(from, decimals) + unit, format(to, decimals) + unit];
  const size = Math.min(...endLabels.map(label => fitted(label, 840, 150)));
  return <Board {...{width, height, params}}>{({top, bottom}) => {
    const mid = (top + bottom) / 2;
    return <g>
      <text x="70" y={mid + size * .24} fontSize={size} fontWeight="600" fill={params.color ?? COLORS[0]}>
        {format(value, decimals)}<tspan dx={unit ? 10 : 0} fontSize={size * .33}>{unit}</tspan>
      </text>
      <line x1="70" x2="930" y1={bottom} y2={bottom} stroke={RULE} strokeWidth="2"/>
    </g>;
  }}</Board>;
}

export function BarChart({params, width, height, frames}) {
  const frame = useCurrentFrame(), rows = series(params, 1, 6);
  const [start, settle] = cueRange(params, frames);
  const low = Math.min(0, ...rows.map(row => row.value));
  const high = Math.max(0, ...rows.map(row => row.value));
  const scale = n => interpolate(n, [low, high === low ? low + 1 : high], [282, 725], bounds);
  const zero = scale(0);
  return <Board {...{width, height, params}}>{({top, bottom}) => {
    const stride = (bottom - top) / rows.length;
    return <g>
      <line x1={zero} x2={zero} y1={top} y2={bottom} stroke={MUTED} strokeWidth="1.5"/>
      {rows.map((row, i) => {
        const y = top + stride * (i + .5);
        const offset = (settle - start) * .2 * i / Math.max(1, rows.length - 1);
        const p = reveal(frame, start + offset, settle);
        const tip = zero + (scale(row.value) - zero) * p;
        const weight = Math.min(25, stride * .33);
        const text = format(row.value, params.decimals) + (params.unit ?? '');
        return <g key={i}>
          <line x1="282" x2="725" y1={y} y2={y} stroke={RULE} strokeWidth="1"/>
          <text x="70" y={y} dominantBaseline="middle" fontSize={fitted(row.label, 194, 29)} fill={INK}>{row.label}</text>
          <rect x={Math.min(zero, tip)} y={y - weight / 2} width={Math.abs(tip - zero)} height={weight} fill={row.color ?? COLORS[i]}/>
          <text x="930" y={y} textAnchor="end" dominantBaseline="middle" opacity={p} fontSize={fitted(text, 188, 35)} fontWeight="600" fill={INK}>{text}</text>
        </g>;
      })}
    </g>;
  }}</Board>;
}

export function LineChart({params, width, height, frames}) {
  const frame = useCurrentFrame(), rows = series(params, 2, 240);
  const [start, settle] = cueRange(params, frames);
  const p = reveal(frame, start, settle);
  const hasX = rows.some(row => row.x !== undefined);
  const xs = rows.map((row, i) => hasX ? number(row.x, 'x') : i);
  if (xs.some((x, i) => i > 0 && x <= xs[i - 1])) throw new Error('x coordinates must strictly increase');
  const low = Math.min(...rows.map(row => row.value)), high = Math.max(...rows.map(row => row.value));
  const color = params.color ?? COLORS[0];
  const unit = params.unit ?? '';
  return <Board {...{width, height, params}}>{({top, bottom}) => {
    const xMin = 260, xMax = 905;
    const points = rows.map((row, i) => ({
      x: interpolate(xs[i], [xs[0], xs.at(-1)], [xMin, xMax]),
      y: high === low ? (top + bottom) / 2 : interpolate(row.value, [low, high], [bottom, top]),
    }));
    const path = points.map(({x, y}, i) => (i ? 'L' : 'M') + x + ' ' + y).join(' ');
    const axis = high === low ? [0.5] : [0, 0.5, 1];
    return <g>
      {axis.map(t => {
        const value = format(low + (high - low) * t, params.decimals) + unit;
        const y = bottom + (top - bottom) * t;
        return <g key={t}><line x1={xMin} x2={xMax} y1={y} y2={y} stroke={RULE}/>
          <text x={xMin - 18} y={y} textAnchor="end" dominantBaseline="middle" fill={MUTED} fontSize={fitted(value, 162, 25)}>{value}</text></g>;
      })}
      <path d={path} fill="none" stroke={color} strokeWidth="6" strokeLinejoin="round"
        {...evolvePath(p, path)}/>
      {p > 0 && <circle cx={points[0].x} cy={points[0].y} r="6" fill={color}/>}
      <circle cx={points.at(-1).x} cy={points.at(-1).y} r="7" opacity={reveal(p, .97, 1)} fill={color}/>
      <text x={xMin} y={bottom + 39} fontSize={fitted(rows[0].label, 298, 27)} fill={INK}>{rows[0].label}</text>
      <text x={xMax} y={bottom + 39} textAnchor="end" fontSize={fitted(rows.at(-1).label, 298, 27)} fill={INK}>{rows.at(-1).label}</text>
    </g>;
  }}</Board>;
}

export function DonutChart({params, width, height, frames}) {
  const frame = useCurrentFrame(), rows = series(params, 1, 6);
  if (rows.some(row => row.value < 0)) throw new Error('Ring segments require nonnegative values');
  const total = rows.reduce((sum, row) => sum + row.value, 0);
  if (!Number.isFinite(total) || total <= 0) throw new Error('Ring total must be positive and finite');
  const [start, settle] = cueRange(params, frames);
  const p = reveal(frame, start, settle);
  return <Board {...{width, height, params}}>{({top, bottom}) => {
    const radius = Math.min(140, (bottom - top) * .39);
    const cy = (top + bottom) / 2, cx = 252;
    const sumLabel = format(total, params.decimals) + (params.unit ?? '');
    let offset = 0;
    return <g>
      <circle cx={cx} cy={cy} r={radius} fill="none" stroke={RULE} strokeWidth={radius * .18}/>
      {rows.map((row, i) => {
        const startFraction = offset;
        const fraction = row.value / total;
        offset += fraction;
        const drawn = Math.max(0, Math.min(fraction, p - startFraction));
        const y = top + (bottom - top) * (i + .5) / rows.length;
        const color = row.color ?? COLORS[i];
        return <g key={i}>
          {drawn > 0 && <path d={makePie({radius, progress: drawn, rotation: startFraction * Math.PI * 2, closePath: false}).path}
            transform={`translate(${cx - radius} ${cy - radius})`} stroke={color} strokeWidth={radius * .18} fill="none"/>}
          <rect x="485" y={y - 7} width="14" height="14" fill={color}/>
          <text x="518" y={y} dominantBaseline="middle" fill={INK} fontSize={fitted(row.label, 246, 28)}>{row.label}</text>
          <text x="930" y={y} textAnchor="end" dominantBaseline="middle" fill={INK} fontSize={30}>
            {format(fraction * 100, 1)}%
          </text>
        </g>;
      })}
      <text x={cx} y={cy} textAnchor="middle" dominantBaseline="middle" fontWeight="600" fontSize={fitted(sumLabel, radius * 1.55, 50)} fill={INK}>{sumLabel}</text>
    </g>;
  }}</Board>;
}

export function ProgressSteps({params, width, height, frames}) {
  const frame = useCurrentFrame(), labels = params.steps;
  if (!Array.isArray(labels) || labels.length < 2 || labels.length > 6 ||
      labels.some(s => typeof s !== 'string' || !s.trim())) throw new Error('steps requires 2–6 nonempty labels');
  const [start, settle] = cueRange(params, frames);
  const cues = params.cues ?? labels.map((_, i) => start + (settle - start) * i / (labels.length - 1));
  if (!Array.isArray(cues) || cues.length !== labels.length ||
      cues.some((n, i) => !Number.isFinite(n) || n < 0 || n >= frames || (i > 0 && n < cues[i - 1]))) {
    throw new Error('cues must increase in composition frame coordinates, one per step');
  }
  const color = params.color ?? COLORS[0];
  return <Board {...{width, height, params}}>{({top, bottom}) => {
    const gap = (bottom - top) / (labels.length - 1);
    return <g>
      {labels.map((label, i) => {
        const y = top + i * gap, yNext = y + gap, arrived = frame >= cues[i];
        const p = i < labels.length - 1 ? reveal(frame, cues[i], cues[i + 1]) : 0;
        return <g key={i}>
          {i < labels.length - 1 && <>
            <line x1="97" x2="97" y1={y} y2={yNext} stroke={RULE} strokeWidth="4"/>
            <line x1="97" x2="97" y1={y} y2={y + gap * p} stroke={color} strokeWidth="4"/>
          </>}
          <circle cx="97" cy={y} r={Math.min(15, gap * .27)} stroke={arrived ? color : MUTED} strokeWidth="3" fill={arrived ? color : PAPER}/>
          <text x="140" y={y} dominantBaseline="middle" fill={arrived ? INK : MUTED} fontSize={fitted(label, 765, Math.min(37, gap * .69))} fontWeight={arrived ? 650 : 450}>{label}</text>
        </g>;
      })}
    </g>;
  }}</Board>;
}
