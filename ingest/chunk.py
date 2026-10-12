"""Heading-aware chunking measured with the embedding tokenizer."""

from dataclasses import dataclass
import re
from typing import Any

from ingest.load import SourceDocument


TARGET_TOKENS = 200
OVERLAP_TOKENS = 30
MAX_TOKENS = 256
HEADING_PATTERN = re.compile(r"^(#{1,6})[ \t]+(.+?)\s*$")


@dataclass(frozen=True)
class TextChunk:
    text: str          # original source span (heading + body as written): stored and shown to the model
    section: str
    token_count: int   # measured on embed_text, exactly as before
    embed_text: str    # tokenizer-decoded text: the unchanged embedding input


def _sections(text: str, title: str) -> list[tuple[str, str, str]]:
    sections: list[tuple[str, str, str]] = []
    heading: str | None = None
    heading_line = f"# {title}"
    body_lines: list[str] = []

    def append_section() -> None:
        body = "\n".join(body_lines).strip()
        if body:
            section = heading or title
            prefix = heading_line if heading else f"# {title}"
            sections.append((section, prefix + "\n", body))

    for line in text.splitlines():
        match = HEADING_PATTERN.match(line)
        if match:
            append_section()
            heading = match.group(2).strip()
            heading_line = f"{match.group(1)} {heading}"
            body_lines = []
        else:
            body_lines.append(line)
    append_section()
    return sections


def _token_count(tokenizer: Any, text: str) -> int:
    return len(tokenizer.encode(text, add_special_tokens=True))


def chunk_document(
    document: SourceDocument,
    tokenizer: Any,
    target_tokens: int = TARGET_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
    max_tokens: int = MAX_TOKENS,
) -> list[TextChunk]:
    if target_tokens < 1 or target_tokens > max_tokens:
        raise ValueError("target_tokens must be between 1 and max_tokens")
    if overlap_tokens < 0 or overlap_tokens >= target_tokens:
        raise ValueError("overlap_tokens must be non-negative and smaller than target_tokens")

    special_token_count = len(tokenizer.encode("", add_special_tokens=True))
    chunks: list[TextChunk] = []
    for section, heading, body in _sections(document.text, document.metadata["title"]):
        heading_token_count = len(tokenizer.encode(heading, add_special_tokens=False))
        body_ids = tokenizer.encode(body, add_special_tokens=False)
        if not body_ids:
            continue
        # Character offsets of every body token, to recover the original text of a token window.
        encoded = tokenizer(body, add_special_tokens=False, return_offsets_mapping=True)
        offsets = encoded["offset_mapping"]
        if list(encoded["input_ids"]) != list(body_ids) or len(offsets) != len(body_ids):
            raise ValueError(f"{document.source}: tokenizer offsets do not align with token ids")

        body_capacity = max_tokens - special_token_count - heading_token_count
        if body_capacity < 1:
            raise ValueError(f"{document.source}: heading exceeds the {max_tokens}-token chunk limit")
        window_size = min(body_capacity, target_tokens - special_token_count - heading_token_count)
        window_size = max(1, window_size)
        overlap = min(overlap_tokens, window_size - 1)

        start = 0
        while start < len(body_ids):
            end = min(start + window_size, len(body_ids))
            while end > start:
                body_text = tokenizer.decode(body_ids[start:end], skip_special_tokens=True)
                chunk_text = (heading + body_text).strip()
                count = _token_count(tokenizer, chunk_text)
                if count <= max_tokens:
                    break
                end -= 1
            if end <= start:
                raise ValueError(f"{document.source}: unable to fit content under {max_tokens} tokens")

            assert count <= max_tokens, f"{document.source}: chunk has {count} tokens (limit {max_tokens})"
            original_span = body[offsets[start][0]:offsets[end - 1][1]]
            original_text = (heading + original_span).strip()
            chunks.append(TextChunk(original_text, section, count, chunk_text))
            if end == len(body_ids):
                break
            start = max(start + 1, end - overlap)

    if not chunks:
        raise ValueError(f"{document.source}: produced no non-empty chunks")
    assert all(chunk.token_count <= max_tokens for chunk in chunks)
    return chunks