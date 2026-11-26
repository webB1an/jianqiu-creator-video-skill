import React from 'react';
import {AbsoluteFill, Easing, Img, OffthreadVideo, Sequence, interpolate, staticFile, useCurrentFrame} from 'remotion';
import {ShotTransition} from '../../motion/shot-transition-view';
import {EvidenceCut} from '../../motion/evidence-cut-view';

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'};

function media(file, start = 0, anchor = [0.5, 0.5]) {
  if (typeof file !== 'string' || !file.trim()) throw Error('请指定本机媒体文件');
  if (!Number.isInteger(start) || start < 0) throw Error('素材起始帧应为非负整数');
  if (!Array.isArray(anchor) || anchor.length !== 2 || !anchor.every(v => Number.isFinite(v) && v >= 0 && v <= 1)) throw Error('取景中心为 0 到 1 之间的坐标');
  return {asset: file, still: /\.(svg|png|jpe?g|gif|webp|bmp)$/i.test(file), trimFrames: start, anchor};
}

function Layer({source}) {
  const props = {
    src: staticFile(source.asset),
    style: {width: '100%', height: '100%', objectFit: 'cover',
      objectPosition: source.anchor.map(v => `${v * 100}%`).join(' ')},
  };
  return <AbsoluteFill style={{backgroundColor: '#131A17'}}>
    {source.still ? <Img {...props}/> : <OffthreadVideo {...props} trimBefore={source.trimFrames} muted/>}
  </AbsoluteFill>;
}

function bridge(type, {params: p, width, height, fps, frames}) {
  const overlap = p.overlap ?? Math.min(frames, Math.max(2, Math.round(fps * .5)));
  const at = p.atFrame ?? Math.floor((frames - overlap) / 2);
  if (!Number.isInteger(overlap) || overlap < 1 || !Number.isInteger(at) || at < 0 || at + overlap > frames) {
    throw Error('转场重叠段须在本镜帧数内');
  }
  const outgoing = {...media(p.outgoing, p.outStart, p.outAnchor), frames: at + overlap};
  const incoming = {...media(p.incoming, p.inStart, p.inAnchor), frames: frames - at};
  return <ShotTransition spec={{width, height, fps, frames, sources: [outgoing, incoming],
    transition: {type, frames: overlap, direction: p.direction, timing: p.timing}}}/>;
}

export const Slide = props => bridge('slide', props);
export const Wipe = props => bridge('wipe', props);
export const Fade = props => bridge('fade', props);
export const Iris = props => bridge('iris', props);
export const Flip = props => bridge('flip', props);
export const Cube = props => bridge('cube', props);
export const ClockWipe = props => bridge('clock-wipe', props);
export const PushCut = props => bridge('push-cut', props);

