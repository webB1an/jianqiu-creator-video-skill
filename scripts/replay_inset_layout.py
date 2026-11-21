"""Replay seven 1080p plate, unit and table-cell measurements without rendering video."""
import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image, ImageFont

from alignment_review import deviations, scope as layout_scope
from optical_layout import draw_bottom_runs, draw_ink_plate, editable_row_boxes
from perception_review import missing_spots, project_scope, scope_for, validate_spot
from production_run import digest
from screen_plan import validate as validate_plan


SIZE = (1920, 1080)
PLATES = [{'id': name, 'kind': 'label_plate', 'axis': 'y'} for name in ('camera-title', 'camera-credit')]
DISTANCE = [{'id': 'distance', 'kind': 'number_unit'}]
DOCUMENT = [{'id': 'source', 'kind': 'source_image', 'source': 'Original prospectus excerpt'},
            {'id': 'terms', 'kind': 'editable_card', 'rows': [
                {'id': 'demand', 'cells': ['label', 'value']},
                {'id': 'commitment', 'cells': ['label', 'value']}]}]

# Half-open boxes. The rulings at y=495–496 and y=636–637 precede the two content rows.
GEOMETRY = [
    ('camera-title', [94,92,582,130], [65,72,736,167], -8.5),
    ('camera-credit', [92,854,578,878], [65,841,715,901], -5),
    ('distance', [1500,736,1538,776], [1238,676,1440,764], 12),
    ('terms/demand/label', [157,521,287,553], [150,497,385,636], -29.5),
    ('terms/demand/value', [433,514,843,558], [415,497,1060,636], -30.5),
    ('terms/commitment/label', [157,662,289,693], [150,638,385,793], -38),
    ('terms/commitment/value', [432,655,890,699], [415,638,1060,793], -38.5),
]


def geometry(group, ink, ref, first):
    return {'start_frame': first, 'end_frame': first+10, 'perception': {
        'kind': 'alignment', 'method': 'encoded_geometry', 'group': group, 'axis': 'y',
        'relation': 'bottom' if group == 'distance' else 'center',
        'subject': group, 'playback': 'Frozen native-size layer and its settled margins.',
        'samples': [{'frame': first+i, 'ink_box': list(ink), 'reference_box': list(ref)} for i in (2,6)]}}


def inventory(first, components):
    return {'start_frame': first, 'end_frame': first+10, 'perception': {
        'kind': 'alignment', 'method': 'frame_inventory', 'components': components,
        'subject': 'Visible source and added graphics', 'basis': 'Examined all visible shot states.',
        'playback': 'Read the actual frame including inserts and both in-camera plates.'}}


