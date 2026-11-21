"""Build narration, picture timing and SRT from edit.json in the working directory."""
import argparse
from collections import Counter
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path.home()/'.cache/creator-video-models/melo'
RATE = 48000

def load_melo():
    import sherpa_onnx as so
    model = so.OfflineTtsVitsModelConfig(model=str(ROOT/'model.int8.onnx'),lexicon=str(ROOT/'lexicon.txt'),tokens=str(ROOT/'tokens.txt'))
    config = so.OfflineTtsConfig(model=so.OfflineTtsModelConfig(vits=model,num_threads=3))
    if not config.validate():
        raise RuntimeError('Install the pinned Melo model with setup_model.py')
    return so.OfflineTts(config)

def wav(path, samples, rate):
    import numpy as np
    with wave.open(str(path),'wb') as writer:
        writer.setparams((1,2,rate,0,'NONE','not compressed'))
        writer.writeframes(np.clip(samples*32767,-32768,32767).astype('<i2').tobytes())

def write_generated(path, voice):
    import numpy as np
    samples = np.asarray(voice.samples)
    if not samples.size or not np.isfinite(samples).all() or np.max(np.abs(samples)) < .004:
        raise ValueError('Empty or invalid generated narration')
    wav(path, samples, voice.sample_rate)

def stamp(sample):
    ms = round(sample*1000/RATE)
    return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'