export function WhipPan({params: p, width, height, fps, frames}) {
  const frame = useCurrentFrame();
  const overlap = p.overlap ?? Math.min(frames, Math.max(2, Math.round(fps * .34)));
  const at = p.atFrame ?? Math.floor((frames - overlap) / 2);
  if (!Number.isInteger(overlap) || overlap < 2 || !Number.isInteger(at) || at < 0 || at + overlap > frames) {
    throw Error('转场重叠段须在本镜帧数内');
  }
  const direction = p.direction ?? 'from-right';
  if (!['from-right', 'from-left', 'from-top', 'from-bottom'].includes(direction)) {
    throw Error('direction 取 from-left、from-right、from-top 或 from-bottom');
  }
  const outgoing = media(p.outgoing, p.outStart, p.outAnchor);
  const incoming = media(p.incoming, p.inStart, p.inAnchor);
  const horizontal = direction === 'from-right' || direction === 'from-left';
  const sign = (direction === 'from-right' || direction === 'from-bottom') ? -1 : 1;
  const dist = horizontal ? width : height;
  const t = interpolate(frame, [at, at + overlap - 1], [0, 1], {...clamp, easing: Easing.inOut(Easing.cubic)});
  const blurMax = p.blur ?? 24;
  const blur = frame <= at || frame >= at + overlap - 1 ? 0 : blurMax * Math.pow(Math.sin(Math.PI * t), 1.5);
  const shift = dist * t * sign;
  const filterId = `whip-blur-${horizontal ? 'h' : 'v'}`;
  const filterStyle = blur > 0.4 ? {filter: `url(#${filterId})`} : {};
  const axis = horizontal ? 'X' : 'Y';
  return <AbsoluteFill style={{backgroundColor: '#131A17', overflow: 'hidden'}}>
    {blur > 0.4 && <svg width="0" height="0" style={{position: 'absolute'}}>
      <filter id={filterId} x="-25%" y="-25%" width="150%" height="150%">
        <feGaussianBlur stdDeviation={horizontal ? `${blur.toFixed(1)} 0` : `0 ${blur.toFixed(1)}`}/>
      </filter>
    </svg>}
    {frame < at + overlap && <Sequence from={0} durationInFrames={at + overlap} layout="none">
      <AbsoluteFill style={{transform: `translate${axis}(${shift}px)`, ...filterStyle}}>
        <Layer source={outgoing}/>
      </AbsoluteFill>
    </Sequence>}
    {frame >= at && <Sequence from={at} durationInFrames={frames - at} layout="none">
      <AbsoluteFill style={{transform: `translate${axis}(${shift - dist * sign}px)`, ...filterStyle}}>
        <Layer source={incoming}/>
      </AbsoluteFill>
    </Sequence>}
  </AbsoluteFill>;
}

export function BlurDissolve({params: p, fps, frames}) {
  const frame = useCurrentFrame();
  const overlap = p.overlap ?? Math.min(frames, Math.max(2, Math.round(fps * .45)));
  const at = p.atFrame ?? Math.floor((frames - overlap) / 2);
  if (!Number.isInteger(overlap) || overlap < 2 || !Number.isInteger(at) || at < 0 || at + overlap > frames) {
    throw Error('转场重叠段须在本镜帧数内');
  }
  const outgoing = media(p.outgoing, p.outStart, p.outAnchor);
  const incoming = media(p.incoming, p.inStart, p.inAnchor);
  const t = interpolate(frame, [at, at + overlap - 1], [0, 1], {...clamp, easing: Easing.inOut(Easing.cubic)});
  const blurMax = p.blur ?? 18;
  const outBlur = frame <= at ? 0 : t * blurMax;
  const inBlur = frame >= at + overlap - 1 ? 0 : (1 - t) * blurMax;
  return <AbsoluteFill style={{backgroundColor: '#131A17', overflow: 'hidden'}}>
    {frame < at + overlap && <Sequence from={0} durationInFrames={at + overlap} layout="none">
      <AbsoluteFill style={{opacity: 1 - t, filter: outBlur > 0.2 ? `blur(${outBlur.toFixed(1)}px)` : undefined,
        transform: outBlur > 0.2 ? `scale(${(1 + 0.04 * t).toFixed(4)})` : undefined}}>
        <Layer source={outgoing}/>
      </AbsoluteFill>
    </Sequence>}
    {frame >= at && <Sequence from={at} durationInFrames={frames - at} layout="none">
      <AbsoluteFill style={{opacity: t, filter: inBlur > 0.2 ? `blur(${inBlur.toFixed(1)}px)` : undefined,
        transform: inBlur > 0.2 ? `scale(${(1 + 0.04 * (1 - t)).toFixed(4)})` : undefined}}>
        <Layer source={incoming}/>
      </AbsoluteFill>
    </Sequence>}
  </AbsoluteFill>;
}