def main():
    # A camera cut without an overlay declaration still enters the ordinary editorial gate.
    cuts = [{'start': 0, 'end': 10, 'kind': 'video', 'subject': 'camera'},
            {'start': 10, 'end': 20, 'kind': 'photo', 'subject': 'map', 'alignment_components': DISTANCE},
            {'start': 20, 'end': 30, 'kind': 'document', 'subject': 'filing', 'alignment_components': DOCUMENT}]
    for cut in cuts:
        cut['action'] = 'Inspect the local content'
    plan = {'cuts': cuts, 'overlays': []}
    assert validate_plan(plan, {'frames': 30})['cuts'] == 3
    invalid = deepcopy(plan)
    invalid['cuts'][1]['alignment_components'][0]['kind'] = 'bogus_unit'
    try:
        validate_plan(invalid, {'frames': 30})
    except ValueError as error:
        assert 'bogus_unit' in str(error)
    else:
        raise AssertionError('Screen-plan preflight accepted an unknown layout kind')
    with TemporaryDirectory() as tmp:
        path = Path(tmp)/'edit.json'
        path.write_text('{}')
        plan_path = path.parent/'screen-plan.json'
        plan_path.write_text(json.dumps({'cuts': cuts, 'overlays': []}))
        scope = project_scope(path)
        plan_path.unlink()
        timeline_path = path.parent/'timeline.json'
        timeline_path.write_text(json.dumps({'frames': 30, 'shots': [
            {'id': c['subject'], 'start_frame': c['start'], 'frames': 10} for c in cuts]}))
        assert [t['shot'] for t in project_scope(path)['alignment']] == ['camera', 'map', 'filing']
        older = [{'shot': 'route', 'start': 10, 'end': 20, 'groups': ['legend']}]
        (path.parent/'bundle.json').write_text(json.dumps({'alignment_scope_version': 1,
            'timeline_sha256': digest(timeline_path), 'perception_checks': {'alignment': older},
            'rhythm_cuts': [{'start': c['start']} for c in cuts], 'viewing_windows': cuts}))
        recovered = scope_for(path.parent/'candidate.mp4')['alignment']
        assert [r['shot'] for r in recovered] == ['camera', 'route', 'filing']
        assert all(r['inventory'] for r in recovered) and recovered[1]['groups'] == ['legend']
        probe_fixture = path.parent/'published.stub'
        probe_fixture.touch()
        timeline_path.unlink()
        for stale_timeline in (False, True):
            if stale_timeline:
                timeline_path.write_text('{"frames":999}')
            with patch('perception_review.probe', return_value={'nb_frames': '30'}) as inspect:
                assert scope_for(probe_fixture)['alignment'] == recovered
                inspect.assert_called_once_with(probe_fixture, 'nb_frames')
    inv = [inventory(i*10, comps) for i, comps in enumerate([PLATES, DISTANCE, DOCUMENT])]
    def validate(row):
        validate_spot(row, scope, 30, SIZE)
    def missing(rows):
        return missing_spots({'perception_checks': scope, 'reviews': [{'observations': rows}]})
    for row in inv:
        validate(row)
    assert len(missing(inv)) == 7
    good = []
    for group, ink, ref, expected in GEOMETRY:
        first = 20 if '/' in group else (10 if group == 'distance' else 0)
        old = geometry(group, ink, ref, first)
        delta = deviations(old['perception'], SIZE)[0][2]
        assert delta == expected, (group, delta, expected)
        try:
            validate(old)
        except ValueError as error:
            assert 'Offset exceeds optical tolerance' in str(error), error
        else:
            raise AssertionError(f'Old offset accepted for {group}')
        shift = round(-delta)
        fixed = geometry(group, [ink[0], ink[1]+shift, ink[2], ink[3]+shift], ref, first)
        validate(fixed)
        good.append(fixed)
        print(f'{group}: old {delta:+g} px; aligned {delta+shift:+g} px')
    assert not missing(inv+good)
    assert missing(inv+good[:-1]) == ['alignment:filing@20/terms/commitment/value']

    # A whole-card reference repeated for different rows must fail even if the reported centers agree.
    for row in good[-4:]:
        for sample in row['perception']['samples']:
            sample['ink_box'] = [200,595,235,625]
            sample['reference_box'] = [115,429,1085,791]
    assert any('use-local-row-edges' in item for item in missing(inv+good))
    assert all('/source' not in item for item in missing(inv))

    for row in good[-4:]:
        first = 21 if '/demand/' in row['perception']['group'] else 27
        for i, sample in enumerate(row['perception']['samples']):
            sample['frame'] = first+i
    assert any('sample-common-settled-frame' in item for item in missing(inv+good))

    replacement = deepcopy(DOCUMENT)
    card = replacement[1]
    card['display'] = 'sequence'
    for row, span in zip(card['rows'], [[0,5], [5,10]]):
        row['visible'] = span
    seq_plan = deepcopy(plan)
    seq_plan['cuts'][2]['alignment_components'] = replacement
    assert validate_plan(seq_plan, {'frames': 30})['cuts'] == 3
    seq_scope = {'alignment': layout_scope(seq_plan)}
    seq_inv = [*inv[:2], inventory(20, replacement)]
    for item in seq_inv+good:
        validate_spot(item, seq_scope, 30, SIZE)
    assert not missing_spots({'perception_checks': seq_scope, 'reviews': [{'observations': seq_inv+good}]})
    bad_row = deepcopy(good[-1])
    bad_row['perception']['samples'][0]['frame'] = 23
    try:
        validate_spot(bad_row, seq_scope, 30, SIZE)
    except ValueError as error:
        assert 'visible span' in str(error)
    else:
        raise AssertionError('Accepted a replacement row sample from a different state')
    try:
        validate(seq_inv[-1])
    except ValueError as error:
        assert 'simultaneous or sequential' in str(error)
    else:
        raise AssertionError('A static plan was silently relabeled as a carousel')

    canvas = Image.new('RGB', (220,140), 'white')
    large = ImageFont.load_default(size=30)
    small = ImageFont.load_default(size=14)
    plate, ink = draw_ink_plate(canvas, (3,4), 'NASA / JPL', small, 'white', 'black', (8,6))
    assert (ink[0]-plate[0], ink[1]-plate[1]) == (plate[2]-ink[2], plate[3]-ink[3]) == (8,6)
    units = draw_bottom_runs(canvas, 7, 90, [('246', large, 'black'), ('m', small, 'black')], 8)
    assert units[0][3] == units[1][3] == 90
    assert editable_row_boxes((4,11,180,90), [49]) == [(4,11,180,49), (4,49,180,90)]
    assert editable_row_boxes((113,497,1090,793), [(636,638)]) == [
        (113,497,1090,636), (113,638,1090,793)]
    print('All seven old offsets rejected; fixed geometry passed, omitted and whole-card rows detected.')


if __name__ == '__main__':
    main()
