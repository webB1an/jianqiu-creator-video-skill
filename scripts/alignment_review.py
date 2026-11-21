"""Review visible-ink relationships on encoded picture, using the cut's local geometry."""
import math


def _name(value):
    if not isinstance(value, str) or not value.strip() or '/' in value:
        raise ValueError('Give each component, row or cell a nonempty local name without /')
    return value


def component_relations(components, duration=None, *, card_windows=None):
    """Expand editable content to its measured relationships; original pixels have no redesign target."""
    if not isinstance(components, list):
        raise ValueError('Inventory the visible components, using [] for a shot without authored components')
    relations, ids = {}, set()
    for obj in components:
        if not isinstance(obj, dict):
            raise ValueError('Each component needs an id and kind')
        name, kind = _name(obj.get('id')), obj.get('kind')
        if name in ids:
            raise ValueError('Component ids must be unique within their shot')
        ids.add(name)
        if kind == 'source_image':
            _text(obj, 'source')
        elif kind == 'label_plate':
            axis = obj.get('axis', 'xy')
            if axis not in ('y', 'xy'):
                raise ValueError('Measure a plate vertically, and horizontally for a centered label')
            relations[name] = ('center', axis)
        elif kind == 'number_unit':
            relations[name] = ('bottom', 'y')
        elif kind == 'editable_card':
            rows = obj.get('rows')
            if not isinstance(rows, list) or not rows:
                raise ValueError('List every editable row inside the inset card')
            display = obj.get('display', 'together')
            if display not in ('together', 'sequence'):
                raise ValueError('An editable card displays its rows together or in sequence')
            windows = {}
            row_ids = set()
            for row in rows:
                row_id = _name(row.get('id') if isinstance(row, dict) else row)
                if row_id in row_ids:
                    raise ValueError('Card row ids must be unique')
                row_ids.add(row_id)
                span = row.get('visible') if isinstance(row, dict) else None
                if display == 'sequence':
                    if (not isinstance(span, list) or len(span) != 2 or
                            any(type(f) is not int for f in span) or not 0 <= span[0] < span[1] or
                            duration is not None and span[1] > duration):
                        raise ValueError('Give each replacement row its actual half-open visible frame span within the cut')
                    if any(span[0] < end and start < span[1] for start, end in windows.values()):
                        raise ValueError('Replacement rows sharing a slot need nonoverlapping visible spans')
                    windows[row_id] = tuple(span)
                elif span is not None:
                    raise ValueError('Declare display=sequence for rows replacing each other in one slot')
                cells = row.get('cells') if isinstance(row, dict) else None
                key = f'{name}/{row_id}'
                if cells is None:
                    relations[key] = ('center', 'y')
                else:
                    if not isinstance(cells, list) or not cells:
                        raise ValueError('List visible text cells for this row')
                    names = [_name(c) for c in cells]
                    if len(set(names)) != len(names):
                        raise ValueError('Card cell ids must be unique in their row')
                    relations.update({f'{key}/{c}': ('center', 'y') for c in names})
            if card_windows is not None:
                card_windows[name] = windows if display == 'sequence' else None
        else:
            raise ValueError(f'Unknown layout component kind: {kind}')
    return relations


def scope(plan):
    """Inspect the finished picture of every cut, including graphics baked into camera footage."""
    targets = []
    for cut in plan.get('cuts', []):
        start, end = cut['start'], cut['end']
        groups = cut.get('alignment_groups', [])
        if (not isinstance(groups, list) or any(not isinstance(g, str) or not g.strip() for g in groups)
                or len(groups) != len(set(groups))):
            raise ValueError('alignment_groups must list unique nonempty names on a cut')
        components = cut.get('alignment_components', [])
        component_relations(components, end-start)
        targets.append(dict(start=start, end=end, groups=groups, components=components,
                            inventory=True, shot=cut.get('source_key') or cut.get('subject') or 'picture'))
    return targets


def _text(spot, key):
    v = spot.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f'Alignment review needs {key}')
    return v


def _box(row, key, size):
    box = row.get(key)
    if not (isinstance(box, list) and len(box) == 4
            and all(type(n) in (float, int) and math.isfinite(n) for n in box)
            and 0 <= box[0] < box[2] <= size[0] and 0 <= box[1] < box[3] <= size[1]):
        raise ValueError(f'{key} must be measured [left, top, right, bottom] in decoded frame pixels')
    return box


def _delta(spot, ink, ref, a):
    if spot.get('relation', 'center') == 'bottom':
        return ink[3] - ref[3]
    return ((ink[a] + ink[a+2]) - (ref[a] + ref[a+2])) / 2


