"""Bind generated visual plates and their on-screen project media to the export."""
import json
import os
from pathlib import Path

from production_run import digest

# The registered-clip decoder uses the same source clock, upright raster and colour
# interpretation as production. Bind all local Python code in that import chain.
CLIP_ENGINE = tuple(f'scripts/{name}.py' for name in (
    'explainer_clip', 'explainer_scene_receipt', 'action_takes', 'picture_roughcut',
    'production_run', 'source_framing', 'color_finish',
))


def load(shot, timing, fps, read_path=Path, project=None):
    project = Path.cwd() if project is None else Path(project)
    path = project / (shot['visual'] + '.scene.json')
    try:
        receipt = json.loads(read_path(path).read_text())
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError(f"{shot['id']}: generate the explainer scene from the prepared timeline") from exc
    values = (receipt.get('shot'), receipt.get('start_frame'), receipt.get('frames'), receipt.get('fps'))
    if (receipt.get('schema') != 1 or values != (shot['id'], timing['start_frame'], timing['frames'], fps)
            or not {'edit.json','timeline.json'} <= receipt.get('inputs', {}).keys()
            or 'scripts/explainer_scene.cjs' not in receipt.get('engine', {})):
        raise ValueError(f"{shot['id']}: scene receipt differs from this shot; rebuild its visual")
    clips = shot.get('explainer_scene', {}).get('clips', {})
    files = {name: spec['file'] for name, spec in clips.items() if spec.get('file')}
    if files and (not set(CLIP_ENGINE) <= receipt['engine'].keys()
                  or not all(receipt.get('decoded_clips', {}).get(name, {}).get('file') == file
                             and os.path.relpath(project / file, project) in receipt['inputs']
                             for name, file in files.items())):
        raise ValueError(f"{shot['id']}: rebuild its visual to bind the clip clock and colour conversion")
    return receipt


def verify(shot, timing, fps, pins):
    root = Path.cwd()
    engine_root = Path(__file__).resolve().parents[1]
    receipt = load(shot, timing, fps, pins.pin)
    pins.expect(shot['visual'], receipt['output_sha256'])
    for folder, sources in ((root, receipt['inputs']), (engine_root, receipt['engine'])):
        for name, checksum in sources.items():
            path = folder / name
            if Path(name).is_absolute() or not path.resolve().is_relative_to(folder):
                raise ValueError(f"Scene dependency leaves its project: {name}")
            key = pins.key(path)
            if key not in pins.hashes and (not path.is_file() or digest(path) != checksum):
                raise ValueError(f"{shot['id']}: {name} changed; render its explainer scene again")
            pins.expect(path, checksum)


def sources(shot, timing, fps, project, read_path):
    receipt = load(shot, timing, fps, read_path, project)
    for row in receipt['used']:
        a, b = row['frames']
        if row['file'] not in receipt['inputs'] or not 0 <= a < b <= timing['frames']:
            raise ValueError('Invalid source or frame span in the generated scene receipt')
        yield dict(file=row['file'], role='picture', shot=shot['id'], range=row['range'],
                   frames=[a+timing['start_frame'], b+timing['start_frame']])
