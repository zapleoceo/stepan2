"""Landing demo lead capture: when a demo-chat visitor shows real buy intent AND leaves a
contact, DM the owner once on Telegram. No DB (product owner's choice) — in-process dedup by
contact; a duplicate ping after a worker restart is acceptable for a landing gimmick."""
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


def _has_contactish(history: list[dict]) -> bool:
    for m in history:
        if m.get("role") == "user":
            t = str(m.get("content", ""))
            if _EMAIL.search(t) or _HANDLE.search(t) or _PHONE.search(t):
                return True
    return False


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
        if not _has_contactish(history):
            return
        target = settings().demo_notify_tg_id or settings().bootstrap_super_admin
        token = settings().tg_bot_token
        if not target or not token:
            _log.warning("demo lead: contact seen but no TG target/token configured — skipped")
            return
        convo = _transcript(history)
        try:
            text, _meta = await BrokerLLM().chat(
                [{"role": "system", "content": _EXTRACT_SYS},
                 {"role": "user", "content": convo[:6000]}],
                capability="chat:fast", max_tokens=300, temperature=0.0,
                workflow="landing_demo_capture", read_timeout_s=30.0,
            )
        except Exception as exc:  # noqa: BLE001 — extraction is best-effort
            _log.warning("demo lead extraction failed: %s", type(exc).__name__)
            return
        data = _parse_json(text or "")
        if not data or not data.get("ready"):
            return
        contact = str(data.get("contact", "")).strip()
        if not contact:
            return
        key = contact.lower()
        if key in _notified:
            return
        label = _CHANNEL_LABEL.get(str(data.get("contact_type", "")).lower(), "контакт")
        wants = str(data.get("wants", "")).strip() or "—"
        summary = str(data.get("summary", "")).strip() or "—"
        msg = (
            "🟢 <b>Новый лид с демо-чата на лендинге</b>\n\n"
            f"<b>Контакт:</b> {_h.escape(contact)} ({_h.escape(label)})\n"
            f"<b>Хочет:</b> {_h.escape(wants)}\n\n"
            f"<b>Кратко:</b> {_h.escape(summary)}\n\n"
            f"<b>— Переписка —</b>\n<pre>{_h.escape(convo[:3500])}</pre>"
        )
        status = await TelegramNotifier(bot_token=token, group_chat_id=int(target)).send(text=msg)
        if status == "ok":
            _notified.add(key)
            if len(_notified) > _MAX_NOTIFIED:
                _notified.clear()
            _log.info("demo lead notified owner (contact_type=%s)", data.get("contact_type"))
        else:
            _log.warning("demo lead: telegram send returned %s", status)
    except Exception:  # noqa: BLE001 — background task: log and swallow, never crash the loop
        _log.warning("demo lead notify errored", exc_info=True)
