"""v3 prompt core — a small contract over a rich dossier.

The size assertion is a regression guard, not a style preference: v2's contract reached 25 317
chars by absorbing one incident fix at a time until instructions were 55% of the prompt and the
lead's own words were 5%. If this file starts growing the same way, a test should fail first.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.adapters.db.models import Message
from app.modules.conversation.contract import (
    FIRST_TURN_NOTE,
    MOVES,
    build_messages_v3,
    contract,
    dossier_block,
)
from app.modules.conversation.dossier import LeadDossier, Objection

_NOW = datetime.now(UTC).replace(tzinfo=None)
_CONTRACT_CEILING = 6_000


def _msg(text: str, direction: str = "in") -> Message:
    return Message(branch_id=1, thread_id=1, channel_id=1, external_id=text[:20],
                   direction=direction, sent_by="lead" if direction == "in" else "bot",
                   text=text, occurred_at=_NOW)


# ── the contract ──────────────────────────────────────────────────────────────

def test_the_contract_stays_small() -> None:
    """The retired contract reached 30 146 chars by absorbing one incident fix at a time,
    until instructions were 55% of the prompt and the lead's own words 5%."""
    assert len(contract("id")) < _CONTRACT_CEILING


def test_every_move_is_offered_to_the_model() -> None:
    """The enumerated set is what lets the dossier drive the next step — a move missing from
    the text would be unreachable."""
    text = contract("id")
    assert all(move in text for move in MOVES)


def test_the_answer_first_rule_is_stated_as_the_top_priority() -> None:
    """65% of leads died within two turns because the opener ignored what they asked."""
    text = contract("id")
    assert "FIRST sentence answers it" in text
    assert "outweighs every other rule" in text


def test_reply_language_is_bound_to_the_branch_language() -> None:
    assert "Reply in Bahasa Indonesia" in contract("id")
    assert "Reply in Russian" in contract("ru")


def test_the_contract_leaves_voice_to_the_persona() -> None:
    """Register was described in both documents, in different words — the same clash that made
    the entry hints fight the answer-first rule. The persona owns how Stepan sounds; the
    contract owns the shape of the message."""
    text = contract("id")
    assert "persona above" in text
    for voice_rule in ("particles", "Mas", "Anda"):
        assert voice_rule not in text


# ── the dossier block ─────────────────────────────────────────────────────────

def test_an_unknown_lead_contributes_no_block() -> None:
    """A first turn must stay clean — no empty scaffolding for the model to pad against."""
    assert dossier_block(LeadDossier()) == ""


def test_known_facts_are_rendered_and_marked_as_not_to_be_re_asked() -> None:
    block = dossier_block(LeadDossier(
        role="student", job_to_be_done="pindah karier", pains=["takut telat"]))
    assert "never re-ask" in block
    assert "student" in block and "pindah karier" in block and "takut telat" in block


def test_open_objections_are_flagged_as_blocking() -> None:
    block = dossier_block(LeadDossier(objections=[Objection("mahal")]))
    assert "STILL UNRESOLVED" in block and "mahal" in block


def test_a_handled_objection_is_shown_as_settled_not_as_open() -> None:
    block = dossier_block(LeadDossier(
        objections=[Objection("mahal", "handled", "dipecah jadi cicilan")]))
    assert "STILL UNRESOLVED" not in block
    assert "don't re-argue" in block and "dipecah jadi cicilan" in block


def test_what_was_already_spent_is_listed_so_it_is_not_served_twice() -> None:
    block = dossier_block(LeadDossier(
        prices_quoted=["DP 500rb"], cases_used=["alumni Dimas"],
        arguments_used=["portfolio nyata"], products_named=["vibe_coding"]))
    assert "ALREADY USED" in block
    for used in ("DP 500rb", "alumni Dimas", "portfolio nyata", "vibe_coding"):
        assert used in block


def test_a_refusal_is_surfaced_with_its_degree() -> None:
    assert "blunt" in dossier_block(LeadDossier(refusal="blunt"))
    assert "degree" not in dossier_block(LeadDossier(refusal="none"))


# ── message assembly ──────────────────────────────────────────────────────────

def test_system_block_orders_facts_then_knowledge_then_method() -> None:
    """The contract sits closest to the conversation, so the last thing read before writing is
    the method, not a policy footnote."""
    system = build_messages_v3(
        "KB FACTS", [_msg("halo")], "id",
        LeadDossier(role="student"))[0]["content"]
    assert system.index("KB FACTS") < system.index("LEAD DOSSIER") < system.index("PICK ONE MOVE")


