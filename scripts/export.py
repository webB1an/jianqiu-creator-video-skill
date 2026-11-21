"""Render a prepared edit, mix stems, stage a candidate for editorial review."""
import argparse
import json
import hashlib
import sys
import re
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
import color_finish
from source_framing import crop_filter, upright_raster
import motion_layers
from picture_finish import brand_image, overlay_chain
import montage_pins
import explainer_scene_receipt
import remotion_effect_receipt
from floating_captions import caption_input, top_caption_ranges
from perception_review import expected_audio
from music_direction import RATE, has_effects, mix_stems, normalization_filter, resolve_cues
from covers import SIZES, run as render_covers
from PIL import Image
from transitions import STILLS, plan_bridges, source_handles, stitch
from screen_plan import validate as validate_screen_plan
from review_bundle import stage
from release_version import installed_version
from visual_case import frozen_cards
from project_learning import LOG as LEARNING_LOG
from library_credits import build as build_library_credits
from production_run import WORK, InputSnapshot, atomic_json, digest, install_file, stage_run


def ff(*args, cwd=None, log='error'):
    return subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel',log,'-y',*map(str,args)],cwd=cwd,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True).stderr

def probe(path):
    return json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)]))

def levels(path):
    log=ff('-i',path,'-af','loudnorm=I=-16:TP=-1.5:LRA=9:print_format=json','-vn','-f','null','-',log='info')
    return json.loads(re.findall(r'\{[^{}]+"input_i"[^{}]+\}',log)[-1])

def check_audio_request(edit):
    for key in expected_audio(edit):
        if key == 'effects' and has_effects(edit):
            continue
        if not edit.get(key) or edit.get(key+'_gain',1) == 0:
            raise ValueError(f'{key} was requested for this cut. Supply the stem and a nonzero mix gain.')


