import React from 'react';
import {AbsoluteFill, Easing, interpolate, spring, useCurrentFrame} from 'remotion';
import {evolvePath} from '@remotion/paths';
import {makePie} from '@remotion/shapes';

const PALETTES = {
  light: {
    bg: '#F4EDDD',
    surface: '#ECE3CE',
    ink: '#131A17',
    muted: '#56605A',
    rule: '#C9C4B5',
    accent: '#82501D',
    colors: ['#176663', '#82501D', '#4F687B', '#836653', '#5C6651', '#83667A'],
  },
  dark: {
    bg: '#131A17',
    surface: '#1D2722',
    ink: '#F4EDDD',
    muted: '#A9AB9F',
    rule: '#2F3B35',
    accent: '#EDA961',
    colors: ['#EDA961', '#8AD7CD', '#7AA6C2', '#D49A79', '#99B88C', '#C494B5'],
  },
};

const LABEL = '"Avenir Next", "PingFang SC", "Heiti SC", sans-serif';
const TITLE = '"Songti SC", "STSong", serif';
const bounds = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'};
const ease = {...bounds, easing: Easing.out(Easing.cubic)};

function palette(params, hasBackground = false) {
  const mode = params.theme ?? 'light';
  if (mode !== 'light' && mode !== 'dark') throw new Error('theme 取 light 或 dark');
  const p = PALETTES[mode];
  const transparent = Boolean(params.transparent || hasBackground);
  const surface = transparent
    ? (mode === 'dark' ? 'rgba(22, 31, 27, 0.78)' : 'rgba(244, 237, 221, 0.86)')
    : p.surface;
  return {...p, mode, surface, transparent, bgFill: transparent ? 'transparent' : (params.background ?? p.bg)};
}

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

function fitted(text, available, preferred) {
  const ems = Array.from(String(text)).reduce((sum, c) => sum + (c.charCodeAt(0) > 255 ? 1.06 : .66), 0);
  return Math.min(preferred, available / Math.max(1, ems));
}

function Board({width, height, frames = 90, params, hasBackground = false, children}) {
  const frame = useCurrentFrame();
  const p = palette(params, hasBackground);
  const h = Math.max(445, Math.min(1100, height / width * 1000));
  const top = h * .135;
  const bottom = h * (params.source ? .77 : .79);
  const bodyTop = params.title ? top + (params.subtitle ? 84 : 53) : h * .19;
  const ambient = params.ambient !== false;
  const frac = frames > 1 ? frame / (frames - 1) : 0;
  const camScale = ambient ? 1 + 0.022 * frac : 1;
  const glowX = 24 + 52 * frac;
  const glowY = 22 + 16 * Math.sin(frac * Math.PI);
  const ambientBg = !p.transparent && ambient
    ? (p.mode === 'dark'
      ? `radial-gradient(ellipse at ${glowX.toFixed(1)}% ${glowY.toFixed(1)}%, rgba(138,215,205,0.09) 0%, transparent 56%), radial-gradient(ellipse at ${(100 - glowX).toFixed(1)}% 82%, rgba(237,169,97,0.07) 0%, transparent 54%), ${p.bgFill}`
      : `radial-gradient(ellipse at ${glowX.toFixed(1)}% ${glowY.toFixed(1)}%, rgba(23,102,99,0.06) 0%, transparent 56%), ${p.bgFill}`)
    : undefined;
  return <AbsoluteFill style={{backgroundColor: p.bgFill, background: ambientBg, overflow: 'hidden'}}>
    <svg width="100%" height="100%" viewBox={'0 0 1000 ' + h}
      style={{fontFamily: LABEL, color: p.ink, fontVariantNumeric: 'tabular-nums',
        transform: `scale(${camScale.toFixed(4)})`, transformOrigin: '50% 48%'}}>
      {params.title && <g>
        <rect x="70" y={top - 48} width={46 + (ambient ? 10 * Math.sin(frame * 0.08) : 0)} height="4" rx="2" fill={params.color ?? p.accent}/>
        <text x="70" y={top} fontFamily={TITLE} fontWeight="700" fill={p.ink}
          fontSize={fitted(params.title, 860, 47)}>{params.title}</text>
      </g>}
      {params.subtitle && <text x="70" y={top + 34} fill={p.muted}
        fontSize={fitted(params.subtitle, 860, 22)}>{params.subtitle}</text>}
      {children({top: bodyTop, bottom, h, p, frame})}
      {params.source && <text x="930" y={h * .855} textAnchor="end" fill={p.muted}
        fontSize={fitted(params.source, 860, 18)}>{params.source}</text>}
    </svg>
  </AbsoluteFill>;
}

