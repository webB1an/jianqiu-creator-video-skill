"""Resolve picture cues against the actual recorded delivery, including repeated words."""
from bisect import bisect_left
from difflib import SequenceMatcher
from functools import lru_cache
import math
import subprocess
import unicodedata

import numpy as np

from voice_endings import MODEL_DIR


def symbols(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())


@lru_cache(maxsize=1)
def recognizer():
    import sherpa_onnx as so

    if not (MODEL_DIR/'model.int8.onnx').is_file():
        raise ValueError('For word timing install the local SenseVoice model or supply reviewed word_times')
    return so.OfflineRecognizer.from_sense_voice(model=str(MODEL_DIR/'model.int8.onnx'),
        tokens=str(MODEL_DIR/'tokens.txt'), language='auto', use_itn=False, num_threads=3)


def observations(pcm, rate):
    """At 48 kHz, resample to the recognizer clock and divide long recordings near quiet gaps."""
    if rate != 48000:
        raise ValueError('The prepared sound bus runs at 48000 Hz')
    if not len(pcm):
        raise ValueError('Recording is empty')
    raw = subprocess.run(['ffmpeg','-nostdin','-v','error','-f','s16le','-ar',str(rate),'-ac','1',
                          '-i','pipe:0','-ar','16000','-f','f32le','pipe:1'],
                         input=np.asarray(pcm,dtype='<i2').tobytes(),stdout=subprocess.PIPE,check=True).stdout
    samples = np.frombuffer(raw, dtype='<f4')
    hz, pos = 16000, 0
    observed = []
    while pos < len(samples):
        stop = min(pos + 24*hz, len(samples))
        if stop < len(samples):
            # Center a cut on the lowest-energy 120 ms in the final four seconds.
            runs = samples[stop-4*hz:stop].reshape(-1, 320)
            energy = np.mean(runs*runs, axis=1)
            step = int(np.argmin(np.convolve(energy, np.ones(6), 'valid')))
            stop = stop - 4*hz + (step+3)*320
        stream = recognizer().create_stream()
        stream.accept_waveform(hz, np.pad(samples[pos:stop], (1600,3200)))
        recognizer().decode_stream(stream)
        res = stream.result
        groups = [(symbols(word), min(stop/hz, max(pos/hz, (pos-1600)/hz+t)))
                  for word,t in zip(res.tokens,res.timestamps) if not word.startswith('<|') and symbols(word)]
        for i,(word,start) in enumerate(groups):
            limit = groups[i+1][1] if i+1<len(groups) else stop/hz
            end = min(limit, start + max(.20, .12*len(word)))
            if end > start:
                observed.append([word,start,end])
        pos = stop
    return observed


def validate_words(words, duration):
    if not isinstance(words, list) or not words:
        raise ValueError('word_times must list [heard word, start seconds, end seconds]')
    result = []
    last = 0
    for row in words:
        if not isinstance(row, (list, tuple)) or len(row)!=3 or not isinstance(row[0],str):
            raise ValueError('Invalid word_times row')
        token = symbols(row[0])
        start,end = row[1:]
        if (not token or any(type(v) not in (int,float) or not math.isfinite(v) for v in (start,end))
                or not last <= start < end <= duration+1e-7):
            raise ValueError(f'Invalid or overlapping word interval: {row}')
        for j,char in enumerate(token):
            result.append((char,start+(end-start)*j/len(token),start+(end-start)*(j+1)/len(token)))
        last = end
    return result


def align(lines, words, duration, origin=0., method='sensevoice'):
    """Keep original text/indices. Mark interpolated symbols so estimated anchors can be reviewed."""
    heard = validate_words(words,duration)
    target, positions = [], []
    for sid, line_id, text in lines:
        if not isinstance(text,str) or not text.strip() or not symbols(text):
            raise ValueError(f'{line_id}: supply speakable text')
        pos = []
        for ch in text:
            norm = symbols(ch)
            pos.append((len(target),len(norm)))
            target.extend(norm)
        positions.append(pos)
    matched = {}
    for block in SequenceMatcher(None,target,[h[0] for h in heard],autojunk=False).get_matching_blocks():
        for k in range(block.size):
            matched[block.a+k] = heard[block.b+k][1:]
    indices = sorted(matched)
    if not indices: raise ValueError('No speech aligns to the script. Check the recording or supply reviewed word_times.')
    aligned = dict(matched)
    for i in range(len(target)):
        if i in aligned: continue
        j = bisect_left(indices,i)
        left = indices[j-1] if j else -1
        right = indices[j] if j<len(indices) else len(target)
        a = matched[left][1] if j else max(0,matched[right][0]-.18*(right-left-1))
        b = matched[right][0] if j<len(indices) else min(duration,a+.18*(right-left-1))
        step = (b-a)/(right-left-1)
        aligned[i] = (a+(i-left-1)*step, a+(i-left)*step)
    result=[]
    for (sid,line_id,text),pos in zip(lines,positions):
        span=[i for at,count in pos for i in range(at,at+count)]
        coverage=sum(i in matched for i in span)/len(span)
        if coverage<.50:
            raise ValueError(f'{line_id}: script alignment {coverage:.0%}; listen and correct spoken text or word_times')
        start, end = aligned[span[0]][0], aligned[span[-1]][1]
        if end<=start:raise ValueError(f'{line_id}: empty speech interval')
        chars=[]; estimated=[]
        for index,(ch,(at,count)) in enumerate(zip(text,pos)):
            sec = aligned[at][0] if count else (aligned[at-1][1] if at>span[0] else start)
            if count and any(i not in matched for i in range(at,at+count)): estimated.append(index)
            chars.append([ch,round(sec+origin,6)])
        result.append(dict(shot=sid,id=line_id,text=text,start=round(start+origin,6),end=round(end+origin,6),
                           chars=chars,estimated_indices=estimated,matched=round(coverage,4),method=method))
    return result


def line_identity(shot, line, index):
    ident=line.get('id',f'{shot}.L{index+1}')
    if not isinstance(ident,str) or not ident or any(c in ident for c in '@:#') or ident=='title':
        raise ValueError('Use a nonempty cue line id without @, : or #, distinct from title')
    return (shot,ident,line.get('spoken',line['text']))


def take_beat(shot, line, index, pcm, rate, start):
    reviewed = 'word_times' in line
    words = line['word_times'] if reviewed else observations(pcm, rate)
    return align([line_identity(shot,line,index)],words,len(pcm)/rate,start/rate,
                 'reviewed' if reviewed else 'sensevoice')[0]


def timed_captions(line, pcm, rate, *, beat=None, origin=0):
    from captioning import parts, timed_parts
    if beat is None or 'caption_offsets' in line:
        return timed_parts(line,pcm,rate)
    units=parts(line)
    spoken=line.get('spoken_parts', units)
    if len(units)!=len(spoken) or any(not s for s in spoken):
        return timed_parts(line,pcm,rate)
    tokens=[symbols(s) for s in spoken]
    seconds=[sec for ch,sec in beat['chars'] for _ in symbols(ch)]
    if not all(tokens) or ''.join(tokens)!=symbols(beat['text']):
        return timed_parts(line,pcm,rate)
    boundaries=np.cumsum([len(s) for s in tokens])[:-1]
    cuts=[0,*(round(seconds[i]*rate)-origin for i in boundaries),len(pcm)]
    if any(a>=b for a,b in zip(cuts,cuts[1:])):
        return timed_parts(line,pcm,rate)
    return units,cuts
