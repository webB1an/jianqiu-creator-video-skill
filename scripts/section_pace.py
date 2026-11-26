"""Summarize cut lengths and caption timing for the editor's narrative sections."""
import json
from statistics import median


def required_word_lines(edit, pins):
    """Align only lines referenced by a directed sound, picture, camera, comparison or reading event."""
    from motion_layers import word_fields
    from shot_camera import word_fields as camera_word_fields
    cues = list(edit.get('music_cues', []))
    plan = edit.get('sfx_plan')
    if plan:
        source = pins.pin(plan, optional=True)
        if source.is_file():
            cues.extend(json.loads(source.read_text()))
    anchors = [cue.get('at') for cue in cues]
    for shot in edit['shots']:
        anchors.extend(shot.get('remotion_effect', {}).get('cues', {}).values())
        anchors.extend(entry[field] for entry, field in word_fields(shot))
        anchors.extend(entry[field] for entry, field in camera_word_fields(shot))
        vc = shot.get('value_comparison') or {}
        if isinstance(vc, dict):
            for row in vc.get('rows', []):
                if isinstance(row, dict) and isinstance(row.get('reveal'), list):
                    anchors.extend(row['reveal'])
            if isinstance(vc.get('gap'), list):
                anchors.extend(vc['gap'])
        reading = shot.get('evidence_read', {})
        anchors.extend((reading.get('reveal_word'), reading.get('next_information')))
    return {value[1:].split(':', 1)[0] for value in anchors if isinstance(value, str) and value.startswith('@')}


def reading_recut(edit, timeline, timing, row, next_cue):
    """Two alternative timing targets. Neither changes the picture, the text or the voice track."""
    first, end = row['first_readable_frame'], row['visible_until_frame']
    reveal, following = row['reveal_frame'], row['next_information_frame']
    preview = row.get('min_preview_frames', 0)
    required = max(1, preview, row.get('min_read_frames', 0))
    stop = end if following is None else min(end, following)
    latest_first = min(reveal - preview, stop - required)
    if latest_first >= first:
        return None

    picture = dict(advance_frames=first-latest_first)
    if latest_first < 0:
        picture['before_programme_frames'] = -latest_first
    else:
        picture.update(first_readable_by=latest_first, keep_readable_until=latest_first+required)
        if latest_first < timing['start_frame']:
            picture['before_shot_frames'] = timing['start_frame'] - latest_first

    # Keeping the first visible frame can require moving several *different* constraints.
    # Their targets are absolute programme frames, so simultaneous shortfalls never add up.
    keep = {}
    if reveal < first + preview:
        keep['reveal_word'] = dict(at=row['reveal_word'], earliest_frame=first+preview)
    needed_end = first + required
    if end < needed_end:
        keep['keep_readable_until'] = needed_end
        over = needed_end - (timing['start_frame'] + timing['frames'])
        if over > 0:
            keep['past_cut_frames'] = over
    if following is not None and following < needed_end:
        if isinstance(next_cue, str):
            current_line = row['reveal_word'][1:].split(':', 1)[0]
            next_line = next_cue[1:].split(':', 1)[0]
            move = dict(at=next_cue, earliest_frame=needed_end)
            if current_line == next_line:
                move['same_line_as_reveal'] = True
            elif not timeline.get('presenter_source'):
                from roughcut_adoption import narration_shots
                lines = [line.get('id', f"{shot['id']}.L{i+1}")
                         for shot in narration_shots(edit) for i, line in enumerate(shot.get('lines', []))]
                if (current_line, next_line) in zip(lines, lines[1:]):
                    move['pause_after_line'] = current_line
            keep['next_word'] = move
        else:
            keep['next_picture_event_from'] = needed_end
    return dict(keep_word_timing=picture, keep_first_readable=keep)


