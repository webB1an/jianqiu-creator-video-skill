import React from 'react';
import {AbsoluteFill, Easing, Img, Loop, OffthreadVideo, Sequence, interpolate, spring, staticFile, useCurrentFrame} from 'remotion';
import {wipe} from '@remotion/transitions/wipe';

const stage = {overflow: 'hidden', backgroundColor: '#171c1b'};
const videoFile = /\.(mp4|m4v|mov|webm|mkv)$/i;
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'};
const sweep = wipe({direction: 'from-left'});
const WipeLayer = sweep.component;

function bounded(value, name, min, max) {
  if (!Number.isFinite(value) || value < min || value > max) {
    throw new Error(`${name} must be a number between ${min} and ${max}`);
  }
  return value;
}

function point(coords, name) {
  if (!Array.isArray(coords) || coords.length !== 2) throw new Error(`${name} must contain x and y`);
  return coords.map((value) => bounded(value, name, 0, 1));
}

function progress(frame, frames, params, defaultEnd) {
  const last = frames - 1;
  const start = params.startFrame ?? 0;
  const end = params.endFrame ?? Math.max(start, Math.round(last * defaultEnd));
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end < start || end > last) {
    throw new Error('Use 0 <= startFrame <= endFrame < frames, with integer frame numbers');
  }
  if (start === end) return frame >= start ? 1 : 0;
  return interpolate(frame, [start, end], [0, 1], {...clamp, easing: Easing.inOut(Easing.cubic)});
}

function mediaLayer(file, fit = 'cover', trim = 0, style = {}) {
  if (typeof file !== 'string' || !file) throw new Error('Supply a local media file');
  if (!['cover', 'contain'].includes(fit)) throw new Error('fit must be cover or contain');
  if (!Number.isInteger(trim) || trim < 0) throw new Error('Source offsets use nonnegative integer frames');
  const props = {src: staticFile(file), style: {display: 'block', width: '100%', height: '100%', objectFit: fit, ...style}};
  return videoFile.test(file) ? <OffthreadVideo {...props} trimBefore={trim} muted /> : <Img {...props} />;
}

export function KenBurns({params, frames}) {
  const t = progress(useCurrentFrame(), frames, params, 0.88);
  const from = point(params.fromFocus ?? [0.5, 0.5], 'fromFocus');
  const to = point(params.toFocus ?? from, 'toFocus');
  const zoom = interpolate(t, [0, 1], [
    bounded(params.fromZoom ?? 1, 'fromZoom', 1, 8), bounded(params.toZoom ?? 1.35, 'toZoom', 1, 8),
  ]);
  const origin = from.map((v, i) => `${interpolate(t, [0, 1], [v, to[i]]) * 100}%`).join(' ');
  return <AbsoluteFill style={stage}>
    {mediaLayer(params.source, params.fit, params.sourceStart, {transformOrigin: origin, transform: `scale(${zoom})`})}
  </AbsoluteFill>;
}

export function ImageComparison({params, width, frames}) {
  const t = progress(useCurrentFrame(), frames, params, 0.83);
  const cut = interpolate(t, [0, 1], [
    bounded(params.from ?? 0, 'from', 0, 1), bounded(params.to ?? 1, 'to', 0, 1),
  ]);
  const line = Math.max(1, width / 600);
  return <AbsoluteFill style={stage}>
    {mediaLayer(params.before, params.fit ?? 'contain', params.beforeStart)}
    <WipeLayer passedProps={sweep.props} presentationDirection="entering" presentationProgress={cut} durationInFrames={frames}>
      <AbsoluteFill style={stage}>{mediaLayer(params.after, params.fit ?? 'contain', params.afterStart)}</AbsoluteFill>
    </WipeLayer>
    {cut > 0 && cut < 1 && <div style={{position: 'absolute', top: 0, bottom: 0, left: width * cut - line / 2,
      width: line, backgroundColor: params.divider ?? '#f6f1e5', boxShadow: '0 0 3px #20242188'}} />}
  </AbsoluteFill>;
}

