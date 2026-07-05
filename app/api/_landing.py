"""Public marketing landing for Stepan — the AI sales agent product. Served at "/".

Standalone, self-contained HTML (own <!doctype> + inline CSS, no external assets), so it
renders for anonymous visitors without the app shell. No mention of any specific client."""
# ruff: noqa: E501 — inline CSS/HTML string; long lines are inherent, not code smell
from __future__ import annotations

# Where "Talk to Stepan" sends a visitor — the live demo Instagram DM.
_DEMO_IG = "https://ig.me/m/zapleo_ceo"

_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0f1117;--card:#141925;--line:#232a38;--ink:#e8eef4;--mut:#8b98ab;--acc:#4da6ff;--acc2:#7c5cff}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,sans-serif;line-height:1.55;-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}
.wrap{max-width:1080px;margin:0 auto;padding:0 22px}
.btn{display:inline-block;border-radius:10px;padding:.8rem 1.4rem;font-weight:600;font-size:.95rem;transition:transform .1s,box-shadow .15s,background .15s;cursor:pointer;border:none}
.btn-p{background:linear-gradient(135deg,var(--acc),var(--acc2));color:#fff;box-shadow:0 6px 22px rgba(77,166,255,.32)}
.btn-p:hover{transform:translateY(-2px);box-shadow:0 10px 30px rgba(77,166,255,.42)}
.btn-g{background:transparent;color:var(--ink);border:1px solid var(--line)}
.btn-g:hover{border-color:var(--acc);color:var(--acc)}
/* nav */
nav{position:sticky;top:0;z-index:20;background:rgba(15,17,23,.82);backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
.nav{display:flex;align-items:center;justify-content:space-between;height:60px}
.brand{display:flex;align-items:center;gap:.55rem;font-weight:800;font-size:1.15rem;letter-spacing:.01em}
.logo{width:30px;height:30px;border-radius:8px;background:linear-gradient(135deg,var(--acc),var(--acc2));display:flex;align-items:center;justify-content:center;font-weight:800;color:#fff;font-size:1rem}
.brand small{font-weight:500;font-size:.66rem;color:var(--mut);letter-spacing:.06em;text-transform:uppercase}
.login{font-size:.9rem;color:var(--mut);border:1px solid var(--line);padding:.45rem .95rem;border-radius:9px;transition:.15s}
.login:hover{color:var(--ink);border-color:var(--acc)}
/* hero */
.hero{text-align:center;padding:5.5rem 0 3.5rem;position:relative;overflow:hidden}
.hero::before{content:"";position:absolute;inset:-30% 0 auto 0;height:520px;background:radial-gradient(60% 60% at 50% 0%,rgba(124,92,255,.18),transparent 70%),radial-gradient(50% 50% at 70% 10%,rgba(77,166,255,.16),transparent 70%);pointer-events:none}
.chip{display:inline-block;font-size:.74rem;color:var(--acc);background:rgba(77,166,255,.1);border:1px solid rgba(77,166,255,.28);padding:.32rem .8rem;border-radius:20px;margin-bottom:1.4rem;position:relative}
h1{font-size:clamp(2.1rem,5.4vw,3.7rem);line-height:1.08;font-weight:800;letter-spacing:-.02em;position:relative}
h1 .g{background:linear-gradient(120deg,var(--acc),var(--acc2));-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.sub{max-width:640px;margin:1.4rem auto 0;color:var(--mut);font-size:1.12rem;position:relative}
.cta{margin-top:2.2rem;display:flex;gap:.8rem;justify-content:center;flex-wrap:wrap;position:relative}
.note{margin-top:1rem;font-size:.8rem;color:var(--mut);position:relative}
/* sections */
section{padding:4.2rem 0}
.kick{text-align:center;color:var(--acc);font-size:.78rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;margin-bottom:.7rem}
h2{text-align:center;font-size:clamp(1.6rem,3.6vw,2.4rem);font-weight:800;letter-spacing:-.02em}
.lead{text-align:center;color:var(--mut);max-width:600px;margin:.9rem auto 0}
/* steps */
.steps{display:grid;grid-template-columns:repeat(4,1fr);gap:1rem;margin-top:2.6rem}
.step{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:1.3rem}
.step .n{width:30px;height:30px;border-radius:8px;background:rgba(77,166,255,.12);color:var(--acc);font-weight:800;display:flex;align-items:center;justify-content:center;margin-bottom:.8rem}
.step h3{font-size:1rem;margin-bottom:.35rem}
.step p{font-size:.86rem;color:var(--mut)}
/* grid */
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1rem;margin-top:2.6rem}
.feat{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:1.3rem;transition:.15s}
.feat:hover{border-color:var(--acc);transform:translateY(-3px)}
.feat .ic{font-size:1.5rem;margin-bottom:.6rem}
.feat h3{font-size:.95rem;margin-bottom:.3rem}
.feat p{font-size:.82rem;color:var(--mut)}
/* compare */
.cmp{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-top:2.6rem}
.col{border-radius:14px;padding:1.5rem;border:1px solid var(--line)}
.col.bad{background:#15161c}
.col.good{background:linear-gradient(180deg,rgba(77,166,255,.08),var(--card));border-color:rgba(77,166,255,.35)}
.col h3{font-size:1.05rem;margin-bottom:.9rem;display:flex;align-items:center;gap:.5rem}
.col li{list-style:none;font-size:.9rem;color:var(--mut);padding:.4rem 0 .4rem 1.6rem;position:relative;border-top:1px solid var(--line)}
.col li:first-of-type{border-top:none}
.col.bad li::before{content:"✕";position:absolute;left:0;color:#ff6b6b;font-weight:700}
.col.good li::before{content:"✓";position:absolute;left:0;color:#51cf66;font-weight:700}
.col.good li{color:var(--ink)}
/* channels */
.chan{display:flex;gap:.7rem;justify-content:center;flex-wrap:wrap;margin-top:2rem}
.pill{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:.6rem 1.2rem;font-size:.9rem;font-weight:600}
/* final */
.final{text-align:center;background:linear-gradient(135deg,rgba(77,166,255,.1),rgba(124,92,255,.1));border:1px solid rgba(124,92,255,.3);border-radius:20px;padding:3.2rem 1.5rem;margin:1rem 0}
.final h2{font-size:clamp(1.7rem,4vw,2.6rem)}
/* footer */
footer{border-top:1px solid var(--line);padding:2.2rem 0;color:var(--mut);font-size:.85rem}
.foot{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:1rem}
@media (max-width:760px){.steps,.grid{grid-template-columns:1fr 1fr}.cmp{grid-template-columns:1fr}.hero{padding:3.5rem 0 2.5rem}}
@media (max-width:460px){.steps,.grid{grid-template-columns:1fr}}
"""


def _step(n: str, title: str, body: str) -> str:
    return (f'<div class="step"><div class="n">{n}</div>'
            f'<h3>{title}</h3><p>{body}</p></div>')


def _feat(icon: str, title: str, body: str) -> str:
    return (f'<div class="feat"><div class="ic">{icon}</div>'
            f'<h3>{title}</h3><p>{body}</p></div>')


def landing_html() -> str:
    steps = "".join([
        _step("1", "Greets every lead", "The moment someone DMs — from an ad, a comment, a "
              "story reply — Stepan answers in seconds, day or night."),
        _step("2", "Qualifies like a pro", "It asks the right questions, uncovers the real "
              "goal and the pain behind it — not a rigid form, a real conversation."),
        _step("3", "Sells, not just chats", "Value before price, honest objection handling, "
              "the right offer at the right moment. It moves the deal forward."),
        _step("4", "Follows up & hands off", "Revives silent leads with fresh angles, and "
              "passes a hot, qualified lead to your team exactly when it's ready to buy."),
    ])
    feats = "".join([
        _feat("🧠", "Consultative selling", "Reaches the emotional layer and handles "
              "objections — a trusted advisor, not a FAQ bot."),
        _feat("💬", "Instagram &amp; WhatsApp", "Meets buyers where they already are: IG, "
              "WhatsApp and Messenger DMs, one brain across all."),
        _feat("🌍", "Speaks their language", "Replies in each lead's own language, "
              "automatically — no separate setup per market."),
        _feat("🛡️", "Never makes things up", "Every claim is grounded in your facts — no "
              "fake promises, no invented prices. It survives a screenshot."),
        _feat("🔁", "Smart follow-ups", "Brings back leads who went quiet — varied, natural, "
              "never spammy, and safe for your account."),
        _feat("🤝", "Human handoff", "Alerts your team and passes the lead the instant it's "
              "hot — never a dead-end bot."),
        _feat("📊", "Live funnel &amp; analytics", "See every stage, peak activity hours and "
              "which ad drives which sale — operator-grade, not vanity metrics."),
        _feat("🎓", "You coach it in plain words", "Teach it a new fact or a better pitch in "
              "one sentence — it updates its own playbook, with your approval."),
    ])
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>Stepan — the AI sales agent that closes in your DMs</title>"
        "<meta name=\"description\" content=\"Stepan is an AI sales agent that qualifies and "
        "sells to your leads in Instagram &amp; WhatsApp DMs — like your best rep, 24/7.\">"
        "<link rel=\"icon\" href=\"data:image/svg+xml,"
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
        "<rect width='32' height='32' rx='8' fill='%234da6ff'/>"
        "<text x='16' y='23' font-size='20' font-weight='800' fill='white' "
        "text-anchor='middle' font-family='Arial'>S</text></svg>\">"
        f"<style>{_CSS}</style></head><body>"
        # nav — login top-right
        "<nav><div class=\"wrap nav\">"
        "<div class=\"brand\"><span class=\"logo\">S</span>Stepan"
        "<small>AI Sales Agent</small></div>"
        "<a class=\"login\" href=\"/login\">Log in</a>"
        "</div></nav>"
        # hero
        "<header class=\"hero\"><div class=\"wrap\">"
        "<span class=\"chip\">Your DMs, working while you sleep</span>"
        "<h1>Your best salesperson,<br><span class=\"g\">on autopilot, in every DM.</span></h1>"
        "<p class=\"sub\">Stepan is an AI sales agent that greets, qualifies and actually "
        "<b>sells</b> to your leads in Instagram &amp; WhatsApp — like your sharpest rep, "
        "24/7, in any language.</p>"
        "<div class=\"cta\">"
        f"<a class=\"btn btn-p\" href=\"{_DEMO_IG}\" target=\"_blank\" rel=\"noopener\">"
        "Talk to Stepan →</a>"
        "<a class=\"btn btn-g\" href=\"#how\">See how it works</a>"
        "</div>"
        "<p class=\"note\">The best demo is Stepan itself — message it and watch it qualify "
        "you.</p>"
        "</div></header>"
        # how it works
        "<section id=\"how\"><div class=\"wrap\">"
        "<div class=\"kick\">How it works</div>"
        "<h2>From \"hi\" to a hot lead — on its own</h2>"
        "<p class=\"lead\">Stepan runs the whole first conversation the way your best closer "
        "would, then hands you the ready-to-buy leads.</p>"
        f"<div class=\"steps\">{steps}</div>"
        "</div></section>"
        # capabilities
        "<section><div class=\"wrap\">"
        "<div class=\"kick\">What it does</div>"
        "<h2>Everything a great rep does — at scale</h2>"
        f"<div class=\"grid\">{feats}</div>"
        "</div></section>"
        # comparison
        "<section><div class=\"wrap\">"
        "<div class=\"kick\">Why Stepan</div>"
        "<h2>Not another flow bot</h2>"
        "<p class=\"lead\">Rule-based DM bots capture leads. Stepan closes them.</p>"
        "<div class=\"cmp\">"
        "<div class=\"col bad\"><h3>🤖 Typical DM bot</h3>"
        "<li>Canned button flows — breaks off-script</li>"
        "<li>Can't handle a real objection</li>"
        "<li>Just collects a contact, then stalls</li>"
        "<li>Makes things up when it doesn't know</li>"
        "<li>One channel, one language</li></div>"
        "<div class=\"col good\"><h3>💎 Stepan</h3>"
        "<li>Real conversation — adapts to every lead</li>"
        "<li>Diagnoses the pain, reframes objections</li>"
        "<li>Sells value, times the offer, drives the deal</li>"
        "<li>Grounded in your facts — never invents</li>"
        "<li>IG + WhatsApp + Messenger, any language</li></div>"
        "</div></div></section>"
        # channels
        "<section><div class=\"wrap\" style=\"text-align:center\">"
        "<div class=\"kick\">Where it works</div>"
        "<h2>Right inside the chats your buyers already use</h2>"
        "<div class=\"chan\">"
        "<span class=\"pill\">📸 Instagram</span>"
        "<span class=\"pill\">🟢 WhatsApp</span>"
        "<span class=\"pill\">💬 Messenger</span>"
        "</div></div></section>"
        # final CTA
        "<section><div class=\"wrap\"><div class=\"final\">"
        "<div class=\"kick\">See it for yourself</div>"
        "<h2>Let Stepan sell <span class=\"g\">you</span>.</h2>"
        "<p class=\"lead\">Message it like one of your leads and watch it qualify and pitch — "
        "in real time. Pricing is tailored to your volume; that's a conversation, not a "
        "checkout.</p>"
        "<div class=\"cta\">"
        f"<a class=\"btn btn-p\" href=\"{_DEMO_IG}\" target=\"_blank\" rel=\"noopener\">"
        "Talk to Stepan →</a></div>"
        "</div></div></section>"
        # footer
        "<footer><div class=\"wrap foot\">"
        "<div class=\"brand\" style=\"font-size:1rem\"><span class=\"logo\" "
        "style=\"width:24px;height:24px;font-size:.8rem\">S</span>Stepan</div>"
        "<div>AI sales agent for Instagram &amp; WhatsApp · "
        "<a href=\"/login\" style=\"color:var(--acc)\">Log in</a></div>"
        "</div></footer>"
        "</body></html>"
    )