export function StatCounter({params, width, height, fps, frames, hasBackground}) {
  const frame = useCurrentFrame();
  const from = number(params.from ?? 0, 'from'), to = number(params.to, 'to');
  const [start, settle] = cueRange(params, frames);
  const progress = reveal(frame, start, settle);
  const value = progress === 1 ? to : interpolate(progress, [0, 1], [from, to]);
  const decimals = params.decimals ?? Math.max(...[from, to].map(n => (format(n).split('.')[1] || '').length));
  const unit = params.unit ?? '';
  const endLabels = [format(from, decimals) + unit, format(to, decimals) + unit];
  const size = Math.min(...endLabels.map(label => fitted(label, 840, 150)));
  const deltaPop = params.delta && frame >= settle ? spring({
    frame: frame - settle, fps, durationInFrames: Math.max(8, Math.round(fps * .36)),
    config: {damping: 22, stiffness: 180},
  }) : 0;
  return <Board {...{width, height, frames, params, hasBackground}}>{({top, bottom, p}) => {
    const mid = (top + bottom) / 2;
    const accent = params.color ?? p.colors[0];
    return <g>
      <text x="70" y={mid + size * .22} fontSize={size} fontWeight="600" fill={accent}>
        {format(value, decimals)}<tspan dx={unit ? 10 : 0} fontSize={size * .33}>{unit}</tspan>
      </text>
      {params.delta && deltaPop > 0 && (() => {
        const label = String(params.delta);
        const ems = Array.from(label).reduce((s, c) => s + (c.charCodeAt(0) > 255 ? 1.05 : 0.64), 0);
        const badgeW = Math.min(360, Math.max(96, Math.round(ems * 22 + 38)));
        const badgeY = mid + size * .32;
        return <g opacity={deltaPop} transform={`translate(0 ${(1 - deltaPop) * 14})`}>
          <rect x="70" y={badgeY} width={badgeW} height="42" rx="8" fill={p.surface} stroke={accent} strokeWidth="1.5"/>
          <text x={70 + badgeW / 2} y={badgeY + 22} textAnchor="middle" dominantBaseline="middle" fontSize="22" fontWeight="600" fill={accent}>
            {label}
          </text>
        </g>;
      })()}
      <line x1="70" x2="930" y1={bottom} y2={bottom} stroke={p.rule} strokeWidth="2"/>
      <line x1="70" x2={70 + 860 * progress} y1={bottom} y2={bottom} stroke={accent} strokeWidth="4" strokeLinecap="round"/>
    </g>;
  }}</Board>;
}

