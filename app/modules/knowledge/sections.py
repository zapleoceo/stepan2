"""Split a doc into `## ` sections for the editor and reassemble it on save (pure).

Mirrors Stepan-1's per-section editing: a doc is edited as one textarea per `## heading`,
never as a single wall of text. Text before the first heading is the preamble (empty
heading). Reassembly restores the exact `## heading` markers so the stored markdown — and
thus the RAG chunk boundaries — stay stable."""
from __future__ import annotations


def split_sections(content: str) -> list[tuple[str, str]]:
    """Return (heading, body) pairs in document order; preamble has an empty heading."""
    sections: list[tuple[str, str]] = []
    heading = ""
    buf: list[str] = []
    for line in (content or "").splitlines():
        if line.startswith("## "):
            if heading or buf:
                sections.append((heading, "\n".join(buf).strip()))
            heading = line[3:].strip()
            buf = []
        else:
            buf.append(line)
    if heading or buf:
        sections.append((heading, "\n".join(buf).strip()))
    return [(h, b) for h, b in sections if h or b]


def reassemble(pairs: list[tuple[str, str]]) -> str:
    """Rebuild markdown from (heading, body) pairs — inverse of split_sections. Sections
    with an empty body are dropped so unfilled placeholder headings aren't persisted."""
    out: list[str] = []
    for heading, raw in pairs:
        body = raw.strip()
        if not body:
            continue
        out.append(f"## {heading}\n{body}" if heading else body)
    return "\n\n".join(out)
