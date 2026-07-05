"""Public marketing landing for Stepan — the AI sales agent product. Served at "/".

Standalone, self-contained HTML (own <!doctype> + inline CSS, no external assets), so it
renders for anonymous visitors without the app shell. No mention of any specific client."""
# ruff: noqa: E501 — inline CSS/HTML string; long lines are inherent, not code smell
from __future__ import annotations

# Secondary contact link in the footer (the main demo is the in-page chat widget).
_DEMO_IG = "https://ig.me/m/zapleo_ceo"

_WIDGET_JS = r"""
var STP={msgs:[],busy:false};
var STP_GREET="Hey 👋 I'm Stepan. Quick one — what do you sell, and where do most of your leads come from (Instagram, WhatsApp, ads)?";
function stpAdd(role,text){
  STP.msgs.push({role:role,content:text});
  var b=document.getElementById('stp-body');
  var d=document.createElement('div');
  d.className='stp-msg '+(role==='user'?'u':'a');
  d.textContent=text; b.appendChild(d); b.scrollTop=b.scrollHeight;
}
function stpTyping(on){
  var t=document.getElementById('stp-typing');
  if(on&&!t){var b=document.getElementById('stp-body');t=document.createElement('div');t.id='stp-typing';t.className='stp-msg a stp-typ';t.innerHTML='<span></span><span></span><span></span>';b.appendChild(t);b.scrollTop=b.scrollHeight;}
  if(!on&&t)t.remove();
}
function openStepan(){
  document.getElementById('stp-w').classList.add('on');
  document.getElementById('stp-fab').style.display='none';
  if(!STP.msgs.length)stpAdd('assistant',STP_GREET);
  document.getElementById('stp-in').focus();
}
function closeStepan(){
  document.getElementById('stp-w').classList.remove('on');
  document.getElementById('stp-fab').style.display='';
}
async function sendStepan(){
  var inp=document.getElementById('stp-in');var text=(inp.value||'').trim();
  if(!text||STP.busy)return;
  inp.value='';inp.style.height='auto';stpAdd('user',text);STP.busy=true;stpTyping(true);
  try{
    var r=await fetch('/demo/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({messages:STP.msgs})});
    var j=await r.json();stpTyping(false);stpAdd('assistant',(j&&j.reply)||'…');
  }catch(e){stpTyping(false);stpAdd('assistant','Connection hiccup — try again?');}
  STP.busy=false;inp.focus();
}
function stpKey(e){
  var ta=e.target;ta.style.height='auto';ta.style.height=Math.min(ta.scrollHeight,110)+'px';
  if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendStepan();}
}
"""

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
/* chat widget */
.stp-fab{position:fixed;right:20px;bottom:20px;z-index:60;background:linear-gradient(135deg,var(--acc),var(--acc2));color:#fff;border:none;border-radius:999px;padding:.85rem 1.3rem;font-weight:700;font-size:.92rem;box-shadow:0 8px 26px rgba(77,166,255,.4);cursor:pointer;transition:transform .12s}
.stp-fab:hover{transform:translateY(-2px)}
.stp-w{position:fixed;right:20px;bottom:20px;z-index:61;width:370px;max-width:calc(100vw - 32px);height:560px;max-height:calc(100vh - 40px);background:var(--card);border:1px solid var(--line);border-radius:18px;box-shadow:0 24px 60px rgba(0,0,0,.55);display:none;flex-direction:column;overflow:hidden}
.stp-w.on{display:flex}
.stp-hd{display:flex;align-items:center;gap:.6rem;padding:.85rem 1rem;border-bottom:1px solid var(--line);background:linear-gradient(135deg,rgba(77,166,255,.14),rgba(124,92,255,.14))}
.stp-hd b{font-size:.95rem}.stp-hd small{display:block;font-size:.66rem;color:var(--mut)}
.stp-x{margin-left:auto;background:none;border:none;color:var(--mut);font-size:1.4rem;cursor:pointer;line-height:1}
.stp-body{flex:1;overflow-y:auto;padding:1rem;display:flex;flex-direction:column;gap:.5rem}
.stp-msg{max-width:82%;padding:.55rem .8rem;border-radius:14px;font-size:.9rem;line-height:1.4;white-space:pre-wrap;word-wrap:break-word}
.stp-msg.a{align-self:flex-start;background:#1c2431;border:1px solid var(--line);border-bottom-left-radius:5px}
.stp-msg.u{align-self:flex-end;background:linear-gradient(135deg,var(--acc),var(--acc2));color:#fff;border-bottom-right-radius:5px}
.stp-typ{display:flex;gap:4px;align-items:center}
.stp-typ span{width:6px;height:6px;border-radius:50%;background:var(--mut);animation:stpb 1s infinite}
.stp-typ span:nth-child(2){animation-delay:.15s}.stp-typ span:nth-child(3){animation-delay:.3s}
@keyframes stpb{0%,60%,100%{opacity:.3}30%{opacity:1}}
.stp-foot{display:flex;gap:.5rem;padding:.7rem;border-top:1px solid var(--line);align-items:flex-end}
.stp-foot textarea{flex:1;background:var(--bg);border:1px solid var(--line);border-radius:10px;color:var(--ink);padding:.55rem .7rem;font-size:.9rem;resize:none;font-family:inherit;max-height:110px}
.stp-foot textarea:focus{outline:none;border-color:var(--acc)}
.stp-send{background:linear-gradient(135deg,var(--acc),var(--acc2));color:#fff;border:none;border-radius:10px;width:40px;height:38px;font-size:1rem;cursor:pointer}
@media (max-width:460px){.stp-w{right:8px;bottom:8px;width:calc(100vw - 16px);height:calc(100vh - 16px)}.stp-fab{right:12px;bottom:12px}}
/* product mockups (illustrative — not real data) */
.shots{display:grid;grid-template-columns:1fr 1fr;gap:1.4rem;margin-top:2.6rem;align-items:start}
.frame{background:#0d0f15;border:1px solid var(--line);border-radius:16px;overflow:hidden;box-shadow:0 22px 55px rgba(0,0,0,.5)}
.fbar{display:flex;align-items:center;gap:.4rem;padding:.55rem .8rem;border-bottom:1px solid var(--line);background:#12151d}
.fbar i{width:11px;height:11px;border-radius:50%;display:inline-block}
.fd1{background:#ff5f57}.fd2{background:#febc2e}.fd3{background:#28c840}
.furl{margin-left:.5rem;font-size:.68rem;color:var(--mut);background:#0d0f15;border:1px solid var(--line);border-radius:6px;padding:.16rem .6rem;flex:1;text-align:center}
.ph-top{display:flex;align-items:center;gap:.55rem;padding:.7rem .9rem;border-bottom:1px solid var(--line);background:#12151d}
.ph-ava{width:32px;height:32px;border-radius:50%;background:linear-gradient(135deg,#ff8fb1,#c86fff);display:flex;align-items:center;justify-content:center;font-weight:700;color:#fff;font-size:.85rem}
.ph-top b{font-size:.9rem}.ph-top small{display:block;font-size:.64rem;color:#51cf66}
.ph-body{padding:.9rem;display:flex;flex-direction:column;gap:.45rem;background:#0d0f15}
.mb{max-width:82%;padding:.5rem .75rem;border-radius:14px;font-size:.83rem;line-height:1.42}
.mb.in{align-self:flex-start;background:#1c2431;border:1px solid var(--line);border-bottom-left-radius:5px}
.mb.out{align-self:flex-end;background:linear-gradient(135deg,var(--acc),var(--acc2));color:#fff;border-bottom-right-radius:5px}
.mb .who{display:block;font-size:.58rem;opacity:.65;margin-bottom:.15rem}
.dash{padding:1rem;display:flex;flex-direction:column;gap:.9rem;background:#0d0f15}
.mcard{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:.9rem}
.mlbl{font-size:.6rem;color:var(--mut);text-transform:uppercase;letter-spacing:.09em;margin-bottom:.55rem}
.leadrow{display:flex;align-items:center;gap:.5rem;margin-bottom:.65rem}
.av{width:28px;height:28px;border-radius:50%;background:linear-gradient(135deg,#5ac8fa,#4d7cff);display:flex;align-items:center;justify-content:center;font-size:.72rem;font-weight:700;color:#fff}
.leadrow b{font-size:.85rem}
.spill{margin-left:auto;font-size:.6rem;background:rgba(155,122,255,.16);color:#b79bff;border-radius:20px;padding:.12rem .6rem}
.chips{display:flex;flex-wrap:wrap;gap:.35rem}
.ch2{font-size:.68rem;border-radius:8px;padding:.2rem .5rem;border:1px solid}
.c-goal{background:#12233b;color:#7db8ff;border-color:#1e3a5f}
.c-pain{background:#301a1a;color:#ffb0a3;border-color:#4a2a2a}
.c-gain{background:#132a1e;color:#8fe3ac;border-color:#1e3a2a}
.fn{display:flex;flex-direction:column;gap:.45rem}
.fnrow{display:flex;align-items:center;gap:.6rem}
.fnrow .nm{width:74px;color:var(--mut);font-size:.72rem}
.fnbar{height:15px;border-radius:5px;background:linear-gradient(90deg,var(--acc),var(--acc2))}
.fnrow .v{font-weight:700;font-size:.72rem;margin-left:auto}
.alert{display:flex;align-items:center;gap:.5rem;font-size:.78rem;color:#8fe3ac;background:rgba(81,207,102,.1);border:1px solid rgba(81,207,102,.28);border-radius:10px;padding:.6rem .75rem}
.mnote{text-align:center;font-size:.72rem;color:var(--mut);margin-top:1rem;opacity:.8}
/* meta / attribution mockup */
.meta{display:grid;grid-template-columns:1.15fr .85fr;gap:1.4rem;margin-top:2.4rem;align-items:stretch}
.mpanel{background:#0d0f15;border:1px solid var(--line);border-radius:16px;overflow:hidden;box-shadow:0 22px 55px rgba(0,0,0,.5);display:flex;flex-direction:column}
.mhd{display:flex;align-items:center;gap:.5rem;padding:.7rem .95rem;border-bottom:1px solid var(--line);background:#12151d;font-size:.8rem;font-weight:600}
.mhd .dot{width:8px;height:8px;border-radius:50%;background:#51cf66;box-shadow:0 0 8px #51cf66}
.mhd .pill{margin-left:auto;font-size:.6rem;color:#8fe3ac;background:rgba(81,207,102,.12);border:1px solid rgba(81,207,102,.28);border-radius:20px;padding:.1rem .55rem}
.mbody{padding:1rem;display:flex;flex-direction:column;gap:.7rem}
.adrow{display:flex;align-items:center;gap:.7rem;padding:.6rem .7rem;background:var(--card);border:1px solid var(--line);border-radius:11px}
.adth{width:38px;height:38px;border-radius:9px;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:1.1rem}
.a1{background:linear-gradient(135deg,#ff8fb1,#c86fff)}.a2{background:linear-gradient(135deg,#5ac8fa,#4d7cff)}.a3{background:linear-gradient(135deg,#ffd36e,#ff9f45)}
.adrow .an{font-size:.8rem;font-weight:600}.adrow .as{font-size:.64rem;color:var(--mut)}
.adkpi{margin-left:auto;text-align:right}.adkpi b{font-size:.95rem;display:block}.adkpi span{font-size:.6rem;color:var(--mut)}
.flow{display:flex;flex-direction:column;gap:.55rem;padding:.2rem 0}
.fsrc{display:flex;align-items:center;gap:.55rem;font-size:.76rem;background:var(--card);border:1px solid var(--line);border-radius:9px;padding:.42rem .6rem}
.fsrc .ic{width:22px;height:22px;border-radius:6px;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:.8rem}
.fsrc .ph{margin-left:auto;font-size:.6rem;color:var(--mut);font-family:ui-monospace,monospace}
.merge{text-align:center;color:var(--acc2);font-size:1.1rem;line-height:1}
.uni{background:linear-gradient(135deg,rgba(124,92,255,.14),rgba(77,166,255,.1));border:1px solid rgba(124,92,255,.35);border-radius:12px;padding:.8rem}
.uni .ur{display:flex;align-items:center;gap:.5rem}
.uni .av{width:30px;height:30px;border-radius:50%;background:linear-gradient(135deg,#5ac8fa,#4d7cff);display:flex;align-items:center;justify-content:center;font-size:.75rem;font-weight:700;color:#fff}
.uni b{font-size:.85rem}.uni .ph2{margin-left:auto;font-size:.62rem;color:var(--mut);font-family:ui-monospace,monospace}
.uni .ut{margin-top:.5rem;font-size:.66rem;color:var(--mut)}
.uni .tags{display:flex;flex-wrap:wrap;gap:.3rem;margin-top:.45rem}
.uni .tg{font-size:.62rem;background:rgba(77,166,255,.14);color:#7db8ff;border:1px solid rgba(77,166,255,.3);border-radius:6px;padding:.14rem .45rem}
.push{display:flex;align-items:center;gap:.5rem;font-size:.72rem;color:#8fe3ac;margin-top:.6rem}
@media (max-width:760px){.shots{grid-template-columns:1fr}.meta{grid-template-columns:1fr}}
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
        "<button class=\"btn btn-p\" onclick=\"openStepan()\">Talk to Stepan →</button>"
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
        # ── a peek inside (illustrative mockups) ──
        "<section><div class=\"wrap\">"
        "<div class=\"kick\">A peek inside</div>"
        "<h2>See what it actually does</h2>"
        "<p class=\"lead\">A real conversation on one side, your live dashboard on the other — "
        "Stepan works the lead end to end and hands you the ready-to-buy ones.</p>"
        "<div class=\"shots\">"
        # chat mockup
        "<div class=\"frame\">"
        "<div class=\"ph-top\"><span class=\"ph-ava\">M</span>"
        "<div><b>Maya</b><small>● Instagram · online</small></div></div>"
        "<div class=\"ph-body\">"
        "<div class=\"mb in\">hi! saw your ad — is this ok if I've literally never trained "
        "before? 😅</div>"
        "<div class=\"mb out\"><span class=\"who\">Stepan</span>Totally — that's exactly where "
        "most people start, Maya 🙌 Quick one: what would you most love to change first — "
        "energy, strength, or how you feel in your clothes?</div>"
        "<div class=\"mb in\">honestly how I feel in my clothes… but I have zero time for a "
        "gym</div>"
        "<div class=\"mb out\"><span class=\"who\">Stepan</span>Hear you — \"no time\" is the "
        "#1 reason people stall. It's built around 20-min sessions you can do at home, "
        "shaped to your week. Want me to show how your first two weeks would look?</div>"
        "<div class=\"mb in\">yes please 🙏</div>"
        "</div></div>"
        # dashboard mockup
        "<div class=\"frame\">"
        "<div class=\"fbar\"><i class=\"fd1\"></i><i class=\"fd2\"></i><i class=\"fd3\"></i>"
        "<span class=\"furl\">Stepan · your dashboard</span></div>"
        "<div class=\"dash\">"
        "<div class=\"mcard\"><div class=\"mlbl\">Lead — captured automatically</div>"
        "<div class=\"leadrow\"><span class=\"av\">M</span><b>Maya R.</b>"
        "<span class=\"spill\">Qualifying</span></div>"
        "<div class=\"chips\">"
        "<span class=\"ch2 c-goal\">🎯 Feel great in her clothes</span>"
        "<span class=\"ch2 c-pain\">😣 No time for the gym</span>"
        "<span class=\"ch2 c-gain\">✨ 20-min home workouts</span>"
        "</div></div>"
        "<div class=\"mcard\"><div class=\"mlbl\">Live funnel · this week</div>"
        "<div class=\"fn\">"
        "<div class=\"fnrow\"><span class=\"nm\">New</span>"
        "<span class=\"fnbar\" style=\"width:100%\"></span><span class=\"v\">128</span></div>"
        "<div class=\"fnrow\"><span class=\"nm\">Nurturing</span>"
        "<span class=\"fnbar\" style=\"width:58%\"></span><span class=\"v\">74</span></div>"
        "<div class=\"fnrow\"><span class=\"nm\">Qualifying</span>"
        "<span class=\"fnbar\" style=\"width:32%\"></span><span class=\"v\">41</span></div>"
        "<div class=\"fnrow\"><span class=\"nm\">Presenting</span>"
        "<span class=\"fnbar\" style=\"width:18%\"></span><span class=\"v\">22</span></div>"
        "<div class=\"fnrow\"><span class=\"nm\">Ready</span>"
        "<span class=\"fnbar\" style=\"width:8%\"></span><span class=\"v\">9</span></div>"
        "</div></div>"
        "<div class=\"alert\">🔔 Maya's ready to book — handed to your team just now.</div>"
        "</div></div>"
        "</div>"
        "<p class=\"mnote\">Illustrative — sample data, not a real customer.</p>"
        "</div></section>"
        # ── meta integration + attribution + identity resolution ──
        "<section><div class=\"wrap\">"
        "<div class=\"kick\">Connected to Meta</div>"
        "<h2>Every ad measured. Every lead unified.</h2>"
        "<p class=\"lead\">Stepan plugs straight into your Meta account — conversions flow back "
        "so you see which ad actually earns, and the same person across different products is "
        "merged into one profile by phone number.</p>"
        "<div class=\"meta\">"
        # left: per-ad performance
        "<div class=\"mpanel\">"
        "<div class=\"mhd\"><span class=\"dot\"></span>Ad performance · synced from Meta"
        "<span class=\"pill\">● live</span></div>"
        "<div class=\"mbody\">"
        "<div class=\"adrow\"><span class=\"adth a1\">🏋️</span>"
        "<div><div class=\"an\">Home Fitness — Reels</div>"
        "<div class=\"as\">Leads 142 · CPL $3.10</div></div>"
        "<div class=\"adkpi\"><b>28 booked</b><span>ROAS 4.6×</span></div></div>"
        "<div class=\"adrow\"><span class=\"adth a2\">🥗</span>"
        "<div><div class=\"an\">Meal Plan — Stories</div>"
        "<div class=\"as\">Leads 96 · CPL $4.80</div></div>"
        "<div class=\"adkpi\"><b>11 booked</b><span>ROAS 2.1×</span></div></div>"
        "<div class=\"adrow\"><span class=\"adth a3\">💪</span>"
        "<div><div class=\"an\">1-on-1 Coaching — Feed</div>"
        "<div class=\"as\">Leads 54 · CPL $6.20</div></div>"
        "<div class=\"adkpi\"><b>19 booked</b><span>ROAS 5.9×</span></div></div>"
        "<div class=\"push\">↗ Conversions pushed back to Meta — the algorithm learns who buys."
        "</div>"
        "</div></div>"
        # right: identity resolution by phone
        "<div class=\"mpanel\">"
        "<div class=\"mhd\"><span class=\"dot\"></span>One lead, every touchpoint</div>"
        "<div class=\"mbody\">"
        "<div class=\"flow\">"
        "<div class=\"fsrc\"><span class=\"ic a1\">🏋️</span>Home Fitness ad"
        "<span class=\"ph\">+1 ••• 4471</span></div>"
        "<div class=\"fsrc\"><span class=\"ic a3\">💪</span>Coaching ad"
        "<span class=\"ph\">+1 ••• 4471</span></div>"
        "<div class=\"merge\">▼ merged by phone ▼</div>"
        "<div class=\"uni\">"
        "<div class=\"ur\"><span class=\"av\">M</span><b>Maya R.</b>"
        "<span class=\"ph2\">+1 ••• 4471</span></div>"
        "<div class=\"ut\">2 ad sources · first seen 6 days ago · now Qualifying</div>"
        "<div class=\"tags\"><span class=\"tg\">Home Fitness</span>"
        "<span class=\"tg\">1-on-1 Coaching</span></div>"
        "</div>"
        "</div>"
        "</div></div>"
        "</div>"
        "<p class=\"mnote\">Illustrative — sample data, not a real account.</p>"
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
        "<button class=\"btn btn-p\" onclick=\"openStepan()\">Talk to Stepan →</button></div>"
        "</div></div></section>"
        # footer
        "<footer><div class=\"wrap foot\">"
        "<div class=\"brand\" style=\"font-size:1rem\"><span class=\"logo\" "
        "style=\"width:24px;height:24px;font-size:.8rem\">S</span>Stepan</div>"
        "<div>AI sales agent for Instagram &amp; WhatsApp · "
        f"<a href=\"{_DEMO_IG}\" target=\"_blank\" rel=\"noopener\" style=\"color:var(--acc)\">"
        "Instagram</a> · "
        "<a href=\"/login\" style=\"color:var(--acc)\">Log in</a></div>"
        "</div></footer>"
        # live demo chat widget — Stepan sells itself (POST /demo/chat)
        "<button class=\"stp-fab\" id=\"stp-fab\" onclick=\"openStepan()\">💬 Chat with Stepan</button>"
        "<div id=\"stp-w\" class=\"stp-w\">"
        "<div class=\"stp-hd\"><span class=\"logo\">S</span>"
        "<div><b>Stepan</b><small>Live demo · a real conversation</small></div>"
        "<button class=\"stp-x\" onclick=\"closeStepan()\" aria-label=\"Close\">×</button></div>"
        "<div id=\"stp-body\" class=\"stp-body\"></div>"
        "<div class=\"stp-foot\">"
        "<textarea id=\"stp-in\" rows=\"1\" placeholder=\"Message like one of your leads…\""
        " onkeydown=\"stpKey(event)\"></textarea>"
        "<button class=\"stp-send\" onclick=\"sendStepan()\" aria-label=\"Send\">➤</button></div>"
        "</div>"
        f"<script>{_WIDGET_JS}</script>"
        "</body></html>"
    )
