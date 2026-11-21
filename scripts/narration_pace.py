"""Choose a starting pace for generated narration and report where time is spent."""
from collections import defaultdict
import re

HAN = re.compile(r'[\u3400-\u9fff]')
BRISK_SPEED = 1.16
QUICK_SPEED = 1.22
# A short relative-year phrase often just moves the story forward. A four-digit
# calendar year or another measurement elsewhere in the sentence still counts.
RELATIVE_YEARS = re.compile(
    r'(?<![0-9零〇一二两三四五六七八九十百千万亿])'
    r'(?:[0-9]{1,2}|[一二两三四五六七八九十]{1,3})年[以之]?[前后]'
)
SPOKEN_FIGURE = re.compile(
    r'[0-9]|百分之|[零〇一二两三四五六七八九十]点[零〇一二三四五六七八九]|'
    r'[一二两三四五六七八九十百千][万亿]|[零〇一二两三四五六七八九十百千万亿点半]+'
    r'(?:年|月|日|倍|元|美元|英镑|欧元|港元|亿元|股|成|折|分之|'
    r'(?:毫|微|纳)?秒|分钟|小时|公里|(?:千|厘|毫|微|纳)?米|吨|毫升|'
    r'(?:千|兆|吉|毫|微)?瓦(?:时)?|(?:千|毫|微)?伏|(?:毫|微)?安(?:时)?|'
    r'(?:千|兆|吉)?赫(?:兹)?|(?:千|兆|吉|太)?字节|比特|帧)'
)


def new_edit(kind):
    if kind not in ('news', 'explainer'):
        raise ValueError('Use news or explainer for a new narrated draft')
    return {'size': [1920, 1080], 'fps': 30, 'narration_pace': 'quick',
            'story_intent': {'kind': kind, 'main_point': '', 'viewer_value': ''}, 'shots': []}


def narration_pace(edit):
    intent = edit.get('story_intent') or {}
    default = 'brisk' if intent.get('kind') in ('news', 'explainer') else 'measured'
    pace = edit.get('narration_pace', default)
    if pace not in ('quick', 'brisk', 'measured'):
        raise ValueError('narration_pace must be quick, brisk or measured')
    return pace


def jianqiu_speed(edit, line, pace, profile_speed):
    """Resolve both generation paths. Old projects keep the historic directed-voice default."""
    if 'speed' in line:
        return float(line['speed'])
    if 'speed' in edit:
        return float(edit['speed'])
    if pace == 'quick':
        printed = line['text']
        spoken = line.get('spoken', printed)
        careful = line.get('pronunciation') or any(
            SPOKEN_FIGURE.search(RELATIVE_YEARS.sub(' ', words)) for words in (printed, spoken)
        )
        return BRISK_SPEED if careful else QUICK_SPEED
    if line.get('pronunciation') or pace == 'measured':
        return profile_speed
    return BRISK_SPEED


def directed_pace(line, edit, pace, speed):
    """Try one intermediate speed before legacy safety fallbacks on a fresh quick draft."""
    spec = line['pronunciation']
    if pace != 'quick' or 'speeds' in spec or 'speed' in line or 'speed' in edit:
        return line
    # Keep the actual requested ordering distinct even at lower base speeds.
    speeds = list(dict.fromkeys([speed, round(speed * .95, 2), min(1., speed), .91]))
    return dict(line, pronunciation=dict(spec, speeds=speeds))


def clock_totals(timeline):
    """Separate complete recorded takes (including internal pauses) from editorial gaps."""
    takes = timeline.get('voice_takes', [])
    if not takes or not all({'start_sample', 'end_sample', 'sample_rate'} <= row.keys() for row in takes):
        return {}
    programme = timeline['frames'] / timeline['fps']
    spans = [(row['start_sample'] / row['sample_rate'], row['end_sample'] / row['sample_rate']) for row in takes]
    if not all(0 <= a <= b <= programme + 1e-6 for a, b in spans):
        return {}
    between = [right[0] - left[1] for left, right in zip(spans, spans[1:])]
    if any(g < -1e-6 for g in between):
        return {}
    take_seconds = sum(b - a for a, b in spans)
    return dict(programme_seconds=round(programme, 4), take_window_seconds=round(take_seconds, 4),
                outside_takes_seconds=round(programme - take_seconds, 4),
                between_lines_same_shot_seconds=round(sum(g for g, a, b in zip(between, takes, takes[1:])
                                                          if a['shot'] == b['shot']), 4))


def sentence_pace(timeline, subs):
    """Report production sentence windows only when the sidecar agrees with the frozen units."""
    by_shot = defaultdict(list)
    cursor = 0
    for take in timeline.get('voice_takes', []):
        units = take.get('caption_units', [])
        segment = subs[cursor:cursor + len(units)]
        if not units or [s for _, _, s in segment] != units:
            return {'matched_sentence_windows': False}
        start, end = segment[0][0], segment[-1][1]
        text = ''.join(units)
        row = dict(shot=take['shot'], text=text, start=round(start, 3), end=round(end, 3),
                   seconds=round(end - start, 3))
        if 'speed' in take:
            row['synthesis_speed'] = take['speed']
            selected = take.get('pronunciation') or take.get('ending_check') or {}
            row['effective_synthesis_speed'] = selected.get('effective_speed', take['speed'])
        if not re.search(r'[a-zA-Z0-9]', text) and end > start:
            row['caption_han_per_minute'] = round(60 * len(HAN.findall(text)) / (end - start))
        by_shot[take['shot']].append(row)
        cursor += len(units)
    if cursor != len(subs) or not by_shot:
        return {'matched_sentence_windows': False}
    rows, gaps = [], []
    fps = timeline['fps']
    for shot in timeline['shots']:
        last = shot['start_frame'] / fps
        for row in by_shot[shot['id']]:
            if row['start'] - last > .001:
                gaps.append(dict(shot=shot['id'], start=round(last, 3), end=row['start'], before=row['text']))
            last = row['end']
            rows.append(row)
        end = (shot['start_frame'] + shot['frames']) / fps
        if end - last > .001:
            gaps.append(dict(shot=shot['id'], start=round(last, 3), end=round(end, 3), before=None))
    gaps.sort(key=lambda gap: gap['end'] - gap['start'], reverse=True)
    return dict(matched_sentence_windows=True, sentences=rows, longest_caption_free_intervals=gaps[:5],
                **clock_totals(timeline))
