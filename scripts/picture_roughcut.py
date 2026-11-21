"""Render source-footage assemblies for comparing shot order or cut timing."""
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile

from production_run import digest


STILLS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}
SCALE = ("scale=w='max(2,trunc(min(960,540*dar)/2)*2)':"
         "h='max(2,trunc(min(540,960/dar)/2)*2)',setsar=1,pad=960:540:(ow-iw)/2:(oh-ih)/2")


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise ValueError(result.stderr.strip() or f'{args[0]} failed')
    return result.stdout


def probe(path, entries, *, count=False, stream='v:0'):
    options = ['-count_frames'] if count else []
    info = json.loads(command('ffprobe', '-v', 'error', '-select_streams', stream, *options,
                              '-show_entries', 'stream=' + entries, '-of', 'json', str(path)))
    if not info.get('streams'):
        raise ValueError(f'No {stream} stream in {path}')
    return info['streams'][0]


def make_segment(path, cut, fps, output):
    frames = cut['end'] - cut['start']
    start = cut.get('in', 0)
    if type(start) not in (float, int) or not math.isfinite(start) or start < 0:
        raise ValueError(f"{cut['id']}: source in must be finite, nonnegative seconds")
    still = path.suffix.lower() in STILLS
    framing = SCALE
    stream = 'v:0'
    geometry = None
    if 'crop' in cut:
        from source_framing import crop_filter
        streams = json.loads(command('ffprobe', '-v', 'error', '-select_streams', 'v', '-show_entries',
                                     'stream=index,width,height,sample_aspect_ratio,duration_ts,time_base:'
                                     'stream_disposition=attached_pic:stream_side_data=rotation',
                                     '-of', 'json', str(path)))['streams']
        geometry = next((s for s in streams if not s['disposition']['attached_pic']), None)
        if geometry is None:
            raise ValueError(f'{path.name}: crop requires a picture stream other than attached cover art')
        stream = str(geometry['index'])
        framing = crop_filter(cut['crop'], geometry, (960, 540))
    if still:
        if start:
            raise ValueError(f"{cut['id']}: a held still has no source in-point")
        inputs = ['-loop', '1', '-framerate', str(fps), '-i', str(path)]
        filters = framing
        window = {'kind': 'still', 'hold_frames': frames}
    else:
        info = geometry or probe(path, 'duration_ts,time_base', stream=stream)
        end = Fraction(str(start)) + Fraction(frames, fps)
        if 'duration_ts' in info and end - Fraction(info['time_base']) * int(info['duration_ts']) > Fraction(1, 10**9):
            raise ValueError(f"{cut['id']}: requested window ends past the source picture")
        # Keep preceding frames so an in-point between two source timestamps retains
        # the picture visible at that time. 'up' places each change on its next proxy frame.
        seek = max(0, start - 1)
        inputs = ['-ss', str(seek), '-i', str(path)]
        filters = f'setpts=PTS-{start - seek:.9f}/TB,fps={fps}:round=up:start_time=0,{framing}'
        window = {'kind': 'video', 'in': start, 'out': float(end)}
    command('ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', *inputs, '-map', '0:'+stream,
            '-an', '-vf', filters, '-frames:v', str(frames), '-pix_fmt', 'yuv420p',
            '-c:v', 'ffv1', str(output))
    if int(probe(output, 'nb_read_frames', count=True).get('nb_read_frames', 0)) != frames:
        raise ValueError(f"{cut['id']}: source supplied fewer than {frames} frames at original speed")
    return window


