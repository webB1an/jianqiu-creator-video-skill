import React from 'react';
import {AbsoluteFill, Easing, Img, OffthreadVideo, staticFile} from 'remotion';
import {TransitionSeries, linearTiming, springTiming} from '@remotion/transitions';
import {clockWipe} from '@remotion/transitions/clock-wipe';
import {cube} from '@remotion/transitions/cube';
import {flip} from '@remotion/transitions/flip';
import {pushCut} from '@remotion/transitions/push-cut';
import {fade} from '@remotion/transitions/fade';
import {iris} from '@remotion/transitions/iris';
import {slide} from '@remotion/transitions/slide';
import {wipe} from '@remotion/transitions/wipe';

function Shot({source}) {
  const props = {
    src: staticFile(source.asset),
    style: {width: '100%', height: '100%', objectFit: 'cover',
      objectPosition: source.anchor.map(v => (100 * v) + '%').join(' ')},
  };
  return <AbsoluteFill style={{backgroundColor: 'black'}}>
    {source.still ? <Img {...props} /> : <OffthreadVideo {...props} trimBefore={source.trimFrames} muted />}
  </AbsoluteFill>;
}

function makeTiming(mode, frames) {
  if (mode === 'linear') return linearTiming({durationInFrames: frames});
  if (mode === 'spring') return springTiming({durationInFrames: frames, config: {damping: 200, stiffness: 160}});
  return linearTiming({durationInFrames: frames, easing: Easing.inOut(Easing.cubic)});
}

export function ShotTransition({spec}) {
  const [outgoing, incoming] = spec.sources;
  const {type, direction, frames, timing = 'cubic'} = spec.transition;
  const presentations = {
    slide: () => slide({direction}),
    wipe: () => wipe({direction}),
    fade: () => fade(),
    'clock-wipe': () => clockWipe({width: spec.width, height: spec.height}),
    cube: () => cube({direction: direction ?? 'from-right'}),
    flip: () => flip({direction}),
    'push-cut': () => pushCut(),
    iris: () => iris({width: spec.width, height: spec.height}),
  };
  return <TransitionSeries>
    <TransitionSeries.Sequence durationInFrames={outgoing.frames}><Shot source={outgoing} /></TransitionSeries.Sequence>
    <TransitionSeries.Transition presentation={presentations[type]()} timing={makeTiming(timing, frames)} />
    <TransitionSeries.Sequence durationInFrames={incoming.frames}><Shot source={incoming} /></TransitionSeries.Sequence>
  </TransitionSeries>;
}
