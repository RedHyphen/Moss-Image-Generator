"""Conservative named-identity handling for conversational image requests.

This module deliberately does not maintain a fabricated character encyclopedia.
It only extracts an identity when the user supplies both a name and a source,
for example ``Furina from Genshin Impact``. Visual identity still requires a
real image-conditioned backend; text-only handling is explicitly best-effort.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_NAME_TOKEN = r"[A-Z][\w'’.-]*"
_SOURCE_TOKEN = r"[A-Z0-9][\w'’.-]*"
_NAME_FROM_SOURCE = re.compile(
    rf"(?<![\w])(?P<name>{_NAME_TOKEN}(?:\s+{_NAME_TOKEN}){{0,3}})\s+(?:from|of)\s+"
    rf"(?P<source>{_SOURCE_TOKEN}(?:\s+(?:{_SOURCE_TOKEN}|the|of|:|-)){{0,6}})",
    re.UNICODE,
)
_NAME_PAREN_SOURCE = re.compile(
    rf"(?<![\w])(?P<name>{_NAME_TOKEN}(?:\s+{_NAME_TOKEN}){{0,3}})\s*\((?P<source>[^()\n]{{2,80}})\)",
    re.UNICODE,
)
_GENERIC_NAMES = {
    "a",
    "an",
    "the",
    "boy",
    "girl",
    "man",
    "woman",
    "person",
    "character",
    "image",
    "photo",
    "portrait",
}
_LEADING_PROMPT_WORDS = {
    "create",
    "depict",
    "draw",
    "generate",
    "make",
    "render",
    "show",
    "use",
}
_TRAILING_SOURCE_WORDS = {"and", "the", "of", ":", "-"}


@dataclass(frozen=True)
class IdentityEntity:
    name: str
    source: str
    mention_count: int = 1

    @property
    def key(self) -> str:
        return f"{self.name.lower()}::{self.source.lower()}"


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip(" \t\r\n,.;:!?-"))


def _clean_name(value: str) -> str:
    parts = _clean(value).split()
    while len(parts) > 1 and parts[0].lower() in _LEADING_PROMPT_WORDS:
        parts.pop(0)
    return " ".join(parts)


def _valid_name(value: str) -> bool:
    normalized = _clean(value).lower()
    return bool(normalized) and normalized not in _GENERIC_NAMES and len(normalized) > 1


def _candidate_pairs(text: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for pattern in (_NAME_FROM_SOURCE, _NAME_PAREN_SOURCE):
        for match in pattern.finditer(text):
            name = _clean_name(match.group("name"))
            source = _clean(match.group("source"))
            source_parts = source.split()
            while source_parts and source_parts[-1].lower() in _TRAILING_SOURCE_WORDS:
                source_parts.pop()
            source = " ".join(source_parts)
            if _valid_name(name) and len(source) > 1:
                pairs.append((name, source))
    return pairs


def extract_named_identities(text: str) -> tuple[IdentityEntity, ...]:
    """Extract only explicit ``name from source`` identity pairs.

    A conservative false-negative is safer than turning an ordinary phrase
    such as ``a warrior from a forest`` into a named character identity.
    """

    entities: dict[str, IdentityEntity] = {}
    for name, source in _candidate_pairs(text):
        candidate = IdentityEntity(name=name, source=source)
        existing = entities.get(candidate.key)
        if existing:
            entities[candidate.key] = IdentityEntity(
                name=existing.name,
                source=existing.source,
                mention_count=existing.mention_count + 1,
            )
        else:
            entities[candidate.key] = candidate
    return tuple(entities.values())


def identity_instruction(entities: tuple[IdentityEntity, ...]) -> tuple[str, str, tuple[str, ...]]:
    """Return a compact model instruction, mode, and user-visible warnings."""

    if not entities:
        return "", "none", ()

    labels = ", ".join(f'"{item.name}" from "{item.source}"' for item in entities)
    repeated = any(item.mention_count > 1 for item in entities)
    consistency = (
        " If the identity appears more than once, every instance must be the same identity."
        if repeated
        else ""
    )
    instruction = (
        f"Named identity anchor: treat {labels} as one canonical identity and keep the name/source association intact."
        f"{consistency} Do not replace it with an unrelated character."
    )
    warnings = (
        "Named identity is text-only; exact visual likeness is not guaranteed because this MOSS-only workflow "
        "does not accept uploaded-image conditioning.",
    )
    return instruction, "text-only", warnings


def append_identity_instruction(prompt: str, instruction: str) -> str:
    if not instruction:
        return prompt
    return f"{prompt}\n\n{instruction}"
