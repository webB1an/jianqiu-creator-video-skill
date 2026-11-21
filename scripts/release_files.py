"""Publish the master and its companions through one atomically replaced directory link."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

STORE = '.release-versions'


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def publication_lock(parent):
    parent.mkdir(parents=True, exist_ok=True)
    store = parent / STORE
    store.mkdir(exist_ok=True)
    sync_directory(parent)
    with (store / 'publish.lock').open('a+b') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        # These working directories never contain a committed version. A previous
        # writer may have been killed before normal context-manager cleanup.
        for stale in store.glob('.draft-*'):
            if stale.is_dir() and not stale.is_symlink():
                shutil.rmtree(stale)
        yield store


def current_version(store):
    pointer = store / 'current'
    if not pointer.is_symlink():
        return None
    version = pointer.resolve(strict=True)
    if version.parent != store.resolve() or not version.name.startswith('v-'):
        raise ValueError(f'Invalid published version link: {pointer}')
    return version


def alias_target(store, path, relative):
    return os.path.relpath(store.resolve() / 'current' / relative, path.parent.resolve())


def is_alias(store, path, relative):
    return path.is_symlink() and os.readlink(path) == alias_target(store, path, relative)


def replace_link(path, target, scratch):
    # The link contents are relative to their final location, not to scratch.
    temp = scratch / str(uuid4())
    temp.symlink_to(target)
    os.replace(temp, path)
    sync_directory(path.parent)


def copy_checked(source, destination, checksum=None):
    h = hashlib.sha256()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as src, destination.open('xb') as dst:
        for chunk in iter(lambda: src.read(1024 * 1024), b''):
            dst.write(chunk)
            h.update(chunk)
        dst.flush()
        os.fsync(dst.fileno())
    if checksum is not None and h.hexdigest() != checksum:
        raise ValueError(f'Approved bytes changed during release: {source}')


def identical_bytes(left, right):
    with left.open('rb') as a, right.open('rb') as b:
        for left_chunk in iter(lambda: a.read(1024 * 1024), b''):
            if left_chunk != b.read(len(left_chunk)):
                return False
        return not b.read(1)


def matches_file(path, expected):
    if not path.is_file():
        return False
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest() == expected


def save_version(store, previous, output_name, files, sidecars, scratch, legacy=False):
    """Each retained master has its own file, matching receipt and complete sidecars."""
    draft = scratch / str(uuid4())
    version = store / ('v-' + uuid4().hex)
    draft.mkdir()
    # Other named masters can share an output directory. Keep their public links
    # usable, and let readers pin the historical master with its own companions.
    incoming = {output_name, output_name + '.review.json', *sidecars, *files}
    if previous:
        for entry in previous.iterdir():
            if entry.is_file() and not entry.name.startswith('.') and entry.name not in incoming:
                (draft / entry.name).symlink_to(os.path.relpath(entry.resolve(), version))
    for rel, (src, checksum) in files.items():
        copy_checked(src, draft / rel, checksum)
    metadata = dict(master=output_name, previous=previous.name if previous else None,
                    companions=list(sidecars), legacy=legacy)
    with (draft / '.publication.json').open('w', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    for directory, _, _ in os.walk(draft, topdown=False):
        sync_directory(Path(directory))
    draft.rename(version)
    sync_directory(store)
    return version


def snapshot(media, companions_for):
    """Pin managed reads to one generation while still detecting altered public aliases."""
    media = Path(media)
    parent = media.parent
    store = parent / STORE
    lock = store / 'publish.lock'
    if not lock.is_file():
        return media
    with lock.open('rb') as stream:
        fcntl.flock(stream, fcntl.LOCK_SH)
        if not is_alias(store, media, media.name):
            return media
        return pinned_media(media, store, companions_for)


def pinned_media(media, store, companions_for):
    parent = media.parent
    active = current_version(store)
    if not active:
        raise FileNotFoundError(media)
    pinned = (active / media.name).resolve(strict=True)
    if pinned.parent == active:
        for rel in companions_for(pinned):
            public, frozen = parent / rel, pinned.parent / rel
            if not is_alias(store, public, rel):
                # An interrupted first migration can still expose regular legacy
                # files. Accept only their exact prior contents during that window.
                if not (public.is_file() and frozen.is_file() and identical_bytes(public, frozen)):
                    raise ValueError(f'Published companion changed: {public}')
    return pinned


def prune_unused_credits(store, parent):
    directory = parent / 'credits'
    if not directory.is_dir() or directory.is_symlink():
        return
    for root, _, files in os.walk(directory, followlinks=False):
        for name in files:
            path = Path(root) / name
            if not path.exists() and is_alias(store, path, path.relative_to(parent)):
                path.unlink()
                sync_directory(path.parent)


def promote_group(candidate, output, approved_sha, bundle_sha, checksums, receipt, companions_for):
    output = Path(output)
    parent = output.parent.resolve()
    output = parent / output.name
    if output.name in {*checksums, 'covers', 'credits', STORE} or output.name.endswith('.review.json'):
        raise ValueError('The master path overlaps a companion or the release store')
    with publication_lock(parent) as store, tempfile.TemporaryDirectory(prefix='.draft-', dir=store) as temp:
        scratch = Path(temp)
        if bundle_sha is None:
            if output.with_name(output.name + '.review.json').exists():
                raise ValueError('Stage captions and covers before replacing a previously bundled release')
            # An unbundled master is a single-file commit.
            draft = scratch / 'master'
            copy_checked(candidate, draft, approved_sha)
            os.replace(draft, output)
            sync_directory(parent)
            return output

        files = {rel: (candidate.parent / rel, checksum) for rel, checksum in checksums.items()}
        files[output.name + '.review.json'] = (receipt, bundle_sha)
        files[output.name] = (candidate, approved_sha)
        previous = current_version(store)
        # During first migration (or if an editor republished a changed regular
        # companion), all old bytes remain visible until *every* alias is ready.
        old_companions = []
        if previous:
            old_companions = json.loads((previous / '.publication.json').read_text())['companions']
        elif output.is_file() and output.with_name(output.name + '.review.json').is_file():
            old_companions = [rel for rel in companions_for(output) if rel != output.name + '.review.json']
        visible = dict.fromkeys([*files, *old_companions])
        unmanaged = [rel for rel in visible if not is_alias(store, parent / rel, rel)]
        if previous and not unmanaged and not (previous / output.name).is_symlink() and (previous / output.name).is_file():
            if all(matches_file(previous / rel, checksum) for rel, (_, checksum) in files.items()):
                prune_unused_credits(store, parent)
                return output
        if any((parent / rel).is_file() for rel in unmanaged):
            legacy = {rel: (parent / rel, None) for rel in visible if (parent / rel).is_file()}
            previous = save_version(store, previous, output.name, legacy, old_companions, scratch, legacy=True)
            replace_link(store / 'current', previous.name, scratch)

        ready = save_version(store, previous, output.name, files, checksums, scratch)
        for rel in unmanaged:
            path = parent / rel
            if not path.parent.exists():
                path.parent.mkdir(parents=True)
                sync_directory(parent)
            replace_link(path, alias_target(store, path, rel), scratch)
        # This single rename exposes the complete new group. Readers that need
        # multiple files across successive opens can pin one version with snapshot.
        replace_link(store / 'current', ready.name, scratch)
        for rel in set(old_companions) - checksums.keys():
            obsolete = parent / rel
            if is_alias(store, obsolete, rel):
                obsolete.unlink()
                sync_directory(obsolete.parent)
        prune_unused_credits(store, parent)
        return output
