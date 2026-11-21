"""Read a prepared programme's actual scene moves and compare their order with a previous film."""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess

from production_run import digest


def shared_order(first, second):
    """Longest common subsequence, preserving order even when a technique recurs."""
    previous=[0]*(len(second)+1)
    for a in first:
        current=[0]
        for j,b in enumerate(second):
            current.append(previous[j]+1 if a==b else max(current[-1],previous[j+1]))
        previous=current
    return previous[-1]


def presenter_staging(rows, fps):
    states=('room','pip','window','off','unannotated')
    counts=dict.fromkeys(states,0)
    runs=[]
    for row in rows:
        cursor=0
        for run in row['presenter_runs']:
            state=run['state']
            start,end=run['start_frame'],run['end_frame']
            if state not in counts or start!=cursor or not start<end<=row['frames']:
                raise ValueError(f"Invalid presenter staging in {row['shot']}")
            cursor=end
            counts[state]+=end-start
            begin=row['start_frame']+start
            finish=row['start_frame']+end
            if runs and runs[-1]['state']==state and runs[-1]['end_frame']==begin:
                runs[-1].update(end_frame=finish,last_shot=row['shot'])
            else:
                runs.append(dict(state=state,start_frame=begin,end_frame=finish,
                                 first_shot=row['shot'],last_shot=row['shot']))
        if cursor!=row['frames']:
            raise ValueError(f"Incomplete presenter staging in {row['shot']}")
    for run in runs:
        run['frames']=run['end_frame']-run['start_frame']
        run['seconds']=round(run['frames']/fps,6)
    return dict(basis='scene_composition',end_frame_exclusive=True,
                by_state={s:dict(frames=n,seconds=round(n/fps,6)) for s,n in counts.items()},
                runs=runs,off_screen_runs=[r for r in runs if r['state']=='off'])


def direction(project, previous=None):
    edit_path=project/'edit.json'
    edit=json.loads(edit_path.read_text())
    tl=json.loads((project/'timeline.json').read_text())
    if tl.get('edit_sha256')!=digest(edit_path):raise ValueError('Prepare the current edit before comparing scenes')
    if [s['id'] for s in edit['shots']]!=[s['id'] for s in tl['shots']]:raise ValueError('Prepared shot ids differ')
    renderer=Path(__file__).with_name('explainer_scene.cjs')
    rows=[];sequence=[];missing=[]
    for shot,timing in zip(edit['shots'],tl['shots']):
        sid=shot['id'];base=timing['start_frame'];operations=[]
        trans=shot.get('transition','cut')
        name=trans if isinstance(trans,str) else trans.get('name','cut')
        if name!='cut':operations.append(dict(frame=base,move=name,why=shot.get('transition_reason','')))
        for field in ('layer_camera','evidence_window','value_comparison'):
            if shot.get(field):
                n=shot[field]['open'][0] if field=='evidence_window' else 0
                operations.append(dict(frame=base+n,move=field,why=shot.get('visual_reason','')))
        spans=[]
        presenter_runs=[dict(state='unannotated',start_frame=0,end_frame=timing['frames'])]
        if shot.get('explainer_scene'):
            output=subprocess.check_output(['node',str(renderer),'--project',str(project),'--shot',sid,'--moves'],text=True)
            report=json.loads(output)
            spans=report['shots']
            presenter_runs=report['presenter_runs']
            for m in report['moves']:
                operations.append(dict(frame=base+m['frame'],move=m['move'],why=m.get('why','')))
        operations.sort(key=lambda m:m['frame'])
        sequence.extend(m['move'] for m in operations)
        if not operations and spans:sequence.extend(s['scene'] for s in spans)
        for m in operations:
            if not m['why']:missing.append([sid,m['frame'],m['move']])
        rows.append(dict(shot=sid,start_frame=base,frames=timing['frames'],scenes=spans,
                         moves=operations,presenter_runs=presenter_runs))
    repeated=[]
    for a,b in zip(rows,rows[1:]):
        common={m['move'] for m in a['moves']} & {m['move'] for m in b['moves']}
        if common:repeated.append(dict(shots=[a['shot'],b['shot']],moves=sorted(common)))
    result=dict(fps=tl['fps'],seq=sequence,shots=rows,counts=dict(Counter(sequence)),
                repeated_on_adjacent_shots=repeated,without_stated_reason=missing,
                presenter_staging=presenter_staging(rows,tl['fps']))
    if previous is not None:
        old=previous.get('seq')
        if not isinstance(old,list) or not all(isinstance(s,str) for s in old):raise ValueError('Pass a previous direction report')
        length=min(len(sequence),len(old));match=shared_order(sequence,old)
        result['previous_comparison']=dict(shared_in_order=match,shorter_sequence=length,
                                           fraction=round(match/length,3) if length else None)
    return result


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--project',type=Path,default=Path('.'))
    cli.add_argument('--previous',type=Path,help='A previous complete programme direction report')
    cli.add_argument('--out',type=Path,help='New JSON file; default stdout')
    args=cli.parse_args()
    try:
        previous=json.loads(args.previous.read_text()) if args.previous else None
        report=json.dumps(direction(args.project,previous),ensure_ascii=False,indent=2)
        if args.out:
            args.out.parent.mkdir(parents=True,exist_ok=True)
            with args.out.open('x',encoding='utf-8') as f:f.write(report+'\n')
        else:print(report)
    except (OSError,KeyError,ValueError,subprocess.CalledProcessError) as e:cli.exit(1,str(e)+'\n')