export function BarChart({params, width, height, frames, hasBackground}) {
  const frame = useCurrentFrame(), rows = series(params, 1, 6);
  const [start, settle] = cueRange(params, frames);
  const low = Math.min(0, ...rows.map(row => row.value));
  const high = Math.max(0, ...rows.map(row => row.value));
  const scale = n => interpolate(n, [low, high === low ? low + 1 : high], [282, 725], bounds);
  const zero = scale(0);
  const hiIdx = Number.isInteger(params.highlight) ? params.highlight : null;
  return <Board {...{width, height, frames, params, hasBackground}}>{({top, bottom, p}) => {
    const stride = (bottom - top) / rows.length;
    return <g>
      <line x1={zero} x2={zero} y1={top} y2={bottom} stroke={p.muted} strokeWidth="1.5"/>
      {rows.map((row, i) => {
        const y = top + stride * (i + .5);
        const offset = (settle - start) * .2 * i / Math.max(1, rows.length - 1);
        const prog = reveal(frame, start + offset, settle);
        const tip = zero + (scale(row.value) - zero) * prog;
        const weight = Math.min(28, stride * .36);
        const text = format(row.value, params.decimals) + (params.unit ?? '');
        const dim = hiIdx !== null && hiIdx !== i ? 0.52 : 1;
        const barColor = row.color ?? p.colors[i % p.colors.length];
        return <g key={i} opacity={dim}>
          <rect x="282" y={y - weight / 2} width="443" height={weight} rx={weight * .22} fill={p.surface}/>
          <text x="70" y={y} dominantBaseline="middle" fontSize={fitted(row.label, 194, 29)}
            fontWeight={hiIdx === i ? 700 : 500} fill={p.ink}>{row.label}</text>
          {Math.abs(tip - zero) > 0.5 && <rect x={Math.min(zero, tip)} y={y - weight / 2}
            width={Math.abs(tip - zero)} height={weight} rx={weight * .22} fill={barColor}/>}
          <text x="930" y={y} textAnchor="end" dominantBaseline="middle" opacity={prog}
            fontSize={fitted(text, 188, 35)} fontWeight="600" fill={hiIdx === i ? barColor : p.ink}>{text}</text>
        </g>;
      })}
    </g>;
  }}</Board>;
}

export function LineChart({params, width, height, frames, hasBackground}) {
  const frame = useCurrentFrame(), rows = series(params, 2, 240);
  const [start, settle] = cueRange(params, frames);
  const prog = reveal(frame, start, settle);
  const hasX = rows.some(row => row.x !== undefined);
  const xs = rows.map((row, i) => hasX ? number(row.x, 'x') : i);
  if (xs.some((x, i) => i > 0 && x <= xs[i - 1])) throw new Error('x coordinates must strictly increase');
  const low = Math.min(...rows.map(row => row.value)), high = Math.max(...rows.map(row => row.value));
  const unit = params.unit ?? '';
  const annotations = Array.isArray(params.annotations) ? params.annotations : [];
  return <Board {...{width, height, frames, params, hasBackground}}>{({top, bottom, p}) => {
    const color = params.color ?? p.colors[0];
    const xMin = 260, xMax = 905;
    const points = rows.map((row, i) => ({
      x: interpolate(xs[i], [xs[0], xs.at(-1)], [xMin, xMax]),
      y: high === low ? (top + bottom) / 2 : interpolate(row.value, [low, high], [bottom, top]),
    }));
    const path = points.map(({x, y}, i) => (i ? 'L' : 'M') + x + ' ' + y).join(' ');
    const areaPath = `${path} L${points.at(-1).x} ${bottom} L${points[0].x} ${bottom} Z`;
    const axis = high === low ? [0.5] : [0, 0.5, 1];
    const clipW = (xMax - xMin) * prog;
    return <g>
      <defs>
        <linearGradient id="lc-area" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={p.mode === 'dark' ? '0.32' : '0.20'}/>
          <stop offset="100%" stopColor={color} stopOpacity="0"/>
        </linearGradient>
        <clipPath id="lc-clip">
          <rect x={xMin - 4} y={top - 20} width={clipW + 8} height={bottom - top + 40}/>
        </clipPath>
      </defs>
      {axis.map(t => {
        const value = format(low + (high - low) * t, params.decimals) + unit;
        const y = bottom + (top - bottom) * t;
        return <g key={t}><line x1={xMin} x2={xMax} y1={y} y2={y} stroke={p.rule} strokeDasharray={t > 0 ? '4 6' : undefined}/>
          <text x={xMin - 18} y={y} textAnchor="end" dominantBaseline="middle" fill={p.muted} fontSize={fitted(value, 162, 25)}>{value}</text></g>;
      })}
      {prog > 0 && <path d={areaPath} fill="url(#lc-area)" clipPath="url(#lc-clip)"/>}
      <path d={path} fill="none" stroke={color} strokeWidth="6" strokeLinecap="round" strokeLinejoin="round"
        {...evolvePath(prog, path)}/>
      {prog > 0 && <circle cx={points[0].x} cy={points[0].y} r="6" fill={color}/>}
      {annotations.map((note, idx) => {
        const ptIdx = Math.max(0, Math.min(points.length - 1, note.index ?? points.length - 1));
        const threshold = ptIdx / Math.max(1, points.length - 1);
        const vis = reveal(prog, Math.max(0, threshold - 0.02), Math.min(1, threshold + 0.08));
        if (vis <= 0) return null;
        const pt = points[ptIdx];
        const above = pt.y > top + 64;
        const ty = above ? pt.y - 26 : pt.y + 32;
        const anchor = pt.x > xMax - 120 ? 'end' : pt.x < xMin + 120 ? 'start' : 'middle';
        return <g key={idx} opacity={vis}>
          <circle cx={pt.x} cy={pt.y} r="8" fill={p.bg} stroke={color} strokeWidth="3.5"/>
          <text x={pt.x} y={ty} textAnchor={anchor} fontSize="22" fontWeight="700" fill={p.ink}>
            {note.label}{note.value !== undefined ? ` · ${note.value}` : ''}
          </text>
        </g>;
      })}
      <circle cx={points.at(-1).x} cy={points.at(-1).y} r="8" opacity={reveal(prog, .96, 1)} fill={color} stroke={p.bg} strokeWidth="2.5"/>
      <text x={xMin} y={bottom + 39} fontSize={fitted(rows[0].label, 298, 27)} fill={p.ink}>{rows[0].label}</text>
      <text x={xMax} y={bottom + 39} textAnchor="end" fontSize={fitted(rows.at(-1).label, 298, 27)} fill={p.ink}>{rows.at(-1).label}</text>
    </g>;
  }}</Board>;
}

