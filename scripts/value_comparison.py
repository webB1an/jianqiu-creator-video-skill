"""Render two measured quantities on a common zero axis, then trace the difference."""
import argparse
from decimal import Decimal, InvalidOperation, localcontext
import json
import os
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageColor, ImageDraw
from color_finish import FLAGS, source_filter
from identity import card_colors, card_face, comparison_color
from motion import ease
from production_run import digest


def number(raw):
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise ValueError('Use a numeric value for each measured quantity') from exc
    if not value.is_finite():
        raise ValueError('A value must be a finite measured number')
    return value


def caption(value):
    if not isinstance(value, str) or not value.strip() or len(value.splitlines()) != 1:
        raise ValueError('Use one nonempty line for the title, unit and each row label')
    return value.strip()


def resolve_window(pair, frames, timeline=None, timing=None):
    if not isinstance(pair, list) or len(pair) != 2:
        raise ValueError(f'Use a reveal window [first, settled] within shot frames 0..{frames - 1}')
    resolved = []
    for item in pair:
        if isinstance(item, str) and item.startswith('@'):
            if timeline is None or timing is None:
                raise ValueError('Word-cued comparison windows require a prepared timeline')
            from remotion_library import resolve_frame
            resolved.append(resolve_frame(item, timeline, timing))
        elif type(item) is int:
            resolved.append(item)
        else:
            raise ValueError(f'Use integer frame numbers or @line:word cues in comparison windows: {pair}')
    if not 0 <= resolved[0] < resolved[1] < frames:
        raise ValueError(f'Use a reveal window [first, settled] within shot frames 0..{frames - 1}')
    return resolved


def progress(frame, span):
    return ease((frame - span[0]) / (span[1] - span[0]))


def blended(fg, bg, amount):
    return tuple(round(b + (a - b) * amount) for a, b in zip(ImageColor.getrgb(fg), ImageColor.getrgb(bg)))


def fitted(role, text, pixels, max_width):
    # Check real ink as well as advance so overhanging letters stay inside the panel.
    font = card_face(role, pixels, text)
    left, _, right, _ = font.getbbox(text)
    extent = max(font.getlength(text), right - left, 1)
    if extent > max_width:
        size = int(pixels * max_width / extent)
        if size < pixels * .65:
            raise ValueError(f'Shorten this chart label so it is legible at delivery size: {text}')
        font = card_face(role, size, text)
    return font


