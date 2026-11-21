"""Project locking, verified input snapshots and recovery for prepare and export."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time


WORK = Path('.creator-run')
STATUS = Path('qa/production-run.json')
PREPARED = ('audio/narration.wav', 'audio/captions.srt', 'timeline.json')


def digest(path):
    checksum = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def sync_dir(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as writer:
        tmp = Path(writer.name)
        try:
            json.dump(data, writer, ensure_ascii=False, indent=2)
            writer.write('\n')
            writer.flush()
            os.fsync(writer.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, path)
        sync_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def install_file(src, dest):
    """Replace a single target without sharing a mutable inode with the source."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dest.parent, prefix='.install-', delete=False) as writer:
        tmp = Path(writer.name)
    try:
        shutil.copyfile(src, tmp)
        with tmp.open('rb') as handle:
            os.fsync(handle.fileno())
        os.replace(tmp, dest)
        sync_dir(dest.parent)
    finally:
        tmp.unlink(missing_ok=True)


class InputSnapshot:
    """Render copied bytes; changing a live dependency prevents committing the result."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True)
        self.files = {}
        self.hashes = {}

    @staticmethod
    def key(path):
        # Retain logical symlink names so that relinking an input is detected at commit.
        path = Path(os.path.abspath(path))
        try:
            return str(path.relative_to(Path.cwd()))
        except ValueError:
            return str(path)

    def pin(self, path, optional=False):
        key = self.key(path)
        if key in self.files:
            return self.files[key]
        original = Path(key)
        target = self.directory / (str(len(self.files)) + original.suffix)
        if optional and not original.exists():
            checksum = None
        else:
            if not original.is_file():
                raise ValueError(f'Expected a regular media or project file: {original}')
            shutil.copyfile(original, target)
            checksum = digest(target)
        if key in self.hashes and self.hashes[key] != checksum:
            raise ValueError(f'Input changed after snapshot: {key}')
        self.files[key] = target
        self.hashes[key] = checksum
        return target

    def expect(self, path, checksum):
        key = self.key(path)
        if key in self.hashes and self.hashes[key] != checksum:
            raise ValueError(f'Input disagrees with its recorded version: {key}')
        self.hashes[key] = checksum

    def verify(self):
        for name, expected in self.hashes.items():
            original = Path(name)
            current = digest(original) if original.is_file() else None
            if current != expected:
                raise ValueError(f'Input changed during this stage: {name}. Retry using the new source.')

    def recorded(self):
        return {name: h for name, h in self.hashes.items() if h is not None}


def recover_prepared():
    journal = WORK / 'pending-prepare'
    if not journal.exists():
        return False
    expected = json.loads((journal / 'hashes.json').read_text())
    if set(expected) != set(PREPARED) or any(digest(journal / name) != expected[name] for name in PREPARED):
        raise ValueError('Damaged prepare journal. Restore the project or rebuild using the original inputs.')
    for name in PREPARED:
        target = Path(name)
        if not target.is_file() or digest(target) != expected[name]:
            install_file(journal / name, target)
    retired = Path(tempfile.mkdtemp(dir=WORK, prefix='retired-'))
    retired.rmdir()
    os.rename(journal, retired)
    sync_dir(WORK)
    shutil.rmtree(retired)
    return True


def commit_prepared(staging):
    hashes = {name: digest(staging / name) for name in PREPARED}
    for name in PREPARED:
        with (staging / name).open('rb') as stream:
            os.fsync(stream.fileno())
    sync_dir(staging / 'audio')
    atomic_json(staging / 'hashes.json', hashes)
    os.rename(staging, WORK / 'pending-prepare')
    sync_dir(WORK)
    recover_prepared()


@contextmanager
def stage_run(name):
    """Serialize cooperating producers and preserve their latest attempt and timing."""
    WORK.mkdir(exist_ok=True)
    with (WORK / 'project.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        summary = json.loads(STATUS.read_text()) if STATUS.is_file() else {'schema': 1, 'stages': {}}
        now = datetime.now(timezone.utc)
        for previous in summary['stages'].values():
            if previous.get('status') == 'running':
                previous['status'] = 'interrupted'
                previous['interrupted_at'] = now.isoformat()
        prior = summary['stages'].get(name, {})
        entry = {'attempt': prior.get('attempt', 0) + 1, 'status': 'running', 'started_at': now.isoformat()}
        if prior:
            entry['previous_status'] = prior['status']
        summary['stages'][name] = entry
        atomic_json(STATUS, summary)
        started = time.monotonic()
        try:
            entry['recovered_preparation'] = recover_prepared()
            # After a kill, only this project's owned stage scratch survives. A committed
            # prepare journal and the verified shot cache have separate names.
            for orphan in WORK.glob('scratch-*'):
                if orphan.is_dir() and not orphan.is_symlink():
                    shutil.rmtree(orphan)
            with tempfile.TemporaryDirectory(dir=WORK, prefix='scratch-') as temp:
                scratch = Path(temp).resolve()
                try:
                    yield scratch, entry
                finally:
                    entry['scratch_bytes_at_end'] = sum(path.stat().st_size for path in scratch.rglob('*') if path.is_file())
            entry['status'] = 'complete'
        except BaseException as exc:
            detail = getattr(exc, 'stderr', '') or str(exc)
            entry.update(status='failed', error=str(detail)[-2048:])
            raise
        finally:
            entry['elapsed_seconds'] = round(time.monotonic() - started, 3)
            entry['ended_at'] = datetime.now(timezone.utc).isoformat()
            atomic_json(STATUS, summary)