def roll_cut(cuts, fps, incoming_id, shift):
    """Move one boundary. Every visible source keeps its place on the audio clock."""
    ids = [cut['id'] for cut in cuts]
    if incoming_id not in ids[1:] or type(shift) is not int or shift == 0:
        raise ValueError('Choose an incoming cut after the first shot and a nonzero integer frame shift')
    index = ids.index(incoming_id)
    left, right = cuts[index-1:index+1]
    boundary = right['start'] + shift
    if not left['start'] < boundary < right['end']:
        raise ValueError(f'Both sides of the cut at {incoming_id} must retain at least one frame')
    incoming = dict(right, start=boundary)
    if Path(right['visual']).suffix.lower() not in STILLS:
        original_in = right.get('in', 0)
        if type(original_in) not in (int, float) or not math.isfinite(original_in) or original_in < 0:
            raise ValueError(f'{incoming_id}: provide a finite, nonnegative source in-point')
        offset = Fraction(str(original_in)) + Fraction(shift, fps)
        if offset < -Fraction(1, 10**9):
            raise ValueError(f'{incoming_id}: the earlier cut needs more source footage before its in-point')
        incoming['in'] = float(max(0, offset))
    outgoing = dict(left, end=boundary)
    for original, changed in ((left, outgoing), (right, incoming)):
        if 'watch' in original:
            a, b = original['watch']
            first = max(changed['start'], original['start'] + a)
            last = min(changed['end'], original['start'] + b)
            if first < last:
                changed['watch'] = [first - changed['start'], last - changed['start']]
            else:
                del changed['watch']
    return [*cuts[:index-1], outgoing, incoming, *cuts[index+1:]]


def source_segments(plan_path, versions, fps, work, hashes):
    # Both sides of a rolling edit use nested intervals from each source. Decode the
    # entire shared interval once so VFR/fps conversion cannot change phase between A/B.
    longest = {}
    for ordered in versions.values():
        for cut in ordered:
            key = cut['id']
            previous = longest.get(key)
            if previous is None or cut['end']-cut['start'] > previous['end']-previous['start']:
                longest[key] = cut
    sources = {}
    for index, (key, cut) in enumerate(longest.items()):
        visual = cut.get('visual')
        if not isinstance(visual, str) or not visual.strip():
            raise ValueError(f'{key}: provide a local visual path relative to the plan')
        path = (plan_path.parent/visual).resolve(strict=True)
        if path not in hashes:
            hashes[path] = digest(path)
        segment = work/f'{index}.mkv'
        window = make_segment(path, cut, fps, segment)
        sources[key] = (segment, cut['start'], cut['end'], window, hashes[path])
    return sources


def cut_segment(source, cut, fps):
    segment, union_start, union_end, window, sha = source
    start, end = cut['start'], cut['end']
    if start != union_start or end != union_end:
        trimmed = segment.with_name(f'{segment.stem}-{start}-{end}.mkv')
        command('ffmpeg', '-v', 'error', '-nostdin', '-i', str(segment), '-an',
                '-vf', f'trim=start_frame={start-union_start}:end_frame={end-union_start},setpts=PTS-STARTPTS',
                '-fps_mode', 'passthrough', '-c:v', 'ffv1', str(trimmed))
        if int(probe(trimmed, 'nb_read_frames', count=True)['nb_read_frames']) != end-start:
            raise ValueError(f"{cut['id']}: trim lost source frames")
        segment = trimmed
        if window['kind'] == 'still':
            window = dict(kind='still', hold_frames=end-start)
        else:
            source_in = cut.get('in', 0)
            window = {'kind':'video', 'in':source_in, 'out':float(Fraction(str(source_in))+Fraction(end-start, fps))}
    return segment, dict(id=cut['id'], visual=cut['visual'], sha256=sha, **window)


def audio_signature(path):
    return command('ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:a:0', '-c:a', 'copy',
                   '-f', 'hash', '-hash', 'sha256', '-').strip().split('=')[-1]


