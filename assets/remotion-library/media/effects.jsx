import React from 'react';
import {AbsoluteFill, Easing, Img, OffthreadVideo, Sequence, interpolate, spring, staticFile, useCurrentFrame} from 'remotion';
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

export function SpotlightReveal({params, width, height, frames}) {
  const t = progress(useCurrentFrame(), frames, params, 0.75);
  const from = point(params.fromFocus ?? [0.33, 0.5], 'fromFocus');
  const to = point(params.toFocus ?? [0.65, 0.5], 'toFocus');
  const radius = interpolate(t, [0, 1], [
    bounded(params.fromRadius ?? 0.46, 'fromRadius', 0.025, 1.5),
    bounded(params.toRadius ?? 0.19, 'toRadius', 0.025, 1.5),
  ]) * Math.min(width, height);
  const x = interpolate(t, [0, 1], [from[0], to[0]]) * width;
  const y = interpolate(t, [0, 1], [from[1], to[1]]) * height;
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