export function FocusPair({params: p, width, height, fps, frames}) {
  const start = p.startFrame ?? Math.floor(frames * .25), end = p.endFrame ?? Math.floor(frames * .7);
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start || end >= frames) {
    throw Error('双源取景需要两个落在本镜内、依次增加的关键帧');
  }
  if (p.side !== undefined && !['left', 'right'].includes(p.side)) throw Error('side 可取 left 或 right');
  const sources = [media(p.outgoing, p.outStart, p.outAnchor), media(p.incoming, p.inStart, p.inAnchor)];
  for (const source of sources) source.in = source.trimFrames / fps;
  return <EvidenceCut spec={{width, height, fps, frames, sources, focusStart: start, focusEnd: end, focusSide: p.side ?? 'left'}}/>;
}

export function MatchCutRing({params: p, width, height, fps, frames}) {
  const frame = useCurrentFrame();
  const overlap = p.overlap ?? Math.min(frames, Math.max(4, Math.round(fps * 0.40)));
  const at = p.atFrame ?? Math.floor((frames - overlap) / 2);
  if (!Number.isInteger(overlap) || overlap < 2 || !Number.isInteger(at) || at < 0 || at + overlap > frames) {
    throw Error('转场重叠段须在本镜帧数内');
  }
  const outgoing = media(p.outgoing, p.outStart, p.outAnchor);
  const incoming = media(p.incoming, p.inStart, p.inAnchor);
  const anchor = Array.isArray(p.anchor) && p.anchor.length === 2 ? p.anchor : outgoing.anchor;
  const cx = anchor[0] * width, cy = anchor[1] * height;
  const t = interpolate(frame, [at, at + overlap - 1], [0, 1], {...clamp, easing: Easing.inOut(Easing.cubic)});
  const zoom = Number.isFinite(p.zoom) ? Math.max(1.05, Math.min(2.6, p.zoom)) : 1.42;
  const accent = p.accent ?? '#EDA961';
  const maxR = Math.hypot(width, height) * 0.72;
  const baseR = (p.radius ?? 0.16) * Math.min(width, height);
  const ringR = interpolate(t, [0, 0.5, 1], [baseR * 0.85, baseR * 1.15, maxR]);
  const ringAlpha = frame < at || frame >= at + overlap ? 0 : Math.sin(Math.PI * t);
  const outScale = interpolate(t, [0, 1], [1.0, zoom]);
  const inScale = interpolate(t, [0, 1], [zoom * 0.88, 1.0]);
  const clipPct = (ringR / Math.max(width, height) * 140).toFixed(2);
  const origin = `${(anchor[0] * 100).toFixed(2)}% ${(anchor[1] * 100).toFixed(2)}%`;
  return <AbsoluteFill style={{backgroundColor: '#131A17', overflow: 'hidden'}}>
    {frame < at + overlap && <Sequence from={0} durationInFrames={at + overlap} layout="none">
      <AbsoluteFill style={{transformOrigin: origin, transform: `scale(${outScale.toFixed(4)})`}}>
        <Layer source={outgoing}/>
      </AbsoluteFill>
    </Sequence>}
    {frame >= at && <Sequence from={at} durationInFrames={frames - at} layout="none">
      <AbsoluteFill style={{
        transformOrigin: origin, transform: `scale(${inScale.toFixed(4)})`,
        clipPath: frame < at + overlap - 1 ? `circle(${clipPct}% at ${origin})` : undefined,
      }}>
        <Layer source={incoming}/>
      </AbsoluteFill>
    </Sequence>}
    {ringAlpha > 0.02 && <svg width={width} height={height} style={{position: 'absolute', inset: 0, pointerEvents: 'none'}}>
      <circle cx={cx} cy={cy} r={ringR} fill="none" stroke={accent}
        strokeWidth={Math.max(3, Math.round(height * 0.0055))} opacity={(ringAlpha * 0.92).toFixed(3)}/>
      <circle cx={cx} cy={cy} r={ringR * 0.86} fill="none" stroke="#8AD7CD"
        strokeWidth={Math.max(1.5, Math.round(height * 0.0025))} strokeDasharray="12 10"
        opacity={(ringAlpha * 0.65).toFixed(3)} transform={`rotate(${(t * 95).toFixed(1)} ${cx} ${cy})`}/>
    </svg>}
  </AbsoluteFill>;
}