def prepare_soundtrack(source, target, total, fps):
    """Encode an aligned guide once, preserving its gain and an exact sample clock."""
    rate = 48000
    samples = round(Fraction(total * rate, fps))
    track = probe(source, 'duration_ts,time_base', stream='a:0')
    length = Fraction(track['time_base']) * int(track['duration_ts'])
    if round(length * rate) < samples:
        raise ValueError('The aligned rough-cut soundtrack ends before the end of the plan')
    command('ffmpeg', '-nostdin', '-v', 'error', '-i', str(source), '-map', '0:a:0',
            '-af', f'aresample={rate},atrim=end_sample={samples},asettb=1/{rate},asetpts=N',
            '-c:a', 'aac', '-b:a', '192k', '-movie_timescale', str(rate), str(target))
    verify_audio_length(target, samples, rate)
    return dict(sample_rate=rate, samples=samples, aac_sha256=audio_signature(target))


def verify_audio_length(path, samples, rate):
    info = probe(path, 'time_base,duration_ts,start_pts', stream='a:0')
    if int(info['start_pts']) != 0 or Fraction(info['time_base']) * int(info['duration_ts']) != Fraction(samples, rate):
        raise ValueError(f'{path.name}: soundtrack no longer matches the planned sample window')


def selected_decisions(project, shot_ids):
    from project_learning import frozen_learning
    log = frozen_learning(project) or {}
    selected = {k: row for k, row in log.get('decisions', {}).items()
                if row['action'] in ('use', 'adapt') and row['shot'] in shot_ids}
    if not selected:
        raise ValueError('Record a use or adapt decision for a cut id in this plan first')
    return selected


def learning_tag(receipt, name):
    # Bind the source windows, both orders and the pre-view decisions to each actual MP4.
    # The two final file digests can only be added after encoding.
    cuts = {version: row['cuts'] for version, row in receipt['versions'].items()}
    value = [name, receipt['plan_sha256'], receipt['fps'], cuts, receipt['decisions']]
    if 'soundtrack' in receipt:
        value.append(receipt['soundtrack'])
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()


def reviewed_pair(media):
    receipt_path = media.parent / 'receipt.json'
    original = receipt_path.read_bytes()
    receipt = json.loads(original)
    if (media.name != 'first.mp4' or not receipt.get('decisions')
            or set(receipt['versions']) != {'first', 'alternate'}):
        raise ValueError('Select first.mp4 from a two-order rough cut rendered with --project')
    for name, row in receipt['versions'].items():
        path = media.parent / (name + '.mp4')
        if row['file'] != path.name or digest(path) != row['sha256']:
            raise ValueError('A rough-cut MP4 differs from its recorded version')
        tag = command('ffprobe', '-v', 'error', '-show_entries', 'format_tags=comment',
                      '-of', 'default=noprint_wrappers=1:nokey=1', str(path)).strip()
        if tag != learning_tag(receipt, name):
            raise ValueError('The rough-cut source windows or learning decisions differ from the viewed media')
    return receipt, hashlib.sha256(original).hexdigest()


