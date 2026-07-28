"""LeadDossier — the v3 working memory that replaces the leaky `needs` JSON.

Each test here pins one of the four v2 leaks the 2026-07-22 review found: objections wiped by
omission, rephrased pains deleted by a grounding filter, nothing recording what was already
said, and no path from legacy state into the new one.
"""
from __future__ import annotations

import json

from app.modules.conversation.dossier import (
    LeadDossier,
    Objection,
    merge_dossier,
    parse_dossier,
)
from app.modules.conversation.needs import NeedsProfile


def test_roundtrips_through_json() -> None:
    d = LeadDossier(
        role="student", job_to_be_done="pindah karier ke IT", pains=["takut telat mulai"],
        desired_state=["punya portfolio"], decides_with="parents", readiness="considering",
        prices_quoted=["DP 500rb"], payment_preference="cicilan", budget_signal="terbatas",
        objections=[Objection("mahal", "handled", "dipecah jadi cicilan")],
        products_named=["vibe_coding"], cases_used=["alumni Dimas"],
        arguments_used=["portfolio nyata"], refusal="soft")
    assert parse_dossier(d.to_json()) == d


def test_missing_or_broken_state_yields_an_empty_dossier() -> None:
    for raw in (None, "", "not json", "[1,2]", "null"):
        assert parse_dossier(raw) == LeadDossier()


# ── the v2 leaks ──────────────────────────────────────────────────────────────

def test_an_objection_omitted_this_turn_is_not_forgotten() -> None:
    """v2 REPLACED objections every turn: forget to re-list one and it vanished forever."""
    stored = LeadDossier(objections=[Objection("mahal"), Objection("nggak ada waktu")])
    merged = merge_dossier(stored, LeadDossier(objections=[]))
    assert merged.open_objections() == ["mahal", "nggak ada waktu"]


def test_an_objection_can_be_marked_handled_but_never_deleted() -> None:
    stored = LeadDossier(objections=[Objection("mahal")])
    merged = merge_dossier(
        stored, LeadDossier(objections=[Objection("mahal", "handled", "cicilan 6 bulan")]))
    assert merged.objections == [Objection("mahal", "handled", "cicilan 6 bulan")]
    assert merged.open_objections() == []


def test_a_handled_objection_does_not_silently_reopen() -> None:
    """The model re-listing an already-answered objection must not undo the work."""
    stored = LeadDossier(objections=[Objection("mahal", "handled", "cicilan")])
    merged = merge_dossier(stored, LeadDossier(objections=[Objection("mahal")]))
    assert merged.objections[0].status == "handled"
    assert merged.objections[0].handled_by == "cicilan"


def test_the_same_objection_reworded_updates_rather_than_duplicates() -> None:
    stored = LeadDossier(objections=[Objection("harganya mahal banget")])
    merged = merge_dossier(
        stored, LeadDossier(objections=[Objection("harganya mahal", "handled", "DP dulu")]))
    assert len(merged.objections) == 1
    assert merged.objections[0].status == "handled"


def test_a_genuinely_new_objection_is_appended() -> None:
    stored = LeadDossier(objections=[Objection("mahal")])
    merged = merge_dossier(stored, LeadDossier(objections=[Objection("jauh dari rumah")]))
    assert [o.text for o in merged.objections] == ["mahal", "jauh dari rumah"]


def test_a_pain_phrased_better_than_the_lead_phrased_it_survives() -> None:
    """v2's lead_grounded filter deleted any pain not sharing a word with the lead's own text,
    so a sharper paraphrase was thrown away. The dossier keeps it."""
    merged = merge_dossier(
        LeadDossier(), LeadDossier(pains=["takut nggak kekejar sambil kuliah"]))
    assert merged.pains == ["takut nggak kekejar sambil kuliah"]


def test_what_was_already_said_is_recorded_so_it_is_not_served_twice() -> None:
    stored = LeadDossier(cases_used=["alumni Dimas"], arguments_used=["portfolio nyata"])
    merged = merge_dossier(stored, LeadDossier(cases_used=["alumni Rina"]))
    assert merged.cases_used == ["alumni Dimas", "alumni Rina"]
    assert merged.arguments_used == ["portfolio nyata"]


# ── merge semantics ───────────────────────────────────────────────────────────

def test_a_recognised_scalar_overwrites_and_an_unrecognised_one_is_ignored() -> None:
    stored = LeadDossier(role="student", readiness="exploring")
    assert merge_dossier(stored, LeadDossier(readiness="ready")).readiness == "ready"
    assert merge_dossier(stored, LeadDossier(readiness="sangat siap")).readiness == "exploring"
    assert merge_dossier(stored, LeadDossier(role="")).role == "student"


def test_phrase_lists_union_without_near_duplicates() -> None:
    stored = LeadDossier(desired_state=["pengen bisa bikin aplikasi"])
    merged = merge_dossier(stored, LeadDossier(desired_state=["bikin aplikasi sendiri"]))
    assert len(merged.desired_state) == 1


