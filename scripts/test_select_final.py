"""Plain-assert tests for app.retrieve._select_final (synthetic chunks, no API, no model load).

Run from the repo root:  .\\.venv\\Scripts\\python.exe scripts\\test_select_final.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.retrieve import KEEP_BEST, SUPERSEDED_SLOTS, _select_final  # noqa: E402


def chunk(name: str, distance: float, status: str = "current",
          title: str = "Other", section: str = "S") -> dict:
    return {
        "source": f"{name}.md",
        "text": name,
        "metadata": {"doc_status": status, "title": title, "section": section, "chunk_id": name},
        "distance": distance,
    }


def ids(chunks: list[dict]) -> list[str]:
    return [c["metadata"]["chunk_id"] for c in chunks]


def old_behavior(chunks: list[dict]) -> list[dict]:
    ordered = sorted(chunks, key=lambda c: (c["metadata"]["doc_status"] != "current", c["distance"]))
    return ordered[:KEEP_BEST]


def test_far_distance_counterpart_is_paired() -> None:
    # counterpart is 0.60 further than the best current chunk; no distance gate applies
    chunks = [
        chunk("cur_a", 0.10, title="PTO", section="3"),
        chunk("cur_b", 0.20, title="Holiday", section="N"),
        chunk("cur_c", 0.30, title="Sabbatical", section="5"),
        chunk("cur_d", 0.40, title="Parental", section="2"),
        chunk("cur_e", 0.45, title="Expense", section="2"),
        chunk("old_a", 0.70, "superseded", title="PTO", section="3"),
    ]
    out = _select_final(chunks)
    assert ids(out) == ["cur_a", "cur_b", "cur_c", "cur_d", "old_a"], ids(out)
    assert out[-1]["metadata"]["doc_status"] == "superseded"


def test_exact_title_and_section_required() -> None:
    same_title_other_section = [
        chunk("cur_a", 0.1, title="PTO", section="3"),
        chunk("cur_b", 0.2, title="PTO", section="5"),
        chunk("cur_c", 0.3, title="Holiday", section="N"),
        chunk("old_a", 0.05, "superseded", title="PTO", section="6"),
    ]
    assert ids(_select_final(same_title_other_section)) == ids(old_behavior(same_title_other_section))

    same_section_other_title = [
        chunk("cur_a", 0.1, title="PTO", section="3"),
        chunk("old_a", 0.05, "superseded", title="Legacy", section="3"),
    ]
    assert ids(_select_final(same_section_other_title)) == ids(old_behavior(same_section_other_title))


def test_different_section_is_not_paired() -> None:
    chunks = [
        chunk("cur_a", 0.10, title="PTO", section="3. Annual PTO allotment"),
        chunk("cur_b", 0.20, title="PTO", section="5. Carryover"),
        chunk("old_a", 0.05, "superseded", title="PTO", section="4. Requesting PTO"),
        chunk("cur_c", 0.30, title="Holiday", section="N"),
        chunk("cur_d", 0.40, title="Sabbatical", section="5"),
        chunk("cur_e", 0.45, title="Parental", section="2"),
    ]
    out = _select_final(chunks)
    assert ids(out) == ids(old_behavior(chunks))
    assert all(c["metadata"]["doc_status"] == "current" for c in out)


def test_no_counterpart_equals_old_behavior() -> None:
    chunks = [
        chunk("old_a", 0.10, "superseded", title="Legacy", section="1"),
        chunk("cur_a", 0.20, title="A"),
        chunk("cur_b", 0.30, title="B"),
        chunk("cur_c", 0.40, title="C"),
        chunk("cur_d", 0.50, title="D"),
        chunk("cur_e", 0.55, title="E"),
        chunk("cur_f", 0.70, title="F"),
        chunk("old_b", 0.80, "superseded", title="Legacy2", section="1"),
    ]
    assert ids(_select_final(chunks)) == ids(old_behavior(chunks))
    only_superseded = [chunk("old_a", 0.2, "superseded"), chunk("old_b", 0.3, "superseded")]
    assert ids(_select_final(only_superseded)) == ids(old_behavior(only_superseded))


def test_max_two_reserved() -> None:
    chunks = [
        chunk("cur_a", 0.10, title="PTO", section="3"),
        chunk("cur_b", 0.20, title="PTO", section="4"),
        chunk("cur_c", 0.30, title="PTO", section="5"),
        chunk("cur_d", 0.40, title="Holiday", section="N"),
        chunk("cur_e", 0.50, title="Sabbatical", section="5"),
        chunk("old_a", 0.15, "superseded", title="PTO", section="3"),
        chunk("old_b", 0.25, "superseded", title="PTO", section="4"),
        chunk("old_c", 0.35, "superseded", title="PTO", section="5"),
    ]
    out = _select_final(chunks)
    assert SUPERSEDED_SLOTS == 2
    assert ids(out) == ["cur_a", "cur_b", "cur_c", "old_a", "old_b"], ids(out)
    assert sum(c["metadata"]["doc_status"] != "current" for c in out) == 2
    assert len(out) == KEEP_BEST


def test_one_reserved_leaves_four_chunk_head() -> None:
    chunks = [
        chunk("cur_a", 0.10, title="PTO", section="3"),
        chunk("cur_b", 0.20, title="Holiday", section="N"),
        chunk("cur_c", 0.30, title="Sabbatical", section="5"),
        chunk("cur_d", 0.40, title="Parental", section="2"),
        chunk("cur_e", 0.50, title="Expense", section="2"),
        chunk("old_a", 0.15, "superseded", title="PTO", section="3"),
        chunk("old_b", 0.60, "superseded", title="Legacy", section="1"),
    ]
    out = _select_final(chunks)
    assert ids(out) == ["cur_a", "cur_b", "cur_c", "cur_d", "old_a"], ids(out)
    assert sum(c["metadata"]["doc_status"] == "current" for c in out) == KEEP_BEST - 1


def test_few_current_fills_without_duplicates() -> None:
    chunks = [
        chunk("cur_a", 0.20, title="PTO", section="3"),
        chunk("old_a", 0.10, "superseded", title="PTO", section="3"),
        chunk("old_b", 0.35, "superseded", title="Legacy", section="1"),
        chunk("old_c", 0.40, "superseded", title="Legacy", section="2"),
        chunk("old_d", 0.45, "superseded", title="Legacy", section="3"),
    ]
    out = _select_final(chunks)
    assert ids(out) == ["cur_a", "old_a", "old_b", "old_c", "old_d"], ids(out)
    assert len(set(ids(out))) == len(out)


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
