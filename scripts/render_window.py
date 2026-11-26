"""Burn the project's subtitles, optional chapter bar and brand into one freshly rendered picture interval."""
import argparse
from fractions import Fraction
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import color_finish
import montage_pins
from picture_finish import brand_image, chapter_bar_input, overlay_chain
from floating_captions import caption_input, collect_highlights, kinetic_caption_ranges, top_caption_ranges


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path, help='Clean picture starting on the first requested frame')
    p.add_argument('--start-frame', type=int, required=True)
    p.add_argument('--end-frame', type=int, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--captions', type=Path, default=Path('audio/captions.srt'))
    p.add_argument('--input-color', type=json.loads, help='Source color overrides in ffprobe field names, as JSON')
    p.add_argument('--grade', type=json.loads, help='Optional SDR grade for clean, ungraded picture in this interval')
    p.add_argument('--finish', type=json.loads, help='Optional optical finish (vignette/grain/sharpen/bloom/blur) as JSON')
    args = p.parse_args()
    timeline = json.loads(Path('timeline.json').read_text())
    edit = json.loads(Path('edit.json').read_text())
    fps = timeline['fps']
    w, h = edit.get('size', [1920, 1080])
    frames = args.end_frame - args.start_frame
    if not 0 <= args.start_frame < args.end_frame <= timeline['frames']:
        p.error('Time window must be within the project timeline')
    info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                      '-show_streams', '-of', 'json', str(args.source)]))['streams'][0]
    if (info['width'], info['height']) != (w, h) or Fraction(info['avg_frame_rate']) != fps:
        p.error('Render your clean shot at the project size and frame rate')
    if int(info.get('nb_frames') or 0) != frames:
        p.error(f'Clean shot must contain exactly {frames} frames')
    source, output = args.source.resolve(), args.output.resolve()
    if output == source:
        p.error('Choose a different output path')
    output.parent.mkdir(parents=True, exist_ok=True)
    plan_file = Path(edit.get('screen_plan') or 'screen-plan.json')
    screen_plan = json.loads(plan_file.read_text()) if plan_file.is_file() else None
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        work = Path(tmp)
        shutil.copyfile(args.captions, work / 'captions.srt')
        brand_image(w, h, work / 'mark.png')
        font = edit.get('font', 'Heiti SC')
        top_ranges = top_caption_ranges(edit, timeline, args.start_frame, frames,
                                        srt=work / 'captions.srt', size=(w, h))
        style_ranges = kinetic_caption_ranges(edit, timeline, args.start_frame, frames)
        highlights = collect_highlights(edit)
        captions = caption_input(work, work / 'captions.srt', w, h, fps, frames,
                                 start_frame=args.start_frame, style=edit.get('caption_style', 'plate'), font=font,
                                 top_ranges=top_ranges, timeline=timeline, highlights=highlights,
                                 style_ranges=style_ranges)
        next_index = 3 if captions else 2
        pin_args, pin_layers = montage_pins.inputs(
            montage_pins.plan(edit, timeline), work / 'montage-art', (w, h), start=args.start_frame,
            frames=frames, first_index=next_index)
        next_index += len(pin_layers)
        chapter_args, _ = chapter_bar_input(
            edit, timeline, work, w, h, fps, start_frame=args.start_frame, frames=frames,
            screen_plan=screen_plan, top_ranges=top_ranges)
        chapter_index = next_index if chapter_args else None
        src_filter = color_finish.graded_source_filter(info, args.input_color, args.grade)[0]
        fin = color_finish.finish_filter(args.finish)
        if fin:
            src_filter += ',' + fin
        chain = overlay_chain(w, h, font, brand_index=1, caption_index=2 if captions else None, montage_pins=pin_layers,
                              start_frame=args.start_frame, fps=fps, top_ranges=top_ranges,
                              source_filter=src_filter, chapter_index=chapter_index)
        target = work / 'window.mp4'
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(source), '-i', 'mark.png', *captions,
                        *pin_args, *chapter_args,
                        '-filter_complex', chain, '-map', '[picture]', '-an', '-frames:v', str(frames),
                        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '12', '-pix_fmt', 'yuv420p', *color_finish.FLAGS,
                        str(target)], cwd=work, check=True)
        os.replace(target, output)


if __name__ == '__main__':
    main()
