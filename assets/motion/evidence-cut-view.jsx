import React from 'react';
import {AbsoluteFill, Img, OffthreadVideo, staticFile, useCurrentFrame} from 'remotion';

function Aperture({spec, source, start, width, height, fps}) {
  if (width === 0) return null;
  const style = {width:'100%', height:'100%', objectFit:'cover',
    objectPosition: source.anchor.map(n => `${n * 100}%`).join(' ')};
  return <div style={{position:'absolute', left:start, top:0, width, height, overflow:'hidden'}}>
    {source.still
      ? <Img src={staticFile(source.asset)} style={style} />
      : <OffthreadVideo src={staticFile(source.asset)} trimBefore={Math.round((source.in || 0) * fps)} muted style={style} />}
    {source.labelAsset && source.labelWidth + 2 * spec.labelInset <= width &&
      <Img src={staticFile(source.labelAsset)} style={{position:'absolute', left:spec.labelInset, top:spec.labelInset,
        width:source.labelWidth, height:source.labelHeight}} />}
  </div>;
}

export function EvidenceCut({spec}) {
  const frame = useCurrentFrame();
  const {height, width, fps, sources, focusStart, focusEnd, focusSide} = spec;
  const t = Math.max(0, Math.min(1, (frame - focusStart) / (focusEnd - focusStart)));
  const eased = t ** 3 * (t * (t * 6 - 15) + 10);
  const seam = Math.round(width * (0.5 + (focusSide === 'left' ? 0.5 : -0.5) * eased));
  const stroke = Math.round(Math.max(1, width / 450) * (1-eased));
  return <AbsoluteFill style={{backgroundColor:'#16191b'}}>
    <Aperture spec={spec} source={sources[0]} start={0} width={seam} height={height} fps={fps} />
    <Aperture spec={spec} source={sources[1]} start={seam} width={width-seam} height={height} fps={fps} />
    {seam > 0 && seam < width && stroke > 0 && <div style={{position:'absolute', left:seam-Math.floor(stroke/2),
      width:stroke, height, top:0, backgroundColor:'#e2dac6'}} />}
  </AbsoluteFill>;
}