export function PictureInPicture({params, width, height, frames}) {
  const t = progress(useCurrentFrame(), frames, params, 0.45);
  const corner = params.corner ?? 'top-right';
  if (!['top-left', 'top-right', 'bottom-left', 'bottom-right'].includes(corner)) {
    throw new Error('corner must be top-left, top-right, bottom-left or bottom-right');
  }
  const inset = bounded(params.inset ?? 0.045, 'inset', 0, 0.15) * Math.min(width, height);
  const scale = bounded(params.size ?? 0.32, 'size', 0.1, 0.75);
  const aspect = bounded(params.aspect ?? width / height, 'aspect', 0.2, 6);
  const endW = Math.min(width * scale, (height - 2 * inset) * aspect);
  const endH = endW / aspect;
  const left = corner.endsWith('left') ? inset : width - inset - endW;
  const top = corner.startsWith('top') ? inset : height - inset - endH;
  return <AbsoluteFill style={stage}>
    {mediaLayer(params.main, params.mainFit, params.mainStart)}
    <div style={{position: 'absolute', left: left * t, top: top * t, overflow: 'hidden',
      width: interpolate(t, [0, 1], [width, endW]), height: interpolate(t, [0, 1], [height, endH]),
      borderRadius: Math.min(endW, endH) * 0.055 * t, backgroundColor: '#171c1b',
      boxShadow: `0 ${4 * t}px ${24 * t}px rgba(0,0,0,${0.32 * t})`}}>
      {mediaLayer(params.insetSource, params.insetFit, params.insetStart)}
    </div>
  </AbsoluteFill>;
}

export function ParallaxPan({params, width, height, frames}) {
  const t = progress(useCurrentFrame(), frames, params, 0.92);
  const offset = bounded(params.travel ?? 0.045, 'travel', -0.2, 0.2);
  const depth = bounded(params.farRatio ?? 0.28, 'farRatio', 0, 1);
  const vertical = params.axis === 'y';
  if (params.axis !== undefined && !['x', 'y'].includes(params.axis)) throw new Error('axis must be x or y');
  const translation = offset * (vertical ? height : width) * (t - 0.5);
  function layer(source, ratio, trim) {
    const dist = translation * ratio;
    const transform = `translate${vertical ? 'Y' : 'X'}(${dist}px) scale(${1 + Math.abs(offset * ratio)})`;
    return <AbsoluteFill>{mediaLayer(source, 'cover', trim, {transform})}</AbsoluteFill>;
  }
  return <AbsoluteFill style={stage}>
    {layer(params.back, depth, params.backStart)}
    {layer(params.front, 1, params.frontStart)}
  </AbsoluteFill>;
}

function spotlightPosition(params, frames, frame) {
  let t, from, to;
  if (params.stops === undefined) {
    t = progress(frame, frames, params, 0.75);
    from = [...point(params.fromFocus ?? [0.33, 0.5], 'fromFocus'), bounded(params.fromRadius ?? .46, 'fromRadius', .025, 1.5)];
    to = [...point(params.toFocus ?? [0.65, 0.5], 'toFocus'), bounded(params.toRadius ?? .19, 'toRadius', .025, 1.5)];
  } else {
    if (!Array.isArray(params.stops) || params.stops.length < 2) throw new Error('至少指定两个需要观察的位置');
    let previousHold = -1;
    const stops = params.stops.map((stop, i) => {
      const at = stop.at;
      const holdUntil = stop.holdUntil ?? at;
      if (!Number.isInteger(at) || !Number.isInteger(holdUntil) || (i === 0 && at !== 0) ||
          at <= previousHold || holdUntil < at || holdUntil >= frames) {
        throw new Error('首个位置从第 0 帧开始，各停读结束后才移动到下一处，各落点都应在本镜内');
      }
      previousHold = holdUntil;
      return {at, holdUntil, pose: [...point(stop.focus, 'stop.focus'), bounded(stop.radius ?? .19, 'stop.radius', .025, 1.5)]};
    });
    const next = stops.findIndex(stop => frame < stop.at);
    if (next === -1) return stops[stops.length - 1].pose;
    const left = stops[next - 1], right = stops[next];
    t = progress(frame, frames, {startFrame: left.holdUntil, endFrame: right.at}, 1);
    from = left.pose;
    to = right.pose;
  }
  return from.map((v, i) => interpolate(t, [0, 1], [v, to[i]]));
}