def test_dialog_follows_the_system_message_with_roles_mapped() -> None:
    messages = build_messages_v3(
        "KB", [_msg("halo"), _msg("hai kak", "out"), _msg("berapa harganya")], "id",
        LeadDossier())
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]


def test_consecutive_same_role_turns_are_merged() -> None:
    """A lead's message burst would otherwise break APIs requiring strict alternation."""
    messages = build_messages_v3(
        "KB", [_msg("halo"), _msg("mau tanya"), _msg("berapa")], "id", LeadDossier())
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[1]["content"] == "halo\nmau tanya\nberapa"


def test_empty_messages_are_dropped() -> None:
    messages = build_messages_v3("KB", [_msg("halo"), _msg("   ")], "id", LeadDossier())
    assert len(messages) == 2


def test_optional_blocks_appear_only_when_supplied() -> None:
    bare = build_messages_v3("KB", [_msg("halo")], "id", LeadDossier())[0]["content"]
    assert "MANAGER" not in bare

    full = build_messages_v3(
        "KB", [_msg("halo")], "id", LeadDossier(),
        coaching_notes=["jangan janjikan kerja"], manager_note="sudah dicek, belum siap",
        now_block="CURRENT DATE: Rabu", name_block="LEAD NAME: Dimas",
        source_block="ENTRY: iklan")[0]["content"]
    for fragment in ("jangan janjikan kerja", "sudah dicek, belum siap",
                     "CURRENT DATE: Rabu", "LEAD NAME: Dimas", "ENTRY: iklan"):
        assert fragment in full


def test_the_lead_is_not_drowned_out_by_instructions() -> None:
    """The v2 failure in one number: instructions were 55% of the prompt and the conversation
    5%. With a realistic KB the contract must be a minority of the system block."""
    kb = "x" * 12_000
    system = build_messages_v3(kb, [_msg("halo")], "id", LeadDossier())[0]["content"]
    assert len(contract("id")) / len(system) < 0.35


# ── the opener: introduce yourself, never the building ───────────────────────

def test_the_first_turn_note_introduces_who_is_writing() -> None:
    """Measured over 2 619 live openers, an introduction cost nothing (43.5% vs 43.8% reply
    rate); it was the campus line riding along with it that looked expensive."""
    assert "who you are" in FIRST_TURN_NOTE
    assert "FIRST message" in FIRST_TURN_NOTE


def test_the_campus_description_is_banned_from_the_opener() -> None:
    """Any opener mentioning the campus measured 33-38% reply rate against 43.8% without it —
    the single most expensive habit the old engine had."""
    assert "Never describe the campus" in FIRST_TURN_NOTE
    assert "address" in FIRST_TURN_NOTE


def test_the_opener_ends_on_a_question() -> None:
    """Without a question an opener drops from 43.8% to 39.4%."""
    assert "End on a question" in FIRST_TURN_NOTE


def test_the_opener_rule_is_not_carried_on_every_turn() -> None:
    """As a section of the always-on contract the model had to decide whether it applied, and
    on thread 4956 it decided wrong. It is a fact about THIS turn now, not a condition."""
    assert "FIRST message" not in contract("id")


def test_the_contract_did_not_grow_past_its_ceiling() -> None:
    assert len(contract("id")) < _CONTRACT_CEILING


# ── the first turn, and the language it is written in ────────────────────────

def test_the_opener_note_rides_only_on_the_first_turn() -> None:
    from app.modules.conversation.contract import build_messages_v3

    first = build_messages_v3("KB", [_msg("halo")], "id", LeadDossier(),
                              is_first_reply=True)[0]["content"]
    later = build_messages_v3("KB", [_msg("halo")], "id", LeadDossier())[0]["content"]
    assert FIRST_TURN_NOTE in first
    assert FIRST_TURN_NOTE not in later


def test_the_branch_language_is_named_not_coded() -> None:
    """"Reply in id" is an instruction about a string; "Reply in Bahasa Indonesia" is an
    instruction about a language."""
    assert "Reply in Bahasa Indonesia" in contract("id")
    assert "Reply in Russian" in contract("ru")


def test_an_unknown_language_code_passes_through_unharmed() -> None:
    assert "Reply in th" in contract("th")


def test_the_lead_language_wins_and_sticks() -> None:
    """A lead who writes in their own language keeps getting answered in it."""
    text = contract("id")
    assert "wrote to you in another language" in text
    assert "keep answering in it" in text


def test_the_model_is_told_not_to_invent_details_about_the_lead() -> None:
    """Lost when the contract was rebuilt, and it showed: thread 452 was offered an event as
    though discovery were done, on a lead whose pains were still empty. The competitor's
    playbook states it outright, and so does this one again."""
    text = contract("id")
    assert "NEVER ASSUME" in text
    assert "for them or for" in text
