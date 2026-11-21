"""Make a sample-aligned, post-normalization PCM window from a prepared edit's stems."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

from export import check_audio_request, ff, levels
from music_direction import RATE, mix_stems, normalization_filter, resolve_cues
from production_run import InputSnapshot, digest, stage_run


def render_window(start, end, output, calibration):
    with stage_run('mix_window') as (work, status):
        pins = InputSnapshot(work/'inputs')
        edit = json.loads(pins.pin('edit.json').read_text())
        timeline = json.loads(pins.pin('timeline.json').read_text())
        if timeline.get('edit_sha256') != pins.hashes['edit.json']:
            raise ValueError('Re-run prepare.py to bind the revised edit to its timeline')
        if not 0 <= start < end <= timeline['frames']:
            raise ValueError('Use a frame window within the prepared edit')
        expected = timeline.get('prepared_files', {}).get('audio/narration.wav')
        if expected and digest(pins.pin('audio/narration.wav')) != expected:
            raise ValueError('Prepared narration changed. Re-run prepare.py.')
        check_audio_request(edit)
        cues = resolve_cues(edit, timeline)
        mix = mix_stems(ff, work, edit, timeline, pins.pin, cues)
        measured = (levels(mix) if calibration is None else
                    json.loads(pins.pin(calibration).read_text())['mix_normalization'])
        if output.resolve() in {Path(name).resolve() for name in pins.hashes}:
            raise ValueError('Choose an output distinct from the project inputs')
        if output.suffix.lower() != '.wav':
            raise ValueError('Use a .wav output for the replacement mix')
        first, last = (round(f * RATE / timeline['fps']) for f in (start, end))
        chain = (normalization_filter(measured) + f',aformat=sample_rates={RATE}:channel_layouts=stereo,'
                 f'atrim=start_sample={first}:end_sample={last}')
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output.parent) as temp:
            staged = Path(temp)/'window.wav'
            ff('-i', mix, '-af', chain, '-c:a', 'pcm_f32le', staged)
            count = int(subprocess.check_output(['ffprobe','-v','error','-select_streams','a:0',
                        '-show_entries','stream=duration_ts','-of','csv=p=0',str(staged)], text=True).strip())
            if count != last - first:
                raise ValueError('Mixed window does not contain the requested number of samples')
            pins.verify()
            os.replace(staged, output)
        status.update(frame_range=[start, end], output=str(output), music_cues=cues)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start-frame', required=True, type=int)
    parser.add_argument('--end-frame', required=True, type=int)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--levels-from', type=Path, help='Use the original export technical.json to retain its loudness calibration')
    args = parser.parse_args()
    render_window(args.start_frame, args.end_frame, args.output, args.levels_from)
