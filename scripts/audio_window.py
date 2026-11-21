"""Replace a frame-addressed final mix with native AAC packets and copy the H.264 picture."""
import argparse
import hashlib
import json
import math
import os
from fractions import Fraction
from pathlib import Path
import subprocess
import tempfile

import numpy as np

BLOCK = 1024
RATE = 48000


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def ff(*args):
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', *map(str, args)], check=True)


def probe(path, *args):
    data = subprocess.check_output(['ffprobe', '-v', 'error', *args, '-of', 'json', str(path)])
    return json.loads(data)


def packets(path, stream):
    return probe(path, '-select_streams', stream, '-show_packets', '-show_data_hash', 'sha256',
                 '-show_entries', 'packet=pts,dts,duration,flags,data_hash:packet_side_data=side_data_type,skip_samples,discard_padding')['packets']


def decode(path, target, channels):
    ff('-i', path, '-map', '0:a:0', '-ac', channels, '-ar', RATE, '-c:a', 'pcm_f32le', '-f', 'f32le', target)
    return np.memmap(target, mode='r', dtype='<f4').reshape(-1, channels)


def adts_frames(path):
    """Read complete frames including their headers, without guessing payload offsets in MP4."""
    with path.open('rb') as handle:
        while True:
            header = handle.read(7)
            if not header:
                return
            require(len(header) == 7 and header[0] == 255 and header[1] & 0xf6 == 0xf0
                    and header[6] & 3 == 0, 'Expected a single-block ADTS frame')
            length = (header[3] & 3) << 11 | header[4] << 3 | header[5] >> 5
            require(length >= 7, 'Invalid ADTS length')
            rest = handle.read(length - 7)
            require(len(rest) == length - 7, 'Truncated ADTS frame')
            yield header + rest


def error_stats(a, b, ranges):
    count = changed = 0
    energy = maximum = 0.
    first = last = None
    for lo, hi in ranges:
        for offset in range(lo, hi, 65536):
            stop = min(hi, offset + 65536)
            delta = np.asarray(a[offset:stop], dtype='float64') - b[offset:stop]
            count += delta.size
            energy += np.square(delta).sum().item()
            maximum = max(maximum, np.abs(delta).max().item())
            indexes = np.flatnonzero(np.any(delta != 0, axis=1))
            if indexes.size:
                changed += indexes.size
                if first is None:
                    first = offset + int(indexes[0])
                last = offset + int(indexes[-1])
    rms = math.sqrt(energy / count) if count else 0
    return {'samples_per_channel': count // a.shape[1], 'changed_sample_frames': int(changed),
            'first_changed_sample': first, 'last_changed_sample': last,
            'peak_error_full_scale': maximum, 'rms_error_full_scale': rms,
            'rms_error_dbfs': round(20 * math.log10(rms), 2) if rms else None}


