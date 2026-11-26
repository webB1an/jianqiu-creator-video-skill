"""QA domain facade: unified preflight/postcheck, narrative diversity gate, media QC & DeepSeek/local-VLM critique."""
from studio import DOMAINS, load

__all__ = tuple(DOMAINS["qa"]["modules"])


def __getattr__(name: str):
    if name in __all__:
        return load(name)
    raise AttributeError(name)
