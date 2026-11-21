"""Replay the fare-clock alignment miss against the shipping review gate.

Fixtures are unscaled 812×275 RGB crops at (32,610) from original 1080p
frames 58 and 68. The two released video SHA-256s, in before/after order:
1f1fe6cf3331639d5fd05969557920cad9c942ad14e31275fc4dabb3d70cb82d
7eb86655bc43c59853e7474cc8ca40f21a2efd5c1beecc09dca61dde59149d63
"""

from pathlib import Path

import numpy as np
from PIL import Image

from perception_review import missing_spots, validate_spot


CASES = Path(__file__).resolve().parents[1] / 'assets/review-cases/fare-row'
SIZE = [1920, 1080]
SCOPE = {'alignment': [{'shot': 'meter', 'start': 0, 'end': 76,
                        'groups': ['takings-label']}]}


def measure(version, frame):
    with Image.open(CASES / f'{version}-{frame}.png') as image:
        pixels = np.asarray(image.convert('RGB'))
    # Locate the broad paper surface, the divider in its right margin, then the dark glyphs.
    paper = pixels.min(axis=2) > 165
    vertical = np.flatnonzero(paper[:, 638])
    top, bottom = int(vertical[0]), int(vertical[-1]) + 1
    left = int(np.flatnonzero(paper[top + 16])[0])
    strip = pixels[top + 72, 713:].mean(axis=1)
    right = round(float(np.flatnonzero(strip < np.median(strip)-18).mean()) + 713)

    enclosed = pixels[top+12:bottom-12, left+8:right-8]
    ys, xs = np.nonzero(enclosed.max(axis=2) < 135)
    x, y = left+8, top+12
    ink = [x+int(xs.min()), y+int(ys.min()), x+int(xs.max())+1, y+int(ys.max())+1]

    def native(box):
        return [value + offset for value, offset in zip(box, (32,610,32,610))]

    return {'frame': frame, 'reference_box': native([left,top,right,bottom]), 'ink_box': native(ink)}


def observation(version):
    return {'shot': 'meter', 'start_frame': 0, 'end_frame': 76, 'perception': {
        'kind': 'alignment', 'method': 'encoded_geometry', 'group': 'takings-label',
        'subject': '票面左格的流水标签', 'axis': 'xy',
        'samples': [measure(version, frame) for frame in (58, 68)],
        'playback': '回放样本为出票落稳后的第58及68帧。'}}


def missing(observations):
    return missing_spots({'perception_checks': SCOPE, 'reviews': [{'observations': observations}]})


def main():
    old, revised = observation('before'), observation('after')
    if missing([]) != ['alignment:meter@0/takings-label']:
        raise RuntimeError('缺少此组对齐证据，签发检查却未报缺。')
    try:
        validate_spot(old, SCOPE, 1051, SIZE)
    except ValueError as error:
        if 'Offset exceeds optical tolerance' not in str(error):
            raise
    else:
        raise RuntimeError('旧片的实测偏移通过了校验。')
    validate_spot(revised, SCOPE, 1051, SIZE)
    if missing([revised]):
        raise RuntimeError('修订样本有效但仍缺少此组对齐证据。')
    for label, row in [('原版退改', old), ('修订版通过', revised)]:
        for sample in row['perception']['samples']:
            ink, ref = sample['ink_box'], sample['reference_box']
            dx, dy = [(ink[i]+ink[i+2]-ref[i]-ref[i+2])/2 for i in (0,1)]
            print(f"{label} F{sample['frame']} · 横向 {dx:+g} px，纵向 {dy:+g} px")
    print('遗漏此组测量时，签发仍列为缺项。')


if __name__ == '__main__':
    main()
