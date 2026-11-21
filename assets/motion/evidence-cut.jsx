import React from 'react';
import {Composition, registerRoot} from 'remotion';
import {EvidenceCut} from './evidence-cut-view';
import spec from './spec.json';

const Root = () => <Composition id="EvidenceCut" component={EvidenceCut} defaultProps={{spec}} width={spec.width} height={spec.height} durationInFrames={spec.frames} fps={spec.fps} />;
registerRoot(Root);
