"""Install the pinned local ZipVoice and Vocos models once."""
import argparse
import fcntl
import hashlib
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

RELEASE = 'sherpa-onnx-zipvoice-distill-int8-zh-en-emilia'
ARCHIVE_SHA = '77219c8b40f4ee8d73a7f902305ff6c1128ef9b54461c41b4ca6ed890b6c2803'
VOCODER_SHA = 'bcb3b970e384161c4d634f0bb9e999ff1c471b34c9bc0b1049a5014065ed3cc0'
BASE_URL = 'https://github.com/k2-fsa/sherpa-onnx/releases/download'
MODEL_DIR = Path.home() / '.cache/creator-video-models' / RELEASE
REQUIRED = ('encoder.int8.onnx', 'decoder.int8.onnx', 'tokens.txt', 'lexicon.txt',
            'espeak-ng-data/phontab', 'vocos.onnx')


def obtain(local, staging, url, expected):
    path = local or staging
    if local is None:
        subprocess.run(['curl', '--fail', '--location', '--retry', '3', '--silent',
                        '--show-error', '--output', str(path), url], check=True)
    with path.open('rb') as f:
        if hashlib.file_digest(f, 'sha256').hexdigest() != expected:
            raise ValueError(f'Model checksum mismatch: {path}')
    return path


def install(archive=None, vocoder=None):
    parent = MODEL_DIR.parent
    parent.mkdir(parents=True, exist_ok=True)
    signature = f'{ARCHIVE_SHA}\n{VOCODER_SHA}\n'
    with (parent / f'.{RELEASE}.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        stamp = MODEL_DIR / 'checksums.txt'
        if (stamp.is_file() and stamp.read_text() == signature and
                all((MODEL_DIR / name).is_file() for name in REQUIRED)):
            return MODEL_DIR
        with tempfile.TemporaryDirectory(dir=parent, prefix='.zipvoice-') as temp:
            staging = Path(temp)
            package = obtain(archive, staging / 'model.tar.bz2',
                             f'{BASE_URL}/tts-models/{RELEASE}.tar.bz2', ARCHIVE_SHA)
            vocos = obtain(vocoder, staging / 'vocoder.onnx',
                           f'{BASE_URL}/vocoder-models/vocos_24khz.onnx', VOCODER_SHA)
            with tarfile.open(package, 'r:bz2') as tar:
                tar.extractall(staging, filter='data')
            model = staging / RELEASE
            shutil.copyfile(vocos, model / 'vocos.onnx')
            if not all((model / name).is_file() for name in REQUIRED):
                raise ValueError('Incomplete ZipVoice installation')
            (model / 'checksums.txt').write_text(signature)
            backup = staging / 'previous'
            if MODEL_DIR.exists():
                MODEL_DIR.rename(backup)
            try:
                model.rename(MODEL_DIR)
            except OSError:
                if backup.exists():
                    backup.rename(MODEL_DIR)
                raise
    return MODEL_DIR


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, help='Existing model tar.bz2 for offline installation')
    parser.add_argument('--vocoder', type=Path, help='Existing Vocos ONNX for offline installation')
    args = parser.parse_args()
    print(install(args.archive, args.vocoder))