class Comparison:
    def __init__(self, spec, roles, size, frames, *, timeline=None, timing=None):
        self.w, self.h = size
        if any(type(v) is not int or v <= 0 or v % 2 for v in size) or self.w * 9 != self.h * 16:
            raise ValueError('Use even 16:9 project dimensions')
        surface = spec.get('surface', 'dark')
        if surface not in ('light', 'dark'):
            raise ValueError('Use a light or dark chart surface')
        self.palette = card_colors(surface)
        self.title = caption(spec['title'])
        self.subtitle = caption(spec['subtitle']) if spec.get('subtitle') else None
        self.source = caption(spec['source']) if spec.get('source') else None
        unit = caption(spec['unit'])
        entries = spec['rows']
        if not isinstance(entries, list) or len(entries) != 2:
            raise ValueError('A difference chart requires two measurements using a common unit')
        self.values = [number(row['value']) for row in entries]
        self.names = [caption(row['label']) for row in entries]
        self.colors = [comparison_color(roles, row.get('entity'), surface) for row in entries]
        self.windows = [resolve_window(row['reveal'], frames, timeline, timing) for row in entries]
        self.gap = resolve_window(spec['gap'], frames, timeline, timing)
        if self.gap[0] < max(pair[1] for pair in self.windows):
            raise ValueError('Begin the difference bracket once both values have settled')

        self.labels = [f'{value:,f} {unit}' for value in self.values]
        lo, hi = min(0, *self.values), max(0, *self.values)
        left, right = round(self.w * .085), round(self.w * .915)
        exponents = [v.as_tuple().exponent for v in self.values]
        with localcontext() as context:
            context.prec = max(28, max(v.adjusted() for v in self.values) - min(exponents) + 3)
            delta = abs(self.values[1] - self.values[0])
            span = hi - lo
            self.zero = left + round((-lo / span) * (right - left)) if span else left
            self.ends = [left + round(((v - lo) / span) * (right - left)) if span else left for v in self.values]
        gap_unit = '个百分点' if unit in ('%', '％') else unit
        self.difference = f'相差 {delta:,f} {gap_unit}'
        self.left, self.right = left, right
        self.row_y = [round(self.h * .36), round(self.h * .565)]
        self.bar_h = max(2, round(self.h * .042))
        self.bar_r = max(2, self.bar_h // 5)
        self.bracket_y = round(self.h * .662)
        self.rule = max(1, round(self.h * .003))
        self.title_font = fitted('title', self.title, max(1, round(self.h * .076)), right - left)
        self.sub_font = fitted('label', self.subtitle, max(1, round(self.h * .028)), right - left) if self.subtitle else None
        self.source_font = fitted('label', self.source, max(1, round(self.h * .022)), right - left) if self.source else None
        self.name_fonts = [fitted('label', s, max(1, round(self.h * .055)), (right - left) * .51) for s in self.names]
        self.metric_fonts = [fitted('metric', s, max(1, round(self.h * .074)), (right - left) * .44) for s in self.labels]
        self.delta_font = fitted('metric', self.difference, max(1, round(self.h * .072)), right - left)

    def frame(self, n):
        p = self.palette
        im = Image.new('RGB', (self.w, self.h), p['background'])
        draw = ImageDraw.Draw(im)
        # Subtle editorial header rule and title block.
        draw.rectangle((self.left, round(self.h * .08), self.left + round(self.w * .045),
                        round(self.h * .08) + max(2, self.rule)), fill=p['accent'])
        draw.text((self.left, self.h * .14), self.title, anchor='lm', font=self.title_font, fill=p['text'])
        if self.subtitle:
            draw.text((self.left, self.h * .21), self.subtitle, anchor='lm', font=self.sub_font, fill=p['muted'])
        track = blended(p['muted'], p['background'], .15)
        for y in self.row_y:
            draw.rounded_rectangle((self.left, y, self.right, y + self.bar_h - 1), radius=self.bar_r, fill=track)
        draw.line((self.zero, self.row_y[0] - self.rule * 3, self.zero, self.row_y[-1] + self.bar_h + self.rule * 3),
                  width=self.rule, fill=blended(p['muted'], p['background'], .55))
        gap = progress(n, self.gap)
        if gap > 0:
            guide = blended(p['muted'], p['background'], gap * .6)
            for x, y in zip(self.ends, self.row_y):
                draw.line((x, y + self.bar_h, x, self.bracket_y), width=self.rule, fill=guide)
            a, b = self.ends
            tip = round(a + (b - a) * gap)
            draw.line((a, self.bracket_y, tip, self.bracket_y), fill=p['text'], width=self.rule)
            draw.line((a, self.bracket_y - self.rule * 3, a, self.bracket_y + self.rule), fill=p['text'], width=self.rule)
            if gap == 1:
                draw.line((b, self.bracket_y - self.rule * 3, b, self.bracket_y + self.rule), fill=p['text'], width=self.rule)
            draw.text((self.left, self.h * .752), self.difference, anchor='lm', font=self.delta_font,
                      fill=blended(p['accent'], p['background'], max(0, 2 * gap - 1)))
        for i, y in enumerate(self.row_y):
            build = progress(n, self.windows[i])
            if n < self.windows[i][0]:
                continue
            draw.text((self.left, y - self.h * .052), self.names[i], anchor='lm', font=self.name_fonts[i],
                      fill=blended(p['text'], p['background'], min(1.0, build * 2.4)))
            x = round(self.zero + (self.ends[i] - self.zero) * build)
            if x != self.zero:
                x0, x1 = min(self.zero, x), max(self.zero, x) - 1
                if x1 - x0 >= self.bar_r * 2:
                    draw.rounded_rectangle((x0, y, x1, y + self.bar_h - 1), radius=self.bar_r, fill=self.colors[i])
                else:
                    draw.rectangle((x0, y, x1, y + self.bar_h - 1), fill=self.colors[i])
            draw.text((self.right, y - self.h * .055), self.labels[i], anchor='rm', font=self.metric_fonts[i],
                      fill=blended(self.colors[i], p['background'], max(0, 2 * build - 1)))
        if self.source:
            draw.text((self.right, self.h * .84), self.source, anchor='rm', font=self.source_font, fill=p['muted'])
        return im


def output_path(project, edit, shot):
    project = project.resolve()
    target = (project / shot['visual']).resolve()
    if not target.is_relative_to(project) or target.suffix.lower() != '.mp4':
        raise ValueError('Set shot.visual to an MP4 path inside this project')

    def inputs(node):
        if isinstance(node, dict):
            for key, item in node.items():
                if node is shot and key == 'visual':
                    continue
                if key in {'visual', 'file', 'audio', 'music', 'effects', 'image'} and isinstance(item, str):
                    yield project / item
                else:
                    yield from inputs(item)
        elif isinstance(node, list):
            for item in node:
                yield from inputs(item)

    paths = list(inputs(edit))
    manifest = project / 'assets.json'
    if manifest.is_file():
        paths.extend(project / 'assets' / row['file'] for row in json.loads(manifest.read_text()))
    for source in paths:
        if source.resolve() == target or (target.exists() and source.exists() and target.samefile(source)):
            raise ValueError(f'The chart output is already a project input: {source}')
    return target


def check_owner(target, marker):
    if target.exists():
        tags = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format_tags=comment', '-of', 'json', str(target)],
            capture_output=True, text=True)
        if tags.returncode or json.loads(tags.stdout)['format'].get('tags', {}).get('comment') != marker:
            raise ValueError(f'Choose a new visual path; this file was not generated for this comparison: {target}')


