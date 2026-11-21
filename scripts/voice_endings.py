"""Check actual sentence endings against the spoken script and retain localized retakes."""
import hashlib
import os
import re
import subprocess
import tempfile
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

import numpy as np


MODEL_DIR = Path.home() / '.cache/creator-video-models/sensevoice'
RATE = 16000


def speech_tokens(text):
    clean = re.sub(r'<\|.*?\|>', '', unicodedata.normalize('NFKC', text).casefold())
    return re.findall(r'[a-z]+|[0-9]+|[^\W_]', clean, re.UNICODE)


def classify_ending(spoken, transcript):
    """Only a recognizable prefix ending early is evidence for retrying a take."""
    target = speech_tokens(spoken)
    heard = speech_tokens(transcript)
    matcher = SequenceMatcher(None, target, heard, autojunk=False)
    status = 'uncertain'
    missing = []
    tail_size = min(3, len(target))
    if target and heard and target[-tail_size:] == heard[-tail_size:]:
        status = 'matched'
    elif target and heard:
        blocks = matcher.get_matching_blocks()
        last = blocks[-2] if len(blocks) > 1 else None
        if last and last.b + last.size == len(heard):
            remaining = target[last.a + last.size:]
            if 0 < len(remaining) <= 4 and last.size >= 2 and matcher.ratio() >= .65:
                status = 'possible_missing_ending'
                missing = remaining
    return dict(status=status, spoken=spoken, recognized=transcript,
                expected_tail=target[-4:], missing=missing, similarity=round(matcher.ratio(), 3))


