"""Verify declared video sources and measure third-party screen time from rendered frame ranges."""
import argparse
import json
import subprocess
from fractions import Fraction
from pathlib import Path
from urllib.parse import urlsplit
from production_run import digest


def read_probe(path, entries, *extra):
    command = ['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
               '-select_streams', 'V:0', *extra, '-show_entries', entries,
               '-of', 'json', str(path)]
    proc = subprocess.run(command, capture_output=True, text=True)
    if proc.returncode or proc.stderr.strip():
        raise ValueError(f'Cannot decode footage source {path.name}: {proc.stderr.strip()[:360]}')
    return json.loads(proc.stdout)


def decoded_window(path, stream, origin, start, stop):
    """Confirm actual presentation coverage on the source playhead, independently of any audio."""
    time_base = Fraction(stream['time_base'])
    first, end = origin + start, origin + stop
    interval = f'{max(origin, first-1):.9f}%{end+1:.9f}'
    rows = read_probe(path, 'frame=best_effort_timestamp,duration,pkt_duration',
                      '-read_intervals', interval, '-show_frames').get('frames', [])
    timings = []
    for row in rows:
        if row.get('best_effort_timestamp') in (None, 'N/A'):
            continue
        duration = next((row.get(k) for k in ('duration', 'pkt_duration') if row.get(k) not in (None, 'N/A')), 0)
        timings.append((int(row['best_effort_timestamp']), int(duration)))
    timings.sort()
    cursor = first
    ticks = set()
    visible_start = visible_end = None
    for i, (pts, duration) in enumerate(timings):
        next_pts = timings[i+1][0] if i+1 < len(timings) else None
        if duration <= 0:
            if next_pts is None:
                continue
            duration = next_pts - pts
        elif time_base <= Fraction(1, 1000) and next_pts == pts + duration + 1:
            # A millisecond playhead can round 1001/30000-second frames to alternating 33 and 34 ticks.
            duration += 1
        a, b = float(pts*time_base), float((pts+duration)*time_base)
        if b <= first+1e-7 or a >= end-1e-7:
            continue
        if a > cursor+1e-6:
            break
        ticks.add(pts)
        if visible_start is None:
            visible_start = a
        visible_end = max(b, visible_end if visible_end is not None else b)
        cursor = max(cursor, b)
    if len(ticks) < 2 or cursor < end-1e-6:
        raise ValueError(f'{path.name} does not cover [{start:g}, {stop:g}) with multiple decoded video frames')
    return dict(decoded_frames_in_window=len(ticks), first_display_seconds=round(visible_start-origin, 9),
                last_display_end_seconds=round(visible_end-origin, 9))


def freeze_footage_sources(probes, project, *, reuse_frozen=False):
    """Freeze source eligibility for declared video regions. Original pictures still require visual comparison."""
    frozen = []
    sources = {}
    windows = {}
    for original in probes:
        part = original.copy()
        frozen.append(part)
        if part.get('mode') != 'footage':
            continue
        previous = part.get('source_verification', {})
        if (reuse_frozen and previous.get('status') == 'verified' and
                previous.get('sha256') and previous.get('decoded_frames_in_window', 0) >= 2):
            continue
        source = part['source']
        declared = source['file']
        if urlsplit(declared).scheme:
            part['source_verification'] = dict(status='pending', reason='external_source')
            continue
        path = Path(declared).expanduser()
        if not path.is_absolute():
            if project is None:
                part['source_verification'] = dict(status='pending', reason='project_source_unavailable')
                continue
            path = Path(project)/path
        if not path.is_file():
            part['source_verification'] = dict(status='pending', reason='local_file_unavailable')
            continue
        if path.suffix.lower() in {'.m3u8', '.mpd', '.ffconcat'}:
            part['source_verification'] = dict(status='pending', reason='external_media_segments')
            continue
        actual = path.resolve()
        try:
            if actual not in sources:
                before = actual.stat()
                metadata = read_probe(actual, 'stream=index,codec_name,width,height,start_time,duration,time_base:format=format_name,start_time')
                streams = metadata.get('streams', [])
                if not streams:
                    raise ValueError(f'No video stream in {declared}')
                fmt, video = metadata['format'], streams[0]
                if set(fmt['format_name'].split(',')) & {'hls', 'dash', 'concat', 'image2'}:
                    raise ValueError(f'{declared} requires a self-contained moving source')
                origin = float(next((d['start_time'] for d in (fmt, video)
                                     if d.get('start_time') not in (None, 'N/A')), 0))
                info = dict(status='verified', sha256=digest(actual), byte_size=before.st_size,
                            container=fmt['format_name'], stream_index=video['index'], codec=video['codec_name'],
                            width=video['width'], height=video['height'], time_base=video['time_base'],
                            playhead_origin_seconds=origin)
                if video.get('duration') not in (None, 'N/A'):
                    info['video_duration_seconds'] = float(video['duration'])
                sources[actual] = (before, video, origin, info)
            before, video, origin, info = sources[actual]
            key = (actual, source['in'], source['out'])
            if key not in windows:
                windows[key] = decoded_window(actual, video, origin, source['in'], source['out'])
            after = path.stat()
            if any(getattr(before, k) != getattr(after, k) for k in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')):
                raise ValueError('Source changed while its hash and decoded frames were being frozen')
            part['source_verification'] = {**info, **windows[key]}
        except (OSError, ValueError) as exc:
            raise ValueError(f'Footage region {part.get("region") or part.get("id")} ({declared}): {exc}') from exc
    return frozen


def coverage(windows):
    count = 0
    right = 0
    for start, end in sorted(windows):
        count += max(0, end - max(right, start))
        right = max(right, end)
    return count


def measure(timeline, usage):
    fps = timeline['fps']
    shots = {shot['id']: shot for shot in timeline['shots']}
    kinds = {'video': [], 'photo': []}
    by_source = {}
    for entry in usage:
        shot = shots[entry['shot']]
        start, end = entry['start_frame'], entry['end_frame']
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= shot['frames']:
            raise ValueError(f"Range outside shot {shot['id']}: {start}, {end}")
        kind = entry['kind']
        if kind not in kinds or not entry['source'].strip() or not entry['license'].strip():
            raise ValueError(f"Missing source, license or supported kind: {entry}")
        url = urlsplit(entry['source_url'])
        if url.scheme not in ('https', 'http') or not url.netloc:
            raise ValueError(f"Missing public source URL: {entry['source']}")
        window = (shot['start_frame'] + start, shot['start_frame'] + end)
        kinds[kind].append(window)
        by_source.setdefault(entry['source'], []).append(window)
    total = timeline['frames']
    result = {}
    for name, windows in {**kinds, 'either': kinds['video'] + kinds['photo']}.items():
        frames = coverage(windows)
        result[name] = {'frames':frames, 'seconds':round(frames / fps, 3), 'percent':round(frames * 100 / total, 1)}
    return {'fps':fps, 'total_frames':total, 'coverage':result,
            'source_seconds':{name: round(coverage(windows) / fps, 3) for name, windows in by_source.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeline', type=Path, default=Path('timeline.json'))
    parser.add_argument('--usage', type=Path, default=Path('footage-usage.json'))
    parser.add_argument('--out', type=Path, default=Path('qa/footage.json'))
    args = parser.parse_args()
    result = measure(json.loads(args.timeline.read_text()), json.loads(args.usage.read_text()))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result['coverage'], ensure_ascii=False))


if __name__ == '__main__':
    main()
