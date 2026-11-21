"""Measure the independently staged regions of an encoded shot on its edit clock."""
import argparse
from contextlib import closing
import json
import math
import subprocess
from pathlib import Path


SAMPLE_FPS = 8
WIDTH, HEIGHT = 480, 270
DEFAULT_ROI = [.06, .29, .94, .78]
MODES = {'trace', 'readout', 'footage', 'still', 'change'}
STILL_SUFFIXES = {'.jpg', '.jpeg', '.bmp'}


def bounds(roi):
    if (not isinstance(roi, (tuple, list)) or len(roi) != 4 or
            not all(type(v) in (float, int) and math.isfinite(v) for v in roi) or
            not (0 <= roi[0] < roi[2] <= 1 and 0 <= roi[1] < roi[3] <= 1)):
        raise ValueError(f'Invalid normalized motion ROI: {roi}')
    left, top, right, bottom = (round(x * n) for x, n in zip(roi, (WIDTH, HEIGHT, WIDTH, HEIGHT)))
    if left >= right or top >= bottom:
        raise ValueError(f'Motion ROI is smaller than one sampled pixel: {roi}')
    return (slice(top, bottom), slice(left, right))


def nonempty(text):
    return isinstance(text, str) and bool(text.strip())


def probes_from_plan(plan, frames):
    probes = []
    for cut in plan.get('cuts', []):
        multiple = 'motion_probes' in cut
        if multiple and 'motion_probe' in cut:
            raise ValueError('Use one motion probe form per cut')
        if multiple:
            entries = cut['motion_probes']
            if not isinstance(entries, list) or not entries:
                raise ValueError('motion_probes must be a nonempty list')
        else:
            info = cut.get('motion_probe')
            if not info and cut.get('kind') not in ('graphic', 'animation', 'chart', 'diagram'):
                continue
            entries = [info or {}]
        a, b = cut['start'], cut['end']
        if type(a) is not int or type(b) is not int or not 0 <= a < b <= frames:
            raise ValueError(f'Invalid component motion window at frame {a}')
        names = set()
        for info in entries:
            if not isinstance(info, dict):
                raise ValueError(f'Expected a motion probe object at frame {a}')
            roi = info.get('roi', DEFAULT_ROI)
            bounds(roi)
            probe = dict(id=cut.get('source_key') or str(a), start=a, end=b,
                         subject=info.get('subject') or cut['subject'], roi=list(roi),
                         authored=multiple or bool(cut.get('motion_probe')))
            if multiple:
                region, mode = info.get('region'), info.get('mode')
                if (not nonempty(region) or region in names or not nonempty(mode) or mode not in MODES or
                        not nonempty(info.get('action')) or 'roi' not in info):
                    raise ValueError(f'Give each region at frame {a} a unique name, ROI, mode and observable action')
                names.add(region)
                window = info.get('span', [0, b-a])
                if (not isinstance(window, list) or len(window) != 2 or
                        not all(type(x) is int for x in window) or not 0 <= window[0] < window[1] <= b-a):
                    raise ValueError(f'Invalid local span for {region}')
                probe.update(region=region, mode=mode, action=info['action'],
                             start=a+window[0], end=a+window[1])
                if 'reference_roi' in info:
                    bounds(info['reference_roi'])
                    if list(info['reference_roi']) == list(roi):
                        raise ValueError(f'Use a separate stationary feature for the reference ROI in {region}')
                    probe['reference_roi'] = list(info['reference_roi'])
                if mode in {'footage', 'still'}:
                    source = info.get('source')
                    if not isinstance(source, dict) or not nonempty(source.get('file')):
                        raise ValueError(f'Identify the visual source for {region}')
                    if mode == 'footage':
                        times = [source.get('in'), source.get('out')]
                        if (Path(source['file']).suffix.lower() in STILL_SUFFIXES or
                                not all(type(t) in (int, float) and math.isfinite(t) for t in times) or
                                not 0 <= times[0] < times[1]):
                            raise ValueError(f'Give the actual moving source and its in/out seconds for {region}')
                    elif not nonempty(source.get('reason')):
                        raise ValueError(f'Explain the reading task served by the still in {region}')
                    probe['source'] = source.copy()
            probes.append(probe)
    return probes


