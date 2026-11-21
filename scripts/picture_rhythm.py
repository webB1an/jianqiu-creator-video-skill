"""Measure visible changes and reading holds in the encoded picture at its native frame cadence."""
import json
import subprocess
from pathlib import Path

import numpy as np

from picture_holds import select_picture_holds


WIDTH = 160
HEIGHT = 90
# Ignore the brand and floating-caption zones. Keep optical motion tied to the picture itself.
BODY = slice(14, 75)
MIN_PIXEL_DELTA = 10
MIN_AREA_FRACTION = .005
MIN_REPORTED_SECONDS = 1.0
FLAT_RGB_STD = 2.0


def difference(a, b):
    return float(np.mean(np.abs(a - b) >= MIN_PIXEL_DELTA))


def inspect_rhythm(master, timeline, bridges, subs, *, components=(), windows=()):
    """Measure motion on luma and find near-uniform colour fields in the picture body."""
    fps = timeline['fps']
    frame_count = timeline['frames']
    shots = timeline['shots']
    if not shots or frame_count < 1:
        raise ValueError('Picture rhythm requires a nonempty timeline')
    by_shot = {b['shot']: b for b in bridges or []}
    minimum = max(2, round(fps * MIN_REPORTED_SECONDS))
    result = []
    seams = []
    flat_spans = []
    previous = None
    read_size = 4 * WIDTH * HEIGHT
    scale = f'scale={WIDTH}:{HEIGHT}:flags=area'
    # Preserve the original full-range gray sampler byte-for-byte for historical motion scores.
    # GBR occupies the first three planes. Carry the separate luma proxy as the fourth.
    graph = (f'[0:v:0]split=2[cl][lm];[cl]{scale},format=gbrp[rgb];[lm]{scale},format=gray[gray];'
             '[rgb][gray]mergeplanes=format=gbrap:map1p=1:map2p=2:map3s=1[proxy]')
    cmd = ['ffmpeg', '-nostdin', '-hide_banner', '-v', 'error', '-i', str(master),
           '-filter_complex_threads', '2', '-filter_complex', graph, '-map', '[proxy]', '-an', '-sn',
           '-fps_mode', 'passthrough', '-threads', '1', '-f', 'rawvideo', 'pipe:1']
    def keep_hold(start, stop, holds):
        if stop - start >= minimum:
            holds.append((start, stop))

    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as process:
        try:
            frames_seen = 0
            for shot in shots:
                start = shot['start_frame']
                end = start + shot['frames']
                if start != frames_seen or end <= start:
                    raise ValueError('Picture rhythm requires contiguous, nonempty shot windows')
                run_start = start
                anchor = None
                holds = []
                flat_start = None
                motion_frames = 0
                for n in range(start, end):
                    raw = process.stdout.read(read_size)
                    if len(raw) != read_size:
                        raise ValueError(f'Encoded picture ended before timeline frame {n}')
                    planes = np.frombuffer(raw, dtype=np.uint8).reshape(4, HEIGHT, WIDTH)[:, BODY, :]
                    current = planes[3].astype(np.int16)
                    # A missing graphic may leave a white or coloured stage behind a perfectly valid subtitle.
                    flat = np.max(planes[:3].std(axis=(1, 2))) < FLAT_RGB_STD
                    if flat and flat_start is None:
                        flat_start = n
                    if flat_start is not None and (not flat or n == end-1):
                        stop = n+1 if flat else n
                        if stop-flat_start >= max(2, round(.2*fps)) or flat_start == 0 or stop == frame_count:
                            flat_spans.append(dict(shot=shot['id'], start_frame=flat_start, end_frame=stop))
                        flat_start = None
                    if n == start:
                        anchor = current
                        if previous is not None:
                            bridge = by_shot.get(shot['id'])
                            seams.append(dict(frame=start, incoming=shot['id'],
                                              transition=bridge['preset'] if bridge else ('cut' if bridges is not None else None),
                                              changed_area=round(difference(previous, current), 4)))
                    else:
                        # Compare both neighboring frames and the last settled reference. Slow pans
                        # can be subthreshold in one frame while covering visible distance over time.
                        per_frame = difference(previous, current)
                        if per_frame >= MIN_AREA_FRACTION or difference(anchor, current) >= MIN_AREA_FRACTION:
                            keep_hold(run_start, n, holds)
                            run_start = n
                            anchor = current
                        if per_frame >= MIN_AREA_FRACTION:
                            motion_frames += 1
                    previous = current
                keep_hold(run_start, end, holds)
                quiet_spans = [dict(start_frame=a, end_frame=b) for a, b in holds]
                if not holds and end-start < minimum and end-start >= .6*fps and run_start == start:
                    quiet_spans.append(dict(start_frame=start, end_frame=end))
                longest = sorted(holds, key=lambda span: span[1]-span[0], reverse=True)[:3]
                samples = [dict(start_frame=a, end_frame=b, seconds=round((b-a)/fps, 3),
                                captions=[text for lo,hi,text in subs if lo < b/fps and a/fps < hi])
                           for a,b in sorted(longest)]
                result.append(dict(shot=shot['id'], start_frame=start, end_frame=end,
                                   changing_frame_fraction=round(motion_frames/max(1, end-start-1), 4),
                                   quiet_seconds=round(sum(b-a for a,b in holds)/fps, 3),
                                   quiet_spans=quiet_spans, longest_quiet_spans=samples))
                frames_seen = end
            if frames_seen != frame_count or process.stdout.read(1):
                raise ValueError('Encoded picture frame count differs from the rhythm timeline')
            if process.wait():
                raise RuntimeError('Could not decode the entire encoded picture')
        finally:
            if process.poll() is None:
                process.terminate()

    return dict(method='luma_change', spoken_hold_version=2, proxy_size=[WIDTH, HEIGHT],
                proxy_rows=[BODY.start, BODY.stop], pixel_delta=MIN_PIXEL_DELTA,
                minimum_changed_fraction=MIN_AREA_FRACTION, minimum_quiet_seconds=minimum/fps,
                shots=result, seams=seams, flat_picture_candidates=flat_spans,
                flat_picture_max_rgb_std_below=FLAT_RGB_STD,
                hold_candidates=select_picture_holds(result, fps, subs, takes=timeline.get('voice_takes'),
                                                   components=components, windows=windows))


if __name__ == '__main__':
    import argparse
    from review import captions as read_captions

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('media', type=Path)
    parser.add_argument('--timeline', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    folder = args.media.parent
    timeline_path = args.timeline or folder/'timeline.json'
    tl = json.loads(timeline_path.read_text())
    tech = folder/'technical.json'
    transitions = json.loads(tech.read_text()).get('transitions') if tech.is_file() else None
    captions = list(read_captions(folder)) if (folder/'captions.srt').is_file() else []
    from review_bundle import digest, receipt_for
    receipt = receipt_for(args.media)
    manifest = json.loads(receipt.read_text()) if receipt.is_file() else {}
    windows = manifest.get('viewing_windows', [])
    probes = manifest.get('component_motion_windows', [])
    components = []
    if windows or probes:
        if manifest.get('sha256') != digest(args.media) or manifest.get('timeline_sha256') != digest(timeline_path):
            raise ValueError('Use the timeline and picture corresponding to these frozen observation regions')
        from component_motion import inspect
        components = inspect(args.media, probes, tl['fps'])
    value = json.dumps(inspect_rhythm(args.media, tl, transitions, captions, components=components,
                                      windows=windows), ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(value)
    else:
        print(value)