def evidence_readings(edit, timeline):
    """Compare authored visible evidence windows with the delivered speech clock."""
    from remotion_library import resolve_frame
    schedule = {shot['id']: shot for shot in timeline['shots']}
    clock = {'start_frame': 0, 'frames': timeline['frames']}
    result = []
    for shot in edit['shots']:
        if 'evidence_read' not in shot:
            continue
        spec = shot['evidence_read']
        timing = schedule[shot['id']]
        start = timing['start_frame']
        window = spec['watch']
        if (not isinstance(window, list) or len(window) != 2 or any(type(v) is not int for v in window)
                or not 0 <= window[0] < window[1] <= timing['frames']):
            raise ValueError(f"{shot['id']}: evidence_read.watch must be a visible frame interval inside the shot")
        discovery = spec['discovery']
        if not isinstance(discovery, str) or not discovery.strip():
            raise ValueError('Describe the finding visible in the evidence_read window')
        visible, end = (start + value for value in window)
        word = spec['reveal_word']
        if not isinstance(word, str):
            raise ValueError('evidence_read.reveal_word must name a spoken @line or @line:word')
        reveal = resolve_frame(word, timeline, clock)
        line = word[1:].split(':', 1)[0]
        beat = next(b for b in timeline['beats'] if b['id'] == line)
        next_cue = spec.get('next_information')
        if isinstance(next_cue, str):
            following = resolve_frame(next_cue, timeline, clock)
        elif next_cue is None:
            following = None
        elif type(next_cue) is int and 0 <= next_cue <= timing['frames']:
            following = start + next_cue
        else:
            raise ValueError('next_information must be a word cue or a local frame in the shot')
        stop = end if following is None else min(following, end)
        allowance = max(0, stop - visible)
        row = dict(shot=shot['id'], discovery=discovery, reveal_word=word, word_clock=beat.get('method'),
                   first_readable_frame=visible,
                   visible_until_frame=end, reveal_frame=reveal, next_information_frame=following,
                   reveal_after_readable_frames=reveal-visible, available_read_frames=allowance,
                   read_after_reveal_frames=max(0, stop-max(reveal, visible)))
        if 'min_read_frames' in spec:
            minimum = spec['min_read_frames']
            if type(minimum) is not int or minimum < 1:
                raise ValueError('min_read_frames must be a positive integer')
            row.update(min_read_frames=minimum, short_by_frames=max(0, minimum-allowance))
        if 'min_preview_frames' in spec:
            preview = spec['min_preview_frames']
            if type(preview) is not int or preview < 0:
                raise ValueError('min_preview_frames must be a nonnegative integer')
            observed = max(0, min(reveal, stop)-visible)
            row.update(min_preview_frames=preview, preview_frames=observed,
                       preview_short_by_frames=max(0, preview-observed))
        recut = reading_recut(edit, timeline, timing, row, next_cue)
        if recut:
            row['recut_options'] = recut
        if 'readback' in spec:
            from evidence_frames import readback_spec
            row['readback'] = readback_spec(spec['readback'])
        result.append(row)
    return result


def freeze_cuts(plan, total_frames):
    """Legacy projects sometimes have only a partial screen plan; use timeline shots then."""
    cuts = plan.get('cuts', [])
    cursor = 0
    frozen = []
    for cut in cuts:
        first, last = cut.get('start'), cut.get('end')
        if type(first) is not int or type(last) is not int or not cursor == first < last <= total_frames:
            return []
        row = {'start': first}
        if 'section' in cut:
            name = cut['section']
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f'Give the section beginning at frame {first} a name')
            row['section'] = name.strip()
        cue = cut.get('cue_frame')
        if type(cue) is int and first <= cue < last:
            row['cue_frame'] = cue
        frozen.append(row)
        cursor = last
    return frozen if cursor == total_frames else []


def measure_sections(timeline, cuts, subs, windows):
    fps, total = timeline['fps'], timeline['frames']
    source = 'screen_plan' if cuts else 'timeline_shots'
    cuts = cuts or [{'start': shot['start_frame']} for shot in timeline['shots']]
    boundaries = [i for i, cut in enumerate(cuts) if i == 0 or 'section' in cut]
    rows = []
    for i, stop in zip(boundaries, boundaries[1:] + [len(cuts)]):
        group = cuts[i:stop]
        first = group[0]['start']
        last = cuts[stop]['start'] if stop < len(cuts) else total
        starts = [cut['start'] for cut in group]
        cut_lengths = [b-a for a, b in zip(starts, starts[1:] + [last])]
        lo, hi = first/fps, last/fps
        cues = [(a,b) for a,b,_ in subs if a < hi and lo < b]
        # SRT stores milliseconds. A cue at a 30 fps cut may start at 0.033s.
        caption_starts = sum(round(lo, 3) <= a < round(hi, 3) for a,b in cues)
        spans = []
        for a, b in sorted(cues):
            a, b = max(a, lo), min(b, hi)
            if spans and a <= spans[-1][1]:
                spans[-1] = (spans[-1][0], max(spans[-1][1], b))
            else:
                spans.append((a, b))
        caption_time = sum(b-a for a,b in spans)
        watched = 0.
        clear = 0.
        for window in windows:
            a, b = max(first, window['start'])/fps, min(last, window['end'])/fps
            if a < b:
                watched += b-a
                covered = sum(max(0., min(b,y) - max(a,x)) for x,y in spans)
                clear += max(0., b-a-covered)
        rows.append(dict(
            section=group[0].get('section'), start_frame=first, end_frame=last,
            cut_frames=cut_lengths, median_cut_seconds=round(median(cut_lengths)/fps, 3),
            caption_starts=caption_starts, caption_starts_per_10s=round(10*caption_starts/(hi-lo), 2),
            caption_coverage_percent=round(100*caption_time/(hi-lo), 1),
            watch_seconds=round(watched, 3) if watched else None,
            watch_without_captions_seconds=round(clear, 3) if watched else None,
            cue_frames=[c['cue_frame'] for c in group if 'cue_frame' in c]))
    for row in rows:
        readings = [r for r in timeline.get('evidence_readings', [])
                    if row['start_frame'] <= r['first_readable_frame'] < row['end_frame']]
        if readings:
            row['evidence_readings'] = readings
    return {'cut_source': source, 'segments': rows}
