import React from 'react';
import {AbsoluteFill, Composition, Img, OffthreadVideo, staticFile} from 'remotion';

export function makeComposition(Effect, spec) {
  function Picture(){
    const file=spec.background, style={width:'100%', height:'100%', objectFit:'cover'};
    return <AbsoluteFill>
      {file&&<AbsoluteFill>{/\.(mp4|m4v|mov|webm|mkv)$/i.test(file)
        ? <OffthreadVideo src={staticFile(file)} muted style={style}/>
        : <Img src={staticFile(file)} style={style}/>}</AbsoluteFill>}
      <Effect {...spec}/>
    </AbsoluteFill>;
  }
  const Root=()=><Composition id="LibraryShot" component={Picture} width={spec.width} height={spec.height}
    fps={spec.fps} durationInFrames={spec.frames}/>;
  return Root;
}