def test_phrase_lists_are_capped() -> None:
    merged = merge_dossier(
        LeadDossier(), LeadDossier(pains=[f"masalah nomor {i}" for i in range(20)]))
    assert len(merged.pains) <= 6


def test_refusal_tracks_the_latest_reading_in_both_directions() -> None:
    """A lead who re-engages after a hard no must not stay silenced forever."""
    assert merge_dossier(LeadDossier(), LeadDossier(refusal="blunt")).refusal == "blunt"
    hard_no = LeadDossier(refusal="blunt")
    assert merge_dossier(hard_no, LeadDossier(refusal="none")).refusal == "none"
    assert merge_dossier(hard_no, LeadDossier(refusal="")).refusal == "blunt"


def test_discovery_needs_both_a_pain_and_a_desired_state() -> None:
    assert not LeadDossier(pains=["takut telat"]).has_discovery()
    assert not LeadDossier(desired_state=["kerja di IT"]).has_discovery()
    assert LeadDossier(pains=["takut telat"], desired_state=["kerja di IT"]).has_discovery()


# ── the v2 seam, closed by migration rather than by a runtime fallback ────────

def _migration():  # noqa: ANN202
    """The backfill migration, loaded by path — it deliberately imports no app code, so its
    conversion has to be exercised where it lives."""
    import importlib.util
    from pathlib import Path

    path = (Path(__file__).resolve().parent.parent / "migrations" / "versions"
            / "20260728_1700_dossbf00001_backfill_dossier_from_needs.py")
    spec = importlib.util.spec_from_file_location("_dossbf", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_the_backfill_converts_a_v2_record_into_the_v3_shape() -> None:
    """parse_dossier no longer falls back to `needs`; the migration moved every record across
    instead. The fallback existed because two columns held one fact — which is precisely what
    let the chat panel and the needs cloud each read the dead one and show an empty box."""
    legacy = NeedsProfile(
        jobs=["pindah karier", "nambah skill"], pains=["takut telat"],
        gains=["dapat kerja remote"], objections=["mahal"]).to_json()
    d = parse_dossier(_migration()._to_dossier(legacy))
    assert d.job_to_be_done == "pindah karier"
    assert d.pains == ["takut telat"]
    assert "dapat kerja remote" in d.desired_state
    assert "nambah skill" in d.desired_state  # further jobs join the desired state
    assert d.open_objections() == ["mahal"]


def test_the_backfill_skips_records_that_carry_nothing() -> None:
    """An empty or unreadable v2 row leaves `dossier` NULL rather than writing a hollow object:
    downstream, an empty dossier and a missing one read alike, and only the second can later be
    filled in without looking as though it already had been."""
    mod = _migration()
    assert mod._to_dossier(NeedsProfile().to_json()) is None
    assert mod._to_dossier("not json") is None
    assert mod._to_dossier("[]") is None


def test_parse_dossier_no_longer_takes_a_legacy_fallback() -> None:
    """The signature is the guarantee: with one parameter, a second source of truth cannot be
    reintroduced by accident."""
    import inspect

    assert list(inspect.signature(parse_dossier).parameters) == ["raw"]


def test_objections_stored_as_plain_strings_still_load() -> None:
    """Tolerates a hand-edited row or an older dossier shape."""
    raw = json.dumps({"objections": ["mahal", {"text": "jauh", "status": "handled"}]})
    d = parse_dossier(raw)
    assert d.open_objections() == ["mahal"]
    assert d.objections[1].status == "handled"


# ── objection categories: pick which playbook section loads ──────────────────

def test_a_recognised_category_survives_the_roundtrip() -> None:
    d = LeadDossier(objections=[Objection("mahal", category="price")])
    assert parse_dossier(d.to_json()).objections[0].category == "price"


def test_an_unrecognised_category_is_dropped_not_stored() -> None:
    """A model typo must not silently create a playbook section that will never match."""
    d = LeadDossier(objections=[Objection("mahal", category="expensive_stuff")])
    assert parse_dossier(d.to_json()).objections[0].category == ""


def test_open_objection_categories_only_counts_open_ones() -> None:
    d = LeadDossier(objections=[
        Objection("mahal", "handled", category="price"),
        Objection("nggak ada waktu", category="time"),
    ])
    assert d.open_objection_categories() == frozenset({"time"})


def test_open_objection_categories_ignores_uncategorised_objections() -> None:
    d = LeadDossier(objections=[Objection("saya bingung")])
    assert d.open_objection_categories() == frozenset()


def test_merging_can_add_a_category_to_an_objection_first_seen_without_one() -> None:
    """The model may recognise a live objection's category a turn late — that update must
    not be lost just because the objection already existed."""
    stored = LeadDossier(objections=[Objection("mahal banget")])
    merged = merge_dossier(stored, LeadDossier(objections=[Objection("mahal", category="price")]))
    assert merged.objections[0].category == "price"


def test_merging_never_erases_a_category_already_known() -> None:
    stored = LeadDossier(objections=[Objection("mahal", category="price")])
    merged = merge_dossier(stored, LeadDossier(objections=[Objection("mahal")]))
    assert merged.objections[0].category == "price"
