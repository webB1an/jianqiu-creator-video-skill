"""Give selected Mandarin spans explicit ZipVoice phones and measure the actual phrase endings."""
import hashlib
import json
import os
import re
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

from jianqiu_voice import JianqiuVoice
from setup_zipvoice import install

VERSION = 'directed-phones-v1'
RATE = 16000

def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()

def json_hash(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def atomic_output(path, write):
    with tempfile.NamedTemporaryFile(dir=path.parent,suffix=path.suffix,delete=False) as handle:
        temp=Path(handle.name)
    try:
        write(temp)
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)

def decode(recognizer, path):
    pcm=np.frombuffer(subprocess.check_output(['ffmpeg','-nostdin','-v','error','-i',str(path),'-ac','1','-ar',str(RATE),'-f','f32le','-']),'<f4')
    stream=recognizer.create_stream()
    stream.accept_waveform(RATE,np.pad(pcm,(1600,5280)))
    recognizer.decode_stream(stream)
    result=stream.result
    return pcm,re.sub(r'<\|.*?\|>','',result.text).strip(),list(result.tokens),[max(0,float(s)-.1) for s in result.timestamps]

def token_start(tokens, ts, target, *, last=False):
    text=''.join(tokens)
    pos=text.rfind(target) if last else text.find(target)
    if pos<0:return None
    count=0
    for tok,t in zip(tokens,ts):
        if count+len(tok)>pos:return t
        count+=len(tok)
    return None

