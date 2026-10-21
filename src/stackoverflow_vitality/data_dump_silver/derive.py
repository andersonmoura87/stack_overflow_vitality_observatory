"""Deterministic body and tag derivations for Data Dump Silver."""

from __future__ import annotations

import re

from stackoverflow_vitality.silver.content import clean_body

_TAG = re.compile(r"<([^<>]+)>")


def derive_body(body_html: str | None) -> tuple[str | None, list[str] | None]:
    text, blocks = clean_body(body_html)
    if blocks is None:
        return text, None
    return text, list(blocks)


def derive_tags(tags_raw: str | None) -> list[str]:
    if tags_raw is None or tags_raw == "":
        return []
    seen: set[str] = set()
    for raw in _TAG.findall(tags_raw):
        tag = raw.strip()
        if tag:
            seen.add(tag)
    return sorted(seen)