def render_frames(folder, edit, timeline, prepared_covers, captions, pins, status, skill_version):
    fps=timeline['fps']
    w,h=edit.get('size',[1920,1080])
    if w*9!=h*16:raise ValueError('Jianqiu video masters require a 16:9 frame')
    if w%2 or h%2 or min(w,h)<=0:raise ValueError('Use positive even frame dimensions')
    if timeline.get('edit_sha256') != pins.hashes['edit.json']:
        raise ValueError('edit.json changed. Re-run prepare.py and rebuild dependent shots and audio.')
    if [s['id'] for s in edit['shots']]!=[s['id'] for s in timeline['shots']]:raise ValueError('Re-run prepare.py after editing shots')
    music_cues = resolve_cues(edit, timeline, pin=pins.pin)
    cut_frames = {s['start_frame'] for s in timeline['shots']}
    plan = pins.pin(edit.get('screen_plan') or 'screen-plan.json', optional=not edit.get('screen_plan'))
    pins.pin('library-uses.json', optional=True)
    pins.pin(LEARNING_LOG, optional=True)
    pins.pin('cover.json', optional=True)
    for card in frozen_cards(Path('edit.json')):
        for path, checksum in card['files'].items():
            pins.expect(path, checksum)
    if edit.get('screen_plan'):
        screen_plan = json.loads(plan.read_text())
        validate_screen_plan(screen_plan, timeline)
        cut_frames.update(c['start'] for c in screen_plan['cuts'])
    cut_times = ','.join(f'{n / fps:.9f}' for n in sorted(cut_frames))
    black_intervals=[]
    color_sources = {}
    status['shots_rendered'] = status['shots_reused'] = 0
    encoder = subprocess.check_output(['ffmpeg','-version'], text=True).splitlines()[0]
    cache = WORK/'shots'
    cache.mkdir(exist_ok=True)
    transition_adjustments = []
    edges = plan_bridges(edit['shots'], timeline['shots'], fps,
                         default_handles=edit.get('transition_handles', 'hold'), source_path=pins.pin,
                         decisions=transition_adjustments)
    remotion_receipts = {}
    for index,(shot,timing) in enumerate(zip(edit['shots'],timeline['shots'])):
        if 'remotion_effect' in shot:
            remotion_receipts[shot['id']] = remotion_effect_receipt.read_receipt(
                shot, timing, edit, timeline, Path.cwd(), pins.pin, pins)
        if 'roughcut_source_sha256' in shot:
            pins.expect(shot['visual'], shot['roughcut_source_sha256'])
        path=pins.pin(shot['visual'])
        if 'explainer_scene' in shot:
            explainer_scene_receipt.verify(shot, timing, fps, pins)
        frames=timing['frames']
        pic=path.suffix.lower() in STILLS
        before, after = source_handles(edit['shots'], edges, index)
        render_frames_count = before + frames + after
        shot = motion_layers.resolve_shot(shot, timeline, timing)
        layers = motion_layers.parse(shot, frames)
        camera = motion_layers.parse_camera(shot, frames)
        layer_paths = [pins.pin(layer.file) for layer in layers]
        seek = shot.get('in', 0)
        if before:
            seek = float(seek) - before/fps
        if before and seek < -1e-8:
            raise ValueError(f"Shot {shot['id']} needs {before} source frames before its in point for the transition")
        seek = max(0., seek) if before else seek
        profile = None
        if pic:
            path, profile = color_finish.icc_still(path, folder/f'color-{index}.png')
        source = probe(path)
        video_streams = [s for s in source['streams'] if s['codec_type'] == 'video']
        if not video_streams:
            raise ValueError(f"{shot['id']}: visual contains no picture stream")
        video_info = video_streams[0]
        if shot.get('crop') is not None:
            video_info = next((s for s in video_streams if not s.get('disposition', {}).get('attached_pic')), video_info)
        video_map = '0:v:0' if video_info is video_streams[0] else f"0:{video_info['index']}"
        # Color interpretation uses source metadata; odd-size padding follows FFmpeg autorotation.
        raster_size = upright_raster(video_info)[:2] if shot.get('crop') is not None else None
        color, color_sources[shot['id']] = color_finish.graded_source_filter(
            video_info, None if profile else shot.get('input_color'), shot.get('grade'), raster_size=raster_size)
        if profile:
            color_sources[shot['id']]['icc_to_srgb'] = profile
        if not pic and float(source['format']['duration'])-seek+1/fps < render_frames_count/fps:
            raise ValueError(f'{path.name} is too short for {render_frames_count/fps:.2f}s from {seek:.3f}s including transition handles')
        sample_clock = f'fps={fps}'
        if not pic and 'roughcut_source_sha256' in shot:
            from roughcut_adoption import picture_sample_clock
            seek, sample_clock = picture_sample_clock(seek, fps, shot.get('roughcut_clock_in', shot.get('in', 0)))
        inputs=['-loop','1','-framerate',fps] if pic else ['-ss',seek]
        if shot.get('crop') is not None:
            vf = crop_filter(shot['crop'], video_info, [w,h])
        elif 'roughcut_source_sha256' in shot:
            # Match the rough cut's display aspect and use the original raster for delivery.
            vf = (f"scale=w='max(2,trunc(min({w},{h}*dar)/2)*2)':"
                  f"h='max(2,trunc(min({h},{w}/dar)/2)*2)',setsar=1,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2")
        elif shot.get('fit','contain')=='cover':
            vf=f'scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}'
        else:
            vf=f'scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2'
        name=f'shot{index:04}.mp4'
        base_filter = f'{sample_clock},{color},{vf},setsar=1'
        video_args = ['-frames:v',render_frames_count,'-c:v','libx264','-preset','veryfast','-crf',16,*color_finish.FLAGS]
        encode = ['-map',video_map,'-an','-vf',base_filter + ',format=yuv420p',*video_args]
        recipe = [encoder, pins.hashes[pins.key(shot['visual'])], inputs, encode]
        if layers:
            recipe.append(motion_layers.recipe(shot, layers, pins, before, after))
        key = hashlib.sha256(json.dumps(recipe).encode()).hexdigest()
        receipt = cache/(name + '.json')
        try:
            previous = json.loads(receipt.read_text())
        except (FileNotFoundError, ValueError):
            previous = {}
        if not isinstance(previous, dict):
            previous = {}
        cached = cache/name
        reused = previous.get('recipe') == key and cached.is_file() and digest(cached) == previous.get('sha256')
        if reused:
            shutil.copyfile(cached, folder/name)
        elif layers:
            motion_layers.encode(layers, layer_paths, [*inputs, '-i', path], base_filter, video_args,
                                 folder/name, size=(w,h), fps=fps, frames=frames, before=before, after=after,
                                 video_map=video_map, camera=camera)
        else:
            ff(*inputs,'-i',path,*encode,folder/name)
        encoded=probe(folder/name)['streams'][0]
        if int(encoded['nb_frames']) != render_frames_count:
            raise ValueError(f"Shot {shot['id']} did not supply {render_frames_count} frames including source handles")
        black_filter = 'blackdetect=d=0.35:pix_th=0.04:pic_th=0.99'
        if before or after:
            black_filter = f'trim=start_frame={before}:end_frame={before+frames},setpts=PTS-STARTPTS,' + black_filter
        check=ff('-i',folder/name,'-vf',black_filter,'-an','-f','null','-',log='info')
        for a,b in re.findall(r'black_start:([\d.]+) black_end:([\d.]+)',check):
            offset=timing['start_frame']/fps
            black_intervals.append(dict(shot=shot['id'],start=round(offset+float(a),3),end=round(offset+float(b),3),intentional=bool(shot.get('allow_black'))))
        if not shot.get('allow_black',False):
            if any(float(v)>=(frames-2)/fps for v in re.findall(r'black_duration:([\d.]+)',check)):
                raise ValueError(f"Shot {shot['id']} is black throughout. Inspect source or explicitly set allow_black.")
        if reused:
            status['shots_reused'] += 1
        else:
            install_file(folder/name, cached)
            atomic_json(receipt, {'recipe': key, 'sha256': digest(cached)})
            status['shots_rendered'] += 1
    windows = []
    bridges=stitch(folder,edit['shots'],timeline['shots'],fps,ff,probe,edges=edges,
                   source_path=pins.pin,windows_receipt=windows)
    duration=timeline['frames']/fps
    audio_report = {}
    mix = mix_stems(ff, folder, edit, timeline, pins.pin, music_cues, audio_report=audio_report)
    measured=levels(mix)
    samples = timeline['frames'] * RATE // fps
    # Dynamic loudnorm can return contiguous PCM with a discontinuity in its timestamps.
    # Give AAC a continuous sample clock after resampling, including the final partial packet.
    norm = (normalization_filter(measured) + f',aformat=sample_rates={RATE}:channel_layouts=stereo,'
            f'atrim=end_sample={samples},asettb=1/{RATE},asetpts=N')
    shutil.copyfile(captions,folder/'captions.srt')
    master=folder/'master.mp4'
    brand_image(w,h,folder/'mark.png')
    top_ranges=top_caption_ranges(edit,timeline,0,timeline['frames'], srt=folder/'captions.srt', size=(w,h))
    caption_args=caption_input(folder,folder/'captions.srt',w,h,fps,timeline['frames'],
                               style=edit.get('caption_style','plate'),font=edit.get('font','Heiti SC'),top_ranges=top_ranges)
    pin_args, pin_layers = montage_pins.inputs(
        montage_pins.plan(edit, timeline), folder/'montage-art', (w,h), start=0, frames=timeline['frames'],
        first_index=4 if caption_args else 3, source_path=pins.pin)
    overlay=overlay_chain(w,h,edit.get('font','Heiti SC'),brand_index=2,caption_index=3 if caption_args else None,
                          fps=fps,top_ranges=top_ranges,montage_pins=pin_layers)
    ff('-i','picture.mp4','-i','mix.wav','-i','mark.png',*caption_args,*pin_args,'-map','[picture]','-map','1:a:0','-filter_complex',overlay,'-af',norm,'-r',fps,'-force_key_frames',cut_times,'-c:v','libx264','-crf',17,'-preset','fast','-pix_fmt','yuv420p',*color_finish.FLAGS,'-c:a','aac','-b:a','256k','-ar',RATE,'-movie_timescale',RATE,'-movflags','+faststart',master,cwd=folder)
    ff('-xerror','-i',master,'-f','null','-')
    streams=probe(master)['streams']
    v=next(s for s in streams if s['codec_type']=='video')
    a=next(s for s in streams if s['codec_type']=='audio')
    actual=levels(master)
    encoded_samples = int(a['duration_ts']) * Fraction(a['time_base']) * RATE
    if (int(v['nb_frames'])!=timeline['frames'] or [v['width'],v['height']]!=[w,h] or Fraction(v['avg_frame_rate'])!=fps or encoded_samples!=samples or abs(float(actual['input_i'])+16)>1.5 or float(actual['input_tp'])>-.8):raise RuntimeError(f'Encoded master differs from timeline or loudness target: {v}, {a}, {actual}')
    qa=dict(frames=int(v['nb_frames']),duration=duration,size=[w,h],fps=fps,video=v['codec_name'],audio=a['codec_name'],channels=a['channels'],LUFS=float(actual['input_i']),dBTP=float(actual['input_tp']),decoded=True,transitions=bridges,black_intervals=black_intervals)
    if windows:
        qa['evidence_windows'] = windows
    if transition_adjustments:
        qa['transition_adjustments'] = transition_adjustments
    qa.update(audio_report)
    qa['audio_samples'] = int(encoded_samples)
    qa['mix_normalization'] = measured
    if music_cues:
        qa['music_cues'] = music_cues
    qa['color'] = {'output':color_finish.verify(v), 'shots':color_sources}
    Path('qa').mkdir(exist_ok=True)
    if prepared_covers:
        (folder/'covers').mkdir()
        for label, size in SIZES.items():
            src = pins.pin(prepared_covers/(label+'.jpg'))
            with Image.open(src) as im:
                if im.size != size:
                    raise ValueError(f'{src} must be {size}')
                im.verify()
            shutil.copyfile(src,folder/'covers'/(label+'.jpg'))
    else:
        render_covers(edit,folder/'covers',input_path=lambda path: pins.pin(path, optional=True))
    (folder/'technical.json').write_text(json.dumps(qa,indent=2))
    def verify_snapshot():
        pins.verify()
        if installed_version() != skill_version:
            raise ValueError('The installed skill changed during export. Retry with one stable version.')

    automatic_credits = build_library_credits(Path('edit.json'), timeline, folder/'library-credits',
                                             pin=pins.pin, visual_cards=frozen_cards(Path('edit.json')),
                                             remotion_receipts=remotion_receipts) or {}
    return stage(master, folder, technical=folder/'technical.json', timeline=pins.pin('timeline.json'),
                 edit=Path('edit.json'), render_inputs=pins.recorded(), render_skill_version=skill_version,
                 before_commit=verify_snapshot, automatic_credits=automatic_credits)


