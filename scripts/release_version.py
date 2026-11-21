"""Identify the installed skill by the packaged bytes and executable bits."""
import hashlib
import json
from pathlib import Path


def installed_version(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    manifest = {}
    for path in root.rglob('*'):
        rel = path.relative_to(root)
        if any(part.startswith('.') or part == '__pycache__' for part in rel.parts) or path.suffix == '.pyc':
            continue
        if path.is_symlink():
            raise ValueError(f'Skill release contains a symbolic link: {rel}')
        if path.is_file():
            manifest[rel.as_posix()] = [hashlib.sha256(path.read_bytes()).hexdigest(),
                                        bool(path.stat().st_mode & 0o111)]
    if 'SKILL.md' not in manifest:
        raise ValueError(f'Missing skill entry point: {root}')
    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(encoded).hexdigest()


if __name__ == '__main__':
    print(installed_version())
