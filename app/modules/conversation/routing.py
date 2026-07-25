"""Which model tier a turn runs on.

Live replies no longer route at all: every one of them goes to the sales chain. The tiering
that used to live here — cheap model unless the dossier showed an objection, a money
conversation, readiness or a refusal — had a hole big enough to cost sales. Those conditions
read the dossier, and the dossier was populated for ~5% of leads (discovery.py), so in
practice everything but the first reply fell through to the cheapest model in the chain.

Thread 4681 is what that looked like: an engaged lead answering questions, asking "ini bayar
kah kk?" — a money question — and being answered by gpt-oss-120b, which replied with numbered
menus and "terima kasih sudah pilih mau beralih karier", brochure language nobody speaks.

Measured before removing it: 52 of 202 replies in 24h ran on the cheap tier. With the prompt
cache warm a sales-chain reply costs $0.00057 (97.4% cache hit over the last hour), so moving
them is roughly three cents a day — and it warms the cache further, which makes every other
reply cheaper. There was no saving here worth a single lost conversation.

FAST stays for work the lead never sees: extracting the dossier, translating for the admin UI.
"""
from __future__ import annotations

SMART = "chat:smart"  # the strong, scarce model — fallback when the sales chain is capped
FAST = "chat:fast"    # the cheap, effectively unlimited one — service work only
# The broker's Sonnet-first chain. Every live reply rides this.
SALES = "chat:sales"

__all__ = ["FAST", "SALES", "SMART"]
