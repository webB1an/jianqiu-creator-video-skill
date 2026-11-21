"""Bind library-rendered visuals and their project source clocks to the final export."""
import json
from pathlib import Path
import re
import shlex

from production_run import digest
from remotion_library import LIBRARY, SKILL, catalog, engine_files, resolve_frame, safe_path
from remotion_source_usage import media_usage


def read_receipt(shot, timing, edit, timeline, project, read_path, pins=None):
    project = Path(project).resolve()
    try:
        visual = safe_path(project, shot['visual'])
        receipt = json.loads(read_path(Path(str(visual) + '.remotion.json')).read_text())
        settings = shot['remotion_effect']
        entry = catalog()[settings['effect']]
        if shot.get('in') or 'explainer_scene' in shot:
            raise ValueError('The effect requires its own shot visual starting at frame zero')
        expected = dict(schema=1, shot=shot['id'], start_frame=timing['start_frame'],
                        frames=timing['frames'], fps=timeline['fps'])
        if (not isinstance(receipt, dict) or any(receipt.get(k) != v for k, v in expected.items())
                or receipt.get('effect', entry['id']) != entry['id']):
            raise ValueError('Receipt does not match this effect or the prepared timeline')

        params = dict(settings.get('params', {}))
        for key, anchor in settings.get('cues', {}).items():
            params[key] = resolve_frame(anchor, timeline, timing)
        spec = dict(params=params, width=edit.get('size', [1920, 1080])[0],
                    frames=timing['frames'], fps=timeline['fps'])
        engine = set(engine_files(entry))
        inputs = {'edit.json', 'timeline.json'}
        used = []
        media = [(key, params.get(key)) for key in entry.get('media', [])]
        for key, source in [*media, (None, settings.get('background'))]:
            if not source:
                continue
            if source.startswith('@library/'):
                engine.add(str(safe_path(LIBRARY, source[9:]).relative_to(SKILL)))
            else:
                name = str(safe_path(project, source).relative_to(project))
                inputs.add(name)
                usage = media_usage(spec, entry, key, name)
                if usage:
                    used.append(usage)
        if not inputs <= receipt['inputs'].keys() or not engine <= receipt['engine'].keys():
            raise ValueError('Receipt is missing a project source or renderer dependency')
        if receipt.get('used') != used:
            raise ValueError('Receipt source windows differ from the effect settings')

        def check(path, checksum):
            if not isinstance(checksum, str) or not re.fullmatch(r'[0-9a-f]{64}', checksum):
                raise ValueError(f'Invalid digest for {path}')
            pinned = pins.hashes.get(pins.key(path)) if pins is not None else None
            if (pinned if pinned is not None else digest(path)) != checksum:
                raise ValueError(f'{path.name} changed after this visual was generated')
            if pins is not None:
                pins.expect(path, checksum)

        if pins is not None:
            pins.pin(visual)
        check(visual, receipt['output_sha256'])
        for folder, table in ((project, receipt['inputs']), (SKILL, receipt['engine'])):
            for name, checksum in table.items():
                if Path(name).is_absolute():
                    raise ValueError(f'Receipt dependencies must be relative: {name}')
                check(safe_path(folder, name), checksum)
        return receipt
    except (OSError, ValueError, KeyError, AttributeError, TypeError) as exc:
        command = shlex.join(['python3', str(SKILL/'scripts/remotion_library.py'), 'render',
                              '--project', str(project), '--shot', shot['id']])
        raise ValueError(f"{shot['id']}: rebuild its Remotion visual with {command}\n{exc}") from exc


def sources(receipt, shot, timing):
    for row in receipt['used']:
        yield dict(file=row['file'], role='picture', shot=shot['id'], range=row['range'],
                   frames=[timing['start_frame'] + frame for frame in row['frames']])
