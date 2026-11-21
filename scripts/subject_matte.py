"""Build a frame-aligned foreground layer and tracked subject anchors from a single shot."""
import argparse
import json
import math
from pathlib import Path
import subprocess
import tempfile

import cv2
import numpy as np
from PIL import Image, ImageOps


def images(folder):
    paths = [p for p in folder.iterdir() if p.suffix.lower() in {'.png','.jpg','.jpeg','.webp'} and p.stem.isdecimal()]
    paths.sort(key=lambda p: int(p.stem))
    if not paths or any(int(b.stem) != int(a.stem)+1 for a,b in zip(paths,paths[1:])):
        raise ValueError('Supply consecutively numbered source frames from a single shot')
    return paths


def normal_box(values):
    left,top,right,bottom = values
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise ValueError('Use normalized box corners: left top right bottom')
    return values


def vision_masks(files, work):
    out = work/'vision-masks'
    out.mkdir()
    binary = work/'vision'
    subprocess.run(['clang','-O2','-fobjc-arc',str(Path(__file__).with_name('subject_vision.m')),
                    '-framework','Foundation','-framework','Vision','-framework','CoreImage',
                    '-framework','CoreGraphics','-framework','CoreVideo','-o',binary], check=True)
    entries = work/'source.json'
    entries.write_text(json.dumps([str(p.resolve()) for p in files]))
    subprocess.run([binary,entries,out],check=True)
    return out


def source_frame(path, args, masks):
    with Image.open(path) as original:
        image = ImageOps.exif_transpose(original)
        rgb = np.asarray(image.convert('RGB'))
        if masks:
            with Image.open(masks/(path.stem+'.png')) as mask:
                if mask.size != image.size:
                    raise ValueError(f'Mask and source differ in size at {path.name}')
                alpha = np.array(mask.convert('L'))
        elif args.alpha:
            if 'A' not in image.getbands() and 'transparency' not in image.info:
                raise ValueError(f'Missing source alpha: {path.name}')
            alpha = np.array(image.convert('RGBA').getchannel('A'))
        elif args.box:
            w,h = image.size
            l,t,r,b = [round(v*s) for v,s in zip(args.box,(w,h,w,h))]
            if min(r-l,b-t) < 2 or (r-l)*(b-t) >= w*h:
                raise ValueError('GrabCut needs foreground inside the box and background outside')
            labels = np.zeros((h,w),np.uint8)
            cv2.grabCut(rgb,labels,(l,t,r-l,b-t),np.zeros((1,65),float),
                        np.zeros((1,65),float),5,cv2.GC_INIT_WITH_RECT)
            alpha = cv2.GaussianBlur(((labels & 1)*255).astype('uint8'), (3,3), .55)
        else:
            channel = 1 if args.key == 'green' else 2
            colors = rgb.astype(np.float32)
            excess = colors[:,:,channel] - np.max(np.delete(colors,channel,2),axis=2)
            floor,ceiling = args.key_range
            alpha = np.rint(255*np.clip((ceiling-excess)/(ceiling-floor),0,1)).astype('uint8')
    if args.min_component:
        _, labels, stats, _ = cv2.connectedComponentsWithStats((alpha > 8).astype('uint8'),8)
        keep = stats[:,cv2.CC_STAT_AREA] >= args.min_component
        keep[0] = False
        alpha = np.where(keep[labels],alpha,0).astype('uint8')
    return rgb,alpha


def finish(rgb, previous, alpha, following, spill, inset=0):
    # Dampen mild edge flicker; large edge displacements and opaque detail keep their own frame.
    current = alpha.astype(np.int16)
    prev = previous.astype(np.int16)
    nxt = following.astype(np.int16)
    stable = (current > 0) & (current < 255) & (abs(prev-current) <= 40) & (abs(nxt-current) <= 40)
    out = np.where(stable,(prev+2*current+nxt+2)//4,current).astype('uint8')
    cleaned = rgb.copy()
    if inset or spill:
        # Core regions supply uncontaminated foreground colour. Detached fine strands
        # keep their silhouette when an inset is applied to larger solid shapes.
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(7,7))
        substantial = cv2.dilate(cv2.morphologyEx((out>=128).astype('uint8'),cv2.MORPH_OPEN,kernel),kernel)>0
    if inset:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(2*inset+1,2*inset+1))
        out = np.where(substantial,cv2.erode(out,kernel),out)
    if spill:
        c = {'green':1,'blue':2}[spill]
        ceiling = np.maximum.reduce([rgb[:,:,i] for i in range(3) if i != c])
        excess = np.maximum(0,rgb[:,:,c].astype(np.int16)-ceiling)
        core = (out>246).astype(np.float32)
        weight = cv2.GaussianBlur(core,(0,0),2)
        near = cv2.GaussianBlur(rgb*core[:,:,None],(0,0),2)/np.maximum(.001,weight[:,:,None])
        mix = np.where(substantial & (weight>.015) & (out>0),
                       (1-out/255.)*np.clip(excess/65.,0,1),0)[:,:,None]
        cleaned = np.rint(rgb*(1-mix)+near*mix).clip(0,255).astype('uint8')
        ceiling = np.maximum.reduce([cleaned[:,:,i] for i in range(3) if i != c])
        excess = np.maximum(0,cleaned[:,:,c].astype(np.int16)-ceiling)
        cleaned[:,:,c] = np.rint(cleaned[:,:,c] - excess*(1-out/255.)).astype('uint8')
    return Image.fromarray(np.dstack((cleaned,out)))