def deviations(spot, size):
    """Return all axis deltas over a small, resolution-relative optical tolerance."""
    result = []
    for row in spot['samples']:
        ink, ref = row['ink_box'], row['reference_box']
        for axis in spot['axis']:
            a = 'xy'.index(axis)
            delta = _delta(spot, ink, ref, a)
            # Pixel/AA uncertainty; allow a small optical discrepancy relative to the smaller shape.
            limit = max(1., 2.5 * min(size) / 1080,
                        .035 * min(ink[a+2] - ink[a], ref[a+2] - ref[a]))
            if abs(delta) > limit:
                result.append((row['frame'], axis, round(delta, 2), round(limit, 2)))
    return result


def validate(item, targets, size, frames):
    spot = item['perception']
    if not size or len(size) != 2:
        raise ValueError('Begin an alignment review on a media file with known frame dimensions')
    first, end = item['start_frame'], item['end_frame']
    target = next((t for t in targets if t['start'] <= first < end <= t['end']), None)
    if targets and not target:
        raise ValueError('Keep the alignment observation within one registered cut')
    groups = target['groups'] if target else []
    _text(spot, 'playback')
    if spot.get('method') == 'frame_inventory':
        indexed = target and target.get('inventory')
        if indexed:
            duration = target['end']-target['start']
            actual_cards, planned_cards = {}, {}
            discovered = component_relations(spot.get('components'), duration, card_windows=actual_cards)
            planned = component_relations(target['components'], duration, card_windows=planned_cards)
            if any(discovered.get(key) != rule for key, rule in planned.items()):
                raise ValueError('Inventory must include all planned editable components, rows and cells')
            for card, planned_windows in planned_cards.items():
                observed = actual_cards[card]
                if (observed is None) != (planned_windows is None) or (planned_windows is not None and
                        any(observed.get(k) != v for k, v in planned_windows.items())):
                    raise ValueError('Keep the declared simultaneous or sequential states of the observed card')
            for obj in target['components']:
                if obj['kind'] == 'source_image' and not any(
                        c['id'] == obj['id'] and c['kind'] == obj['kind'] for c in spot['components']):
                    raise ValueError('Identify the original source image in the shot inventory')
        elif groups:
            raise ValueError('This shot has named alignment groups; measure them on the picture')
        lo, hi = (target['start'], target['end']) if target else (0, frames)
        if [first, end] != [lo, hi]:
            raise ValueError('A no-group inventory must cover the entire unindexed region')
        _text(spot, 'basis')
        return
    if spot.get('method') != 'encoded_geometry' or spot.get('axis') not in ('x', 'y', 'xy'):
        raise ValueError('Use encoded_geometry with the design axis x, y or xy')
    relation = spot.get('relation', 'center')
    if relation not in ('center', 'bottom') or relation == 'bottom' and spot['axis'] != 'y':
        raise ValueError('Use center on x/y/xy or bottom on y for a number and unit')
    visible = None
    if target and target.get('inventory'):
        name = _text(spot, 'group')
        cards = {}
        rule = component_relations(target['components'], card_windows=cards).get(name)
        if rule and rule != (relation, spot['axis']):
            raise ValueError(f'Measure {name} using {rule[0]} on {rule[1]}')
        names = name.split('/')
        if len(names) > 1 and cards.get(names[0]):
            visible = cards[names[0]].get(names[1])
    elif groups and spot.get('group') not in groups:
        raise ValueError(f'Identify one of the registered local groups: {groups}')
    samples = spot.get('samples')
    if not isinstance(samples, list) or not samples:
        raise ValueError('Measure one settled and one other visible frame where available')
    positions = []
    for row in samples:
        if not isinstance(row, dict):
            raise ValueError('Expected frame and two measured boxes per alignment sample')
        frame = row.get('frame')
        if type(frame) is not int or not first <= frame < end:
            raise ValueError('Alignment sample outside the observed frame interval')
        if visible and not visible[0] <= frame-target['start'] < visible[1]:
            raise ValueError('Measure the replacement row only in its own visible span')
        positions.append(frame)
        ink = _box(row, 'ink_box', size)
        ref = _box(row, 'reference_box', size)
        if relation == 'bottom' and min(ink[2], ref[2]) > max(ink[0], ref[0]):
            raise ValueError('Measure the separate visible numeral and adjacent unit as the two boxes')
    duration = (visible[1]-visible[0] if visible else (target['end']-target['start'] if target else frames))
    if positions != sorted(set(positions)) or len(positions) < min(2, duration):
        raise ValueError('Use distinct increasing sample frames within the shot')
    failed = deviations(spot, size)
    if failed and not spot.get('issue_id'):
        raise ValueError(f'Offset exceeds optical tolerance (frame,axis,delta,limit): {failed}; attach a repair ticket')