def replace_audio(args, work, movie):
    media, wav = args.master, args.wav
    base_hash, wav_hash = digest(media), digest(wav)
    if args.expect_base:
        require(args.expect_base == base_hash, 'Base SHA differs from the reviewed candidate')
    base = probe(media, '-show_streams', '-show_data_hash', 'sha256', '-show_format')
    require('mp4' in base['format']['format_name'].split(','), 'Base must be an MP4')
    streams = base['streams']
    require(len(streams) == 2 and {s['codec_type'] for s in streams} == {'video', 'audio'},
            'Expected exactly one video and one audio stream')
    v = next(s for s in streams if s['codec_type'] == 'video')
    a = next(s for s in streams if s['codec_type'] == 'audio')
    fps = Fraction(v['avg_frame_rate'])
    require(v['codec_name'] == 'h264' and fps == Fraction(v['r_frame_rate']) and int(v['start_pts']) == 0,
            'Expected constant-rate H.264 picture starting at zero')
    channels = a['channels']
    require(a['codec_name'] == 'aac' and a['profile'] == 'LC' and a['time_base'] == '1/48000'
            and int(a['sample_rate']) == RATE and int(a['start_pts']) == 0 and channels in (1, 2),
            'Expected AAC-LC at 48 kHz, mono or stereo, starting at zero')
    window = probe(wav, '-show_streams', '-show_format')
    require(window['format']['format_name'] == 'wav' and len(window['streams']) == 1, 'Replacement must be one WAV track')
    track = window['streams'][0]
    require(track['codec_name'].startswith('pcm_') and int(track['sample_rate']) == RATE and track['channels'] == channels,
            'Replacement must be PCM WAV with matching sample rate and channels')
    frames, total = int(v['nb_frames']), int(a['duration_ts'])
    require(0 <= args.start_frame < args.end_frame <= frames, 'Frame range outside the video timeline')
    lo, hi = (round(Fraction(frame * RATE, 1) / fps) for frame in (args.start_frame, args.end_frame))
    require(0 <= lo < hi <= total, 'Ticket goes outside the original audio')
    incoming = decode(wav, work / 'window.pcm', channels)
    require(len(incoming) == hi - lo and np.all(np.isfinite(incoming)), f'Expected exactly {hi - lo} finite PCM sample frames')
    original = decode(media, work / 'base.pcm', channels)
    require(len(original) >= total, 'Audio decode shorter than the declared timeline')
    original = original[:total]
    require(not np.array_equal(incoming, original[lo:hi]), 'Replacement mix is already identical to the ticketed sound')
    original_video = packets(media, 'v:0')
    tick = 1 / (fps * Fraction(v['time_base']))
    picture_pts = sorted(p['pts'] for p in original_video)
    require(tick.denominator == 1 and len(picture_pts) == frames
            and all(pts == i * tick.numerator for i, pts in enumerate(picture_pts)),
            'Video timestamps must follow the actual frame grid')
    original_audio = packets(media, 'a:0')
    first = original_audio[0]['pts']
    last = original_audio[-1]['pts']
    require(first in (-BLOCK, 0) and 0 < total - last <= BLOCK, 'Unsupported AAC priming or end padding')
    if first == -BLOCK:
        require(any(x.get('skip_samples') == BLOCK for x in original_audio[0].get('side_data_list', [])),
                'Expected one AAC encoder-priming frame')
    for i, packet in enumerate(original_audio):
        pts = first + i * BLOCK
        require(packet['pts'] == packet['dts'] == pts and packet['duration'] == min(BLOCK, total - pts),
                'Discontinuous AAC packet timing')

    # Re-encode the affected blocks, with one neighbouring block for the filterbank at each seam.
    packet_lo = max(first, (lo // BLOCK - 1) * BLOCK)
    packet_hi = min(last + BLOCK, ((hi + BLOCK - 1) // BLOCK + 1) * BLOCK)
    # Extra input to the local encoder settles its analysis window; none of these extra packets is inserted.
    context_lo = max(0, packet_lo - 3 * BLOCK)
    context_hi = min(total, packet_hi + 3 * BLOCK)
    local = np.array(original[context_lo:context_hi], dtype='<f4', copy=True)
    local[lo-context_lo:hi-context_lo] = incoming
    outside = [(0, lo-context_lo), (hi-context_lo, context_hi-context_lo)]
    pre_stats = error_stats(original[context_lo:context_hi], local, outside)
    require(pre_stats['changed_sample_frames'] == 0, 'Original guard samples changed before encoding')
    local.tofile(work / 'edit.pcm')
    ff('-f', 'f32le', '-ar', RATE, '-ac', channels, '-i', work / 'edit.pcm', '-c:a', 'aac', '-profile:a', 'aac_low',
       '-b:a', '256k' if channels == 2 else '128k', work / 'local.m4a')
    local_packets = packets(work / 'local.m4a', 'a:0')
    ff('-i', work / 'local.m4a', '-map', '0:a:0', '-c', 'copy', '-f', 'adts', work / 'local.aac')
    ff('-i', media, '-map', '0:a:0', '-c', 'copy', '-f', 'adts', work / 'base.aac')
    replacements = dict(zip((context_lo + p['pts'] for p in local_packets), adts_frames(work / 'local.aac')))
    changed_blocks = 0
    read_count = 0
    with (work / 'spliced.aac').open('wb') as out:
        for i, data in enumerate(adts_frames(work / 'base.aac')):
            pts = original_audio[i]['pts']
            if packet_lo <= pts < packet_hi:
                data = replacements[pts]
                changed_blocks += 1
            out.write(data)
            read_count += 1
    require(read_count == len(original_audio), 'Source ADTS packet count differs from MP4')
    pts = f'N*{BLOCK}+({first})'
    duration = f'if(eq(N\\,{read_count-1})\\,{total-last}\\,{BLOCK})'
    clock = f'aac_adtstoasc,setts=pts={pts}:dts={pts}:duration={duration}:time_base=1/{RATE}'
    ff('-i', media, '-f', 'aac', '-i', work / 'spliced.aac', '-map', '0:v:0', '-map', '1:a:0',
       '-map_metadata', '0', '-map_metadata:s:a:0', '0:s:a:0', '-c', 'copy', '-bsf:a', clock,
       '-video_track_timescale', Fraction(v['time_base']).denominator, '-movflags', '+faststart', movie)

    after = probe(movie, '-show_streams', '-show_data_hash', 'sha256')['streams']
    require(v['extradata_hash'] == after[0]['extradata_hash'], 'H.264 decoder configuration changed')
    for before, now in ((v, after[0]), (a, after[1])):
        for field in ('codec_name', 'time_base', 'start_pts', 'duration_ts', 'nb_frames'):
            require(before[field] == now[field], f'{field} changed on {before["codec_type"]}')
    require(original_video == packets(movie, 'v:0'), 'A video packet payload, flag or timestamp changed')
    after_audio = packets(movie, 'a:0')
    require(len(after_audio) == read_count, 'Audio packet count changed')
    for before, now in zip(original_audio, after_audio):
        if packet_lo <= before['pts'] < packet_hi:
            before = {k: v for k, v in before.items() if k != 'data_hash'}
            now = {k: v for k, v in now.items() if k != 'data_hash'}
        require(before == now, 'A copied AAC packet changed or a timestamp moved')

    actual = decode(movie, work / 'result.pcm', channels)
    require(len(actual) >= total, 'Patched audio ended early')
    actual = actual[:total]
    # Some AAC tools change decoder noise-state choices across a splice. Report the actual decoded difference.
    guard_lo, guard_hi = max(0, packet_lo), min(total, packet_hi + BLOCK)
    exterior = error_stats(original, actual, [(0, lo), (hi, total)])
    seams = error_stats(original, actual, [(guard_lo, lo), (hi, guard_hi)])
    untouched = error_stats(original, actual, [(0, guard_lo), (guard_hi, total)])
    inside = error_stats(incoming, actual[lo:hi], [(0, hi-lo)])
    if args.require_exact_exterior:
        require(untouched['changed_sample_frames'] == 0, 'Decoded AAC noise differs beyond the splice guard; strict verification rejected the candidate')
    require(untouched['rms_error_full_scale'] <= .001 and untouched['peak_error_full_scale'] <= .032,
            'Decoded signal changed materially far from the ticket; keep the previous cut')
    require(digest(media) == base_hash and digest(wav) == wav_hash, 'An input changed during verification')
    return {'base_sha256': base_hash, 'window_wav_sha256': wav_hash, 'candidate_sha256': digest(movie),
            'fps': str(fps), 'video_frames': frames, 'sample_rate': RATE, 'channels': channels,
            'requested_frames': [args.start_frame, args.end_frame], 'frames': frames,
            'frame_range': [args.start_frame, args.end_frame], 'sample_range': [lo, hi],
            'duration_samples': total, 'all_video_packets_and_timestamps_identical': True,
            'copied_video_packets': len(original_video), 'total_aac_packets': read_count,
            'locally_encoded_aac_packets': changed_blocks, 'identical_outside_aac_packets': read_count - changed_blocks,
            'reencoded_packet_pts_range': [packet_lo, packet_hi], 'decoder_guard_sample_range': [guard_lo, guard_hi],
            'encoder_context_untouched_pcm': pre_stats, 'outside_window_decoded_error': exterior,
            'seams_outside_ticket_decoded_error': seams, 'outside_decoder_guard_decoded_error': untouched,
            'window_vs_supplied_wav_decoded_error': inside}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('master', type=Path)
    p.add_argument('wav', type=Path, help='Finished replacement mix covering only this ticket')
    p.add_argument('--start-frame', type=int, required=True)
    p.add_argument('--end-frame', type=int, required=True, help='Exclusive')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--require-exact-exterior', action='store_true', help='Require sample-exact decoded audio beyond the AAC splice guard')
    p.add_argument('--expect-base', help='SHA-256 of the version on the review ticket')
    args = p.parse_args()
    require(args.output.suffix.lower() == '.mp4' and args.output.name.casefold() != 'final.mp4', 'Choose a new MP4 candidate path')
    paths = [x.resolve() for x in (args.master, args.wav, args.output, args.report)]
    require(len(set(paths)) == 4, 'Inputs, output and report must be distinct')
    require(not args.output.exists() and not args.report.exists(), 'Use new candidate and report paths')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.output.parent) as temp:
        work = Path(temp)
        movie = work / 'candidate.mp4'
        report = replace_audio(args, work, movie)
        with tempfile.TemporaryDirectory(dir=args.report.parent) as temp_report:
            staged = Path(temp_report) / 'check.json'
            staged.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
            # link() is an atomic no-overwrite install even if another editor won the race.
            os.link(staged, args.report)
            try:
                os.link(movie, args.output)
            except BaseException:
                if args.report.samefile(staged):
                    args.report.unlink()
                raise
    print(f"{report['copied_video_packets']} video packets copied; {report['locally_encoded_aac_packets']} AAC packets locally encoded; {args.report}")


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc))