def render(target, chart, frames, fps, marker):
    check_owner(target, marker)
    target.parent.mkdir(parents=True, exist_ok=True)
    transform, _ = source_filter({'width': chart.w, 'height': chart.h, 'pix_fmt': 'rgb24'})
    with tempfile.TemporaryDirectory(dir=target.parent) as temp:
        staged = Path(temp) / 'chart.mp4'
        cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{chart.w}x{chart.h}',
               '-framerate', str(fps), '-i', 'pipe:0', '-an', '-vf', transform, '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
               '-crf', '14', '-preset', 'veryfast', *FLAGS, '-metadata', f'comment={marker}', str(staged)]
        with subprocess.Popen(cmd, stdin=subprocess.PIPE) as encoder:
            with encoder.stdin as stream:
                for frame in range(frames):
                    stream.write(chart.frame(frame).tobytes())
            if encoder.wait():
                raise RuntimeError('FFmpeg could not encode the comparison shot')
        check_owner(target, marker)
        os.replace(staged, target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path('.'))
    parser.add_argument('--shot', required=True, help='ID of the shot with value_comparison in edit.json')
    args = parser.parse_args()
    try:
        edit_path = args.project / 'edit.json'
        edit = json.loads(edit_path.read_text(), parse_float=Decimal)
        timeline = json.loads((args.project / 'timeline.json').read_text())
        if timeline.get('edit_sha256') != digest(edit_path):
            raise ValueError('Prepare the timeline for this edition of edit.json first')
        shot = next((s for s in edit['shots'] if s['id'] == args.shot), None)
        timing = next((s for s in timeline['shots'] if s['id'] == args.shot), None)
        if shot is None or timing is None:
            raise ValueError(f'Unknown prepared shot: {args.shot}')
        if shot.get('in', 0) != 0:
            raise ValueError('A generated comparison shot starts at its own frame 0')
        target = output_path(args.project, edit, shot)
        chart = Comparison(shot['value_comparison'], edit.get('comparison_roles', {}),
                           edit.get('size', [1920, 1080]), timing['frames'], timeline=timeline, timing=timing)
        render(target, chart, timing['frames'], timeline['fps'], f'creator-video/value_comparison/{args.shot}')
        print(target)
    except (KeyError, TypeError, ValueError, ArithmeticError, OSError, RuntimeError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