def missing(targets, rows):
    """A cut's generic row or card border cannot close its individual nested measurements."""
    required, rules, common_frames, visible_windows = {}, {}, {}, {}
    defects = []
    for t in targets:
        start = t['start']
        prefix = f"alignment:{t['shot']}@{start}"
        if t.get('inventory'):
            required[start, None] = prefix + '/inventory'
            duration = t['end']-start
            cards = {}
            declared = component_relations(t['components'], duration, card_windows=cards)
            for row in rows:
                spot = row['perception']
                if (spot.get('method') == 'frame_inventory'
                        and (row['start_frame'], row['end_frame']) == (start, t['end'])):
                    actual_cards = {}
                    declared.update(component_relations(spot.get('components'), duration, card_windows=actual_cards))
                    for card, windows in actual_cards.items():
                        if card in cards and cards[card] != windows:
                            if cards[card] is None or windows is None or any(
                                    row_id in windows and windows[row_id] != span for row_id, span in cards[card].items()):
                                defects.append(prefix + '/' + card + ':conflicting-visible-states')
                                continue
                            windows = {**cards[card], **windows}
                        cards[card] = windows
            rules.update({(start, key): rule for key, rule in declared.items()})
            for name in declared:
                if '/' in name:
                    card, row_id, *_ = name.split('/')
                    windows = cards[card]
                    visible_windows[start, name] = windows.get(row_id) if windows else None
                    # Simultaneous rows share one final picture. Replacement rows reuse the same slot at different times.
                    state = prefix + '/' + card + ('/' + row_id if windows else '')
                    common_frames.setdefault(state, {})[start, name] = set()
            for name in dict.fromkeys([*t['groups'], *declared]):
                required[start, name] = prefix + '/' + name
        else:
            for name in t['groups'] or ['']:
                required[start, name] = prefix + ('/' + name if name else '')
    if not targets:
        required[(0, '')] = 'alignment'
    card_bands = {}
    measured_frames = {key: frames for cells in common_frames.values() for key, frames in cells.items()}
    for row in rows:
        spot = row['perception']
        target = next((t for t in targets
                       if t['start'] <= row['start_frame'] < row['end_frame'] <= t['end']), None)
        if target and target.get('inventory'):
            group = None if spot.get('method') == 'frame_inventory' else spot.get('group')
        else:
            group = (spot.get('group') or '') if target and target['groups'] else ''
        key = (target['start'] if target else 0, group)
        expected = rules.get(key)
        if not expected or expected == (spot.get('relation', 'center'), spot.get('axis')):
            required.pop(key, None)
            if expected and '/' in group:
                card, row_id, *_ = group.split('/')
                for sample in spot['samples']:
                    frame = sample['frame']
                    window = visible_windows[key]
                    if window and not window[0] <= frame-key[0] < window[1]:
                        defects.append(f"alignment:{target['shot']}/{group}:outside-row-visible-span")
                        continue
                    measured_frames[key].add(frame)
                    ref = sample['reference_box']
                    bands = card_bands.setdefault((key[0], card, frame), {})
                    band = (ref[1], ref[3])
                    if any(min(band[1], other[1]) > max(band[0], other[0])
                           for rid, other in bands.items() if rid != row_id):
                        defects.append(f"alignment:{target['shot']}/{card}/{row_id}:use-local-row-edges")
                    bands[row_id] = band
        if spot.get('issue_id'):
            defects.append('alignment:fix/' + spot['issue_id'])
    for state, cells in common_frames.items():
        if all(cells.values()) and not set.intersection(*cells.values()):
            defects.append(state + ':sample-common-settled-frame')
    return list(required.values()) + sorted(set(defects))


def describe(spot):
    if spot['method'] == 'frame_inventory':
        return spot['basis'] + '。' + spot['playback']
    details = []
    for row in spot['samples']:
        ref, ink = row['reference_box'], row['ink_box']
        offsets = []
        for axis in spot['axis']:
            a = 'xy'.index(axis)
            label = '底边' if spot.get('relation') == 'bottom' else axis
            offsets.append(f"{label} {_delta(spot, ink, ref, a):+g} px")
        details.append(f"F{row['frame']} " + ' / '.join(offsets))
    return '，'.join(details) + '。' + spot['playback']