class DirectedVoice:
    def __init__(self, edit, directory):
        self.directory=Path(directory)
        self.directory.mkdir(parents=True,exist_ok=True)
        base=install()
        self.base_hash=digest(base/'lexicon.txt')
        lex={p[0] for s in (base/'lexicon.txt').read_text().splitlines() if (p:=s.split())}
        vocab={s.rsplit('\t',1)[0] for s in (base/'tokens.txt').read_text().splitlines()}
        specs=[l['pronunciation'] for shot in edit['shots'] for l in shot['lines'] if l.get('pronunciation')]
        spoken=''.join(l.get('spoken',l['text']) for shot in edit['shots'] for l in shot['lines'])
        units=sorted(set(' '.join(value.split()) for spec in specs for value in spec.get('phones',{}).values()))
        # A fresh single-character dictionary entry prevents adjacent Chinese words from
        # drawing a polyphonic character into a different, longer dictionary match.
        fresh=(chr(i) for i in range(0x9fff,0x4e00,-1) if chr(i) not in spoken and chr(i) not in lex)
        self.codes={}
        for phones in units:
            if not phones or set(phones.split())-vocab:raise ValueError(f'Invalid ZipVoice phonemes: {phones}')
            self.codes[phones]=next(fresh)
        self.lexicon=self.directory/('phones-'+json_hash(self.codes)[:12]+'.txt')
        expected=''.join(f'{self.codes[k]} {k}\n' for k in units)
        if units and (not self.lexicon.is_file() or self.lexicon.read_text()!=expected):
            atomic_output(self.lexicon,lambda p:p.write_text(expected))
        self.voice=JianqiuVoice(pronunciation_lexicon=self.lexicon if units else None)
        self.recognizer=None

    def get(self,line,default_speed,checker,write_wave,*,mood='neutral',marked_words=None):
        from jianqiu_voice import apply_mood_polish
        from prosody_markup import PROSODY_VERSION, parse_prosody_markup
        from voice_endings import classify_ending
        import sherpa_onnx as so
        spec=line['pronunciation']
        words=line.get('spoken',line['text'])
        directions=spec.get('phones',{})
        for term in directions:
            if not term or term not in words:raise ValueError(f'Pronunciation term absent: {term}')
        text=words
        if directions:
            text=re.sub('|'.join(re.escape(t) for t in sorted(directions,key=len,reverse=True)),
                        lambda m:self.codes[' '.join(directions[m.group()].split())],words)
        speed=float(line.get('speed',default_speed))
        sig = self.voice.mood_signature(mood) if mood and mood != 'neutral' else self.voice.signature
        use_prosody = parse_prosody_markup(marked_words)['has_markup']
        prosody_key = [PROSODY_VERSION, marked_words] if use_prosody else []
        key=json_hash([VERSION,sig,self.base_hash,text,spec,speed,*prosody_key])[:24]
        receipt=self.directory/(key+'.json')
        if receipt.exists():
            prior=json.loads(receipt.read_text());selected=Path(prior['selected'])
            if selected.is_file() and digest(selected)==prior['sha256']:
                return selected,prior
        for k in ('ending','guard'):
            if spec.get(k) and not isinstance(spec[k],str): raise ValueError(f'Invalid {k}')
        if spec.get('guard') and not spec.get('ending'):raise ValueError('Give a guarded ending a target character.')
        guard=spec.get('guard','')
        if not checker.available and guard:
            raise RuntimeError('A carrier-phrase trim needs the local SenseVoice timestamps. Install SenseVoice or supply the separately trimmed take.')
        if not self.recognizer and checker.available:
            self.recognizer=so.OfflineRecognizer.from_sense_voice(model=str(checker.directory/'model.int8.onnx'),
                tokens=str(checker.directory/'tokens.txt'),language='zh',num_threads=2,use_itn=False)
        attempts=[]
        target=spec.get('ending','')
        source=text.rstrip('。？！!?.,，')+'。'+guard+'。' if guard else text
        timing_text = marked_words if use_prosody else None
        if timing_text is not None and guard:
            timing_text = timing_text.rstrip('。？！!?.,，') + '。' + guard + '。'
        for idx,rate in enumerate(spec.get('speeds',[speed,min(1.,speed),.91])):
            raw=self.directory/(key+f'-{idx}-raw.wav')
            if not raw.is_file():
                audio=self.voice.generate(source,rate,preserve_pauses=True,mood=mood,timing_text=timing_text)
                def _write_dir(p, _a=audio, _m=mood):
                    write_wave(p, _a)
                    if _m and _m != 'neutral':
                        apply_mood_polish(p, _m)
                atomic_output(raw,_write_dir)
            if not self.recognizer:
                chosen=raw
                attempts.append({'speed':rate,'file':str(raw),'selected_file':str(raw),'accepted':True,
                                 'verification':'phoneme input applied; audio requires audition'})
                break
            pcm,trans,toks,ts=decode(self.recognizer,raw)
            chosen=raw
            report={'speed':rate,'source_asr':trans,'file':str(raw)}
            if guard:
                point=token_start(toks,ts,target,last=True)
                stop=token_start(toks,ts,guard)
                if point is None or stop is None or stop<=point:
                    report['rejection']='guard or target not localized';attempts.append(report);continue
                # Retain the falling vowel and end at a measured energy valley before
                # the next carrier phrase. Never truncate simply at the TTS file edge.
                win=160; levels=np.sqrt(np.mean(pcm[:len(pcm)//win*win].reshape(-1,win)**2,axis=1))
                lo=int((point+.06)*100); hi=min(len(levels)-3,int((stop-.01)*100))
                if hi<=lo+3:report['rejection']='insufficient measured tail window';attempts.append(report);continue
                sm=np.convolve(levels,np.ones(4)/4,'same')
                at=lo+int(np.argmin(sm[lo:hi])); cut=(at+1)*.01
                if sm[at] > np.max(levels)*.1:report['rejection']='no phrase-boundary valley';attempts.append(report);continue
                chosen=self.directory/(key+f'-{idx}-complete.wav')
                with wave.open(str(raw)) as wav:
                    sr=wav.getframerate(); raw16=np.frombuffer(wav.readframes(round(sr*cut)),'<i2').copy()
                tail=min(round(sr*.008),len(raw16))
                raw16[-tail:]=(raw16[-tail:]*np.linspace(1,0,tail)).astype('<i2')
                def write_trimmed(path):
                    with wave.open(str(path),'wb') as wav:
                        wav.setparams((1,2,sr,0,'NONE',''))
                        wav.writeframes(raw16.tobytes()+bytes(int(sr*.085)*2))
                atomic_output(chosen,write_trimmed)
                report.update(guard=guard,source_target_timestamp=point,source_guard_timestamp=stop,cut_seconds=cut,
                              target_to_cut=round(cut-point,3),valley_db_relative_to_peak=round(float(20*np.log10(max(sm[at],1e-9)/max(np.max(levels),1e-9))),1))
            checked,trans,toks,ts=decode(self.recognizer,chosen)
            end=classify_ending(words,trans)
            report.update(transcript=trans,ending=end['status'],similarity=end['similarity'],selected_file=str(chosen),
                          tokens=toks,timestamps=ts,duration=round(len(checked)/RATE,4))
            required=spec.get('require',[])
            acceptable=all(any(x.lower() in trans.lower() for x in (term if isinstance(term,list) else [term])) for term in required)
            if target:
                heard_at=token_start(toks,ts,target,last=True)
                win=160
                energy=np.sqrt(np.mean(checked[:len(checked)//win*win].reshape(-1,win)**2,axis=1))
                active=np.flatnonzero(energy>max(.001,float(np.max(energy))*.024))
                last_active=(int(active[-1])+1)*.01 if len(active) else 0.
                remaining=round(last_active-heard_at,3) if heard_at is not None else 0.
                report.update(last_active_seconds=round(last_active,3),last_token_timestamp_to_active_end=remaining)
                acceptable=acceptable and trans.rstrip('。！？,.!?').endswith(target) and remaining>=spec.get('min_ending_seconds',.12)
            acceptable=acceptable and end['status']!='possible_missing_ending'
            report['accepted']=acceptable;attempts.append(report)
            if acceptable:break
        if not attempts[-1].get('accepted'):
            (self.directory/(key+'-rejected.json')).write_text(json.dumps(attempts,ensure_ascii=False,indent=2))
            raise ValueError(f'No verified directed recording for {words}; review {key}-rejected.json')
        record={'version':VERSION,'spoken':words,'phones':directions,'encoded_aliases':{term:self.codes[' '.join(v.split())] for term,v in directions.items()},
                'lexicon':str(self.lexicon),'base_lexicon_sha256':self.base_hash,'lexicon_sha256':digest(self.lexicon) if directions else None,
                'attempts':attempts,'selected':str(chosen),'sha256':digest(chosen),'effective_speed':rate}
        atomic_output(receipt,lambda p:p.write_text(json.dumps(record,ensure_ascii=False,indent=2)))
        return chosen,record
