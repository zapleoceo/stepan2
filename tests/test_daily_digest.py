"""Daily dialogue digest — the single .md shipped to Telegram for offline/AI analysis."""
from __future__ import annotations

import json
import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from app.modules.reports.daily_digest import _fmt_needs, _funnel_section, _lead_spoke  # noqa: E402


def test_lead_spoke_ignores_the_ad_prefill_and_autoresponders() -> None:
    assert not _lead_spoke(["💻 Ceritakan lebih detail tentang program kursusnya"])
    assert not _lead_spoke(["Halo, terima kasih sudah menghubungi kami. Kami sudah menerima pesan"])
    assert _lead_spoke(["💻 Ceritakan lebih detail tentang program kursusnya", "berapa harganya?"])


def test_fmt_needs_renders_what_the_bot_understood() -> None:
    raw = json.dumps({"jobs": ["ganti karier"], "pains": ["followers stuck"], "gains": []})
    line = _fmt_needs(raw)
    assert "цели: ganti karier" in line and "боли: followers stuck" in line
    assert "выгоды" not in line  # empty kinds are dropped, not printed as blanks


def test_fmt_needs_says_so_when_nothing_was_captured() -> None:
    assert "ничего не выявлено" in _fmt_needs(json.dumps({"jobs": [], "pains": [], "gains": []}))
    assert "ничего не выявлено" in _fmt_needs(None)
    assert _fmt_needs("not json at all") == "—"


def test_funnel_counts_the_steps_that_matter() -> None:
    meta = {
        1: ("qualifying", json.dumps({"pains": ["mahal"]}), "smm"),   # engaged, pain, priced
        2: ("qualifying", json.dumps({"pains": []}), "smm"),          # silent clicker
        3: ("handed_off", json.dumps({"pains": ["stuck"]}), "vibe"),  # advanced
    }
    dialogs = {
        1: [("in", "berapa harganya?"), ("out", "Rp 1.882.955, DP 500.000 ya Kak")],
        2: [("in", "💻 Ceritakan lebih detail tentang program kursusnya"), ("out", "Hai Kak!")],
        3: [("in", "mau daftar"), ("out", "boleh minta nomor WA Kakak?")],
    }
    md = _funnel_section(meta, dialogs)
    assert "| Всего диалогов | 3 |" in md
    assert "| Молча ушли после клика | 1 |" in md
    assert "| Заговорили своими словами | 2 |" in md
    assert "| Боль выявлена | 2 |" in md
    assert "| Цена названа | 1 |" in md          # only thread 1 quotes a figure
    assert "| Попытка закрытия | 2 |" in md      # DP 500 (t1) + nomor WA (t3)
    assert "| Дошло до ready/handoff/manager | 1 |" in md
