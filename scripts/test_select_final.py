"""Plain-assert tests for app.retrieve._select_final (synthetic chunks, no API, no model load).

Run from the repo root:  .\\.venv\\Scripts\\python.exe scripts\\test_select_final.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.retrieve import KEEP_BEST, _select_final  # noqa: E402


def chunk(name: str, distance: float, status: str = "current", title: str = "Other") -> dict:
    return {
        "source": f"{name}.md",
        "text": name,
        "metadata": {"doc_status": status, "title": title, "chunk_id": name},
        "distance": distance,
    }


def ids(chunks: list[dict]) -> list[str]:
    return [c["metadata"]["chunk_id"] for c in chunks]


def old_behavior(chunks: list[dict]) -> list[dict]:
    ordered = sorted(chunks, key=lambda c: (c["metadata"]["doc_status"] != "current", c["distance"]))
    return ordered[:KEEP_BEST]


def test_reserve_slot_with_five_plus_current() -> None:
    chunks = [
        chunk("old_a", 0.10, "superseded", "PTO Policy"),
        chunk("cur_a", 0.20, title="PTO Policy"),
        chunk("cur_b", 0.30, title="PTO Policy"),
        chunk("cur_c", 0.40, title="Holiday"),
        chunk("cur_d", 0.50, title="Holiday"),
        chunk("cur_e", 0.55, title="Holiday"),
        chunk("old_b", 0.60, "superseded", "PTO Policy"),
        chunk("cur_f", 0.70, title="Holiday"),
    ]
    out = _select_final(chunks)
    assert ids(out) == ["cur_a", "cur_b", "cur_c", "cur_d", "old_a"], ids(out)
    assert sum(c["metadata"]["doc_status"] != "current" for c in out) == 1
    assert out[-1]["metadata"]["doc_status"] == "superseded"


def test_no_title_match_equals_old_behavior() -> None:
    chunks = [
        chunk("old_a", 0.10, "superseded", "Legacy Thing"),
        chunk("cur_a", 0.20, title="A"),
        chunk("cur_b", 0.30, title="B"),
        chunk("cur_c", 0.40, title="C"),
        chunk("cur_d", 0.50, title="D"),
        chunk("cur_e", 0.55, title="E"),
        chunk("cur_f", 0.70, title="F"),
        chunk("old_b", 0.80, "superseded", "Legacy Other"),
    ]
    assert ids(_select_final(chunks)) == ids(old_behavior(chunks))
    few = [chunk("cur_a", 0.2, title="A"), chunk("old_a", 0.1, "superseded", "Z"),
           chunk("old_b", 0.3, "superseded", "Y")]
    assert ids(_select_final(few)) == ids(old_behavior(few))


def test_two_current_reserved_and_extras_fill_without_duplicates() -> None:
    chunks = [
        chunk("cur_a", 0.20, title="PTO Policy"),
        chunk("cur_b", 0.30, title="Holiday"),
        chunk("old_a", 0.10, "superseded", "PTO Policy"),
        chunk("old_b", 0.35, "superseded", "Unrelated"),
        chunk("old_c", 0.40, "superseded", "Unrelated"),
        chunk("old_d", 0.45, "superseded", "Unrelated"),
    ]
    out = _select_final(chunks)
    assert ids(out) == ["cur_a", "cur_b", "old_a", "old_b", "old_c"], ids(out)
    assert len(out) == KEEP_BEST
    assert len(set(ids(out))) == len(out), "duplicates"


def _pto_case(superseded_distance: float) -> list[dict]:
    return [
        chunk("cur_a", 0.20, title="PTO Policy"),
        chunk("cur_b", 0.30, title="PTO Policy"),
        chunk("cur_c", 0.40, title="Holiday"),
        chunk("cur_d", 0.50, title="Holiday"),
        chunk("cur_e", 0.55, title="Holiday"),
        chunk("cur_f", 0.70, title="Holiday"),
        chunk("old_a", superseded_distance, "superseded", "PTO Policy"),
    ]


def test_superseded_within_margin_is_reserved() -> None:
    chunks = _pto_case(0.29)  # best current 0.20 + 0.10 margin
    out = _select_final(chunks)
    assert ids(out) == ["cur_a", "cur_b", "cur_c", "cur_d", "old_a"], ids(out)
    assert ids(out) != ids(old_behavior(chunks))


def test_superseded_outside_margin_uses_old_behavior() -> None:
    chunks = _pto_case(0.35)  # title matches but 0.15 behind best current
    out = _select_final(chunks)
    assert ids(out) == ids(old_behavior(chunks)), ids(out)
    assert all(c["metadata"]["doc_status"] == "current" for c in out)


def test_margin_is_measured_from_best_current_not_head() -> None:
    chunks = _pto_case(0.29)
    chunks[0]["distance"] = 0.10  # best current is now 0.10 -> limit 0.20; 0.29 is outside
    assert ids(_select_final(chunks)) == ids(old_behavior(chunks))


def test_no_current_chunks_uses_old_behavior() -> None:
    chunks = [chunk("old_a", 0.2, "superseded", "PTO Policy"),
              chunk("old_b", 0.3, "superseded", "PTO Policy")]
    assert ids(_select_final(chunks)) == ids(old_behavior(chunks))


def test_empty_input() -> None:
    assert _select_final([]) == []


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