export function SpotlightReveal({params, width, height, frames}) {
  const [cx, cy, size] = spotlightPosition(params, frames, useCurrentFrame());
  const x = cx * width, y = cy * height, radius = size * Math.min(width, height);
  const feather = bounded(params.feather ?? 0.22, 'feather', 0, 0.95);
  const dim = bounded(params.dim ?? 0.70, 'dim', 0, 0.92);
  const shade = `radial-gradient(circle at ${x}px ${y}px, transparent ${radius * (1 - feather)}px, rgba(0,0,0,${dim}) ${radius}px)`;
  return <AbsoluteFill style={stage}>
    {mediaLayer(params.source, params.fit ?? 'contain', params.sourceStart)}
    <AbsoluteFill style={{backgroundImage: shade}}/>
  </AbsoluteFill>;
}

function ArchivePrint({source, offset, fit, left, top, cardW, cardH, tilt, travel, duration, fps, border}) {
  const frame = useCurrentFrame();
  const t = duration === 0 || frame >= duration ? 1 : spring({
    frame, fps, durationInFrames: duration, config: {damping: 24, stiffness: 145, overshootClamping: true},
  });
  const transform = `translateY(${travel * (1 - t)}px) rotate(${tilt * (1.7 - 0.7 * t)}deg) scale(${0.92 + 0.08 * t})`;
  return <div style={{position: 'absolute', left, top, width: cardW, height: cardH,
    boxSizing: 'border-box', padding: border, paddingBottom: border * 1.6, transform,
    opacity: 0.44 + 0.56 * t, backgroundColor: '#e7e2d2', boxShadow: `0 ${border}px ${border * 2.2}px #05080770`}}>
    <div style={{width: '100%', height: '100%', overflow: 'hidden', backgroundColor: '#252b28'}}>
      {mediaLayer(source, fit, offset)}
    </div>
  </div>;
}

export function PhotoStack({params, width, height, fps, frames}) {
  const tall = height > width;
  const secondAt = params.secondAt ?? Math.round((frames - 1) * 0.48);
  const entryFrames = params.entryFrames ?? Math.max(1, Math.min(Math.round(fps * 0.58), Math.round(frames * 0.22)));
  if (!Number.isInteger(secondAt) || secondAt < 0 || secondAt >= frames) throw Error('secondAt must be a frame inside the shot');
  if (!Number.isInteger(entryFrames) || entryFrames < 1) throw Error('entryFrames must be a positive frame count');
  const cardW = width * (tall ? 0.78 : 0.51);
  const cardH = Math.min(cardW / 1.35, height * (tall ? 0.40 : 0.69));
  const border = Math.max(1, Math.min(width, height) * 0.012);
  function print(card, start, x, y, tilt) {
    return <Sequence from={start} durationInFrames={frames - start} layout="none">
      <ArchivePrint source={params[card]} offset={params[card + 'Start']} fit={params[card + 'Fit'] ?? 'contain'}
        left={width * x - cardW / 2} top={height * y - cardH / 2} cardW={cardW} cardH={cardH} tilt={tilt}
        travel={height * 0.11} duration={Math.min(entryFrames, frames - start - 1)} fps={fps} border={border}/>
    </Sequence>;
  }
  return <AbsoluteFill style={stage}>
    {print('first', 0, tall ? 0.46 : 0.34, tall ? 0.37 : 0.46, -3.2)}
    {print('second', secondAt, tall ? 0.55 : 0.66, tall ? 0.63 : 0.55, 2.65)}
  </AbsoluteFill>;
}

