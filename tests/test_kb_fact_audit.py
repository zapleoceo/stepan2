"""kb_fact_audit: catches a KB fact edited in one place but not propagated to a duplicate
copy elsewhere — exactly the gap that let playbook_ready/playbook_social keep saying
"Director's REAL projects (Stepan ini sendiri)" after the origin story was changed
everywhere else to "built by a course alumnus" (2026-07-07)."""
from __future__ import annotations

from scripts.kb_fact_audit import _FACTS, find_drifted_locations

_STEPAN_FACT = next(f for f in _FACTS if f.name == "stepan_origin_story")


def test_no_flags_when_all_copies_match_canonical_wording() -> None:
    rows = [
        ("doc", "stories", "Stepan — built by an ALUMNUS of Vibe Coding, now a startup."),
        ("doc", "playbook_qualify", "Aku dibikin sama alumni Vibe Coding yang dulunya Director "
                                    "juga pernah mampir ke acara ini."),
    ]
    assert find_drifted_locations(_STEPAN_FACT, rows) == []


def test_flags_a_copy_that_still_has_the_old_wording() -> None:
    rows = [
        ("doc", "stories", "Stepan — built by an ALUMNUS of Vibe Coding, now a startup."),
        ("doc", "playbook_ready", "Director's REAL projects (Stepan ini sendiri)."),
    ]
    flags = find_drifted_locations(_STEPAN_FACT, rows)
    assert len(flags) == 1
    assert flags[0][:2] == ("doc", "playbook_ready")


def test_no_flag_when_stepan_and_director_never_co_occur() -> None:
    rows = [("doc", "faq", "Stepan is our AI assistant. " + "x " * 200
                          + "The Director runs the campus open house every Thursday.")]
    assert find_drifted_locations(_STEPAN_FACT, rows) == []
