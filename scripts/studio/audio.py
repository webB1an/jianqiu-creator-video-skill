"""Audio domain facade: CC0 multi-stem music, fills/cadence, Foley SFX & broadcast mix."""
from studio import DOMAINS, load

__all__ = tuple(DOMAINS["audio"]["modules"])


def __getattr__(name: str):
    if name in __all__:
        return load(name)
    raise AttributeError(name)