export function Spatial3DCard({params, width, height, fps, frames, hasBackground}) {
  const frame = useCurrentFrame();
  const t = progress(frame, frames, params, 0.86);
  const fromRx = bounded(params.fromRotateX ?? 14, 'fromRotateX', -45, 45);
  const toRx = bounded(params.toRotateX ?? 2, 'toRotateX', -45, 45);
  const fromRy = bounded(params.fromRotateY ?? -16, 'fromRotateY', -45, 45);
  const toRy = bounded(params.toRotateY ?? 6, 'toRotateY', -45, 45);
  const fromRz = bounded(params.fromRotateZ ?? -2, 'fromRotateZ', -20, 20);
  const toRz = bounded(params.toRotateZ ?? 0.5, 'toRotateZ', -20, 20);
  const fromZoom = bounded(params.fromZoom ?? 0.86, 'fromZoom', 0.5, 2.2);
  const toZoom = bounded(params.toZoom ?? 1.04, 'toZoom', 0.5, 2.2);
  const rx = interpolate(t, [0, 1], [fromRx, toRx]);
  const ry = interpolate(t, [0, 1], [fromRy, toRy]);
  const rz = interpolate(t, [0, 1], [fromRz, toRz]);
  const zoom = interpolate(t, [0, 1], [fromZoom, toZoom]);
  const rackFocus = params.rackFocus !== false;
  const focusEnd = Math.min(frames - 1, Math.round(fps * 0.55));
  const blur = rackFocus ? interpolate(frame, [0, focusEnd], [7.5, 0], clamp) : 0;
  const cardW = width * bounded(params.cardWidth ?? 0.74, 'cardWidth', 0.35, 0.94);
  const cardH = height * bounded(params.cardHeight ?? 0.72, 'cardHeight', 0.35, 0.92);
  const accent = params.accent ?? '#EDA961';
  const transparent = Boolean(hasBackground || params.transparent);
  const bg = transparent ? 'transparent' : 'radial-gradient(ellipse at 35% 30%, #1F2E28 0%, #111714 75%)';
  const shadowX = (-ry * 1.6).toFixed(1);
  const shadowY = (26 + rx * 1.1).toFixed(1);
  return <AbsoluteFill style={{background: bg, overflow: 'hidden', perspective: '1400px',
    alignItems: 'center', justifyContent: 'center'}}>
    <div style={{
      width: cardW, height: cardH, borderRadius: 16, overflow: 'hidden', position: 'relative',
      border: `2px solid ${accent}88`, backgroundColor: '#171E1B',
      transformStyle: 'preserve-3d',
      transform: `scale(${zoom.toFixed(4)}) rotateX(${rx.toFixed(2)}deg) rotateY(${ry.toFixed(2)}deg) rotateZ(${rz.toFixed(2)}deg)`,
      boxShadow: `${shadowX}px ${shadowY}px 64px rgba(0,0,0,0.62)`,
      filter: blur > 0.15 ? `blur(${blur.toFixed(2)}px)` : undefined,
    }}>
      {mediaLayer(params.source, params.fit ?? 'cover', params.sourceStart ?? 0)}
      {params.label && <div style={{
        position: 'absolute', left: 26, bottom: 24, padding: '10px 18px', borderRadius: 8,
        background: 'rgba(19,26,23,0.84)', borderLeft: `4px solid ${accent}`,
        color: '#F4EDDD', fontFamily: '"PingFang SC","Heiti SC",sans-serif',
        fontSize: Math.round(height * 0.026), fontWeight: 600,
      }}>{params.label}</div>}
    </div>
  </AbsoluteFill>;
}