export function DonutChart({params, width, height, frames, hasBackground}) {
  const frame = useCurrentFrame(), rows = series(params, 1, 6);
  if (rows.some(row => row.value < 0)) throw new Error('Ring segments require nonnegative values');
  const total = rows.reduce((sum, row) => sum + row.value, 0);
  if (!Number.isFinite(total) || total <= 0) throw new Error('Ring total must be positive and finite');
  const [start, settle] = cueRange(params, frames);
  const prog = reveal(frame, start, settle);
  const hiIdx = Number.isInteger(params.highlight) ? params.highlight : null;
  return <Board {...{width, height, frames, params, hasBackground}}>{({top, bottom, p}) => {
    const radius = Math.min(140, (bottom - top) * .39);
    const cy = (top + bottom) / 2, cx = 252;
    const sumLabel = format(total, params.decimals) + (params.unit ?? '');
    let offset = 0;
    return <g>
      <circle cx={cx} cy={cy} r={radius} fill="none" stroke={p.surface} strokeWidth={radius * .20}/>
      {rows.map((row, i) => {
        const startFraction = offset;
        const fraction = row.value / total;
        offset += fraction;
        const drawn = Math.max(0, Math.min(fraction, prog - startFraction));
        const y = top + (bottom - top) * (i + .5) / rows.length;
        const color = row.color ?? p.colors[i % p.colors.length];
        const dim = hiIdx !== null && hiIdx !== i ? 0.45 : 1;
        const strokeW = radius * (hiIdx === i ? .24 : .19);
        return <g key={i} opacity={dim}>
          {drawn > 0 && <path d={makePie({radius, progress: drawn, rotation: startFraction * Math.PI * 2, closePath: false}).path}
            transform={`translate(${cx - radius} ${cy - radius})`} stroke={color} strokeWidth={strokeW} fill="none"/>}
          <rect x="485" y={y - 8} width="16" height="16" rx="3" fill={color}/>
          <text x="518" y={y} dominantBaseline="middle" fill={p.ink} fontWeight={hiIdx === i ? 700 : 500}
            fontSize={fitted(row.label, 246, 28)}>{row.label}</text>
          <text x="930" y={y} textAnchor="end" dominantBaseline="middle" fill={hiIdx === i ? color : p.ink}
            fontWeight="600" fontSize={30}>{format(fraction * 100, 1)}%</text>
        </g>;
      })}
      <text x={cx} y={cy} textAnchor="middle" dominantBaseline="middle" fontWeight="600"
        fontSize={fitted(sumLabel, radius * 1.55, 50)} fill={p.ink}>{sumLabel}</text>
    </g>;
  }}</Board>;
}

