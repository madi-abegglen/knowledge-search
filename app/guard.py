"""Exact-string guard for emails and URLs in a model answer.

Why code and not prompt: models occasionally alter exact strings (for example writing
"security @ northwind. example"). A prompt rule reduces that; a code check removes it.

Data fact: before 7b2 the chunk text was stored as the embedding tokenizer decoded it
(spaced, lowercased: "security @ northwind. example"); since 7b2 it is the original source
text. Matching removes whitespace on BOTH sides (answer candidate and chunk text) so it works
on either, and the repair target is the chunk's own string with stray whitespace removed
(its original casing on a 7b2 index).

Behavior (answer and conflict_note only):
- The only reference is the PERMITTED retrieved chunks passed to the model.
- repair: an email/URL-like candidate that is not verbatim in a chunk but matches exactly ONE
  string in the chunks once whitespace is removed (case-insensitive) is replaced by that string.
- blocked: a candidate that matches no string in the chunks (or more than one) makes the guard
  return "blocked"; the caller treats that as a validation failure (retry once, then refusal).
- none: no candidates, or all already verbatim.

The guard never raises and never returns the strings it handled (only counts and an action),
so nothing here needs to be logged beyond guard_action.
"""

from dataclasses import dataclass
import re
from typing import Iterable

ACTION_NONE = "none"
ACTION_REPAIRED = "repaired"
ACTION_BLOCKED = "blocked"

_EMAIL_LEFT = re.compile(r"([A-Za-z0-9._%+\-]+)\s*\Z")
_EMAIL_RIGHT = re.compile(r"\s*([A-Za-z0-9\-]+(?:\s*\.\s*[A-Za-z0-9\-]+)+)")
_URL_START = re.compile(r"(?:https?\s*:\s*/\s*/|www\s*\.\s*)", re.IGNORECASE)
_URL_BODY = re.compile(r"(?:[A-Za-z0-9\-_~%?#=&+:@]|\s*[./]\s*)+")
_CLOSERS = ".,;:!?)]}\"'"
_STRING_CHARS = re.compile(r"[A-Za-z0-9._%+\-@/:?#=&~]")
_LEFT_WINDOW = 80
_RIGHT_WINDOW = 300


@dataclass(frozen=True)
class GuardResult:
    action: str
    answer: str | None
    conflict_note: str | None
    repaired: int = 0
    blocked: int = 0


