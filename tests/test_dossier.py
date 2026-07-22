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
    from_needs,
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


# ── legacy compatibility: no thread loses context at the switchover ───────────

def test_legacy_needs_are_converted_when_no_dossier_exists_yet() -> None:
    legacy = NeedsProfile(
        jobs=["pindah karier", "nambah skill"], pains=["takut telat"],
        gains=["dapat kerja remote"], objections=["mahal"]).to_json()
    d = parse_dossier(None, legacy_needs=legacy)
    assert d.job_to_be_done == "pindah karier"
    assert d.pains == ["takut telat"]
    assert "dapat kerja remote" in d.desired_state
    assert "nambah skill" in d.desired_state
    assert d.open_objections() == ["mahal"]


def test_a_stored_dossier_wins_over_legacy_needs() -> None:
    """During the v2→v3 window both columns exist; the dossier is the one kept current."""
    legacy = NeedsProfile(pains=["stale"]).to_json()
    current = LeadDossier(pains=["fresh"]).to_json()
    assert parse_dossier(current, legacy_needs=legacy).pains == ["fresh"]


def test_converting_an_empty_legacy_profile_is_harmless() -> None:
    assert from_needs(NeedsProfile()) == LeadDossier()


def test_objections_stored_as_plain_strings_still_load() -> None:
    """Tolerates a hand-edited row or an older dossier shape."""
    raw = json.dumps({"objections": ["mahal", {"text": "jauh", "status": "handled"}]})
    d = parse_dossier(raw)
    assert d.open_objections() == ["mahal"]
    assert d.objections[1].status == "handled"
