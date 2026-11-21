"""Check which already-encoded audio stream a mux actually contains."""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess


def audio(path):
    command=['ffprobe','-v','error','-select_streams','a:0','-show_packets','-show_streams',
             '-show_data_hash','SHA256','-show_entries',
             'stream=codec_name,time_base,channels,sample_rate:packet=pts,duration,data_hash:packet_side_data=skip_samples,discard_padding',
             '-of','json',str(path)]
    data=json.loads(subprocess.check_output(command))
    if not data.get('streams') or not data.get('packets'):
        raise ValueError(f'No encoded audio packets in {path}')
    stream=data['streams'][0]
    clock=Fraction(stream['time_base'])
    packets=[(p['data_hash'],int(p['pts'])*clock,int(p['duration'])*clock,
              [(v.get('skip_samples',0),v.get('discard_padding',0)) for v in p.get('side_data_list',[])])
             for p in data['packets']]
    return (stream['codec_name'],stream['channels'],stream['sample_rate']),packets


def compare(candidate, approved=None, voice_only=None):
    if approved is None and voice_only is None:
        raise ValueError('Supply the approved pre-encoded mix or a matched-encoder dry narration control')
    layout,packets=audio(candidate)
    result=dict(codec=layout[0],channels=layout[1],sample_rate=layout[2],packets=len(packets),
                audio_payload_sha256=hashlib.sha256(''.join(p[0] for p in packets).encode()).hexdigest())
    if approved is not None:
        if audio(approved)!=(layout,packets):
            raise ValueError('The submitted container differs from the approved audio in format, data or timing.')
        result['approved_stream_and_timing_match']=True
    if voice_only is not None:
        fmt,control=audio(voice_only)
        if fmt==layout and [p[0] for p in control]==[p[0] for p in packets]:
            raise ValueError('Every encoded packet matches the dry narration; the requested soundtrack is absent.')
        result['dry_control_has_different_payload']=True
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('candidate',type=Path)
    p.add_argument('--approved-audio',type=Path,help='Encoded reference intended to be copied losslessly')
    p.add_argument('--voice-only',type=Path,help='Voice control made with identical encoding settings')
    a=p.parse_args()
    try:print(json.dumps(compare(a.candidate,a.approved_audio,a.voice_only),indent=2))
    except ValueError as exc:p.exit(1,str(exc)+'\n')
