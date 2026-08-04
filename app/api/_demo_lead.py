"""Landing demo lead capture: when a demo-chat visitor leaves a contact, DM the owner once on
Telegram. No DB (product owner's choice) — in-process dedup by contact; a duplicate ping after
a worker restart is acceptable, a MISSED ping is not.

`history` is the SERVER's transcript (app/modules/website/session.py), never the request body.
It used to be whatever the browser posted, `assistant` turns included, so a visitor could type
Stepan's half of the conversation, add a contact, and have the forgery arrive here as a real
lead card in the owner's Telegram.

The contact is found by regex and the alert is sent on that alone. Buy-intent and a summary
are added by a model afterwards, purely as labels on the card. It used to be the other way
round — the model decided whether the owner heard about the lead at all — and on 28.07.2026
that cost a real one: the verdict came back not-ready (or unparsable; the code logged neither),
the owner was never told, and the visitor had meanwhile been promised an email that no part of
this system is able to send. A contact typed on the landing page is the only thing this page
produces. It gets forwarded, always, and every failure to do so is logged at ERROR."""
from __future__ import annotations

import html as _h
import json
import logging
import re

from app.adapters.llm.broker import BrokerLLM
from app.adapters.notify.telegram import TelegramNotifier
from app.config import settings

_log = logging.getLogger(__name__)

# Cheap pre-gate: only spend an extraction call on turns whose history actually contains
# something contact-shaped (email / phone / @handle). No point classifying small talk.
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_HANDLE = re.compile(r"(?<![A-Za-z0-9_])@[A-Za-z0-9_]{4,}")

_notified: set[str] = set()  # normalized contacts already DM'd (this process)
_MAX_NOTIFIED = 10_000

_CHANNEL_LABEL = {
    "whatsapp": "WhatsApp", "telegram": "Telegram", "email": "Email", "phone": "Телефон",
}

_EXTRACT_SYS = (
    "You read a short chat between a website visitor and a sales agent (Stepan) on the "
    "product's own landing page. Decide if the visitor BOTH (a) clearly wants to buy / start "
    "with the product now — not merely curious, not merely asking the price — AND (b) has "
    "given a real contact to be reached at (email, phone / WhatsApp number, or Telegram "
    "@handle). Return ONLY compact JSON, no prose, no code fence:\n"
    '{"ready": true|false, "contact_type": "whatsapp|telegram|email|phone|", '
    '"contact": "<verbatim contact or empty>", "wants": "<one short RU line: what they want '
    'to buy/do>", "summary": "<=2 sentence RU summary>"}\n'
    "ready=true ONLY if BOTH real buy intent AND a usable contact are present. If unsure, "
    "ready=false."
)


def _found_contact(history: list[dict]) -> tuple[str, str]:
    """The newest contact the VISITOR typed, as (contact, type). Empty when there is none.

    Regex, not the model: this is what decides whether the owner hears about the lead at all,
    and it must not depend on a broker call that can time out or answer badly. The model only
    enriches the card afterwards. Newest-first because a corrected number supersedes a typo."""
    for m in reversed(history):
        if m.get("role") != "user":
            continue
        t = str(m.get("content", ""))
        for rx, kind in ((_EMAIL, "email"), (_HANDLE, "telegram"), (_PHONE, "phone")):
            hit = rx.search(t)
            if hit:
                return hit.group(0).strip(), kind
    return "", ""


def _parse_json(text: str) -> dict | None:
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s[s.find("{"):] if "{" in s else s
    a, b = s.find("{"), s.rfind("}")
    if a == -1 or b == -1 or b < a:
        return None
    try:
        data = json.loads(s[a : b + 1])
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def _transcript(history: list[dict]) -> str:
    lines = []
    for m in history:
        who = "Гость" if m.get("role") == "user" else "Степан"
        lines.append(f"{who}: {m.get('content', '')}")
    return "\n".join(lines)


async def maybe_notify(history: list[dict]) -> None:
    """Fire-and-forget after a demo reply: if the visitor is ready-to-buy with a contact,
    DM the owner once. Never raises — a missed capture must not affect the chat response."""
    try:
        contact, kind = _found_contact(history)
        if not contact:
            return
        key = contact.lower()
        if key in _notified:
            return
        target = settings().demo_notify_tg_id or settings().bootstrap_super_admin
        token = settings().tg_bot_token
        if not target or not token:
            _log.error("DEMO LEAD LOST: contact %s seen but no TG target/token configured",
                       contact)
            return

        # Enrichment is optional and must never gate the alert. A contact typed into the demo
        # is the only artefact this page produces; on 28.07.2026 one was lost because the whole
        # notification hung off an LLM verdict that returned quietly — the visitor was told
        # (falsely) that material had been emailed, and nobody was told anything at all.
        convo = _transcript(history)
        wants = summary = ""
        ready = None
        try:
            raw, _meta = await BrokerLLM().chat(
                [{"role": "system", "content": _EXTRACT_SYS},
                 {"role": "user", "content": convo[:6000]}],
                capability="chat:fast", max_tokens=300, temperature=0.0,
                workflow="landing_demo_capture", read_timeout_s=30.0,
            )
            data = _parse_json(raw or "")
            if data:
                ready = bool(data.get("ready"))
                wants = str(data.get("wants", "")).strip()
                summary = str(data.get("summary", "")).strip()
                kind = str(data.get("contact_type", "")).strip() or kind
            else:
                _log.warning("demo lead: extraction returned unparsable JSON, sending raw card")
        except Exception as exc:  # noqa: BLE001 — enrichment is best-effort, the card still goes
            _log.warning("demo lead: extraction failed (%s), sending raw card",
                         type(exc).__name__)

        label = _CHANNEL_LABEL.get(kind.lower(), "контакт")
        heat = {True: "🟢 горячий", False: "🟡 контакт есть, готовность неясна",
                None: "🟡 контакт есть, распознать не удалось"}[ready]
        msg = (
            "<b>Новый лид с демо-чата на лендинге</b>\n\n"
            f"<b>Статус:</b> {heat}\n"
            f"<b>Контакт:</b> {_h.escape(contact)} ({_h.escape(label)})\n"
            f"<b>Хочет:</b> {_h.escape(wants or '—')}\n\n"
            f"<b>Кратко:</b> {_h.escape(summary or '—')}\n\n"
            f"<b>— Переписка —</b>\n<pre>{_h.escape(convo[:3500])}</pre>"
        )
        status = await TelegramNotifier(bot_token=token, group_chat_id=int(target)).send(text=msg)
        if status == "ok":
            _notified.add(key)
            if len(_notified) > _MAX_NOTIFIED:
                _notified.clear()
            _log.info("demo lead notified owner: %s (%s, ready=%s)", contact, kind, ready)
        else:
            # Not added to _notified on purpose: the next turn retries instead of losing it.
            _log.error("DEMO LEAD LOST: telegram send returned %s for contact %s",
                       status, contact)
    except Exception:  # noqa: BLE001 — background task: log and swallow, never crash the loop
        _log.exception("DEMO LEAD LOST: notify errored")
