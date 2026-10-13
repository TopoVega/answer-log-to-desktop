"""Self-describing HTML-comment markers embedded in exported Markdown.

Two problems this solves:

  * the workbench used to re-parse its own rendered text with regexes looking
    for `**最后更新**: ...` — so renaming a label silently broke the panel;
  * incoming drops were segmented by matching Chinese heading text, so a peer
    platform rewording a heading degraded the import.

An HTML comment is invisible in rendered Markdown, survives a copy/paste or a
drag into another agent, and carries JSON, so nothing has to be guessed from
prose. It is advisory: the database remains authoritative for the UI, and a
missing or malformed marker always falls back to the legacy heuristics rather
than failing the import.
"""

from __future__ import annotations

import hashlib
import json
import re

SCHEMA = 1
PREFIX = "agent-workbench"

_ENTRY = re.compile(rf"<!--\s*{PREFIX}:entry:v1\s+(\{{.*?\}})\s*-->", re.DOTALL)
_DOC = re.compile(rf"<!--\s*{PREFIX}:doc:v1\s+(\{{.*?\}})\s*-->", re.DOTALL)


def _render(kind: str, meta: dict) -> str:
    # "</" inside a JSON string would terminate the comment early.
    blob = json.dumps(meta, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f"<!-- {PREFIX}:{kind}:v1 {blob} -->"


def entry_marker(meta: dict) -> str:
    return _render("entry", meta)


def doc_marker(meta: dict) -> str:
    return _render("doc", meta)


def _parse(pattern: re.Pattern, text: str) -> dict | None:
    m = pattern.search(text or "")
    if not m:
        return None
    try:
        raw = m.group(1).replace("<\\/", "</")
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def read_entry(text: str) -> dict | None:
    return _parse(_ENTRY, text)


def read_doc(text: str) -> dict | None:
    return _parse(_DOC, text)


def content_sha(text: str) -> str:
    """Stable hash of body content, used to dedupe re-dropped files by content
    rather than by name."""
    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()[:16]


def strip_markers(text: str) -> str:
    """Remove our comments so they never land inside a parsed section body."""
    text = _ENTRY.sub("", text)
    return _DOC.sub("", text)