def decode_proxy(media, sampling, limit=None):
    size = WIDTH * HEIGHT
    command = ['ffmpeg', '-v', 'error', '-threads', '2', '-i', str(media), '-map', '0:v:0',
               '-an', '-vf', f'{sampling},scale={WIDTH}:{HEIGHT}:flags=area,format=gray',
               '-fps_mode', 'passthrough']
    if limit is not None:
        command.extend(['-frames:v', str(limit)])
    command.extend(['-f', 'rawvideo', '-threads', '1', 'pipe:1'])
    import numpy as np
    with subprocess.Popen(command, stdout=subprocess.PIPE) as process:
        try:
            while True:
                data = process.stdout.read(size)
                if not data:
                    break
                if len(data) != size:
                    raise ValueError('Truncated motion sample')
                yield np.frombuffer(data, np.uint8).reshape(HEIGHT, WIDTH)
        finally:
            process.stdout.close()
    if process.returncode:
        raise ValueError(f'Decoder failed on {media}')


def decode(media):
    yield from decode_proxy(media, f'fps={SAMPLE_FPS}')


def resembles_blend(samples):
    """Could the visible intermediates be linear dissolves of two fixed endpoint images?"""
    import numpy as np
    if len(samples) < 3:
        return None
    first, last = samples[0].astype(float), samples[-1].astype(float)
    delta = last - first
    mask = abs(delta) >= 12
    if mask.sum() < 8:
        return None
    denom = np.square(delta[mask]).sum()
    amplitude = abs(delta[mask]).mean()
    intermediates = []
    for sample in samples[1:-1]:
        offset = sample - first
        alpha = (offset[mask] * delta[mask]).sum() / denom
        if .07 < alpha < .93:
            # Include the whole region: a moving glyph can pass entirely outside the final glyph.
            error = abs(offset - alpha * delta)
            active = mask | (abs(offset) >= 12)
            intermediates.append(float(error[active].mean() / amplitude))
    if not intermediates:
        return None
    return max(intermediates) < .12


def visible_delta(current, previous, crop, thin=False):
    import numpy as np
    return changed_area(np.abs(current[crop]-previous[crop]), thin)


def changed_area(delta, thin):
    import numpy as np
    if np.mean(delta >= 9) >= .0025:
        return True
    if not thin:
        return False
    # A line tip can move several contiguous pixels inside a large map ROI.
    # Isolated compression specks do not form repeated short runs.
    active = delta >= 16
    horizontal = active[:, :-2] & active[:, 1:-1] & active[:, 2:]
    vertical = active[:-2] & active[1:-1] & active[2:]
    return horizontal.sum() >= 5 or vertical.sum() >= 5


def measure_samples(samples, probes, fps):
    """A single decoded sample can contribute to multiple spatial or temporal windows."""
    import numpy as np
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError('Expected a positive source frame rate')
    states = []
    for p in sorted(probes, key=lambda part: part['start']):
        states.append(dict(probe=p, crop=bounds(p['roi']), marks=[], compared=0, shared=0, ref_changes=0,
                           reference=bounds(p['reference_roi']) if 'reference_roi' in p else None,
                           appearances=[], bucket=-1))
    active = []
    next_region = 0
    previous = None
    for i, image in enumerate(samples):
        frame = i * fps / SAMPLE_FPS
        active = [s for s in active if frame < s['probe']['end']]
        while next_region < len(states) and states[next_region]['probe']['start'] <= frame:
            state = states[next_region]
            next_region += 1
            if frame < state['probe']['end']:
                active.append(state)
        arr = np.asarray(image, dtype=np.int16)
        for s in active:
            p = s['probe']
            crop = s['crop']
            if p.get('mode') in {'trace', 'readout'}:
                # Keep at most 64 small states even for a long take.
                bucket = int((frame-p['start']) * 64 / (p['end']-p['start']))
                area = arr[crop]
                sample = area[::max(1, math.ceil(area.shape[0]/64)), ::max(1, math.ceil(area.shape[1]/96))]
                if bucket != s['bucket']:
                    s['appearances'].append(sample.astype(np.uint8))
                    s['bucket'] = bucket
            if previous is None or (i-1)*fps/SAMPLE_FPS < p['start']:
                continue
            s['compared'] += 1
            changed = visible_delta(arr, previous, crop, p.get('mode') in {'trace', 'readout'})
            ref = s['reference']
            reference_changed = ref is not None and visible_delta(arr, previous, ref)
            s['ref_changes'] += int(reference_changed)
            if changed:
                s['marks'].append(round((frame-p['start'])/fps, 3))
                s['shared'] += int(reference_changed)
        previous = arr
    result = []
    for s in states:
        p = s['probe'].copy()
        marks, n = s['marks'], s['compared']
        duration = (p['end']-p['start'])/fps
        times = [0, *marks, duration]
        p.update(duration_seconds=round(duration, 3), sampled_pairs=n, changed_pairs=len(marks),
                 active_share=round(len(marks)/n, 3) if n else None,
                 longest_quiet_seconds=round(max(y-x for x, y in zip(times, times[1:])), 3),
                 first_change=marks[0] if marks else None, last_change=marks[-1] if marks else None)
        p['quiet_spans'] = [dict(start_frame=p['start'] + round(lo*fps),
                                 end_frame=min(p['end'], p['start'] + round(hi*fps)))
                            for lo, hi in zip(times, times[1:]) if hi-lo >= 2.5]
        p['review_hold'] = duration >= 4 and (len(marks) < 4 or p['longest_quiet_seconds'] >= max(3., duration*.72))
        if 'mode' in p:
            hints = []
            if n == 0:
                hints.append('insufficient_samples')
            if p['mode'] == 'footage' and p.get('source_verification', {}).get('status') != 'verified':
                hints.append('source_not_verified')
            if p['mode'] in {'trace', 'readout'}:
                blend = resembles_blend(s['appearances'])
                p['resembles_endpoint_blend'] = blend
                if blend:
                    hints.append('looks_like_dissolve')
            if n and not marks and p['mode'] != 'still' and not p.get('resembles_endpoint_blend'):
                hints.append('no_detected_pair_change')
            if s['reference'] is not None:
                p['reference_changed_pairs'] = s['ref_changes']
                p['reference_changed_with_subject_pairs'] = s['shared']
                if len(marks) >= 2 and s['shared']/len(marks) >= .65:
                    hints.append('surroundings_move_with_subject')
            p['review_hints'] = hints
            # Pixel statistics locate a region for review; they do not verify its meaning or provenance.
            p['review_hold'] = p['mode'] != 'still' and (p['review_hold'] or bool(hints))
        result.append(p)
    return result