export function ProgressSteps({params, width, height, frames, hasBackground}) {
  const frame = useCurrentFrame(), labels = params.steps;
  if (!Array.isArray(labels) || labels.length < 2 || labels.length > 6 ||
      labels.some(s => typeof s !== 'string' || !s.trim())) throw new Error('steps requires 2–6 nonempty labels');
  const [start, settle] = cueRange(params, frames);
  const cues = params.cues ?? labels.map((_, i) => start + (settle - start) * i / (labels.length - 1));
  if (!Array.isArray(cues) || cues.length !== labels.length ||
      cues.some((n, i) => !Number.isFinite(n) || n < 0 || n >= frames || (i > 0 && n < cues[i - 1]))) {
    throw new Error('cues must increase in composition frame coordinates, one per step');
  }
  const pulse = 0.5 + 0.5 * Math.sin(frame * 0.14);
  return <Board {...{width, height, frames, params, hasBackground}}>{({top, bottom, p}) => {
    const color = params.color ?? p.colors[0];
    const gap = (bottom - top) / (labels.length - 1);
    const activeIdx = cues.reduce((acc, c, i) => (frame >= c ? i : acc), -1);
    return <g>
      {labels.map((label, i) => {
        const y = top + i * gap, yNext = y + gap, arrived = frame >= cues[i];
        const done = i < activeIdx;
        const segP = i < labels.length - 1 ? reveal(frame, cues[i], cues[i + 1]) : 0;
        const r = Math.min(16, gap * .28);
        return <g key={i}>
          {i < labels.length - 1 && <>
            <line x1="97" x2="97" y1={y} y2={yNext} stroke={p.rule} strokeWidth="4"/>
            <line x1="97" x2="97" y1={y} y2={y + gap * segP} stroke={color} strokeWidth="4"/>
          </>}
          {i === activeIdx && <circle cx="97" cy={y} r={r * (1.45 + 0.45 * pulse)} fill={color} opacity={0.14 + 0.16 * (1 - pulse)}/>}
          <circle cx="97" cy={y} r={r} stroke={arrived ? color : p.muted} strokeWidth="3" fill={arrived ? color : p.bg}/>
          {done && <path d={`M${97 - r * .42} ${y} L${97 - r * .08} ${y + r * .34} L${97 + r * .45} ${y - r * .34}`}
            fill="none" stroke={p.bg} strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round"/>}
          <text x="142" y={y} dominantBaseline="middle" fill={arrived ? p.ink : p.muted}
            fontSize={fitted(label, 765, Math.min(37, gap * .69))} fontWeight={arrived ? 650 : 450}>{label}</text>
        </g>;
      })}
    </g>;
  }}</Board>;
}

export function MetricGrid({params, width, height, fps, frames, hasBackground}) {
  const frame = useCurrentFrame();
  const items = params.items;
  if (!Array.isArray(items) || items.length < 2 || items.length > 4) {
    throw new Error('items requires 2–4 KPI cards');
  }
  const [start, settle] = cueRange(params, frames);
  const cues = params.cues ?? items.map((_, i) => Math.round(start + (settle - start) * i / Math.max(1, items.length)));
  if (!Array.isArray(cues) || cues.length !== items.length) throw new Error('cues must match items length');
  const tall = height > width;
  const cols = tall ? 1 : (items.length === 4 ? 2 : items.length);
  const rowsCount = Math.ceil(items.length / cols);
  const breathe = 0.5 + 0.5 * Math.sin(frame * 0.11);
  return <Board {...{width, height, frames, params, hasBackground}}>{({top, bottom, p}) => {
    const gap = 22;
    const cellW = (860 - gap * (cols - 1)) / cols;
    const cellH = Math.min(260, (bottom - top - gap * (rowsCount - 1)) / rowsCount);
    const totalH = cellH * rowsCount + gap * (rowsCount - 1);
    const y0 = top + (bottom - top - totalH) / 2;
    return <g>
      {items.map((item, i) => {
        const col = i % cols, row = Math.floor(i / cols);
        const x = 70 + col * (cellW + gap);
        const y = y0 + row * (cellH + gap);
        const local = frame - cues[i];
        const pop = local < 0 ? 0 : spring({frame: local, fps, durationInFrames: Math.max(8, Math.round(fps * .42)),
          config: {damping: 24, stiffness: 170, overshootClamping: true}});
        if (pop <= 0) return null;
        const accent = item.color ?? (item.highlight ? p.accent : p.colors[i % p.colors.length]);
        const valText = String(item.value) + (item.unit ? ` ${item.unit}` : '');
        const barW = 38 + (item.highlight ? 18 * breathe : 0);
        return <g key={i} opacity={pop} transform={`translate(0 ${(1 - pop) * 22})`}>
          <rect x={x} y={y} width={cellW} height={cellH} rx="14" fill={p.surface}
            stroke={item.highlight ? accent : p.rule} strokeWidth={item.highlight ? 2.2 + 0.8 * breathe : 1.2}/>
          <rect x={x + 26} y={y + 22} width={barW} height="4" rx="2" fill={accent}/>
          <text x={x + 26} y={y + cellH * .32} fontSize={fitted(item.label, cellW - 52, 24)} fill={p.muted} fontWeight="500">
            {item.label}
          </text>
          <text x={x + 26} y={y + cellH * .66} fontSize={fitted(valText, cellW - 52, 62)} fill={item.highlight ? accent : p.ink} fontWeight="700">
            {valText}
          </text>
          {item.delta && <text x={x + 26} y={y + cellH * .87} fontSize={fitted(item.delta, cellW - 52, 20)} fill={accent} fontWeight="600">
            {item.delta}
          </text>}
        </g>;
      })}
    </g>;
  }}</Board>;
}

export function ComparisonTable({params, width, height, frames, hasBackground}) {
  const frame = useCurrentFrame();
  const cols = params.columns;
  const rows = params.rows;
  if (!Array.isArray(cols) || cols.length < 2 || cols.length > 4) throw new Error('columns requires 2–4 headers');
  if (!Array.isArray(rows) || rows.length < 1 || rows.length > 6) throw new Error('rows requires 1–6 entries');
  const [start, settle] = cueRange(params, frames);
  const cues = params.cues ?? rows.map((_, i) => Math.round(start + (settle - start) * i / Math.max(1, rows.length)));
  const hiCol = Number.isInteger(params.highlightCol) ? params.highlightCol : null;
  return <Board {...{width, height, frames, params, hasBackground}}>{({top, bottom, p}) => {
    const colW = 860 / cols.length;
    const headerH = 54;
    const rowH = Math.min(72, (bottom - top - headerH) / rows.length);
    return <g>
      {hiCol !== null && hiCol >= 0 && hiCol < cols.length && (
        <rect x={70 + hiCol * colW} y={top} width={colW} height={headerH + rowH * rows.length}
          rx="10" fill={p.surface}/>
      )}
      <line x1="70" x2="930" y1={top + headerH} y2={top + headerH} stroke={p.accent} strokeWidth="2"/>
      {cols.map((header, c) => (
        <text key={c} x={70 + c * colW + (c === 0 ? 16 : colW / 2)} y={top + headerH / 2}
          textAnchor={c === 0 ? 'start' : 'middle'} dominantBaseline="middle"
          fontSize={fitted(header, colW - 24, 26)} fontWeight="700"
          fill={hiCol === c ? p.accent : p.muted}>{header}</text>
      ))}
      {rows.map((cells, r) => {
        const y = top + headerH + r * rowH;
        const rowP = reveal(frame, cues[r] ?? start, Math.min(frames - 1, (cues[r] ?? start) + 10));
        return <g key={r} opacity={rowP} transform={`translate(0 ${(1 - rowP) * 10})`}>
          <line x1="70" x2="930" y1={y + rowH} y2={y + rowH} stroke={p.rule} strokeWidth="1"/>
          {cols.map((_, c) => {
            const text = String(cells[c] ?? '');
            return <text key={c} x={70 + c * colW + (c === 0 ? 16 : colW / 2)} y={y + rowH / 2}
              textAnchor={c === 0 ? 'start' : 'middle'} dominantBaseline="middle"
              fontSize={fitted(text, colW - 24, c === 0 ? 25 : 27)}
              fontWeight={c === 0 || hiCol === c ? 650 : 450}
              fill={hiCol === c ? p.accent : p.ink}>{text}</text>;
          })}
        </g>;
      })}
    </g>;
  }}</Board>;
}

export function TimelineStrip({params, width, height, frames, hasBackground}) {
  const frame = useCurrentFrame();
  const items = params.items;
  if (!Array.isArray(items) || items.length < 2 || items.length > 5) {
    throw new Error('items requires 2–5 milestones');
  }
  const [start, settle] = cueRange(params, frames);
  const cues = params.cues ?? items.map((_, i) => Math.round(start + (settle - start) * i / Math.max(1, items.length - 1)));
  const pulse = 0.5 + 0.5 * Math.sin(frame * 0.14);
  return <Board {...{width, height, frames, params, hasBackground}}>{({top, bottom, p}) => {
    const color = params.color ?? p.accent;
    const midY = (top + bottom) * .52;
    const x0 = 135, x1 = 865;
    const span = x1 - x0;
    const spineP = reveal(frame, start, settle);
    const activeIdx = cues.reduce((acc, c, i) => (frame >= c ? i : acc), 0);
    return <g>
      <line x1={x0} x2={x1} y1={midY} y2={midY} stroke={p.rule} strokeWidth="4" strokeLinecap="round"/>
      <line x1={x0} x2={x0 + span * spineP} y1={midY} y2={midY} stroke={color} strokeWidth="4" strokeLinecap="round"/>
      {items.map((item, i) => {
        const x = x0 + span * i / (items.length - 1);
        const arrived = reveal(frame, cues[i] ?? start, Math.min(frames - 1, (cues[i] ?? start) + 10));
        const isCurrent = i === activeIdx;
        const colW = Math.min(240, span / Math.max(1, items.length - 1) - 18);
        return <g key={i}>
          {isCurrent && arrived > 0 && <circle cx={x} cy={midY} r={18 + 8 * pulse} fill={color} opacity={(0.14 + 0.18 * (1 - pulse)) * arrived}/>}
          <circle cx={x} cy={midY} r="10" fill={arrived > 0.2 ? color : p.bg} stroke={color} strokeWidth="3"/>
          <g opacity={arrived} transform={`translate(0 ${(1 - arrived) * 10})`}>
            <text x={x} y={midY - 34} textAnchor="middle" fontSize={fitted(item.date, colW, 28)}
              fontWeight="700" fill={isCurrent ? color : p.muted}>{item.date}</text>
            <text x={x} y={midY + 42} textAnchor="middle" fontSize={fitted(item.title, colW, 25)}
              fontWeight="650" fill={p.ink}>{item.title}</text>
            {item.detail && <text x={x} y={midY + 76} textAnchor="middle" fontSize={fitted(item.detail, colW, 19)}
              fill={p.muted}>{item.detail}</text>}
          </g>
        </g>;
      })}
    </g>;
  }}</Board>;
}

export function ExplodedDiagram({params, width, height, fps, frames, hasBackground}) {
  const frame = useCurrentFrame();
  const layers = params.layers;
  if (!Array.isArray(layers) || layers.length < 2 || layers.length > 6) {
    throw new Error('layers requires 2–6 structural layer entries');
  }
  const [start, settle] = cueRange(params, frames);
  const explodeAt = params.explodeFrame ?? start;
  const explodeDur = Math.max(12, Math.round(fps * 0.72));
  const explodeP = frame < explodeAt ? 0 : spring({
    frame: frame - explodeAt, fps, durationInFrames: explodeDur,
    config: {damping: 20, stiffness: 130, overshootClamping: true},
  });
  const cues = params.cues ?? layers.map((_, i) => Math.round(explodeAt + (settle - explodeAt) * i / Math.max(1, layers.length)));
  const activeIdx = cues.reduce((acc, c, i) => (frame >= c ? i : acc), 0);
  const pulse = 0.5 + 0.5 * Math.sin(frame * 0.14);
  return <Board {...{width, height, frames, params, hasBackground}}>{({top, bottom, p}) => {
    const cx = 315;
    const midY = (top + bottom) * 0.54;
    const totalSpread = Math.min(250, (bottom - top) * 0.68) * explodeP;
    const stepY = layers.length > 1 ? totalSpread / (layers.length - 1) : 0;
    const baseY0 = midY - totalSpread * 0.5;
    // Render bottom-to-top so upper layers naturally overlap lower planes in isometric 2.5D.
    const order = layers.map((layer, i) => ({layer, i})).reverse();
    return <g>
      {order.map(({layer, i}) => {
        const y = baseY0 + i * stepY;
        const isCurrent = i === activeIdx && frame >= (cues[i] ?? explodeAt);
        const accent = layer.color ?? p.colors[i % p.colors.length];
        const rx = 152, ry = 54;
        const lift = isCurrent ? -6 * (0.6 + 0.4 * pulse) : 0;
        const py = y + lift;
        const diamond = `${cx},${py - ry} ${cx + rx},${py} ${cx},${py + ry} ${cx - rx},${py}`;
        const thickness = 11;
        const sideL = `${cx - rx},${py} ${cx},${py + ry} ${cx},${py + ry + thickness} ${cx - rx},${py + thickness}`;
        const sideR = `${cx},${py + ry} ${cx + rx},${py} ${cx + rx},${py + thickness} ${cx},${py + ry + thickness}`;
        const labelP = reveal(frame, (cues[i] ?? explodeAt), Math.min(frames - 1, (cues[i] ?? explodeAt) + 12));
        const ly = top + (bottom - top) * ((i + 0.5) / layers.length);
        return <g key={i} opacity={0.42 + 0.58 * (isCurrent ? 1 : (0.55 + 0.45 * explodeP))}>
          <polygon points={sideL} fill={accent} opacity={isCurrent ? 0.58 : 0.28}/>
          <polygon points={sideR} fill={accent} opacity={isCurrent ? 0.42 : 0.20}/>
          <polygon points={diamond} fill={p.surface} stroke={isCurrent ? accent : p.rule}
            strokeWidth={isCurrent ? 3.2 : 1.6}/>
          <text x={cx} y={py + 4} textAnchor="middle" dominantBaseline="middle"
            fontSize={fitted(layer.tag ?? `L${i + 1}`, 210, 22)} fontWeight="700"
            fill={isCurrent ? accent : p.ink}>{layer.tag ?? layer.label}</text>
          {labelP > 0.01 && <g opacity={labelP}>
            <polyline points={`${cx + rx + 8},${py} ${522},${py} ${556},${ly}`}
              fill="none" stroke={isCurrent ? accent : p.rule} strokeWidth={isCurrent ? 2.5 : 1.5}
              strokeDasharray={isCurrent ? undefined : '4 5'}/>
            <circle cx={556} cy={ly} r={isCurrent ? 6 + 2 * pulse : 4} fill={isCurrent ? accent : p.muted}/>
            <text x="574" y={ly - (layer.detail ? 8 : 0)} dominantBaseline="middle"
              fontSize={fitted(layer.label, 350, 28)} fontWeight={isCurrent ? 700 : 500}
              fill={isCurrent ? p.ink : p.muted}>{layer.label}</text>
            {layer.detail && <text x="574" y={ly + 19} dominantBaseline="middle"
              fontSize={fitted(layer.detail, 350, 19)} fill={isCurrent ? accent : p.muted}>{layer.detail}</text>}
          </g>}
        </g>;
      })}
    </g>;
  }}</Board>;
}
