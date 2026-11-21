"""Locate editorial reading cues and check the designated text region in encoded frames."""
import hashlib
import math
from pathlib import Path
import subprocess
import tempfile
import unicodedata


def readback_spec(value):
    if not isinstance(value, dict) or not isinstance(value.get('text'), str) or not value['text'].strip() or len(value['text'].strip().splitlines()) != 1:
        raise ValueError('evidence_read.readback needs the complete single line of text to read in one region')
    box = value.get('roi')
    if (not isinstance(box, list) or len(box) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
            or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1)):
        raise ValueError('Give readback.roi normalized left, top, right, bottom in the delivery frame')
    return {'text': value['text'].strip(), 'roi': box}


def cue_frames(reading, total):
    """Keep simultaneous cues together and distinguish an exclusive film end from a visible frame."""
    first = reading['first_readable_frame']
    end = reading['visible_until_frame']
    if not 0 <= first < end <= total:
        raise ValueError('Evidence reading window exceeds the candidate picture')
    cues = {}
    for role, n in [('before', first-1), ('read', first), ('after', first+1),
                    ('voice', reading['reveal_frame']), ('last', end-1),
                    ('next', reading.get('next_information_frame'))]:
        if n is not None and 0 <= n < total:
            cues.setdefault(n, []).append(role)
    return dict(sorted(cues.items()))


def literal(text):
    # Normalize typographic width and spacing while retaining digit boundaries, signs,
    # case, decimal points and superscripts. 10² and 102 are different readings.
    text = unicodedata.normalize('NFC', text)
    return ''.join(chr(ord(c)-0xFEE0) if '！' <= c <= '～' else c
                   for c in text.replace('−', '-') if not c.isspace())


def readback_report(movie, timeline, view_width, recognize, *, proof_dir=None):
    """OCR each distinct native-size crop, at every programme frame of the authored read window."""
    from PIL import Image, ImageDraw

    checks = []
    active = {}
    for r in timeline.get('evidence_readings', []):
        if 'readback' not in r:
            continue
        spec = readback_spec(r['readback'])
        start, end = r['first_readable_frame'], r['visible_until_frame']
        if not 0 <= start < end <= timeline['frames']:
            raise ValueError('Evidence reading window exceeds the candidate picture')
        index = len(checks)
        checks.append(dict(shot=r['shot'], **spec, start_frame=start, end_frame=end, reveal_frame=r['reveal_frame'], runs=[]))
        for frame in range(max(0, start-1), end):
            active.setdefault(frame, []).append(index)
    if not checks:
        return []
    picked = sorted(active)
    ranges = []
    for n in picked:
        if ranges and n == ranges[-1][1]+1:
            ranges[-1][1] = n
        else:
            ranges.append([n, n])
    select = '+'.join(f'between(n,{a},{b})' for a,b in ranges)
    filters = f"select='{select}',scale={view_width}:'max(1,round({view_width}/dar))':flags=lanczos,setsar=1"
    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', movie, '-map', '0:v:0', '-an',
                        '-vf', filters, '-fps_mode', 'passthrough', '-frames:v', str(len(picked)),
                        '-pix_fmt', 'rgb24', tmp/'frame-%09d.png'], check=True)
        crops = {}
        occurrences = []
        pictures = dict(zip(picked, sorted(tmp.glob('frame-*.png')), strict=True))
        for n, path in pictures.items():
            with Image.open(path) as image:
                w, h = image.size
                for index in active[n]:
                    left, top, right, bottom = checks[index]['roi']
                    area = image.crop((math.floor(left*w), math.floor(top*h), math.ceil(right*w), math.ceil(bottom*h)))
                    key = (area.size, hashlib.sha256(area.tobytes()).digest())
                    if key not in crops:
                        crops[key] = tmp/f'region-{len(crops):08d}.png'
                        area.save(crops[key])
                    occurrences.append((index, n, crops[key].name))
        reads = recognize(tmp, list(crops.values()))
        texts = {}
        for row in reads:
            # A smaller unit can have a different baseline. Preserve left-to-right order
            # on a shared line, and never join two unrelated rows into a numeric match.
            cells = sorted(row['words'], key=lambda cell: cell['x'])
            same_line = bool(cells) and max(c['y'] for c in cells) < min(c['y']+c['height'] for c in cells)
            texts[row['file']] = (' '.join(w['text'] for w in cells), same_line)
        for index, n, name in occurrences:
            check = checks[index]
            text, same_line = texts[name]
            match = same_line and literal(text) == literal(check['text'])
            if n < check['start_frame']:
                check['preceding_frame'] = dict(frame=n, recognized=text, matches=match)
                continue
            runs = check['runs']
            if runs and runs[-1]['recognized'] == text and runs[-1]['matches'] == match:
                runs[-1]['end_frame'] = n+1
            else:
                runs.append(dict(start_frame=n, end_frame=n+1, recognized=text, matches=match))
        for i, check in enumerate(checks, 1):
            check['first_match_frame'] = next((r['start_frame'] for r in check['runs'] if r['matches']), None)
            check['unconfirmed_frames'] = sum(r['end_frame'] - r['start_frame'] for r in check['runs'] if not r['matches'])
            gaps, visible = [], []
            for run in check['runs']:
                spans = visible if run['matches'] else gaps
                if spans and spans[-1]['end_frame'] == run['start_frame']:
                    spans[-1]['end_frame'] = run['end_frame']
                else:
                    spans.append(dict(start_frame=run['start_frame'], end_frame=run['end_frame']))
            check['longest_confirmed_frames'] = max((r['end_frame']-r['start_frame'] for r in visible), default=0)
            check['longest_confirmed_before_reveal_frames'] = max(
                (min(r['end_frame'], check['reveal_frame'])-r['start_frame'] for r in visible
                 if r['start_frame'] < check['reveal_frame']), default=0)
            for j, gap in enumerate(gaps, 1):
                first, end = gap['start_frame'], gap['end_frame']
                frames = [n for n in sorted({first-1, first, end-1, end}) if n in pictures
                          and max(0, check['start_frame']-1) <= n < check['end_frame']]
                gap['sampled_frames'] = frames
                if proof_dir is not None:
                    with Image.open(pictures[first]) as source:
                        w, h = source.size
                    sheet = Image.new('RGB', (w, (h+20)*len(frames)), '#17221e')
                    draw = ImageDraw.Draw(sheet)
                    for k, n in enumerate(frames):
                        y = (h+20)*k
                        with Image.open(pictures[n]) as source:
                            sheet.paste(source, (0,y))
                        draw.text((6,y+h+2), f'F{n}   {n/timeline["fps"]:.3f}s', fill='white')
                    gap['sample_sheet'] = f'readback-{i:02}-gap-{j:02}.png'
                    sheet.save(proof_dir/gap['sample_sheet'])
            check['unconfirmed_spans'] = gaps
            check['view_width'] = view_width
    return checks