def render(folder=None, edit=None, timeline=None, prepared_covers=None, *, captions=None,
           begin_review=False, previous=None):
    if edit is not None:
        check_audio_request(edit)
    with stage_run('export') as (work, status):
        skill_version = installed_version()
        folder = Path(folder).resolve() if folder is not None else work/'encode'
        folder.mkdir(parents=True, exist_ok=True)
        pins = InputSnapshot(work/'inputs')
        source_edit = json.loads(pins.pin('edit.json').read_text())
        source_timeline = json.loads(pins.pin('timeline.json').read_text())
        if ((edit is not None and edit != source_edit) or (timeline is not None and timeline != source_timeline)):
            raise ValueError('In-memory edit or timeline differs from the project. Re-read the project.')
        check_audio_request(source_edit)
        generated = source_timeline.get('prepared_files', {})
        if captions is not None:
            pinned_captions = pins.pin(captions)
        else:
            pinned_captions = pins.pin('audio/captions.srt', optional=not generated)
            if not pinned_captions.is_file():
                pinned_captions = pins.pin('out/captions.srt')
        narration = pins.pin('audio/narration.wav')
        if generated:
            presenter = source_timeline.get('presenter_source', {})
            for path_key, hash_key in [('file','sha256'),('timing_file','timing_sha256')]:
                if path_key in presenter:
                    pins.expect(presenter[path_key], presenter[hash_key])
            if digest(narration) != generated['audio/narration.wav']:
                raise ValueError('Prepared narration changed. Re-run prepare.py.')
            if captions is None and digest(pinned_captions) != generated['audio/captions.srt']:
                raise ValueError('Prepared subtitles changed. Re-run prepare.py or select approved SRT with --captions.')
            for take in source_timeline.get('voice_takes', []):
                if 'source_clip' in take:
                    original=take['source_clip']
                    pins.expect(original['file'], original['sha256'])
                pins.pin(take['file'])
                if pins.hashes[pins.key(take['file'])] != take['sha256']:
                    raise ValueError(f"Prepared voice take changed: {take['file']}. Re-run prepare.py.")
        status['prepared_binding'] = 'verified' if generated else 'legacy_edit_only'
        status['edit_sha256'] = pins.hashes['edit.json']
        if captions is not None:
            status['caption_override'] = pins.key(captions)
        candidate = render_frames(folder, source_edit, source_timeline, prepared_covers, pinned_captions,
                                  pins, status, skill_version)
        status.update(candidate=str(candidate), candidate_bytes=candidate.stat().st_size, frames=source_timeline['frames'])
        if begin_review:
            cmd = [sys.executable, str(Path(__file__).with_name('editorial_review.py')), 'begin', str(candidate)]
            if previous:
                cmd += ['--from', previous]
            subprocess.run(cmd, check=True)
        return candidate


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared-covers',type=Path,help='Directory with three project-designed JPG covers')
    parser.add_argument('--captions',type=Path,help='Approved replacement SRT, used both for the picture and sidecar')
    parser.add_argument('--from', dest='previous', help='Parent SHA-256 for a revision with review tickets')
    args=parser.parse_args()
    try:
        print(render(prepared_covers=args.prepared_covers, captions=args.captions, begin_review=True, previous=args.previous))
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.stderr or str(error))


if __name__=='__main__':
    main()
