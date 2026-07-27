"""What is allowed to sit between the lead's message and the send.

The reply is not visible to the outbox sender until its transaction commits, so every await
before that commit is time the lead spends looking at a screen with nothing new on it.
Measured 2026-07-27 over 1296 replies: p50 145s from the lead's message to the send, of which
the model itself was 9-15s. The rest was waiting.

The rule these tests pin: work belongs inside the reply transaction only if it changes WHAT we
say or WHETHER we may say it. Reading the lead and reporting a conversion to Facebook do
neither.
"""
from __future__ import annotations

import asyncio

import pytest

from app.modules.conversation.discovery import _TIMEOUT_S, extract_discovery
from app.modules.conversation.dossier import LeadDossier


class _Message:
    """Minimal stand-in for the ORM row — discovery only reads direction and text."""

    def __init__(self, direction: str, text: str) -> None:
        self.direction = direction
        self.text = text


_DIALOG = [_Message("in", "aku mau bikin aplikasi buat toko")]


class _HangingLLM:
    """The free chain's actual failure shape: it does not error, it stops answering. Broker
    numbers for chat:fast over 24h — p50 0.68s, p90 55.8s, max 59s."""

    async def chat(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        await asyncio.sleep(60)
        raise AssertionError("should have been cut off long before this")


class _SlowButFineLLM:
    def __init__(self, delay: float) -> None:
        self.delay = delay

    async def chat(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        await asyncio.sleep(self.delay)
        return ('{"job_to_be_done": "bikin aplikasi untuk toko", "pains": [], '
                '"desired_state": [], "objections": [], "role": "", "readiness": "", '
                '"refusal": "none", "payment_preference": "", "budget_signal": "", '
                '"product_slug": ""}'), {"cost_usd": 0.0}


async def test_a_hanging_extractor_cannot_hold_the_reply(monkeypatch) -> None:
    """One turn in ten used to wait most of a minute here for a field the NEXT turn reads.
    The ceiling is shortened for the test only — what matters is that it exists and holds."""
    monkeypatch.setattr("app.modules.conversation.discovery._TIMEOUT_S", 0.2)
    loop = asyncio.get_running_loop()
    at = loop.time()
    out = await extract_discovery(
        _HangingLLM(), _DIALOG, LeadDossier(), "id", branch_id=1, thread_id=1)
    elapsed = loop.time() - at
    assert elapsed < 2
    assert out == LeadDossier()  # fail-open: the turn simply learns nothing


async def test_a_fast_extractor_is_still_used() -> None:
    """The ceiling must not cost us the median case, which is the overwhelming majority:
    p50 is 0.68s, two orders of magnitude inside the limit."""
    out = await extract_discovery(
        _SlowButFineLLM(0.01), _DIALOG, LeadDossier(), "id", branch_id=1, thread_id=1)
    assert out.job_to_be_done == "bikin aplikasi untuk toko"


@pytest.mark.parametrize("delay", [0.0, 0.5])
async def test_the_extraction_survives_normal_slowness(delay: float) -> None:
    out = await extract_discovery(
        _SlowButFineLLM(delay), _DIALOG, LeadDossier(), "id", branch_id=1, thread_id=1)
    assert out.job_to_be_done


def test_the_ceiling_is_well_clear_of_the_median() -> None:
    """A ceiling tight enough to cut normal calls would trade a tail for a permanent loss."""
    assert _TIMEOUT_S >= 10