export function BrollEvidencePop({params, width, height, fps, frames}) {
  const frame = useCurrentFrame();
  const popups = Array.isArray(params.popups) && params.popups.length ? params.popups : [{
    source: params.evidence,
    title: params.title,
    enterFrame: params.enterFrame ?? Math.round(fps * 0.6),
    exitFrame: params.exitFrame ?? Math.max(Math.round(fps * 1.2), frames - Math.round(fps * 0.6)),
    side: params.side ?? 'right',
    width: params.popupWidth ?? 0.46,
  }];
  const accent = params.accent ?? '#EDA961';
  const maxBlur = bounded(params.backdropBlur ?? 4.5, 'backdropBlur', 0, 20);
  const maxDim = bounded(params.backdropDim ?? 0.48, 'backdropDim', 0, 0.85);
  const easeSpan = Math.max(6, Math.round(fps * 0.36));

  let activeAmount = 0;
  const renderedPopups = popups.map((pop, idx) => {
    if (!pop || typeof pop.source !== 'string' || !pop.source) {
      throw new Error('broll-evidence-pop requires popup source media');
    }
    const enter = pop.enterFrame ?? Math.round(fps * 0.5);
    const exit = pop.exitFrame ?? frames;
    const enterP = frame < enter ? 0 : spring({
      frame: frame - enter, fps, durationInFrames: easeSpan,
      config: {damping: 22, stiffness: 175, overshootClamping: true},
    });
    const exitP = exit < frames && frame >= exit - easeSpan
      ? interpolate(frame, [exit - easeSpan, exit], [1, 0], {...clamp, easing: Easing.in(Easing.cubic)})
      : (frame >= exit ? 0 : 1);
    const amt = Math.max(0, Math.min(1, enterP * exitP));
    activeAmount = Math.max(activeAmount, amt);
    if (amt <= 0.005) return null;
    const side = pop.side ?? 'right';
    const wFrac = bounded(pop.width ?? 0.46, 'popup.width', 0.22, 0.82);
    const cardW = width * wFrac;
    const cardH = Math.min(height * 0.64, cardW * 0.68);
    const left = side === 'left' ? width * 0.06 : (side === 'center' ? (width - cardW) / 2 : width * 0.94 - cardW);
    const top = (height - cardH) * 0.44;
    const slideX = side === 'left' ? -44 : (side === 'right' ? 44 : 0);
    return <div key={idx} style={{
      position: 'absolute', left, top, width: cardW, height: cardH,
      opacity: amt, transform: `translate(${((1 - amt) * slideX).toFixed(1)}px, ${((1 - amt) * 20).toFixed(1)}px) scale(${(0.92 + 0.08 * amt).toFixed(4)})`,
      borderRadius: 14, overflow: 'hidden', background: 'rgba(19,26,23,0.92)',
      border: `2px solid ${accent}`, boxShadow: '0 22px 54px rgba(0,0,0,0.62)',
      display: 'flex', flexDirection: 'column',
    }}>
      {pop.title && <div style={{
        padding: '10px 18px', background: 'rgba(19,26,23,0.96)', borderBottom: `1px solid ${accent}55`,
        color: '#F4EDDD', fontFamily: '"PingFang SC","Heiti SC",sans-serif',
        fontSize: Math.round(height * 0.022), fontWeight: 700, display: 'flex', alignItems: 'center', gap: 10,
      }}>
        <span style={{width: 10, height: 10, borderRadius: 5, background: accent, display: 'inline-block'}}/>
        <span>{pop.title}</span>
      </div>}
      <div style={{flex: 1, position: 'relative', overflow: 'hidden', background: '#101513'}}>
        {mediaLayer(pop.source, pop.fit ?? 'contain', pop.sourceStart ?? 0)}
      </div>
    </div>;
  });

  const baseZoom = interpolate(frame, [0, Math.max(1, frames - 1)], [1.0, 1.05], clamp);
  const baseLayer = mediaLayer(params.source, params.fit ?? 'cover', params.sourceStart ?? 0);
  const loopedBase = videoFile.test(params.source ?? '') && params.sourceLoop !== false &&
    Number.isInteger(params.sourceDurationFrames) && params.sourceDurationFrames > 1
    ? <Loop durationInFrames={params.sourceDurationFrames}>{baseLayer}</Loop>
    : baseLayer;
  return <AbsoluteFill style={stage}>
    <AbsoluteFill style={{
      transform: `scale(${baseZoom.toFixed(4)})`,
      filter: activeAmount * maxBlur > 0.15 ? `blur(${(activeAmount * maxBlur).toFixed(2)}px)` : undefined,
    }}>
      {loopedBase}
    </AbsoluteFill>
    {activeAmount > 0.005 && <AbsoluteFill style={{backgroundColor: `rgba(13,19,16,${(activeAmount * maxDim).toFixed(3)})`}}/>}
    {renderedPopups}
  </AbsoluteFill>;
}