def prepare(edit, pins, staging):
    import numpy as np
    from captioning import MIN_CUE_SECONDS
    from jianqiu_voice import JianqiuVoice, VOICE_ID
    from narration_pace import directed_pace, jianqiu_speed, narration_pace
    from speech_clock import timed_captions
    from voice_endings import EndingChecker, checked_take

    from pronunciation_review import with_context_directions
    edit = with_context_directions(edit)
    pace = narration_pace(edit)
    w,h=edit.get('size',[1920,1080])
    if min(w,h)<=0 or w*9!=h*16:raise ValueError('Jianqiu video masters require a 16:9 frame')
    fps=edit.get('fps',30)
    if not isinstance(fps,int) or fps<=0 or RATE%fps:
        raise ValueError('Use a positive integer fps dividing 48000, e.g. 24, 25, 30, 50, 60')
    folder=Path('audio/lines')
    folder.mkdir(parents=True,exist_ok=True)
    Path('out').mkdir(exist_ok=True)
    presenter=edit.get('presenter') or {}
    if isinstance(presenter,dict) and presenter.get('recording'):
        from presenter_source import prepare_performance
        return finish_prepare(staging,pins,fps,*prepare_performance(edit,pins,fps,stamp))
    cursor=0
    pieces=[]
    shots=[]
    captions=[]
    jianqiu=None
    directed_voice=None
    melo=None
    voice_takes=[]
    beats=[]
    ending_checker=EndingChecker()
    prior_checks={}
    if Path('timeline.json').is_file():
        try:
            prior_takes=json.loads(Path('timeline.json').read_text()).get('voice_takes', [])
            for old in prior_takes:
                check=old.get('ending_check', {})
                if check.get('attempts'):
                    prior_checks[check['attempts'][0]['file']]=check
        except (ValueError, KeyError, TypeError, AttributeError):
            prior_checks={}
    def pause(seconds):
        nonlocal cursor
        size=round(seconds*RATE)
        if size<0: raise ValueError('Negative pause')
        pieces.append(np.zeros(size,dtype='<i2'))
        cursor+=size
    for shot in edit['shots']:
        start=cursor
        pause(shot.get('lead',0.08))
        for index,line in enumerate(shot['lines']):
            words=line.get('spoken',line['text'])
            if 'source_audio' in line:
                if 'audio' in line:raise ValueError('Choose one source for this line of original sound')
                from presenter_source import source_bite
                path,bite=source_bite(line['source_audio'],pins)
                take={'voice':'recording','source_clip':bite}
            elif line.get('pronunciation') and 'audio' not in line:
                from pronunciation_takes import DirectedVoice
                if line.get('voice',edit.get('voice',VOICE_ID)) != VOICE_ID:
                    raise ValueError('Explicit ZipVoice phonemes require the Jianqiu voice.')
                directed_voice=directed_voice or DirectedVoice(edit,Path('audio/pronunciation'))
                speed=jianqiu_speed(edit,line,pace,directed_voice.voice.profile['speed'])
                path,record=directed_voice.get(directed_pace(line,edit,pace,speed),speed,ending_checker,write_generated)
                take={'voice':VOICE_ID,'speed':record['effective_speed'],'pronunciation':record,
                      'reference_sha256':directed_voice.voice.profile['prompt_sha256']}
            elif 'audio' in line:
                path=Path(line['audio'])
                take={'voice':'recording'}
            else:
                selected=line.get('voice',edit.get('voice',VOICE_ID))
                if selected==VOICE_ID:
                    jianqiu=jianqiu or JianqiuVoice()
                    signature=jianqiu.signature
                    default_speed=jianqiu_speed(edit,line,pace,jianqiu.profile['speed'])
                elif selected=='melo':
                    signature=['melo-int8-v1']
                    default_speed=1.18
                else:
                    raise ValueError(f'Unknown voice {selected!r}; use {VOICE_ID}, melo or line.audio')
                speed=float(line.get('speed',edit.get('speed',default_speed)))
                key=hashlib.sha256(json.dumps([*signature,speed,words],ensure_ascii=False).encode()).hexdigest()[:24]
                path=folder/(key+'.wav')
                take={'voice':selected,'speed':speed}
                if selected==VOICE_ID:
                    take['reference_sha256']=jianqiu.profile['prompt_sha256']
                    path,take['ending_check']=checked_take(path, words, speed, jianqiu, ending_checker,
                                                          write_generated, prior_checks.get(path.name))
                elif not path.exists():
                    melo=melo or load_melo()
                    voice=melo.generate(words,sid=0,speed=speed)
                    with tempfile.NamedTemporaryFile(dir=folder,suffix='.wav',delete=False) as tmp:
                        part=Path(tmp.name)
                    try:
                        write_generated(part,voice)
                        os.replace(part,path)
                    finally:
                        part.unlink(missing_ok=True)
            pinned = pins.pin(path)
            if 'ending_check' not in take:
                take['ending_check']=ending_checker.inspect_file(pinned, words)
            elif take['ending_check']['sha256'] != pins.hashes[pins.key(path)]:
                raise ValueError(f'Narration changed during its ending check: {path}')
            if take['ending_check']['status']=='possible_missing_ending':
                print(f"句尾待听辨 · {shot['id']} · {words} · {path}", file=sys.stderr)
            raw=subprocess.check_output(['ffmpeg','-nostdin','-v','error','-i',str(pinned),'-ar',str(RATE),'-ac','1','-f','s16le','-'])
            arr=np.frombuffer(raw,'<i2')
            if not len(arr) or np.max(np.abs(arr.astype('i4')))<128:
                raise ValueError('Empty or silent narration: '+words)
            end=cursor+len(arr)
            beat=None
            cue_clock = shot.get('remotion_effect', {}).get('cues')
            if edit.get('word_clock') or 'word_times' in line or cue_clock or (shot.get('explainer_scene') and line.get('id')):
                from speech_clock import take_beat
                beat=take_beat(shot['id'],line,index,arr,RATE,cursor)
                beats.append(beat)
            voice_takes.append(dict(shot=shot['id'],file=str(path),sha256=pins.hashes[pins.key(path)],
                                    spoken=words,start_sample=cursor,end_sample=end,sample_rate=RATE,**take))
            units,cuts=timed_captions(line,arr,RATE,beat=beat,origin=cursor)
            voice_takes[-1]['caption_units']=units
            for a,b,unit in zip(cuts,cuts[1:],units):
                if b-a < MIN_CUE_SECONDS * RATE:
                    print(f"字幕停留 {(b-a)/RATE:.2f} 秒 · {shot['id']} · {unit}", file=sys.stderr)
            for start_sample,end_sample,unit in zip(cuts,cuts[1:],units):
                captions.append(f"{len(captions)+1}\n{stamp(cursor+start_sample)} --> {stamp(cursor+end_sample)}\n{unit}\n")
            pieces.append(arr);cursor=end
            pause(line.get('pause',0.09))
        pause(shot.get('tail',0.18))
        if cursor<=start: raise ValueError('Empty shot')
        quantum=RATE//fps
        padding=(-cursor)%quantum
        pieces.append(np.zeros(padding,dtype='<i2'));cursor+=padding
        shots.append(dict(id=shot['id'],start_frame=start//quantum,frames=(cursor-start)//quantum))
    return finish_prepare(staging,pins,fps,pieces,cursor,shots,captions,voice_takes,beats)


def finish_prepare(staging,pins,fps,pieces,cursor,shots,captions,voice_takes,beats,presenter_source=None):
    from production_run import digest
    if not shots or len(set(s['id'] for s in shots))!=len(shots): raise ValueError('Use unique shot IDs')
    if len({b['id'] for b in beats})!=len(beats): raise ValueError('Use unique spoken line ids across the edit')
    (staging/'audio').mkdir(parents=True)
    with wave.open(str(staging/'audio/narration.wav'),'wb') as writer:
        writer.setparams((1,2,RATE,0,'NONE','not compressed'))
        for chunk in pieces: writer.writeframesraw(chunk.tobytes())
    (staging/'audio/captions.srt').write_text('\n'.join(captions))
    prepared_files = {name: digest(staging/name) for name in ('audio/narration.wav', 'audio/captions.srt')}
    timeline = dict(edit_sha256=pins.hashes['edit.json'], claim_surfaces_version=1, fps=fps, frames=round(cursor*fps/RATE),
                    shots=shots, voice_takes=voice_takes, prepared_files=prepared_files)
    from pronunciation_review import summary as pronunciation_summary
    timeline['pronunciation_review'] = pronunciation_summary(voice_takes)
    if beats:timeline['beats']=beats
    if presenter_source:timeline['presenter_source']=presenter_source
    (staging/'timeline.json').write_text(json.dumps(timeline,indent=2))
    return timeline


def read_script(edit, line_ids):
    """Print the author's actual spoken lines, leaving editorial metadata off the blind copy."""
    printed = False
    missing = []
    presenter = edit.get('presenter')
    recorded_programme = isinstance(presenter, dict) and bool(presenter.get('recording'))
    for shot in edit['shots']:
        shot_id = shot['id']
        if 'lines' not in shot:
            missing.append((shot_id, '缺少实录逐字稿' if recorded_programme else '镜头未登记台词'))
            continue
        for index, line in enumerate(shot['lines'], start=1):
            words = line.get('spoken', line.get('text'))
            label = f"{shot_id} / {line['id']}" if 'id' in line else f"{shot_id}.L{index}"
            if not isinstance(words, str) or not words.strip():
                recorded = recorded_programme or 'source_audio' in line or 'audio' in line
                missing.append((label, '缺少实录逐字稿' if recorded else '缺少台词'))
                continue
            print(f'[{label}] {words}' if line_ids else words)
            printed = True
    if missing:
        if line_ids:
            for label, reason in missing:
                print(f'[{label}] {reason}', file=sys.stderr)
        else:
            for reason, count in Counter(reason for _, reason in missing).items():
                print(f'{reason}（{count} 处）。', file=sys.stderr)
        return 1
    if not printed:
        if recorded_programme:
            print('工程中有连续录音，尚未填写实录逐字稿。', file=sys.stderr)
            return 1
        print('本工程没有台词。', file=sys.stderr)
    return 0


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    mode = cli.add_mutually_exclusive_group()
    mode.add_argument('--read-script', action='store_true',
                      help='Print only the scripted spoken words from edit.json, without audio preparation')
    mode.add_argument('--new-edit', choices=('news', 'explainer'),
                      help='Create a new edit.json with the current spoken pace, without generating media')
    cli.add_argument('--line-ids', action='store_true', help='With --read-script, label each line for revision')
    args = cli.parse_args()
    if args.line_ids and not args.read_script:
        cli.error('--line-ids requires --read-script')
    if args.new_edit:
        from narration_pace import new_edit
        try:
            with Path('edit.json').open('x',encoding='utf-8') as output:
                output.write(json.dumps(new_edit(args.new_edit),ensure_ascii=False,indent=2)+'\n')
        except OSError as exc:
            cli.exit(1, f'无法新建 edit.json：{exc}\n')
        return 0
    if args.read_script:
        try:
            edit = json.loads(Path('edit.json').read_text(encoding='utf-8'))
            return read_script(edit, args.line_ids)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            cli.exit(1, f'无法读取口播稿：{exc}\n')

    from production_run import InputSnapshot, commit_prepared, stage_run
    with stage_run('prepare') as (work, status):
        pins = InputSnapshot(work/'inputs')
        edit = json.loads(pins.pin('edit.json').read_text())
        timeline = prepare(edit, pins, work/'prepared')
        pins.verify()
        commit_prepared(work/'prepared')
        status.update(edit_sha256=timeline['edit_sha256'], frames=timeline['frames'], shots=len(timeline['shots']))


if __name__=='__main__':
    sys.exit(main())