def inspect(media, probes, fps):
    rows = measure_samples(decode(media), probes, fps) if probes else []
    # Preserve the historical 8 Hz scores. Spoken-hold review needs exact interval boundaries.
    eligible = [p for p in rows if p.get('authored') and p['end'] - p['start'] >= 2.5 * fps]
    if eligible:
        measure_native_holds(media, eligible, fps)
    return rows


def measure_native_holds(media, rows, fps):
    """Stream every encoded frame in the union of authored subject windows."""
    import numpy as np
    starts = {}
    windows = []
    for row in sorted(rows, key=lambda p: p['start']):
        a, b = row['start'], row['end']
        row['quiet_spans'] = []
        state = dict(row=row, crop=bounds(row['roi']), thin=row.get('mode') in {'trace', 'readout'})
        starts.setdefault(a, []).append(state)
        if windows and a <= windows[-1][1]:
            windows[-1][1] = max(windows[-1][1], b)
        else:
            windows.append([a, b])
    selection = '+'.join(f'between(n,{a},{b-1})' for a, b in windows)
    minimum = math.ceil(2.5 * fps)

    def end_hold(state, stop):
        if stop - state['first'] >= minimum:
            state['row']['quiet_spans'].append(dict(start_frame=state['first'], end_frame=stop))

    selected_frames = sum(b - a for a, b in windows)
    with closing(decode_proxy(media, f"select='{selection}'", selected_frames)) as images:
        for a, b in windows:
            active = []
            previous = None
            for n in range(a, b):
                raw = next(images, None)
                if raw is None:
                    raise ValueError(f'Encoded picture ended before motion ROI frame {n}')
                image = raw.astype(np.int16)
                active.extend(starts.get(n, ()))
                for state in active:
                    row, crop = state['row'], state['crop']
                    area = image[crop]
                    if n == row['start']:
                        state.update(first=n, anchor=area.copy())
                    elif (visible_delta(image, previous, crop, state['thin'])
                          or changed_area(np.abs(area - state['anchor']), state['thin'])):
                        # A brief action and its return both split the run. A settled anchor also
                        # catches gradual movement whose individual steps are below the noise floor.
                        end_hold(state, n)
                        state.update(first=n, anchor=area.copy())
                    if n + 1 == row['end']:
                        end_hold(state, n + 1)
                        del state['anchor']
                active = [state for state in active if state['row']['end'] > n + 1]
                previous = image
        if next(images, None) is not None:
            raise ValueError('Unexpected extra frames in motion ROI decode')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('media', type=Path)
    parser.add_argument('plan', type=Path)
    parser.add_argument('timeline', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    tl = json.loads(args.timeline.read_text())
    probes = probes_from_plan(json.loads(args.plan.read_text()), tl['frames'])
    if any(p.get('mode') == 'footage' for p in probes):
        from footage_audit import freeze_footage_sources
        probes = freeze_footage_sources(probes, args.timeline.parent)
    args.output.write_text(json.dumps(inspect(args.media, probes, tl['fps']), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