def inspect_subject(frame):
    alpha = np.asarray(frame.getchannel('A'))
    rows,cols = np.nonzero(alpha >= 128)
    if not len(rows):
        return None
    top,bottom = int(rows.min()),int(rows.max())+1
    left,right = int(cols.min()),int(cols.max())+1
    upper = cols[rows < top + (bottom-top)*.35]
    return [left,top,right,bottom],float(np.median(upper if len(upper) else cols))


def run(args):
    paths = images(args.source)
    if args.output.exists():
        raise ValueError('Choose a new directory for the extracted subject')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.output.parent) as temporary:
        work = Path(temporary)
        out = work/'cut'; out.mkdir()
        masks = vision_masks(paths,work) if args.vision else args.masks
        partials = (source_frame(p,args,masks) for p in paths)
        current = next(partials)
        previous = current[1]
        h,w = previous.shape
        boxes, centres, cuts = [], [], []
        last_proxy = None
        for i in range(len(paths)):
            following = next(partials,None)
            if current[1].shape != (h,w) or (following is not None and following[1].shape != (h,w)):
                raise ValueError('All frames must use the same upright raster')
            frame = finish(current[0],previous,current[1],following[1] if following is not None else current[1],
                           args.despill or args.key,args.inset)
            subject = inspect_subject(frame)
            boxes.append(None if subject is None else subject[0])
            centres.append(None if subject is None else subject[1])
            proxy = cv2.resize(cv2.cvtColor(current[0],cv2.COLOR_RGB2GRAY),(80,45),interpolation=cv2.INTER_AREA).astype(np.int16)
            if last_proxy is not None and np.mean(abs(proxy-last_proxy)) > 30:
                cuts.append(i+1)
            last_proxy = proxy
            frame.save(out/f'{i+1:04d}.png',compress_level=3)
            previous,current = current[1],following
        present = [i for i,b in enumerate(boxes) if b]
        if not present:
            raise ValueError('No visible subject. Check the mask or key on the original shot')
        # A short symmetric median rejects one frame of tracking jitter. Keep the stage geometry intact.
        tracking = np.interp(range(len(boxes)),present,[centres[i] for i in present])
        cx = [round(float(np.median(tracking[max(0,i-1):i+2])),2) for i in range(len(boxes))]
        tops = np.interp(range(len(boxes)),present,[boxes[i][1] for i in present])
        data = dict(x=0,y=0,w=w,h=h,src_w=w,src_h=h,scale=1,frames=len(paths),fps=args.fps,
                    cx=cx,top=tops.tolist(),bounds=boxes,possible_cut_frames=cuts,
                    source_first=int(paths[0].stem),source_last=int(paths[-1].stem))
        (out/'box.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
        out.rename(args.output)
    return data


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path,help='Upright, numbered source images')
    p.add_argument('output',type=Path,help='New project asset directory for the RGBA sequence')
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--masks',type=Path,help='Same-stem grayscale PNG mattes, white means foreground')
    group.add_argument('--alpha',action='store_true',help='Use existing alpha from the source frames')
    group.add_argument('--vision',action='store_true',help='Segment foreground instances locally on macOS 14+')
    group.add_argument('--key',choices=['green','blue'])
    group.add_argument('--box',nargs=4,type=float,help='Segment an object within normalized L T R B corners')
    p.add_argument('--key-range',nargs=2,type=float,default=(12,90),help='Colour excess for opaque and clear pixels')
    p.add_argument('--min-component',type=int,default=0,help='Remove disconnected components below this area in source pixels')
    p.add_argument('--inset',type=int,default=0,help='Inset broad alpha edges by 0..6 source pixels while retaining detached fine features')
    p.add_argument('--despill',choices=['green','blue'],help='Suppress colour contamination at translucent edges')
    p.add_argument('--fps',type=float,required=True,help='Cadence used when extracting the input sequence')
    args = p.parse_args()
    try:
        if args.box: normal_box(args.box)
        if not 0 <= args.key_range[0] < args.key_range[1] <= 255 or args.min_component < 0 or not 0 <= args.inset <= 6:
            raise ValueError('Use key levels inside 0..255, a nonnegative component area and an inset of 0..6')
        if not math.isfinite(args.fps) or args.fps <= 0:
            raise ValueError('Use the positive frame rate of the source sequence')
        data = run(args)
        print(json.dumps({k:data[k] for k in ('frames','w','h','possible_cut_frames')}))
    except (ValueError,OSError,subprocess.CalledProcessError,cv2.error) as exc:
        p.exit(1,f'{exc}\n')

if __name__ == '__main__':
    main()