export function InfiniteCanvas({params, width, height, frames, hasBackground}) {
  const frame = useCurrentFrame();
  const nodes = params.nodes;
  if (!Array.isArray(nodes) || nodes.length < 2 || nodes.length > 6) {
    throw new Error('infinite-canvas requires 2–6 spatial board nodes');
  }
  const boardW = 3200;
  const boardH = 1800;
  const dark = (params.theme ?? 'dark') !== 'light';
  const accent = params.accent ?? (dark ? '#EDA961' : '#82501D');
  const teal = dark ? '#8AD7CD' : '#176663';
  const ink = dark ? '#F4EDDD' : '#131A17';
  const muted = dark ? '#A9B3AA' : '#56605A';
  const surface = dark ? 'rgba(24, 34, 29, 0.94)' : 'rgba(248, 243, 232, 0.96)';
  const bg = (hasBackground || params.transparent)
    ? 'transparent'
    : (dark ? 'radial-gradient(ellipse at 50% 45%, #1B2722 0%, #101613 78%)' : '#EFE7D4');

  // Default flight visits every node sequentially, then pulls back to show the full connected board.
  const defaultWaypoints = [
    ...nodes.map((n, i) => ({
      frame: Math.round((frames - 1) * (i / (nodes.length + 0.35))),
      at: [n.x ?? (0.24 + i * 0.22), n.y ?? (i % 2 === 0 ? 0.42 : 0.60)],
      zoom: 1.55,
      rotate: (i % 2 === 0 ? -1.4 : 1.4),
    })),
    {frame: frames - 1, at: [0.5, 0.5], zoom: 0.72, rotate: 0},
  ];
  const waypoints = Array.isArray(params.waypoints) && params.waypoints.length >= 2 ? params.waypoints : defaultWaypoints;

  // Locate active waypoint segment with smooth cubic easing.
  let seg = 0;
  while (seg < waypoints.length - 2 && frame >= (waypoints[seg + 1].frame ?? 0)) {
    seg += 1;
  }
  const wpA = waypoints[seg];
  const wpB = waypoints[Math.min(waypoints.length - 1, seg + 1)];
  const fA = wpA.frame ?? 0;
  const fB = Math.max(fA + 1, wpB.frame ?? (frames - 1));
  const t = interpolate(frame, [fA, fB], [0, 1], {...clamp, easing: Easing.inOut(Easing.cubic)});
  const camX = interpolate(t, [0, 1], [wpA.at[0], wpB.at[0]]) * boardW;
  const camY = interpolate(t, [0, 1], [wpA.at[1], wpB.at[1]]) * boardH;
  const camZoom = interpolate(t, [0, 1], [wpA.zoom ?? 1.4, wpB.zoom ?? 1.4]) * (width / 1920);
  const camRot = interpolate(t, [0, 1], [wpA.rotate ?? 0, wpB.rotate ?? 0]);

  const links = Array.isArray(params.links)
    ? params.links
    : nodes.slice(0, -1).map((_, i) => ({from: i, to: i + 1}));

  // Determine which node is currently closest to camera center for glow emphasis.
  let focusIdx = 0;
  let bestDist = Infinity;
  nodes.forEach((n, i) => {
    const d = Math.hypot((n.x ?? 0.5) * boardW - camX, (n.y ?? 0.5) * boardH - camY);
    if (d < bestDist) {
      bestDist = d;
      focusIdx = i;
    }
  });
  const pulse = 0.5 + 0.5 * Math.sin(frame * 0.16);

  return <AbsoluteFill style={{background: bg, overflow: 'hidden'}}>
    <div style={{
      position: 'absolute', left: 0, top: 0, width: boardW, height: boardH,
      transformOrigin: '0 0',
      transform: `translate(${(width / 2).toFixed(1)}px, ${(height / 2).toFixed(1)}px) scale(${camZoom.toFixed(4)}) rotate(${camRot.toFixed(2)}deg) translate(${(-camX).toFixed(1)}px, ${(-camY).toFixed(1)}px)`,
    }}>
      {/* Tactical investigation grid + pinboard cords */}
      <svg width={boardW} height={boardH} style={{position: 'absolute', inset: 0, pointerEvents: 'none'}}>
        <defs>
          <pattern id="ic-grid" width="80" height="80" patternUnits="userSpaceOnUse">
            <path d="M 80 0 L 0 0 0 80" fill="none" stroke={dark ? 'rgba(138,215,205,0.08)' : 'rgba(19,26,23,0.08)'} strokeWidth="1.5"/>
          </pattern>
        </defs>
        <rect width={boardW} height={boardH} fill="url(#ic-grid)"/>
        {links.map((lk, idx) => {
          const a = nodes[lk.from];
          const b = nodes[lk.to];
          if (!a || !b) return null;
          const x1 = (a.x ?? 0.3) * boardW, y1 = (a.y ?? 0.5) * boardH;
          const x2 = (b.x ?? 0.7) * boardW, y2 = (b.y ?? 0.5) * boardH;
          const mx = (x1 + x2) * 0.5, my = (y1 + y2) * 0.5 - 65;
          const col = lk.color ?? accent;
          return <g key={idx}>
            <path d={`M ${x1} ${y1} Q ${mx} ${my} ${x2} ${y2}`} fill="none"
              stroke={col} strokeWidth="4.5" strokeDasharray="12 8" opacity="0.72"/>
            <circle cx={x1} cy={y1} r="8" fill={col}/>
            <circle cx={x2} cy={y2} r="8" fill={col}/>
          </g>;
        })}
      </svg>

      {/* Spatial Evidence Cards staying persistently on the world canvas */}
      {nodes.map((node, idx) => {
        const nx = (node.x ?? (0.24 + idx * 0.22)) * boardW;
        const ny = (node.y ?? 0.5) * boardH;
        const nw = node.width ?? 640;
        const isFocus = idx === focusIdx && camZoom > 0.95;
        const nAccent = node.accent ?? (idx % 2 === 0 ? accent : teal);
        return <div key={idx} style={{
          position: 'absolute', left: nx - nw / 2, top: ny - 185, width: nw,
          padding: '28px 34px', borderRadius: 18, background: surface,
          border: `${isFocus ? 3.5 : 1.8}px solid ${isFocus ? nAccent : (dark ? 'rgba(244,237,221,0.18)' : 'rgba(19,26,23,0.18)')}`,
          boxShadow: isFocus ? `0 28px 72px rgba(0,0,0,0.62), 0 0 ${26 + 12 * pulse}px ${nAccent}44` : '0 14px 36px rgba(0,0,0,0.34)',
          transform: `rotate(${(node.tilt ?? (idx % 2 === 0 ? -1.1 : 1.1))}deg) scale(${isFocus ? 1.03 : 0.98})`,
          fontFamily: '"Avenir Next","PingFang SC","Heiti SC",sans-serif',
          color: ink,
        }}>
          <div style={{display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12}}>
            <span style={{fontSize: 20, fontWeight: 700, color: nAccent, letterSpacing: '0.06em'}}>
              {node.kicker ?? `EVIDENCE 0${idx + 1}`}
            </span>
            <span style={{width: 12, height: 12, borderRadius: 6, background: nAccent, boxShadow: `0 0 12px ${nAccent}`}}/>
          </div>
          {node.image && <div style={{height: 190, borderRadius: 10, overflow: 'hidden', marginBottom: 16, border: `1px solid ${nAccent}44`}}>
            {mediaLayer(node.image, 'cover', 0)}
          </div>}
          <div style={{fontFamily: '"Songti SC","STSong",serif', fontSize: 38, fontWeight: 700, lineHeight: 1.24}}>
            {node.title}
          </div>
          {node.stat && <div style={{fontSize: 54, fontWeight: 800, color: nAccent, marginTop: 10, fontVariantNumeric: 'tabular-nums'}}>
            {node.stat}
          </div>}
          {node.body && <div style={{fontSize: 24, lineHeight: 1.48, color: muted, marginTop: 12}}>
            {node.body}
          </div>}
          {node.source && <div style={{fontSize: 18, color: muted, marginTop: 16, opacity: 0.8}}>
            {node.source}
          </div>}
        </div>;
      })}
    </div>
    {params.boardTitle && <div style={{
      position: 'absolute', left: 54, top: 42, padding: '10px 20px', borderRadius: 10,
      background: dark ? 'rgba(15,22,19,0.82)' : 'rgba(244,237,221,0.88)',
      borderLeft: `4px solid ${accent}`, color: ink,
      fontFamily: '"PingFang SC","Heiti SC",sans-serif', fontSize: 24, fontWeight: 700,
    }}>{params.boardTitle}</div>}
  </AbsoluteFill>;
}