class EndingChecker:
    """Use the optional local recognizer. Read-only analysis works on supplied recordings too."""

    def __init__(self, model_dir=MODEL_DIR):
        self.directory = Path(model_dir)
        self.available = all((self.directory / name).is_file() for name in ('model.int8.onnx', 'tokens.txt'))
        self.engine = None

    def inspect_file(self, path, spoken):
        if not self.available:
            return dict(status='asr_unavailable', spoken=spoken)
        if self.engine is None:
            import sherpa_onnx as so
            self.engine = so.OfflineRecognizer.from_sense_voice(
                model=str(self.directory / 'model.int8.onnx'), tokens=str(self.directory / 'tokens.txt'),
                language='auto', num_threads=3, use_itn=False)
        raw = subprocess.check_output(['ffmpeg', '-nostdin', '-v', 'error', '-i', path, '-map', '0:a:0',
                                       '-ac', '1', '-ar', str(RATE), '-f', 'f32le', '-'])
        stream = self.engine.create_stream()
        # Quiet context for the ASR only. No sample is appended to the production audio.
        stream.accept_waveform(RATE, np.pad(np.frombuffer(raw, '<f4'), (RATE // 10, RATE // 3)))
        self.engine.decode_stream(stream)
        transcript = re.sub(r'<\|.*?\|>', '', stream.result.text).strip()
        outcome = classify_ending(spoken, transcript)
        ticks = stream.result.timestamps
        if ticks:
            pcm = np.frombuffer(raw, '<f4')
            frames = pcm[:len(pcm)//160*160].reshape(-1,160)
            if len(frames):
                rms = np.sqrt(np.mean(frames**2,axis=1))
                active = np.flatnonzero(rms > max(.001, float(np.max(rms))*.024))
                if len(active):
                    end = (int(active[-1])+1)*.01
                    stamp = max(0., float(ticks[-1])-.1)
                    outcome['acoustic_boundary'] = {
                        'asr_last_token_seconds':round(stamp,3),
                        'active_end_seconds':round(end,3),
                        'active_after_token_seconds':round(end-stamp,3),
                        'quiet_to_file_edge_seconds':round(max(0,len(pcm)/RATE-end),3),
                        'inspect_final_vowel':end-stamp<.10 and len(pcm)/RATE-end<.032}
        return outcome


def checksum(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


@lru_cache(maxsize=1)
def syllables():
    # Local ZipVoice's Mandarin lexicon is already installed for these retakes.
    from setup_zipvoice import MODEL_DIR as zipvoice_dir
    path = zipvoice_dir / 'lexicon.txt'
    if not path.is_file():
        return {}
    phones = {}
    with path.open(encoding='utf-8') as source:
        for line in source:
            fields = line.split()
            if len(fields) > 1 and len(fields[0]) == 1:
                phones[fields[0]] = tuple(phone.rstrip('0123456789') for phone in fields[1:])
    return phones


def same_sounding_tail(attempt, missing):
    """Identify a plausible audition alternative; tone and lexical identity remain unverified."""
    target = speech_tokens(attempt['spoken'])
    heard = speech_tokens(attempt['recognized'])
    count = len(missing)
    context = len(target) - count
    if (not count or len(heard) < count + 2 or context < 2
            or heard[-count-2:-count] != target[context-2:context]):
        return False
    phones = syllables()
    return all(a == b or (a in phones and phones[a] == phones.get(b))
               for a, b in zip(missing, heard[-count:]))


def checked_take(path, spoken, speed, voice, checker, write_wave, previous=None):
    """Reuse the requested take; store alternatives under separate, deterministic filenames."""
    if previous and previous.get('status') == 'matched':
        records = previous.get('attempts', [])
        index = previous.get('selected_attempt')
        if isinstance(index, int) and 0 <= index < len(records):
            cached = path if index == 0 else path.with_stem(f'{path.stem}.ending-v1-{index}')
            if (records[0].get('spoken') == spoken and records[0].get('speed') == speed
                    and records[0].get('file') == path.name and path.is_file() and cached.is_file()
                    and checksum(path) == records[0].get('sha256') and checksum(cached) == previous.get('sha256')):
                return cached, previous
    attempts = []
    paths = []
    selected = 0
    settings = ((1., False), (1., True), (.92, True), (1., False))
    for index, (scale, preserve) in enumerate(settings):
        candidate = path if index == 0 else path.with_stem(f'{path.stem}.ending-v1-{index}')
        if not candidate.is_file():
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.wav', delete=False) as handle:
                temp = Path(handle.name)
            try:
                audio = voice.generate(spoken, speed * scale, preserve_pauses=preserve)
                write_wave(temp, audio)
                os.replace(temp, candidate)
            finally:
                temp.unlink(missing_ok=True)
        report = checker.inspect_file(candidate, spoken)
        sha = checksum(candidate)
        attempts.append(dict(**report, file=candidate.name, sha256=sha, speed=speed * scale,
                             preserve_pauses=preserve))
        paths.append(candidate)
        if index == 0 and report['status'] != 'possible_missing_ending':
            break
        # A clear ending alone cannot redeem a new misreading in the middle.
        if index and report['status'] == 'matched' and report['similarity'] >= attempts[0]['similarity']:
            selected = index
            break
        if (index and report['status'] == 'uncertain' and report['similarity'] >= attempts[selected]['similarity']
                and same_sounding_tail(report, attempts[0]['missing'])):
            selected = index
    choice = attempts[selected]
    status = choice['status']
    if selected and status != 'matched':
        # This alternative has audible-material evidence for the missing syllables,
        # but ASR cannot settle how they were pronounced.
        status = 'possible_missing_ending'
    return paths[selected], dict(status=status, selected_attempt=selected, recognized=choice.get('recognized'),
                                 effective_speed=choice['speed'], sha256=choice['sha256'], attempts=attempts)


def final_ending_checks(timeline, subs, recognize):
    """Inspect one whole spoken sentence in the delivered mix, including its end."""
    from listening_review import review_lines

    takes = timeline.get('voice_takes', [])
    duration = timeline['frames'] / timeline['fps']
    windows = []
    if takes and all(all(key in t for key in ('start_sample', 'end_sample', 'sample_rate', 'spoken'))
                     for t in takes):
        for take in takes:
            rate = take['sample_rate']
            if rate <= 0 or not 0 <= take['start_sample'] < take['end_sample'] <= round(duration * rate):
                raise ValueError('Invalid source speech interval in timeline')
            windows.append((take['start_sample'] / rate, take['end_sample'] / rate, take['spoken'], take))
    else:
        groups = list(review_lines(timeline, subs))
        matched = (len(takes) == len(groups) and
                   all(t.get('caption_units') == [subs[i][2] for i in g] for t, g in zip(takes, groups)))
        for n, group in enumerate(groups):
            caption = ''.join(subs[i][2] for i in group)
            take = takes[n] if matched else {}
            windows.append((subs[group.start][0], subs[group.stop - 1][1], take.get('spoken', caption), take))
    rows = []
    for n, (start, end, spoken, take) in enumerate(windows):
        # Keep the neighboring spoken sentence out of the recognition window.
        before = windows[n - 1][1] if n else 0.
        after = windows[n + 1][0] if n + 1 < len(windows) else duration
        left, right = max(before, start - .08), min(after, end + .12)
        outcome = (classify_ending(spoken, recognize(left, right)) if recognize is not None
                   else dict(status='asr_unavailable', spoken=spoken))
        source_status = take.get('ending_check', {}).get('status', 'not_recorded')
        rows.append(dict(shot=take.get('shot'), start=round(start, 4), end=round(end, 4),
                         reference='spoken' if 'spoken' in take else 'caption', source_status=source_status,
                         **outcome))
    return rows
