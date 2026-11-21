"""Measure internal caption breaks from word timestamps in the actual recorded takes."""
import argparse
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import subprocess
import wave

import numpy as np
import sherpa_onnx as so
from captioning import clean_parts, parts


def normalized(text):
    return ''.join(re.findall('[一-龥a-z0-9]+',text.lower()))


def recognize(engine, path):
    audio=np.frombuffer(subprocess.check_output(['ffmpeg','-v','error','-i',path,'-ar','16000','-ac','1','-f','f32le','-']),'<f4')
    stream=engine.create_stream()
    stream.accept_waveform(16000,audio)
    engine.decode_stream(stream)
    res=stream.result
    observed=[]
    times=[]
    for token, time in zip(res.tokens,res.timestamps):
        if token.startswith('<|'):
            continue
        chars=normalized(token)
        observed.extend(chars)
        times.extend([time]*len(chars))
    return ''.join(observed), times


def boundaries(units, observed, times):
    chars=[normalized(u) for u in units]
    expected=''.join(chars)
    compare=SequenceMatcher(None,expected,observed,autojunk=False)
    score=compare.ratio()
    if score < .78:
        return [], score
    index={}
    for a,b,length in compare.get_matching_blocks():
        for i in range(length):index[a+i]=b+i
    proposed=[]
    for boundary in np.cumsum([len(u) for u in chars])[:-1]:
        if boundary not in index:
            return [], score
        proposed.append(round(max(0,times[index[boundary]]-.025),3))
    return proposed, score


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--model',type=Path,default=Path.home()/'.cache/creator-video-models/sensevoice')
    ap.add_argument('--write',action='store_true',help='Set measured caption_offsets in edit.json. Run prepare again afterward.')
    args=ap.parse_args()
    engine=so.OfflineRecognizer.from_sense_voice(model=str(args.model/'model.int8.onnx'),tokens=str(args.model/'tokens.txt'),language='zh',num_threads=2,use_itn=False)
    edit=json.loads(Path('edit.json').read_text())
    timeline=json.loads(Path('timeline.json').read_text())
    from roughcut_adoption import narration_shots
    rows=[(s,l) for s in narration_shots(edit) for l in s['lines']]
    report=[]
    for (shot,line),record in zip(rows,timeline['voice_takes'],strict=True):
        if record['shot']!=shot['id']:
            raise ValueError('Voice takes and shots differ. Run prepare first.')
        units=record.get('caption_units') or parts(line)
        spoken=line.get('spoken',line['text'])
        spoken_units=line.get('spoken_parts') or (units if spoken==line['text'] else clean_parts(spoken))
        observed,times=recognize(engine,record['file'])
        offsets,score=boundaries(spoken_units,observed,times)
        with wave.open(record['file']) as take:
            seconds=take.getnframes()/take.getframerate()
        timespan=[0,*offsets,seconds]
        accepted=(len(units)==len(spoken_units) and len(offsets)==len(units)-1 and score>=.78 and
                  all(b-a>=.43 for a,b in zip(timespan,timespan[1:])))
        if accepted and args.write and 'caption_offsets' not in line:
            line['caption_parts']=units
            line['caption_offsets']=offsets
        report.append(dict(shot=shot['id'],text=line['text'],recognized=observed,similarity=round(score,3),accepted=accepted,offsets=offsets))
    Path('qa').mkdir(exist_ok=True)
    Path('qa/caption-alignment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    if args.write:
        Path('edit.json').write_text(json.dumps(edit,ensure_ascii=False,indent=2)+'\n')
    print(f'{sum(r["accepted"] for r in report)}/{len(report)} aligned lines')


if __name__=='__main__':
    main()
