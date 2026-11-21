"""Check a cut plan and the authored overlay track before encoding a master."""
import argparse
import json
from pathlib import Path
import re
from alignment_review import scope as alignment_scope

PURPOSES={'identity','archive_date','measurement','evidence','time','rights'}
PRODUCTION_NOTE=re.compile(r'键盘为输入示意|键盘输入示意|打字动作示意|翻阅文件为示意画面|分类由签名人填写|本镜头为|此处素材|示意画面[／/]|[／/]\s*打字动作示意')

def viewing_windows(plan):
    """Carry the editor's intended observation intervals from local to master frames."""
    windows = []
    for cut in plan.get('cuts', []):
        if 'watch' not in cut:
            continue
        first, last = cut['start'], cut['end']
        if type(first) is not int or type(last) is not int or not 0 <= first < last:
            raise ValueError('An observed cut must have valid start and end frames')
        span = cut['watch']
        if (not isinstance(span, list) or len(span) != 2 or any(type(n) is not int for n in span)
                or not (0 <= span[0] < span[1] <= last - first)):
            raise ValueError(f'watch must be two frame offsets inside the cut beginning at {first}')
        windows.append(dict(start=first + span[0], end=first + span[1],
                            subject=cut['subject'], discovery=cut.get('discovery') or cut['action']))
    return windows


def validate(plan, timeline):
    total=timeline['frames']
    cursor=0
    for cut in plan['cuts']:
        first,last=cut['start'],cut['end']
        if type(first) is not int or type(last) is not int or first!=cursor or not first<last<=total:
            raise ValueError(f'Cuts must cover the timeline once: {first}, {last} after {cursor}')
        if not cut['subject'].strip() or not cut['action'].strip():
            raise ValueError(f'Identify the visible subject and action at frame {first}')
        for field in ('discovery', 'eye_trace', 'section'):
            if field in cut and (not isinstance(cut[field], str) or not cut[field].strip()):
                raise ValueError(f'Give a visible {field} at frame {first}, or omit this optional field')
        if 'cue_frame' in cut and (type(cut['cue_frame']) is not int or not first<=cut['cue_frame']<last):
            raise ValueError(f'Sound cue outside its visible action at frame {first}')
        cursor=last
    if cursor!=total:
        raise ValueError(f'Cut list ends at frame {cursor}; master has {total}')
    for overlay in plan['overlays']:
        first,last=overlay['start'],overlay['end']
        if type(first) is not int or type(last) is not int or not 0<=first<last<=total:
            raise ValueError(f'Overlay outside timeline at {first}, {last}')
        text=overlay['text'].strip()
        if not text or overlay['purpose'] not in PURPOSES or not overlay['reason'].strip():
            raise ValueError(f'Each authored overlay needs a specific audience use: {text}')
        if PRODUCTION_NOTE.search(text):
            raise ValueError(f'Move the production note from the picture to credits.md: {text}')
    viewing_windows(plan)
    alignment_scope(plan)
    if any('motion_probes' in cut for cut in plan['cuts']):
        from component_motion import probes_from_plan
        probes_from_plan(plan, total)
    return {'cuts':len(plan['cuts']), 'authored_overlays':len(plan['overlays']), 'frames':total}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('plan',type=Path)
    p.add_argument('--timeline',type=Path,help='Validate against a prepared timeline (default: timeline.json)')
    p.add_argument('--roughcut',type=Path,metavar='NEW_DIR',help='Render a source-footage assembly from the plan')
    p.add_argument('--audio',type=Path,metavar='WAV',help='Overlay the same aligned WAV, relative to the plan, on all rough-cut versions')
    choices=p.add_mutually_exclusive_group()
    choices.add_argument('--alternate',nargs='+',metavar='CUT_ID',help='Compare a different ordering of the same cuts')
    choices.add_argument('--shift-cut',nargs=2,metavar=('INCOMING_ID','FRAMES'),help='With --audio, move one cut later (+) or earlier (-) at fixed program timing')
    p.add_argument('--project',type=Path,help='Freeze this project’s selected shot decisions into both rough-cut versions')
    a=p.parse_args()
    if a.roughcut:
        from picture_roughcut import render
        if a.timeline:
            p.error('--roughcut uses the timing in the plan; omit --timeline')
        try:
            shift=(a.shift_cut[0], int(a.shift_cut[1])) if a.shift_cut else None
            result=render(a.plan, a.roughcut, a.alternate, validate, project=a.project, audio=a.audio, shift_cut=shift)
        except (ValueError, OSError, KeyError) as err:
            p.error(str(err))
    else:
        if a.alternate or a.shift_cut or a.audio or a.project is not None:
            p.error('--audio, --alternate, --shift-cut and --project require --roughcut')
        timeline=a.timeline or Path('timeline.json')
        result=validate(json.loads(a.plan.read_text()),json.loads(timeline.read_text()))
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    main()
