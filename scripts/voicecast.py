"""Generate standalone Jianqiu takes, or select a Kokoro preset."""
import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import tempfile
import wave
from pathlib import Path

import numpy as np
import sherpa_onnx as so
from jianqiu_voice import JianqiuVoice, VOICE_ID
from voice_endings import EndingChecker, checked_take

MODEL_DIR = Path.home()/'.cache/creator-video-models/kokoro-v1.1'


def load_model(root, threads):
    lexicons = ','.join(str(root/name) for name in ('lexicon-us-en.txt','lexicon-zh.txt'))
    kokoro = so.OfflineTtsKokoroModelConfig(model=str(root/'model.int8.onnx'),
        tokens=str(root/'tokens.txt'), voices=str(root/'voices.bin'), lexicon=lexicons,
        data_dir=str(root/'espeak-ng-data'))
    rules = ','.join(str(root/name) for name in ('date-zh.fst','number-zh.fst','phone-zh.fst'))
    config = so.OfflineTtsConfig(model=so.OfflineTtsModelConfig(kokoro=kokoro,num_threads=threads),
                                  rule_fsts=rules, max_num_sentences=1)
    if not config.validate():
        raise ValueError(f'Install the Kokoro v1.1 Chinese int8 release in {root}')
    return so.OfflineTts(config)


def encode_wave(path, audio):
    samples = np.asarray(audio.samples, dtype=np.float32)
    if not samples.size or not np.isfinite(samples).all() or np.max(np.abs(samples)) < .001:
        raise ValueError('TTS returned an empty or invalid take')
    pcm = np.rint(np.clip(samples, -1, 1)*32767).astype('<i2')
    with wave.open(str(path),'wb') as wav:
        wav.setparams((1,2,audio.sample_rate,0,'NONE','not compressed'))
        wav.writeframes(pcm.tobytes())
    return len(pcm) / audio.sample_rate


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source,'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('script',type=Path,help='JSON array of {id,text,spoken?,speed?,speaker?} takes')
    parser.add_argument('--out',type=Path,default=Path('audio/takes'))
    parser.add_argument('--voice',choices=(VOICE_ID,'kokoro'),help='Defaults to Jianqiu unless a Kokoro preset or path is supplied')
    parser.add_argument('--model-dir',type=Path,help='Kokoro model directory')
    parser.add_argument('--speaker',type=int,help='Kokoro preset ID, e.g. 62 = zm_013, 59 = zm_010, 3 = zf_001')
    parser.add_argument('--speed',type=float)
    parser.add_argument('--threads',type=int,default=3)
    args=parser.parse_args()
    records=json.loads(args.script.read_text(encoding='utf-8'))
    if not isinstance(records,list) or not records or args.threads < 1:
        parser.error('Supply at least one take and a positive number of threads')
    ids = [r['id'] for r in records]
    if len(set(ids))!=len(ids) or not all(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*',name) for name in ids):
        parser.error('Use unique take IDs consisting of letters, digits, dashes or underscores')
    args.out.mkdir(parents=True,exist_ok=True)
    kokoro_options = args.speaker is not None or args.model_dir is not None or any('speaker' in r for r in records)
    selected = args.voice or ('kokoro' if kokoro_options else VOICE_ID)
    if selected == VOICE_ID and kokoro_options:
        parser.error('Kokoro speaker IDs and model paths cannot be applied to the Jianqiu voice')
    if selected == VOICE_ID:
        engine = JianqiuVoice(args.threads)
        default_speed = engine.profile['speed']
        report = dict(engine='ZipVoice distill int8',voice=VOICE_ID,
                      reference_sha256=engine.profile['prompt_sha256'],num_steps=engine.profile['num_steps'])
    else:
        model_dir = args.model_dir or MODEL_DIR
        engine = load_model(model_dir,args.threads)
        default_speed = 1.08
        report = dict(engine='Kokoro v1.1-zh int8',model_dir=str(model_dir),
                      model_sha256=digest(model_dir/'model.int8.onnx'),voices_sha256=digest(model_dir/'voices.bin'))
    if args.speed is not None:
        default_speed = args.speed
    with tempfile.TemporaryDirectory(dir=args.out,prefix='.voicecast-') as temp:
        staging=Path(temp)
        ending_checker=EndingChecker()
        rendered=[]
        for rec in records:
            name=rec['id']
            spoken=rec.get('spoken',rec['text'])
            speed=float(rec.get('speed',default_speed))
            if not spoken.strip() or not math.isfinite(speed) or speed <= 0:
                raise ValueError(f'Invalid text or speed for {name}')
            take=staging/f'{name}.wav'
            if selected == VOICE_ID:
                # The exported WAV keeps the requested ID regardless of the chosen take.
                with tempfile.TemporaryDirectory(dir=staging) as trial:
                    chosen, check = checked_take(Path(trial)/take.name,spoken,speed,engine,ending_checker,encode_wave)
                    os.replace(chosen,take)
                for attempt in check['attempts']:
                    attempt.pop('file')  # Standalone audition exports the selected WAV only.
                speaker = {'voice':VOICE_ID, 'ending_check':check}
                with wave.open(str(take)) as recorded:
                    duration=recorded.getnframes()/recorded.getframerate()
            else:
                sid=int(rec.get('speaker',62 if args.speaker is None else args.speaker))
                if not 0 <= sid < engine.num_speakers:
                    raise ValueError(f'Invalid speaker for {name}')
                audio = engine.generate(spoken,sid=sid,speed=speed)
                speaker = {'speaker':sid}
                duration=encode_wave(take,audio)
                speaker['ending_check']=ending_checker.inspect_file(take,spoken)
            rendered.append(dict(id=name,text=rec['text'],spoken=spoken,speed=speed,**speaker,
                file=take.name,seconds=round(duration,4),sha256=digest(take)))
            print(f'{name} {duration:.2f}s',flush=True)
        report.update(sherpa_onnx=so.__version__,takes=rendered)
        (staging/'voicecast.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        # A rejected batch must preserve the last working set of takes.
        names=[row['file'] for row in rendered]+['voicecast.json']
        with (args.out/'.voicecast.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            published=[]
            try:
                for i,name in enumerate(names):
                    final=args.out/name
                    backup=staging/f'previous-{i}'
                    if final.exists():
                        os.link(final,backup)
                    os.replace(staging/name,final)
                    published.append((final,backup))
            except OSError:
                for final,backup in reversed(published):
                    if backup.exists():
                        os.replace(backup,final)
                    else:
                        final.unlink()
                raise

if __name__=='__main__':
    main()
