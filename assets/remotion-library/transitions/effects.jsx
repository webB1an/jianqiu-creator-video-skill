import React from 'react';
import {ShotTransition} from '../../motion/shot-transition-view';
import {EvidenceCut} from '../../motion/evidence-cut-view';

function media(file, start=0, anchor=[0.5,0.5]) {
  if (typeof file !== 'string' || !file.trim()) throw Error('请指定本机媒体文件');
  if (!Number.isInteger(start) || start<0) throw Error('素材起始帧应为非负整数');
  if (!Array.isArray(anchor) || anchor.length!==2 || !anchor.every(v=>Number.isFinite(v)&&v>=0&&v<=1)) throw Error('取景中心为 0 到 1 之间的坐标');
  return {asset:file, still:/\.(svg|png|jpe?g|gif|webp|bmp)$/i.test(file), trimFrames:start, anchor};
}

function bridge(type,{params:p,width,height,fps,frames}) {
  const overlap=p.overlap ?? Math.min(frames,Math.max(2,Math.round(fps*.5)));
  const at=p.atFrame ?? Math.floor((frames-overlap)/2);
  if (!Number.isInteger(overlap) || overlap<1 || !Number.isInteger(at) || at<0 || at+overlap>frames)
    throw Error('转场重叠段须在本镜帧数内');
  const outgoing={...media(p.outgoing,p.outStart,p.outAnchor),frames:at+overlap};
  const incoming={...media(p.incoming,p.inStart,p.inAnchor),frames:frames-at};
  return <ShotTransition spec={{width,height,fps,frames,sources:[outgoing,incoming],transition:{type,frames:overlap,direction:p.direction}}}/>;
}

export const Slide = props => bridge('slide',props);
export const Wipe = props => bridge('wipe',props);
export const Fade = props => bridge('fade',props);
export const Iris = props => bridge('iris',props);
export const Flip = props => bridge('flip',props);
export const Cube = props => bridge('cube',props);
export const ClockWipe = props => bridge('clock-wipe',props);
export const PushCut = props => bridge('push-cut',props);

export function FocusPair({params:p,width,height,fps,frames}) {
  const start=p.startFrame??Math.floor(frames*.25),end=p.endFrame??Math.floor(frames*.7);
  if(!Number.isInteger(start)||!Number.isInteger(end)||start<0||end<=start||end>=frames)
    throw Error('双源取景需要两个落在本镜内、依次增加的关键帧');
  if(p.side!==undefined&&!['left','right'].includes(p.side)) throw Error('side 可取 left 或 right');
  const sources=[media(p.outgoing,p.outStart,p.outAnchor),media(p.incoming,p.inStart,p.inAnchor)];
  for(const source of sources) source.in=source.trimFrames/fps;
  return <EvidenceCut spec={{width,height,fps,frames,sources,focusStart:start,focusEnd:end,focusSide:p.side??'left'}}/>;
}