class _ChunkText:
    """A chunk with its whitespace-free, casefolded form and a map back to original offsets."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.positions = [i for i, ch in enumerate(text) if not ch.isspace()]
        self.folded = "".join(text[i] for i in self.positions).casefold()
        # casefold can change length for a few characters; fall back to an empty index then
        if len(self.folded) != len(self.positions):
            self.positions, self.folded = [], ""

    def matches(self, needle: str) -> set[str]:
        """Distinct whitespace-free strings in this chunk equal to `needle` (casefolded)."""
        found: set[str] = set()
        if not needle or not self.folded:
            return found
        start = self.folded.find(needle)
        while start != -1:
            first = self.positions[start]
            last = self.positions[start + len(needle) - 1]
            if self._clean_edges(first, last + 1):
                found.add("".join(ch for ch in self.text[first:last + 1] if not ch.isspace()))
            start = self.folded.find(needle, start + 1)
        return found

    def _clean_edges(self, begin: int, end: int) -> bool:
        """The match must not be the middle of a longer string."""
        if begin > 0 and _STRING_CHARS.fullmatch(self.text[begin - 1]):
            return False
        if end < len(self.text):
            nxt = self.text[end]
            if nxt.isspace():
                return True
            after = self.text[end + 1] if end + 1 < len(self.text) else " "
            if nxt in _CLOSERS and (after.isspace() or not _STRING_CHARS.fullmatch(after)):
                return True
            return not _STRING_CHARS.fullmatch(nxt)
        return True


_SENTENCE_TAIL = re.compile(r"\s*\.\s+[A-Z][A-Za-z0-9\-]*\Z")


def _trim_sentence_tail(text: str, start: int, end: int) -> int:
    """Stop before a following sentence: '... example. Do' ends at 'example'. A '. ' plus a
    capitalized word is read as a new sentence, not as another domain label."""
    while True:
        match = _SENTENCE_TAIL.search(text, start, end)
        if not match or match.start() <= start:
            return end
        end = match.start()


def _candidates(text: str) -> list[tuple[int, int]]:
    """Spans of email-like and URL-like substrings, tolerating spaces around '.', '/' and '@'."""
    spans: list[tuple[int, int]] = []
    for start_match in _URL_START.finditer(text):
        body = _URL_BODY.match(text, start_match.end(), min(len(text), start_match.end() + _RIGHT_WINDOW))
        end = body.end() if body else start_match.end()
        spans.append((start_match.start(), _trim_sentence_tail(text, start_match.start(), end)))
    for at in re.finditer("@", text):
        if any(start <= at.start() < end for start, end in spans):
            continue                                    # '@' inside a URL span
        left_from = max(0, at.start() - _LEFT_WINDOW)
        left = _EMAIL_LEFT.search(text, left_from, at.start())
        right = _EMAIL_RIGHT.match(text, at.end(), min(len(text), at.end() + _RIGHT_WINDOW))
        if left and right:
            start = left.start(1)
            spans.append((start, _trim_sentence_tail(text, start, right.end(1))))
    return sorted(spans)


def _email_shaped(prefix: str) -> bool:
    """For email-like prefixes the domain must contain a dot ('security@northwind' is not one)."""
    if "@" not in prefix or _URL_START.match(prefix):
        return True
    domain = prefix.split("@", 1)[1]
    return "." in re.sub(r"\s+", "", domain).strip(".")


def _boundary_ok(candidate: str, cut: int) -> bool:
    if cut == len(candidate) or candidate[cut].isspace():
        return True
    return candidate[cut] in _CLOSERS and (cut + 1 == len(candidate) or candidate[cut + 1].isspace())


def _resolve(candidate: str, chunks: list[_ChunkText]) -> tuple[str, str | None, int]:
    """(kind, exact_string, consumed_length) for the longest clean prefix found in the chunks."""
    for cut in range(len(candidate), 0, -1):
        if not _boundary_ok(candidate, cut):
            continue
        prefix = candidate[:cut]
        if not _email_shaped(prefix):
            continue
        needle = re.sub(r"\s+", "", prefix).casefold()
        targets: set[str] = set()
        for chunk in chunks:
            targets |= chunk.matches(needle)
        if not targets:
            continue
        if prefix in targets:                            # already verbatim in a chunk
            return "exact", prefix, cut
        if len(targets) > 1:                             # altered, and two chunk strings fit
            return "ambiguous", None, cut
        return "repair", next(iter(targets)), cut
    return "unknown", None, len(candidate)


def _guard_text(text: str, chunks: list[_ChunkText]) -> tuple[str, int, int]:
    repairs = blocks = 0
    pieces: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end in _candidates(text):
        if start < last_end:                            # overlapping candidate: skip
            continue
        kind, exact, consumed = _resolve(text[start:end], chunks)
        last_end = end
        if kind in ("unknown", "ambiguous"):
            blocks += 1
        elif kind == "repair" and exact is not None:
            repairs += 1
            pieces.append((start, start + consumed, exact))
    for start, stop, exact in reversed(pieces):
        text = text[:start] + exact + text[stop:]
    return text, repairs, blocks


def apply_exact_string_guard(
    answer: str | None,
    conflict_note: str | None,
    chunk_texts: Iterable[str],
) -> GuardResult:
    """Never raises: on any internal error the texts are returned unchanged with action none
    (the permission leak scan and citation checks still run afterwards)."""
    try:
        chunks = [_ChunkText(t) for t in chunk_texts if isinstance(t, str)]
        new_answer, new_note = answer, conflict_note
        repairs = blocks = 0
        if isinstance(answer, str) and answer:
            new_answer, r, b = _guard_text(answer, chunks)
            repairs, blocks = repairs + r, blocks + b
        if isinstance(conflict_note, str) and conflict_note:
            new_note, r, b = _guard_text(conflict_note, chunks)
            repairs, blocks = repairs + r, blocks + b
        if blocks:
            return GuardResult(ACTION_BLOCKED, answer, conflict_note, repairs, blocks)
        if repairs:
            return GuardResult(ACTION_REPAIRED, new_answer, new_note, repairs, 0)
        return GuardResult(ACTION_NONE, answer, conflict_note)
    except Exception:
        return GuardResult(ACTION_NONE, answer, conflict_note)
