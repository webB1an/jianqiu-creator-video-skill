"""Render domain facade: prepare (J/L-cut), export (master/proxy/patch), beat_snap, portrait & covers."""
from studio import DOMAINS, load

__all__ = tuple(DOMAINS["render"]["modules"])


def __getattr__(name: str):
    if name in __all__:
        return load(name)
    raise AttributeError(name)
