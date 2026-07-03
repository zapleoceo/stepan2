"""Pure section-aware chunking for RAG — no I/O, no DB.

Content is markdown with `## ` section headings. Each section is greedily packed into
chunks of ~`max_chars`, never crossing a section boundary, and every chunk carries its
heading so the embedded text self-identifies. Mirrors Stepan-1's chunk_sections."""
from __future__ import annotations

_MAX_CHARS = 1400


def _split_sections(content: str) -> list[tuple[str, str]]:
    """Split into (heading, body) pairs by `## ` lines; text before the first heading
    is kept under an empty heading."""
    sections: list[tuple[str, str]] = []
    heading = ""
    buf: list[str] = []
    for line in content.splitlines():
        if line.startswith("## "):
            if buf or heading:
                sections.append((heading, "\n".join(buf).strip()))
            heading = line[3:].strip()
            buf = []
        else:
            buf.append(line)
    if buf or heading:
        sections.append((heading, "\n".join(buf).strip()))
    return sections


def _pack(body: str, max_chars: int) -> list[str]:
    """Greedily pack paragraphs (blank-line separated) into ≤max_chars pieces; a paragraph
    longer than the limit is split by lines so nothing is dropped."""
    pieces: list[str] = []
    cur = ""
    for para in body.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if len(para) > max_chars:
            if cur:
                pieces.append(cur)
                cur = ""
            pieces.extend(_split_long(para, max_chars))
            continue
        if cur and len(cur) + len(para) + 2 > max_chars:
            pieces.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        pieces.append(cur)
    return pieces


def _split_long(para: str, max_chars: int) -> list[str]:
    out: list[str] = []
    cur = ""
    for line in para.splitlines():
        if cur and len(cur) + len(line) + 1 > max_chars:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out


def chunk_sections(content: str, max_chars: int = _MAX_CHARS) -> list[str]:
    """Chunk `content` into embeddable pieces; each piece keeps its `## heading`."""
    chunks: list[str] = []
    for heading, body in _split_sections(content):
        if not body:
            continue
        prefix = f"## {heading}\n" if heading else ""
        chunks.extend(prefix + piece for piece in _pack(body, max_chars))
    return chunks
