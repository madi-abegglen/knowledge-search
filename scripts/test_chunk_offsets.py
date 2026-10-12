"""Tests for original-text chunks (tokenizer offset mapping). Uses the real embedding tokenizer.

Run from the repo root:  .\\.venv\\Scripts\\python.exe scripts\\test_chunk_offsets.py

The "unchanged boundaries" test compares chunk.embed_text for every real document with the text
stored in the CURRENT real index (read-only), so chunk boundaries, headings and overlap are
proven identical to what was indexed before 7b2.
"""

import os
from pathlib import Path
import sys

import chromadb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sentence_transformers import SentenceTransformer  # noqa: E402

from ingest.chunk import MAX_TOKENS, chunk_document  # noqa: E402
from ingest.load import SourceDocument, load_documents  # noqa: E402

REAL_CHROMA = ROOT / "chroma_db"
TOKENIZER = SentenceTransformer(os.environ.get("EMBED_MODEL", "all-MiniLM-L6-v2"),
                                trust_remote_code=False).tokenizer


def doc(text: str, title: str = "Test Policy") -> SourceDocument:
    metadata = {"title": title, "version": "1", "effective_date": "2025-01-01",
                "doc_status": "current", "last_modified": "2025-01-01T00:00:00+00:00"}
    return SourceDocument("test.md", text, metadata, frozenset({"all"}))


LONG = "\n".join(
    ["# Test Policy", "", "## 1. Rules"]
    + [f"Rule {i}: Employees MUST Not Share Passwords (see IT-FAQ) or email help@Example.com, "
       f"costs $ {i}0 per year." for i in range(1, 40)]
    + ["", "## 2. Short", "Keep it SHORT & sweet."]
)


def real_documents() -> list[SourceDocument]:
    return load_documents(ROOT / "data")


def test_original_casing_punctuation_and_markdown_preserved() -> None:
    document = next(d for d in real_documents() if d.source == "it_security_incident_reporting.md")
    texts = [c.text for c in chunk_document(document, TOKENIZER)]
    joined = "\n".join(texts)
    assert '**Report immediately** (within 1 hour) using the "Report Phish" button' in joined
    assert "security@northwind.example" in joined
    assert "security @ northwind" not in joined
    expense = next(d for d in real_documents() if d.source == "finance_expense_policy.md")
    assert "$75 per day" in "\n".join(c.text for c in chunk_document(expense, TOKENIZER))


def test_heading_is_kept_on_every_chunk() -> None:
    chunks = chunk_document(doc(LONG), TOKENIZER)
    assert len(chunks) > 3
    for chunk in chunks:
        assert chunk.text.startswith("## "), chunk.text[:40]
        assert chunk.text.split("\n", 1)[0].lstrip("# ").strip() == chunk.section
        assert chunk.embed_text.split("\n", 1)[0] == chunk.text.split("\n", 1)[0]


def test_no_chunk_is_empty_and_text_is_a_source_substring() -> None:
    for document in real_documents() + [doc(LONG)]:
        for chunk in chunk_document(document, TOKENIZER):
            assert chunk.text.strip() and chunk.embed_text.strip()
            _, _, body = chunk.text.partition("\n")
            assert body and body in document.text, (document.source, chunk.section, body[:60])


def test_overlap_is_present_between_consecutive_windows() -> None:
    chunks = [c for c in chunk_document(doc(LONG), TOKENIZER) if c.section == "1. Rules"]
    assert len(chunks) >= 3
    shared = 0
    for first, second in zip(chunks, chunks[1:]):
        tail = first.text.rsplit("\n", 1)[-1]               # last line (or partial line) of chunk k
        head = second.text.split("\n", 1)[1]
        if tail[-25:] in head:
            shared += 1
    assert shared >= 1, "expected overlapping original text between consecutive chunks"


def test_boundaries_headings_and_overlap_identical_to_the_current_real_index() -> None:
    collection = chromadb.PersistentClient(path=str(REAL_CHROMA)).get_collection("knowledge_chunks")
    stored = collection.get(include=["documents", "metadatas"])
    old = {cid: (text, meta["section"]) for cid, text, meta in
           zip(stored["ids"], stored["documents"], stored["metadatas"])}
    produced = {}
    for document in real_documents():
        for index, chunk in enumerate(chunk_document(document, TOKENIZER)):
            produced[f"{document.source}::{index}"] = (chunk.embed_text, chunk.section)
    assert produced == old, "embed_text/sections/ids differ from the indexed chunks"
    assert len(produced) == 65


def test_token_count_semantics_unchanged_and_cap_respected() -> None:
    worst = 0
    for document in real_documents():
        for chunk in chunk_document(document, TOKENIZER):
            decoded_count = len(TOKENIZER.encode(chunk.embed_text, add_special_tokens=True))
            assert chunk.token_count == decoded_count <= MAX_TOKENS
            original_count = len(TOKENIZER.encode(chunk.text, add_special_tokens=True))
            worst = max(worst, original_count)
    assert worst <= MAX_TOKENS, f"original text re-encodes to {worst} tokens (> {MAX_TOKENS})"
    print(f"      max re-encoded tokens of original text: {worst} (cap {MAX_TOKENS})")


def test_non_ascii_and_odd_characters_map_cleanly() -> None:
    text = ("# Odd Policy\n\n## 1. Unicode\nCafé résumé naïve – “quoted” "
            "日本語 \U0001F600 emoji; price €45.50. Contact: SUPPORT@Cafe.example.\n")
    chunks = chunk_document(doc(text, title="Odd Policy"), TOKENIZER)
    assert chunks
    for chunk in chunks:
        assert chunk.text.strip()
        _, _, body = chunk.text.partition("\n")
        assert body in text
    assert "SUPPORT@Cafe.example" in "\n".join(c.text for c in chunks)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
