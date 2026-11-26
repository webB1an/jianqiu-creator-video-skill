import React from 'react';
import {AbsoluteFill, Composition, Img, Loop, OffthreadVideo, interpolate, staticFile, useCurrentFrame} from 'remotion';

const VIDEO_RE = /\.(mp4|m4v|mov|webm|mkv)$/i;

function BackgroundLayer({spec}) {
  const frame = useCurrentFrame();
  const file = spec.background;
  if (!file) return null;
  const isVideo = VIDEO_RE.test(file);
  const startOffset = Number.isInteger(spec.backgroundStart) && spec.backgroundStart > 0 ? spec.backgroundStart : 0;
  const shouldLoop = isVideo && spec.backgroundLoop !== false && Number.isInteger(spec.backgroundDurationFrames) && spec.backgroundDurationFrames > 1;
  const motion = spec.backgroundMotion ?? 'drift';
  const total = Math.max(2, spec.frames);
  const scale = motion === 'none'
    ? 1
    : interpolate(frame, [0, total - 1], [1.0, isVideo ? 1.055 : 1.08], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const panX = motion === 'none'
    ? 0
    : interpolate(frame, [0, total - 1], [0, isVideo ? -0.9 : -1.4], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const blurPx = Number.isFinite(spec.backgroundBlur) ? Math.max(0, spec.backgroundBlur) : (isVideo ? 1.5 : 0);
  const theme = spec.params?.theme === 'light' ? 'light' : 'dark';
  const luma = Number.isFinite(spec.backgroundLuma) ? Math.max(0, Math.min(1, spec.backgroundLuma)) : 0.42;
  // Adaptive radial scrim calibrated to measured background luminance so dark footage stays vivid and bright footage stays legible.
  const darkInner = (0.30 + 0.44 * luma).toFixed(2);
  const darkOuter = (0.54 + 0.36 * luma).toFixed(2);
  const lightInner = (0.64 + 0.24 * (1 - luma)).toFixed(2);
  const lightOuter = (0.80 + 0.14 * (1 - luma)).toFixed(2);
  const defaultScrim = theme === 'light'
    ? `radial-gradient(ellipse at 50% 45%, rgba(244,237,221,${lightInner}) 0%, rgba(244,237,221,${lightOuter}) 100%)`
    : `radial-gradient(ellipse at 50% 45%, rgba(13,19,16,${darkInner}) 0%, rgba(10,15,13,${darkOuter}) 100%)`;
  const scrim = spec.backgroundScrim === false ? null : (
    typeof spec.backgroundScrim === 'string' && spec.backgroundScrim !== 'auto' ? spec.backgroundScrim : defaultScrim
  );
  const mediaStyle = {
    width: '100%',
    height: '100%',
    objectFit: 'cover',
    transform: `scale(${scale.toFixed(4)}) translateX(${panX.toFixed(2)}%)`,
    filter: blurPx > 0 ? `blur(${blurPx}px)` : undefined,
  };
  const videoEl = <OffthreadVideo src={staticFile(file)} muted trimBefore={startOffset} style={mediaStyle}/>;
  return <AbsoluteFill style={{overflow: 'hidden', backgroundColor: theme === 'light' ? '#F4EDDD' : '#131A17'}}>
    {isVideo
      ? (shouldLoop ? <Loop durationInFrames={spec.backgroundDurationFrames}>{videoEl}</Loop> : videoEl)
      : <Img src={staticFile(file)} style={mediaStyle}/>}
    {scrim && <AbsoluteFill style={{background: scrim}}/>}
  </AbsoluteFill>;
}

export function makeComposition(Effect, spec) {
  function Picture() {
    const hasBackground = Boolean(spec.background);
    return <AbsoluteFill>
      <BackgroundLayer spec={spec}/>
      <Effect {...spec} hasBackground={hasBackground}/>
    </AbsoluteFill>;
  }
  const Root = () => <Composition id="LibraryShot" component={Picture} width={spec.width} height={spec.height}
    fps={spec.fps} durationInFrames={spec.frames}/>;
  return Root;
}
