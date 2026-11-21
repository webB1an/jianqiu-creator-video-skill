"""Match a shared neutral patch between two camera angles on their normalized Rec.709 pictures."""
import argparse
from io import BytesIO
import json
from pathlib import Path
import subprocess
import tempfile

import numpy as np
from PIL import Image

from color_finish import graded_source_filter, icc_still
from source_framing import upright_raster


def picture(path, frame, overrides, grade=None):
    if frame < 0:
        raise ValueError('Use a nonnegative decoded source frame')
    with tempfile.TemporaryDirectory() as tmp:
        path, profile = icc_still(path, Path(tmp)/'srgb.png')
        tracks = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'V:0',
                            '-show_streams', '-of', 'json', str(path)]))['streams']
        if not tracks:
            raise ValueError(f'No picture stream in {path}')
        info = tracks[0]
        colour, _ = graded_source_filter(info, None if profile else overrides, grade, raster_size=upright_raster(info)[:2])
        graph = (f"select='eq(n,{frame})',{colour},scale=in_color_matrix=bt709:in_range=tv:out_range=pc,"
                 'format=rgb24')
        data = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:V:0',
                    '-vf', graph, '-frames:v', '1', '-c:v', 'png', '-f', 'image2pipe', 'pipe:1'])
    if not data:
        raise ValueError(f'No image at requested frame {frame}')
    with Image.open(BytesIO(data)) as image:
        return np.asarray(image.convert('RGB'))


def region(image, box):
    x, y, w, h = box
    if min(x,y) < 0 or min(w,h) < 1 or x+w > image.shape[1] or y+h > image.shape[0]:
        raise ValueError(f'Box {box} exceeds the upright {image.shape[1]}×{image.shape[0]} picture')
    return image[y:y+h,x:x+w]


def median_rgb(patch):
    return np.median(patch, axis=(0,1)).tolist()


def changed_limits(before, after):
    """Count pixels acquiring at least one channel at the bottom or top of the RGB display range."""
    new_low = ((before > 0) & (after == 0)).any(axis=2)
    new_high = ((before < 255) & (after == 255)).any(axis=2)
    return {'new_black_channel_percent': round(100*float(new_low.mean()),3),
            'new_white_channel_percent': round(100*float(new_high.mean()),3)}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('source', type=Path)
    cli.add_argument('reference', type=Path)
    cli.add_argument('--source-box', type=int, nargs=4, required=True, metavar=('X','Y','W','H'))
    cli.add_argument('--reference-box', type=int, nargs=4, required=True, metavar=('X','Y','W','H'))
    cli.add_argument('--source-frame', type=int, default=0)
    cli.add_argument('--reference-frame', type=int, default=0)
    cli.add_argument('--source-color', type=json.loads, help='input_color for the source')
    cli.add_argument('--reference-color', type=json.loads, help='input_color for the reference')
    cli.add_argument('--evidence-box', type=int, nargs=4, metavar=('X','Y','W','H'))
    args = cli.parse_args()
    try:
        before = picture(args.source, args.source_frame, args.source_color)
        reference = picture(args.reference, args.reference_frame, args.reference_color)
        grade = {'white_balance': {'sample': median_rgb(region(before,args.source_box)),
                                   'reference': median_rgb(region(reference,args.reference_box))}}
        after = picture(args.source, args.source_frame, args.source_color, grade)
        report = {'grade':grade, 'corrected_patch':median_rgb(region(after,args.source_box)),
                  'picture_limits':changed_limits(before,after)}
        if args.evidence_box:
            a, b = region(before,args.evidence_box), region(after,args.evidence_box)
            report['evidence'] = {'before':median_rgb(a),'after':median_rgb(b),**changed_limits(a,b)}
        print(json.dumps(report,ensure_ascii=False,indent=2))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        cli.exit(1,f'{exc}\n')


if __name__ == '__main__':
    main()
