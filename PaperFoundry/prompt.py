"""Prompts as shaders: compiled, parameter-aware prompt programs.

A ``.prompt`` file is the source. Parsing it yields a :class:`Prompt` — analogous
to a compiled shader program — with two section templates (``[system]`` /
``[user]``) and a discovered set of parameters (the ``{name}`` placeholders).
Calling :meth:`Prompt.render` binds parameters and returns the filled strings,
validating strictly against the declared parameter set.

``PromptLibrary`` is the directory-backed registry: list what's available, load
by name. By default it points at ``PaperFoundry/prompts/``.

File format
-----------
Section header: a line whose stripped form is exactly ``[system]`` or
``[user]``. Body: every line between two headers (or from a header to EOF),
with surrounding whitespace stripped. Both sections are required.

    [system]
    You are a classifier...

    [user]
    Topic: {topic_name}
    Paper: {title}
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from string import Formatter
from typing import Dict, FrozenSet, List, Optional

_DEFAULT_ROOT = Path(__file__).parent / "prompts"
_SECTIONS = ("system", "user")
_OPTIONAL_SECTIONS = ("paginate",)
_ALL_SECTION_HEADERS = frozenset(f"[{s}]" for s in (*_SECTIONS, *_OPTIONAL_SECTIONS))


def _parse_sections(text: str, source: Path) -> Dict[str, str]:
    """Split a .prompt file into {section_name: body_str}.

    Required: [system] and [user]. Optional: [paginate].
    """
    sections: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in _ALL_SECTION_HEADERS:
            current = stripped[1:-1]
            sections[current] = []
        elif current is not None:
            sections[current].append(line)

    for key in _SECTIONS:
        if key not in sections:
            raise ValueError(f"Prompt file {source} missing [{key}] section")
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def _parse_paginate_input(body: str) -> Optional[str]:
    """Return the 'input' value from a [paginate] section body, or None."""
    for line in body.splitlines():
        key, _, val = line.strip().partition("=")
        if key.strip() == "input" and val.strip():
            return val.strip()
    return None


def _discover_params(template: str) -> FrozenSet[str]:
    """Return the set of {name} placeholders in a format-string template."""
    names = set()
    for _, field_name, _, _ in Formatter().parse(template):
        if field_name:
            # Strip attribute/index access, keep only the root name
            root = field_name.split(".", 1)[0].split("[", 1)[0]
            names.add(root)
    return frozenset(names)


@dataclass(frozen=True)
class Prompt:
    """A compiled prompt program: two templates plus a declared parameter set."""

    name: str
    system_template: str
    user_template: str
    parameters: FrozenSet[str]
    source_path: Path
    paginate_input: Optional[str] = None

    @classmethod
    def load(cls, name: str, root: Optional[Path] = None) -> "Prompt":
        """Load ``<root>/<name>.prompt`` and compile it."""
        return PromptLibrary(root).load(name)

    def validate(self, params: dict) -> None:
        """Raise ValueError if params don't exactly match self.parameters."""
        supplied = set(params)
        missing = self.parameters - supplied
        extra = supplied - self.parameters
        if missing or extra:
            parts = []
            if missing:
                parts.append(f"missing: {sorted(missing)}")
            if extra:
                parts.append(f"unknown: {sorted(extra)}")
            raise ValueError(f"Prompt {self.name!r} parameter mismatch — {'; '.join(parts)}")

    def render(self, **params) -> Dict[str, str]:
        """Bind parameters; return {'system': ..., 'user': ...}."""
        self.validate(params)
        return {
            "system": self.system_template.format(**params),
            "user": self.user_template.format(**params),
        }


class PromptLibrary:
    """Directory-backed registry of .prompt files."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else _DEFAULT_ROOT

    def load(self, name: str) -> Prompt:
        path = self.root / f"{name}.prompt"
        if not path.is_file():
            raise FileNotFoundError(f"Prompt file not found: {path}")
        sections = _parse_sections(path.read_text(encoding="utf-8"), path)
        params = _discover_params(sections["system"]) | _discover_params(sections["user"])
        paginate_input = _parse_paginate_input(sections.get("paginate", ""))
        return Prompt(
            name=name,
            system_template=sections["system"],
            user_template=sections["user"],
            parameters=params,
            source_path=path,
            paginate_input=paginate_input,
        )

    def list(self) -> List[str]:
        """Return the names (stems) of all .prompt files, sorted."""
        if not self.root.is_dir():
            return []
        return sorted(p.stem for p in self.root.glob("*.prompt"))
