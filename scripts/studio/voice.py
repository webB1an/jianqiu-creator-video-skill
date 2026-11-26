"""Voice domain facade: multi-mood ZipVoice, prosody markup, pronunciation & speech clock."""
from studio import DOMAINS, load

__all__ = tuple(DOMAINS["voice"]["modules"])


def __getattr__(name: str):
    if name in __all__:
        return load(name)
    raise AttributeError(name)
