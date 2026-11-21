"""Locate camera moves, holds and independent movement at the source's displayed-frame times."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import subprocess

import cv2
import numpy as np

from action_takes import source


def movement(previous, current, dt):
    delta = np.abs(current.astype('int16') - previous)
    mae = float(delta.mean())
    row = {'kind':'unresolved','luma_change':round(mae,2)}
    if mae < .30:
        return dict(row,kind='hold')
    points = cv2.goodFeaturesToTrack(previous,220,.013,6,blockSize=7)
    if points is None or len(points)<10:
        return dict(row,kind='cut_candidate' if mae>30 else 'unresolved')
    moved,status,_ = cv2.calcOpticalFlowPyrLK(previous,current,points,None,winSize=(21,21),maxLevel=2)
    back,reverse,_ = cv2.calcOpticalFlowPyrLK(current,previous,moved,None,winSize=(21,21),maxLevel=2)
    keep = status.ravel().astype(bool) & reverse.ravel().astype(bool) & (np.linalg.norm((back-points)[:,0],axis=1)<.8)
    a,b = points[keep,0],moved[keep,0]
    if len(a) < max(7,.3*len(points)):
        return dict(row,kind='cut_candidate' if mae>18 else 'unresolved')
    matrix,inliers = cv2.estimateAffinePartial2D(a,b,method=cv2.RANSAC,ransacReprojThreshold=.8)
    if matrix is None:
        return row
    stable = inliers.ravel().astype(bool)
    h,w = previous.shape
    span = np.ptp(a[stable],axis=0)
    coverage = np.mean(stable)*min(1,span[0]/max(1,.55*w))*min(1,span[1]/max(1,.55*h))
    if coverage < .46:
        return dict(row,kind='cut_candidate' if mae>27 else 'unresolved')
    centre = np.array([w/2,h/2])
    translation = np.linalg.norm(matrix[:,:2]@centre + matrix[:,2] - centre)
    scale = np.linalg.norm(matrix[:,0])
    magnification = 100*(scale-1)/dt
    pan = 100*translation/w/dt
    rotation = math.degrees(math.atan2(matrix[1,0],matrix[0,0])) / dt
    residual = np.linalg.norm(b-(a@matrix[:,:2].T+matrix[:,2]),axis=1)
    independently_moving = (residual>max(.45,w*dt*.014)).mean()
    camera = abs(magnification)>1.5 or pan>2 or abs(rotation)>1.5
    if camera:
        kind='camera_and_local' if independently_moving >= .12 else 'camera'
    elif independently_moving >= .12 or (delta>12).mean()>.025:
        kind='local_change'
    else:
        kind='hold'
    return dict(row,kind=kind,features=len(a),camera_support=round(float(coverage),3),
                pan_width_pct_s=round(pan,2),zoom_pct_s=round(magnification,2),
                rotation_deg_s=round(rotation,2),local_fraction=round(float(independently_moving),3))


def measure(path, first, stop, top, bottom, details):
    meta = source(path)
    ticks,base = meta['ticks'],meta['tb']
    n=len(ticks)-1
    stop=n if stop is None else stop
    if not 0 <= first < stop-1 < n:
        raise ValueError(f'{path.name}: select at least two displayed frames inside 0..{n-1}')
    sw,sh=meta['raster']
    aspect=sw*float(meta['aspect'])/sh
    w,h=round(320*min(1,aspect)),round(320*min(1,1/aspect))
    lo,hi=round(top*h),round(bottom*h)
    if min(hi-lo,w)<21:
        raise ValueError('Visible region is too thin for reliable optical flow')
    vf=f'trim=start_frame={first}:end_frame={stop},scale={w}:{h}:flags=area,setsar=1,format=gray'
    cmd=['ffmpeg','-v','error','-nostdin','-i',str(path),'-map',f"0:{meta['video']}",'-vf',vf,
         '-fps_mode','passthrough','-frames:v',str(stop-first),'-f','rawvideo','-pix_fmt','gray','pipe:1']
    counts=Counter();runs=[];measurements=[];cuts=[];zoom=[];i0=None;previous=None
    with subprocess.Popen(cmd,stdout=subprocess.PIPE) as ff:
        try:
            for i in range(first,stop):
                buf=ff.stdout.read(w*h)
                if len(buf)!=w*h:raise ValueError(f'Incomplete frame {i} in {path.name}')
                image=np.frombuffer(buf,np.uint8).reshape(h,w)[lo:hi].copy()
                if previous is not None:
                    dt=float((ticks[i]-ticks[i-1])*base)
                    if dt>0:
                        m=movement(previous,image,dt)
                        kind=m['kind']
                        counts[kind]+=dt
                        if kind.startswith('camera'):zoom.append(m['zoom_pct_s'])
                        if kind=='cut_candidate':cuts.append(i)
                        if kind=='hold' and i0 is None:i0=i-1
                        if kind!='hold' and i0 is not None:
                            runs.append((i0,i-1));i0=None
                        if details: measurements.append(dict(source_frame=i,pts=float(ticks[i]*base),**m))
                previous=image
        finally:
            ff.stdout.close()
        if ff.wait():raise ValueError(f'Could not decode {path}')
    if i0 is not None:runs.append((i0,stop-1))
    ordered = sorted(((a,b,float((ticks[b]-ticks[a])*base)) for a,b in runs),key=lambda r:r[2],reverse=True)
    duration=sum(counts.values())
    result=dict(source=str(path),first=first,stop=stop,measured_seconds=round(duration,4),roi=[top,bottom],
                fraction_of_measured_time={k:round(v/duration,4) for k,v in counts.items()} if duration else {},
                cut_candidates=cuts,
                longest_quiet_intervals=[dict(first=a,last=b,seconds=round(s,3)) for a,b,s in ordered[:8]],
                median_abs_camera_zoom_pct_s=round(float(np.median(np.abs(zoom))),2) if zoom else None)
    if details:result['changes']=measurements
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('sources',type=Path,nargs='+')
    p.add_argument('--first',type=int,default=0)
    p.add_argument('--stop',type=int,help='Exclusive end frame')
    p.add_argument('--roi',type=float,nargs=2,default=[.05,.81],metavar=('TOP','BOTTOM'),
                   help='Analyze a vertical slice excluding captions and letterboxing')
    p.add_argument('--details',action='store_true')
    p.add_argument('--output',type=Path,help='New JSON file; default stdout')
    a=p.parse_args()
    if not 0<=a.roi[0]<a.roi[1]<=1:p.error('Use normalized ROI bounds')
    try:
        results=[measure(path,a.first,a.stop,*a.roi,a.details) for path in a.sources]
        report=json.dumps(results,ensure_ascii=False,indent=2,allow_nan=False)
        if a.output:
            a.output.parent.mkdir(parents=True,exist_ok=True)
            with a.output.open('x') as stream:stream.write(report+'\n')
        else:print(report)
    except (OSError,ValueError,subprocess.CalledProcessError,cv2.error) as e:p.exit(1,str(e)+'\n')

if __name__=='__main__':
    main()
