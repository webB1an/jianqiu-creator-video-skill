import React from 'react';
import {Composition, registerRoot} from 'remotion';
import {ShotTransition} from './shot-transition-view';
import spec from './spec.json';

const Root = () => <Composition id="ShotTransition" component={ShotTransition} defaultProps={{spec}}
  width={spec.width} height={spec.height} fps={spec.fps} durationInFrames={spec.frames} />;
registerRoot(Root);
