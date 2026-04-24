"""PaperFoundry — arXiv monitoring + local-LLM paper triage.

Pipeline: monitor → papers.json → filter (with topics + llm).

The primary public API is exposed lazily at the package level, so both

    import PaperFoundry
    PaperFoundry.LLMClient(...)

and

    from PaperFoundry.llm import LLMClient

work. Submodules are imported on first access, which also keeps
``python -m PaperFoundry.<module>`` free of double-import warnings.
"""

from importlib import import_module

_EXPORTS = {
    "LLMClient": "llm",
    "Paper": "monitor",
    "ArxivFetcher": "monitor",
    "LiteratureMonitor": "monitor",
    "Topic": "topics",
    "load_topics": "topics",
    "FastFilter": "filter",
    "Prompt": "prompt",
    "PromptLibrary": "prompt",
}

__all__ = list(_EXPORTS.keys())


def __getattr__(name):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f".{module_name}", __name__), name)


def __dir__():
    return sorted(list(globals().keys()) + __all__)