def render(plan_path, destination, alternate, validate, project=None, *, audio=None, shift_cut=None):
    if destination.exists() or destination.is_symlink():
        raise ValueError(f'Choose a new rough-cut directory: {destination}')
    original = plan_path.read_bytes()
    plan = json.loads(original)
    cuts, fps = plan['cuts'], plan['fps']
    if not cuts or type(fps) is not int or not 1 <= fps <= 120:
        raise ValueError('Rough cut needs at least one cut and a frame rate from 1 to 120')
    total = cuts[-1]['end']
    validate({**plan, 'overlays': plan.get('overlays', [])}, {'frames': total})
    ids = [cut.get('id') for cut in cuts]
    if any(not isinstance(key, str) or not key.strip() for key in ids) or len(set(ids)) != len(ids):
        raise ValueError('Give each rough-cut shot a unique nonempty string id')
    by_id = {cut['id']: cut for cut in cuts}
    versions = {'first': cuts}
    if shift_cut is not None:
        if audio is None or alternate is not None:
            raise ValueError('Compare a cut shift with one aligned --audio track and the original shot order')
        versions['alternate'] = roll_cut(cuts, fps, *shift_cut)
    if alternate is not None:
        if sorted(alternate) != sorted(ids) or alternate == ids:
            raise ValueError('The alternate must reorder exactly the same cut ids once each')
        versions['alternate'] = [by_id[key] for key in alternate]
    if project is not None and alternate is None:
        raise ValueError('Provide an alternate ordering for a learning trial')
    decisions = selected_decisions(project, ids) if project is not None else None

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix='.roughcut-') as temp:
        work = Path(temp)
        bundle = work / 'bundle'
        bundle.mkdir()
        hashes, playlists = {}, {}
        receipt = dict(fps=fps, frames=total, size=[960, 540], audio=audio is not None,
                       plan_sha256=hashlib.sha256(original).hexdigest(), versions={})
        sound = work/'sound.m4a'
        if audio is not None:
            source = (plan_path.parent / audio).resolve(strict=True)
            if source.suffix.lower() not in ('.wav', '.wave'):
                raise ValueError('Provide an aligned WAV as the rough-cut soundtrack')
            hashes[source] = digest(source)
            receipt['soundtrack'] = dict(file=str(audio), sha256=hashes[source],
                                         **prepare_soundtrack(source, sound, total, fps))
        if shift_cut is not None:
            key, shift = shift_cut
            boundary = by_id[key]['start']
            receipt['cut_shift'] = dict(incoming=key, frames=shift, first=boundary, alternate=boundary+shift)
        if decisions is not None:
            receipt['decisions'] = decisions
        sources = source_segments(plan_path, versions, fps, work, hashes)
        for name, ordered in versions.items():
            cursor, edit = 0, []
            playlists[name] = []
            for cut in ordered:
                frames = cut['end'] - cut['start']
                segment, window = cut_segment(sources[cut['id']], cut, fps)
                playlists[name].append(f"file '{segment.name}'\n")
                end = cursor + frames
                edit.append(dict(**window,
                                 **{k: cut[k] for k in ('subject','action','discovery','eye_trace','watch','crop') if k in cut},
                                 frames=frames, start=cursor, end=end))
                cursor = end
            receipt['versions'][name] = dict(file=name + '.mp4', cuts=edit)
        for name in versions:
            playlist = work / 'inputs.txt'
            playlist.write_text(''.join(playlists[name]))
            output = bundle / (name + '.mp4')
            tags = ['-metadata', 'comment=' + learning_tag(receipt, name)] if decisions is not None else []
            tracks = (['-i', str(sound), '-map', '0:v:0', '-map', '1:a:0', '-c:a', 'copy', '-movie_timescale', '48000']
                      if audio is not None else ['-an'])
            # Ignore container-level millisecond rounding at joins. Re-clock by frame number.
            command('ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-f', 'concat',
                    '-safe', '1', '-i', str(playlist), *tracks, '-vf', f'settb=1/{fps},setpts=N',
                    '-r', str(fps), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18',
                    '-pix_fmt', 'yuv420p', '-movflags', '+faststart', *tags, str(output))
            if int(probe(output, 'nb_read_frames', count=True)['nb_read_frames']) != total:
                raise ValueError(f'{name}: encoded proxy has a different frame count')
            if audio is not None:
                guide = receipt['soundtrack']
                verify_audio_length(output, guide['samples'], guide['sample_rate'])
                if audio_signature(output) != guide['aac_sha256']:
                    raise ValueError(f'{name}: the shared soundtrack changed during muxing')
            receipt['versions'][name]['sha256'] = digest(output)
        if plan_path.read_bytes() != original or any(digest(p) != sha for p, sha in hashes.items()):
            raise ValueError('Media source or plan changed during rough-cut rendering')
        if project is not None and selected_decisions(project, ids) != decisions:
            raise ValueError('Selected learning decisions changed during rough-cut rendering')
        (bundle / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
        bundle.rename(destination)
    return dict(receipt=str(destination / 'receipt.json'), frames=total, versions=list(versions))
