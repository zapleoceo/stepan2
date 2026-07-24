"""Internal "How it works" page — an interactive, top-down map of the whole system.

Served at /hiw for the team (technical reviews, onboarding new members) in two
English only. NOT in the
public allowlist (app/api/_auth.py), so with auth enabled it requires a session
like the rest of the app. Self-contained HTML (own <!doctype> + inline CSS/JS,
no CDN). Content drills down in three levels per topic: plain-language gist →
mechanics → file paths and production incidents.
"""
# ruff: noqa: E501 — inline CSS/HTML strings; long lines are inherent, not code smell
from __future__ import annotations

_CSS = r"""
/* Same design language as the landing and /whats-new (app/api/_landing.py, _changelog.py):
   one always-dark palette, Inter for text, Space Grotesk for display, the orange accent.
   The variable NAMES are kept from this page's own older (light, teal, Georgia) theme so the
   ~100 component rules below keep working untouched — only the values move to the site's. */
:root{
  --bg:#08090c; --surface:#0e1014; --ink:#f2f4f7; --muted:#9aa3b2;
  --accent:#ff5c35; --accent-ink:#ff8a63; --accent-soft:rgba(255,92,53,.12);
  --warn:#ffa94d; --warn-soft:rgba(255,169,77,.12);
  --line:#20232b; --code-bg:#15171d;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.3);
  --hero-ink:#f2f4f7; --hero-muted:#9aa3b2;
  --sans:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  --disp:'Space Grotesk',var(--sans);
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
@media (prefers-reduced-motion: reduce){ html{scroll-behavior:auto} *{transition:none!important;animation:none!important} }
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.62 var(--sans);-webkit-font-smoothing:antialiased;font-feature-settings:"cv02","cv03","cv04"}
main{max-width:920px;margin:0 auto;padding:0 20px 96px}
h1,h2,h3{font-family:var(--disp);line-height:1.25;text-wrap:balance;letter-spacing:-.02em}
h1{font-size:clamp(30px,5vw,46px);margin:.2em 0 .3em;font-weight:700}
h2{font-size:26px;margin:0 0 6px}
h3{font-size:19px;margin:20px 0 8px}
p{margin:.5em 0}
a{color:var(--accent-ink)}
code{font:.86em ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--code-bg);padding:1px 6px;border-radius:5px;word-break:break-all}

/* hero — the site's panel tone lifted a step off the page, with the orange glow */
.hero{background:linear-gradient(160deg,#0e1014 0%,#15171d 55%,#0b0c10 100%);color:var(--hero-ink);padding:56px 20px 46px;position:relative;overflow:hidden;border-bottom:1px solid var(--line)}
.hero::after{content:"";position:absolute;inset:auto -20% -60% -20%;height:80%;background:radial-gradient(ellipse at 50% 100%,var(--accent-soft),transparent 70%);pointer-events:none}
.hero-in{max-width:880px;margin:0 auto;position:relative}
.topline{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:12px;margin-bottom:26px}
.eyebrow{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--accent);font-weight:700}
.hero .lede{font-size:19px;color:var(--hero-muted);max-width:64ch}
.hero a.back{color:var(--accent);font-size:13.5px;text-decoration:none;border:1px solid rgba(255,92,53,.35);border-radius:999px;padding:6px 14px;display:inline-block}
.hero a.back:hover{background:var(--accent-soft)}
.langsw{display:flex;gap:4px;border:1px solid rgba(255,92,53,.35);border-radius:999px;padding:3px}
.langsw a{color:var(--hero-muted);font-size:12.5px;font-weight:700;letter-spacing:.06em;text-decoration:none;padding:4px 12px;border-radius:999px}
.langsw a.on{background:var(--accent-soft);color:var(--accent)}
.langsw a:hover{color:var(--accent)}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin:24px 0 4px}
.stat{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.12);border-radius:10px;padding:10px 14px;-webkit-backdrop-filter:blur(2px);backdrop-filter:blur(2px)}
.stat b{display:block;font-size:20px;font-variant-numeric:tabular-nums;color:var(--accent);font-family:var(--disp)}
.stat span{font-size:12.5px;color:var(--hero-muted)}
.hint{font-size:14px;color:var(--hero-muted);margin-top:18px;max-width:70ch}
.hint b{color:var(--hero-ink)}

nav.toc{position:sticky;top:0;z-index:30;background:var(--bg);border-bottom:1px solid var(--line);margin:0 -20px 30px;padding:10px 20px;display:flex;gap:8px;align-items:center;overflow-x:auto;white-space:nowrap}
nav.toc a{font-size:13.5px;color:var(--muted);text-decoration:none;padding:5px 10px;border-radius:999px}
nav.toc a:hover{color:var(--accent-ink);background:var(--accent-soft)}
.controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:0 0 26px}
.controls input[type="search"]{flex:1;min-width:220px;padding:10px 14px;border:1px solid var(--line);border-radius:10px;background:var(--surface);color:var(--ink);font:inherit;font-size:14.5px}
.controls input[type="search"]:focus{outline:2px solid var(--accent);outline-offset:1px}
button.ctl{font:600 13.5px/1 -apple-system,"Segoe UI",Roboto,sans-serif;color:var(--accent-ink);background:var(--accent-soft);border:1px solid transparent;border-radius:999px;padding:9px 14px;cursor:pointer}
button.ctl:hover{border-color:var(--accent)}
button.ctl:focus-visible{outline:2px solid var(--accent);outline-offset:2px}

section.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:26px 28px 20px;margin:0 0 22px;box-shadow:var(--shadow);scroll-margin-top:70px}
.reveal{opacity:0;transform:translateY(14px);transition:opacity .5s ease,transform .5s ease}
.reveal.on{opacity:1;transform:none}
@media (prefers-reduced-motion: reduce){ .reveal{opacity:1;transform:none} }
.kicker{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);font-weight:600}
.gist{color:var(--ink);font-size:16.5px;max-width:66ch}
details{border-top:1px solid var(--line);margin-top:14px;padding-top:2px}
details summary{cursor:pointer;font-weight:600;padding:10px 2px;list-style:none;display:flex;align-items:center;gap:9px;color:var(--ink)}
details summary::-webkit-details-marker{display:none}
details summary::before{content:"+";display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;border-radius:6px;background:var(--accent-soft);color:var(--accent-ink);font-weight:700;flex:none}
details[open] > summary::before{content:"–"}
details summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:6px}
details .body{padding:2px 2px 14px;max-width:70ch}
details.l3{border-top:1px dashed var(--line);margin:10px 0 4px}
details.l3 > summary{font-size:14px;color:var(--muted)}
details.l3 > summary::before{background:transparent;border:1px solid var(--line);color:var(--muted)}
details.l3 .body{font-size:14.5px;color:var(--ink)}
ul{padding-left:22px;margin:.4em 0}
li{margin:4px 0}
li::marker{color:var(--accent)}
table{border-collapse:collapse;width:100%;font-size:14px;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.tablewrap{overflow-x:auto;margin:10px 0}
.inv{background:var(--accent-soft);border-left:3px solid var(--accent);border-radius:0 10px 10px 0;padding:10px 14px;margin:12px 0;font-size:14.5px}

.pipe{display:flex;gap:6px;flex-wrap:wrap;margin:18px 0 0}
.pipe button{flex:1 1 130px;min-width:120px;text-align:left;background:var(--bg);border:1px solid var(--line);border-radius:11px;padding:12px 12px;cursor:pointer;font:inherit;color:var(--ink)}
.pipe button .n{font-size:11px;font-weight:700;color:var(--muted);letter-spacing:.08em}
.pipe button .t{display:block;font-weight:700;font-size:14.5px;margin-top:2px}
.pipe button .s{display:block;font-size:12px;color:var(--muted);margin-top:2px}
.pipe button[aria-selected="true"]{background:var(--accent-soft);border-color:var(--accent)}
.pipe button[aria-selected="true"] .n{color:var(--accent-ink)}
.pipe button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.pipe-panel{border:1px solid var(--accent);border-radius:12px;background:var(--bg);padding:16px 20px;margin-top:12px}
.pipe-panel[hidden]{display:none}
.stagechips{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:10px 0}
.chip{font-size:12.5px;font-weight:600;border:1px solid var(--line);border-radius:999px;padding:3px 11px;background:var(--surface)}
.chip.hot{border-color:var(--accent);color:var(--accent-ink);background:var(--accent-soft)}
.arrow{color:var(--muted);font-size:12px}
.cols2{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:8px 26px}
footer{color:var(--muted);font-size:13px;margin-top:40px;border-top:1px solid var(--line);padding-top:16px}
.qa dt{font-weight:700;margin-top:14px}
.qa dd{margin:4px 0 0 0;color:var(--ink);max-width:70ch}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:13px;color:var(--muted);margin-top:10px}
.legend i{font-style:normal}

/* small screens: tighter cards and hero, full-width pipeline steps */
@media (max-width:520px){
  .hero{padding:36px 16px 32px}
  h1{font-size:28px}
  h2{font-size:22px}
  section.card{padding:20px 16px 14px}
  .pipe button{flex:1 1 46%;min-width:0}
  .controls button.ctl{flex:1}
}
"""

_JS = r"""
(function(){
  var tabs = document.querySelectorAll('.pipe [role="tab"]');
  var panels = document.querySelectorAll('.pipe-panel');
  tabs.forEach(function(tab){
    tab.addEventListener('click', function(){
      tabs.forEach(function(t){ t.setAttribute('aria-selected','false'); });
      panels.forEach(function(p){ p.hidden = true; });
      tab.setAttribute('aria-selected','true');
      document.getElementById(tab.dataset.panel).hidden = false;
    });
  });
  document.getElementById('openAll').addEventListener('click', function(){
    document.querySelectorAll('details').forEach(function(d){ d.open = true; });
    panels.forEach(function(p){ p.hidden = false; });
  });
  document.getElementById('closeAll').addEventListener('click', function(){
    document.querySelectorAll('details').forEach(function(d){ d.open = false; });
  });
  var q = document.getElementById('q');
  var cards = Array.prototype.slice.call(document.querySelectorAll('section.card'));
  var t;
  q.addEventListener('input', function(){
    clearTimeout(t);
    t = setTimeout(function(){
      var v = q.value.trim().toLowerCase();
      cards.forEach(function(c){
        if(!v){ c.style.display=''; return; }
        var match = c.textContent.toLowerCase().indexOf(v) !== -1;
        c.style.display = match ? '' : 'none';
        if(match){
          c.querySelectorAll('details').forEach(function(d){
            if(d.textContent.toLowerCase().indexOf(v) !== -1) d.open = true;
          });
        }
      });
    }, 160);
  });
  // scroll-reveal (skipped when the user prefers reduced motion — CSS shows all)
  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function(entries){
      entries.forEach(function(e){ if(e.isIntersecting){ e.target.classList.add('on'); io.unobserve(e.target); } });
    }, {rootMargin:'0px 0px -8% 0px'});
    document.querySelectorAll('.reveal').forEach(function(el){ io.observe(el); });
  } else {
    document.querySelectorAll('.reveal').forEach(function(el){ el.classList.add('on'); });
  }
})();
"""




# ─── English body ─────────────────────────────────────────────────────────────

_BODY_EN = r"""
<div class="hero">
  <div class="hero-in">
    <div class="topline"><a class="back" href="/ui/inbox">← back to admin</a>__LANGSW__</div>
    <div class="eyebrow">Project map · top-down</div>
    <h1>How Stepan works</h1>
    <p class="lede">A platform where an AI sells over chat in Instagram and WhatsApp: it finds out what a person needs, picks the right course, handles objections and hands the hot lead over to a human manager. Every branch is an isolated tenant with its own knowledge base, bot and people.</p>
    <div class="stats">
      <div class="stat"><b>146</b><span>Python source files</span></div>
      <div class="stat"><b>~1,070</b><span>automated tests in 98 files</span></div>
      <div class="stat"><b>10</b><span>scheduled background jobs</span></div>
      <div class="stat"><b>3</b><span>channels: Instagram · WhatsApp · Meta</span></div>
      <div class="stat"><b>0–5</b><span>build phases — all closed</span></div>
    </div>
    <p class="hint">Every topic below unfolds in three levels: <b>the gist</b> → <b>how it works</b> → <b>all the way down</b> (file paths, subtleties, real production incidents). Use the search box and the "Expand all" button.</p>
  </div>
</div>

<main>
<nav class="toc" aria-label="Contents">
  <a href="#idea">The idea</a>
  <a href="#journey">Message journey</a>
  <a href="#brain">The brain</a>
  <a href="#guard">Kill switches</a>
  <a href="#kb">Knowledge</a>
  <a href="#leads">Leads</a>
  <a href="#channels">Channels</a>
  <a href="#worker">The conveyor</a>
  <a href="#access">Access</a>
  <a href="#money">Money</a>
  <a href="#crm">CRM &amp; ads</a>
  <a href="#extras">Plumbing</a>
  <a href="#quality">Quality</a>
  <a href="#review">Crib sheet</a>
  <a href="#glossary">Glossary</a>
</nav>

<div class="controls">
  <input id="q" type="search" placeholder="Search the document… (e.g.: phone, budget, guard)" aria-label="Search the document">
  <button class="ctl" id="openAll" type="button">Expand all</button>
  <button class="ctl" id="closeAll" type="button">Collapse all</button>
</div>

<!-- THE IDEA -->
<section class="card reveal" id="idea">
  <div class="kicker">01 · The idea</div>
  <h2>What this is and why</h2>
  <p class="gist">Stepan-2 is a "hotel for sales bots". One platform serves many <b>branches</b>: each has its own knowledge base, its own courses and prices, its own bot personality, language and staff. Branch data never crosses over — that is the number-one security rule of the whole project.</p>
  <details>
    <summary>How the second version differs from the first</summary>
    <div class="body">
      <ul>
        <li><b>Stepan-1</b> is a bot for a single client: one account, one database, everything hard-wired for Indonesia.</li>
        <li><b>Stepan-2</b> is a platform: branches are added without rewriting code. There is no "default branch" — every one lives under its own id.</li>
        <li>All LLM traffic goes through a single external gateway (<b>AIbroker</b>): the project holds no model-provider keys at all, and every call returns its exact cost in dollars — that number drives each branch's budget.</li>
        <li>Messengers sit behind a shared adapter: the way we talk to Instagram can be swapped without touching the bot's brain.</li>
        <li>Full independence from version one: separate repository, separate database, separate containers. Stepan-1 keeps running; the switch happens when we are ready.</li>
      </ul>
      <details class="l3">
        <summary>All the way down: the money logic and current status</summary>
        <div class="body">
          <ul>
            <li>Model economics: the expensive model is used only for "money moments" (price, payment, readiness); the cheap one handles the bulk. The boundary is tuned with an offline script, <code>scripts/bakeoff_capability.py</code>: it replays real conversations through both models and compares their decisions.</li>
            <li>The long-term plan is to close the advertising loop: ad → lead → signed contract in the CRM → signal back to Meta, so ads optimise for buyers.</li>
            <li>Status: phases 0–5 are closed, the platform is in production. The sending conveyor is deliberately switched off until the final cutover from Stepan-1 — two bots must not write into the same Instagram account at once (ban risk).</li>
            <li>Documentation: <code>README.md</code>, <code>docs/multitenant-design.md</code> (the key document), 19 topic files under <code>docs/</code>.</li>
          </ul>
        </div>
      </details>
    </div>
  </details>
  <details>
    <summary>What it is built from (the stack in one paragraph)</summary>
    <div class="body">
      <p>Python 3.12. The web part is FastAPI (pages are rendered server-side and come alive through HTMX — no heavy frontend). The database is PostgreSQL, the background-job queue is Redis + ARQ. Schema changes go through Alembic migrations. Everything ships as 4 docker containers: database, Redis, web app, worker.</p>
      <details class="l3">
        <summary>All the way down: the architectural style</summary>
        <div class="body">
          <p>Ports &amp; adapters (hexagonal architecture): the core (<code>app/domain/</code>, <code>app/modules/</code>) knows nothing about Instagram, LLMs or Telegram — it talks to abstract "port" interfaces (<code>app/ports/</code>: channel, LLM, notifications). Concrete implementations are "adapters" (<code>app/adapters/</code>). On top of that, a modular monolith: each module (<code>auth</code>, <code>conversation</code>, <code>knowledge</code>, <code>leads</code>…) owns its area. Any piece of infrastructure can be replaced without rebuilding the brain.</p>
        </div>
      </details>
    </div>
  </details>
</section>

<!-- MESSAGE JOURNEY -->
<section class="card reveal" id="journey">
  <div class="kicker">02 · The main plot</div>
  <h2>The journey of one message</h2>
  <p class="gist">Everything the platform does is six steps between "the client wrote" and "the client got a reply". Click a step to open it.</p>

  <div class="pipe" role="tablist" aria-label="Message journey steps">
    <button role="tab" aria-selected="true" data-panel="p1"><span class="n">STEP 1</span><span class="t">Client writes</span><span class="s">Instagram / WhatsApp</span></button>
    <button role="tab" aria-selected="false" data-panel="p2"><span class="n">STEP 2</span><span class="t">Intake</span><span class="s">who is it, what is new</span></button>
    <button role="tab" aria-selected="false" data-panel="p3"><span class="n">STEP 3</span><span class="t">Decision</span><span class="s">the AI thinks</span></button>
    <button role="tab" aria-selected="false" data-panel="p4"><span class="n">STEP 4</span><span class="t">Verification</span><span class="s">no fabrication, and it must sell</span></button>
    <button role="tab" aria-selected="false" data-panel="p5"><span class="n">STEP 5</span><span class="t">Queue</span><span class="s">human-like pacing</span></button>
    <button role="tab" aria-selected="false" data-panel="p6"><span class="n">STEP 6</span><span class="t">Sending</span><span class="s">limits and courtesy</span></button>
  </div>

  <div class="pipe-panel" id="p1" role="tabpanel">
    <h3 style="margin-top:0">The client writes — and the platform itself asks "anything new?"</h3>
    <p>Messengers do not push notifications here (private APIs simply have none). So every <b>2 minutes</b> the platform polls every active channel of every branch: "any new messages?". A random pause precedes each poll so we never hit Instagram at the same second (ban protection).</p>
    <details class="l3"><summary>All the way down</summary><div class="body">
      <p>The <code>ingest_active_channels</code> job in <code>app/worker/main.py</code> fans out one task per branch; each channel is polled in its own transaction — one failure never breaks the rest. Instagram is read through the private API (instagrapi), and the raw JSON is parsed by hand — the library's own parser crashes on shared posts (<code>app/adapters/channels/ig_parse.py</code>). Message requests (the pending inbox) are read too — that is where cold ad leads live. If a channel session gets "sick" (Instagram demands verification), the channel is frozen across all cycles and the manager gets a "re-login needed" alert.</p>
    </div></details>
  </div>

  <div class="pipe-panel" id="p2" role="tabpanel" hidden>
    <h3 style="margin-top:0">Intake: figure out who wrote and never double-count</h3>
    <p>Every new message passes filters: have we seen it before (duplicate protection), and who wrote it — the client or our own manager replying by hand from the phone app. Then the key trick: <b>one person across different messengers = one client</b>. Merging is by phone number within the branch. A fresh message extends the 24-hour "reply window", cancels scheduled reminders and wakes the bot if it was asleep.</p>
    <details class="l3"><summary>All the way down</summary><div class="body">
      <ul>
        <li><code>app/modules/leads/ingest.py</code> is the single write path for inbound; idempotent on the (channel, external message id) pair.</li>
        <li>The phone is extracted straight from the text (<code>phone.py</code>) with the branch's country in mind: "0812…", "62812…" and "+62 812…" produce one key; the price "Rp 1.200.000" is not mistaken for a number.</li>
        <li>Chat-hijack protection: the phone merge fires only for a brand-new conversation. If someone types a stranger's number into an existing chat, their history does not move to that number's owner (<code>identity.py</code>).</li>
        <li>A manager's manual reply from the Instagram app also lands in the database and advances the "we replied" mark — the bot never writes over a human.</li>
        <li>A dormant client wakes into the discovery stage on a new message; but if a human already owns the client (stages "ready", "handed off", "manager") the bot stays off.</li>
      </ul>
    </div></details>
  </div>

  <div class="pipe-panel" id="p3" role="tabpanel" hidden>
    <h3 style="margin-top:0">Decision: the AI reads everything and answers in a strict format</h3>
    <p>Once a minute the platform finds chats where the client spoke last, and for each one assembles a dossier: the bot's personality, the relevant knowledge-base pieces, the course card under discussion, the chat history, the client's accumulated needs and the sales rulebook. The model returns not just text but a <b>structured decision</b>: what to reply, which funnel stage the client is in, their pains and goals, whether a human is needed.</p>
    <details class="l3"><summary>All the way down</summary><div class="body">
      <ul>
        <li>The <code>reply_pending</code> dispatcher fires at second 45 of each minute — right after the intake cycle, so the reply lands within the same minute. Each chat is a separate job under a lock (double LLM calls for one chat are impossible — a real double-billing incident, closed with an advisory lock).</li>
        <li>The heart is <code>ReplyService.decide</code> (<code>app/modules/conversation/reply.py</code>) and <code>DecisionEngine</code> (<code>engine.py</code>). Before the call: the branch's daily budget check, waiting for voice-note transcripts, choosing the reply language.</li>
        <li>Model routing (<code>routing.py</code>): the cheap "fast" model takes the volume; the expensive "smart" one takes money stages and clients whose replies the guard has already had to fix; "deep" (thinks for up to 8 minutes) serves only the internal AI knowledge-base editor.</li>
        <li>If the cheap model returns broken output — one retry on the expensive one. If the reply is too similar to a previous one — regeneration with a "do not repeat yourself" correction.</li>
        <li>All calls go through the AIbroker gateway in an async "submit a job — poll for the result" mode (<code>app/adapters/llm/broker.py</code>): a slow model no longer breaks connections on proxy timeouts.</li>
      </ul>
    </div></details>
  </div>

  <div class="pipe-panel" id="p4" role="tabpanel" hidden>
    <h3 style="margin-top:0">Verification: a fabrication never reaches the client</h3>
    <p>Between "the AI drafted a reply" and "the reply went out" stands exactly one check — the money gate. It is deterministic and free: every price figure, link, income claim and offered service in the draft must exist in the knowledge base. A problem caught → one regeneration on the strong chain; still bad → the safe hold-line ships instead and the client goes to a manager. Everything else about the reply — tone, structure, sales approach — is the model's own call; the 2026-07 A/B showed the strong model sells better without a review layer (agreements 6/10 vs 3/10, forced hand-offs 0/10 vs 8/10).</p>
    <details class="l3"><summary>All the way down</summary><div class="body">
      <ul>
        <li>The gate was born after a real incident: the bot invented a link to a "lab", free access and a Cisco certificate. Files: <code>app/modules/conversation/money_gate.py</code> + <code>guard.py</code> (the detectors), orchestration in <code>reply.py</code>, the doc <code>docs/free-mode.md</code>.</li>
        <li>It fails CLOSED: an ungrounded figure never ships, and the escalation replaces the offending draft with a content-free hold-line — the flag alone used to protect the CRM record but not the client.</li>
        <li>A hedged market salary range ("kisaran 5-8 juta, tergantung…") is exempt — it is a market reference the KB can't enumerate, not a promise about our own alumni.</li>
      </ul>
    </div></details>
  </div>

  <div class="pipe-panel" id="p5" role="tabpanel" hidden>
    <h3 style="margin-top:0">Queue: the only door to the outside</h3>
    <p>A finished reply is not sent directly — it goes into the outgoing queue (outbox). This is the single path of any message to the outside world, so every limit and rule applies exactly once. A long reply is split into "bubbles" (up to three short messages) with pauses between them, and the reply itself leaves after a small random delay — as if a human were typing.</p>
    <details class="l3"><summary>All the way down</summary><div class="body">
      <p>The <code>outbox</code> table; row sources: bot reply, a manager's manual message from the panel, a follow-up reminder. The bubble separator is <code>|||</code> in the model's reply. Before enqueueing there is a final "has a parallel process already answered?" check (<code>enqueue_reply</code> in <code>reply.py</code>). Enqueueing also applies the model's decision: the funnel stage moves, the manager gets alerted, the client's needs are recorded (<code>needs.py</code> — a merge without duplicates).</p>
    </div></details>
  </div>

  <div class="pipe-panel" id="p6" role="tabpanel" hidden>
    <h3 style="margin-top:0">Sending: limits, quiet hours and courtesy towards the CRM</h3>
    <p>Every 20 seconds the sender takes one due message per chat and walks a chain of rules: is sending enabled at all; is it quiet hours (night-time reminders wait for morning, live replies always go); is the hourly/daily message cap exhausted (ban protection); is the Meta 24-hour window open; and is a human manager in the CRM already working this client — then the bot politely stays silent. Before sending, the bot "reads" the client's message and holds a pause — just like a person.</p>
    <details class="l3"><summary>All the way down</summary><div class="body">
      <ul>
        <li><code>OutboxSender.send_next</code> (<code>app/modules/conversation/outbox.py</code>); priority — a real reply beats a reminder, and a manager's messages bypass all caps.</li>
        <li>A soft Instagram block (challenge / rate limit) → retry with growing backoff; a permanent error → the chat is put to sleep. A closed Meta window → the row is marked "skipped" and the chat sleeps until a new inbound (otherwise the bot would burn money regenerating every tick — the real "Meta 400 loop" incident).</li>
        <li>After a successful send the next follow-up is scheduled. Reminder schedules are per channel; channel choice: open window → WhatsApp → Instagram.</li>
        <li>If the client asked to be left alone ("jangan ganggu", "diem") — reminders are cancelled forever. If a reminder came out as a repeat of an old one, a whole schedule step is "burned", not just the attempt (this used to be the biggest token sink: ~1,300 generations a day).</li>
      </ul>
    </div></details>
  </div>
</section>

<!-- THE BRAIN -->
<section class="card reveal" id="brain">
  <div class="kicker">03 · The brain</div>
  <h2>How Stepan sells: the methodology</h2>
  <p class="gist">The prime rule: <b>discover first, pitch second</b>. Even if someone asks the price straight away, Stepan first asks one question about their situation — and only after understanding the pain does he present a course. These are classic sales techniques (SPIN + the Value Proposition Canvas) baked into an immutable "contract" for the model.</p>
  <div class="stagechips" aria-label="Funnel stages">
    <span class="chip">new</span><span class="arrow">→</span>
    <span class="chip">nurturing</span><span class="arrow">→</span>
    <span class="chip hot">discovery</span><span class="arrow">→</span>
    <span class="chip">presenting</span><span class="arrow">→</span>
    <span class="chip">objections</span><span class="arrow">→</span>
    <span class="chip hot">ready</span><span class="arrow">→</span>
    <span class="chip">handed to manager</span>
  </div>
  <div class="legend"><i>Plus two special stages: <b>dormant</b> (silent; a new message wakes it) and <b>manager</b> (a human took over — the bot is silent).</i></div>

  <details>
    <summary>Rules the model cannot break</summary>
    <div class="body">
      <ul>
        <li><b>One question per turn.</b> Two question marks — the guard trims to the first.</li>
        <li><b>Facts only from the knowledge base.</b> Prices, dates, alumni stories — nothing "from memory".</li>
        <li><b>Phone before handoff.</b> A client cannot be passed to a manager without a phone number: if the model pushes to escalate and there is no phone, the bot first asks for WhatsApp. (Added after a client rode off to a manager with an empty number.)</li>
        <li><b>Presentation only after pain.</b> Double protection: a prompt rule plus a code gate — if the model requests the "presenting" stage but no client pain has been captured, the code rolls it back to discovery. After 4 turns of questioning the gate lets go — no interrogations.</li>
        <li><b>Reads the room.</b> When the client stalls politely ("nanti", "let me think", "I'll ask my family"), signals a tight budget, or turns out to be a school student, the code detects it and steers that one reply on the spot — ease off instead of pushing, lead with an affordable first step, bring a parent in — rather than trusting the model to remember every rule inside a long prompt.</li>
        <li><b>A question gets an answer, not a counter-question.</b> If the client asks something concrete in their own words ("how much is it?", "which days?", "how do I sign up?"), the code makes that reply answer it outright, with the fact from the card — no "could you be more specific", no "first tell me your goal". The one exception is the ad's own prefilled text: a button click is not a question, so it still gets a warm opening instead of a price.</li>
        <li><b>The payoff before the price.</b> Once a pain is on the table but the client hasn't said what they want to gain from fixing it, the code blocks the pitch for one more turn and asks for that outcome. Discovery used to break exactly where it started working: the model would catch the first pain and answer it with the price list — even when the pain was the money itself.</li>
        <li><b>Needs come from the client's own words.</b> Whatever the model reports as a job, pain or gain is kept only if it is grounded in what the client actually typed — the ad's marketing copy, or a worry read into a joke, is dropped, and a question ("is this a bot?") is never filed as a pain. The needs profile is used to steer the sale, so invented entries would steer it wrong.</li>
        <li><b>No price until the client has spoken.</b> A figure quoted to someone who has only tapped the ad — never typed a word of their own — is the fastest way to lose them, so it stopped being a request and became a hard code gate: the reply is regenerated without the price. The same holds for a follow-up into a quiet chat — it never opens with the fee to a client who has only ever clicked.</li>
        <li><b>A date that has passed is never offered.</b> A course card can outlive its own intake. Rather than trust the card to stay current, the code reads any date in the reply and blocks one already gone — the client is told the team will confirm the next batch instead of being sold a class that already started.</li>
        <li><b>A follow-up stays on the course the chat is about.</b> "Try a fresh angle" means a new reason to care — a different worry, a proof, an easier first step — not a different program. After a quiet client once got four courses pitched in four nudges, the instruction now says so in as many words.</li>
        <li><b>A business's auto-reply is not the client.</b> An "we've received your message" auto-responder no longer resets the follow-up timer or earns an answer — the bot doesn't hold a conversation with another company's robot, it waits for a person.</li>
        <li><b>An answerable question gets answered, not deferred.</b> If a client asks a plain question — the price, the days, how to sign up — the reply answers it from the catalogue, even when the client has no phone number on file. It used to be possible to meet that question with "send me your WhatsApp" instead of the answer, the same line every time the client re-asked; now the number is requested alongside the answer, never in place of it.</li>
        <li><b>A polite "not now" is kept, not closed.</b> The system lets only code mark a client ready — but it used to let the model mark one <i>dead</i> on its own, and it did exactly that the moment someone hesitated ("next time", "let me think about it"). Those clients are now held as an open objection and contacted once more, about five days later, then closed if still quiet. One check-back, not four reminders at someone who just said no — and an explicit "stop contacting me" still ends it on the spot.</li>
        <li><b>The price waits for a reason to pay it.</b> When a client has only said how they'd like to study — "online, from home" — and hasn't named what they're after, the reply asks about that first rather than dropping the full fee. A number quoted before there's a goal behind it is just something to balk at.</li>
        <li><b>These rules survive a rewrite.</b> When a reply is regenerated (for a fabrication, a repeat, a premature hand-off), the situation it was written for — a silent clicker, a stalling client, a direct question — travels with it. A correction used to arrive stripped of that context and quietly undo the right behaviour at the worst moment.</li>
        <li><b>Answers in the register he's given.</b> A four-word question gets a short reply, not four hundred characters of brochure — the code measures the client's own message and holds that reply to their length; a client who writes a paragraph still gets the full answer. Messages he sends into a quiet chat are held shorter still, since nobody asked for those. Two exceptions earn their length: the numbered opener that greets a fresh ad click, and the short menu offered to a client who has only ever clicked. Correctness always wins over brevity: a short wrong answer is worse than a long right one.</li>
        <li><b>The system decides readiness.</b> The model cannot set the "ready" stage itself — only raise a flag; the code makes the call.</li>
      </ul>
      <details class="l3"><summary>All the way down: what exactly the model returns</summary><div class="body">
        <p>The answer is strict JSON (the <code>_DECISION_CONTRACT</code> in <code>app/modules/conversation/prompt.py</code>): the reply text, stage and its reason, the course under discussion, "ready"/"needs manager" flags, phone, reply language, two orthogonal client axes — temperature (hot/warm/cold/no budget/non-target) and audience (adult/student — a schoolkid is not a rejection: 10% off and parents pay), plus the client's jobs/pains/gains (accumulated in the profile and fed into every following prompt). Reminders use a lightweight contract a third of the size.</p>
      </div></details>
    </div>
  </details>

  <details>
    <summary>What the prompt is assembled from (in order)</summary>
    <div class="body">
      <ol style="padding-left:22px">
        <li><b>The persona</b> — Stepan's character and voice (always whole, first block).</li>
        <li><b>The full card of the course under discussion</b> — the whole card: essence, price, schedule, format, outcome. The restructured cards are compact, so it goes in whole.</li>
        <li><b>The facts documents</b> — payment/discount/student policy and the market/competitor facts, plus the prohibition list (every turn — this is where policy and market facts live).</li>
        <li><b>The course catalogue</b> — a one-line QUICK FACTS summary of every other product, so a cross-course question is answerable without dumping all fifteen full cards.</li>
        <li><b>Today's date</b> in the branch's timezone — so past class dates are never offered.</li>
        <li><b>Manager notes</b>: branch-wide rules plus a per-client note ("verified, not ready yet").</li>
        <li><b>The client's known needs</b> — everything past turns have accumulated.</li>
        <li><b>The sales contract</b> + the chat history.</li>
      </ol>
      <details class="l3"><summary>All the way down: limits and caching</summary><div class="body">
        <p>The whole fact surface (persona + facts docs + every product card + the objection bank) rides in one byte-stable system prefix, capped at 90k characters — the broker's prompt cache absorbs the size (91% cache-hit measured live), so stability beats trimming. Everything per-client lives in a small second block after it. Context assembly is cheap and deterministic (no retrieval), memoized per turn. Files: <code>free_mode.py</code> (prompt assembly), <code>app/modules/knowledge/service.py</code> (<code>full_knowledge_context</code>).</p>
      </div></details>
    </div>
  </details>

  <details>
    <summary>Character: the persona library</summary>
    <div class="body">
      <p>The methodology ("what to do") is the same for all branches and lives in code. But <b>how to sound</b> is configurable: the library holds versioned personas (the built-in website-demo agent, plus each branch's imported persona), described in sections: voice and tone, discovery style, objection handling, closing style, boundaries. A branch picks a persona and can append its own addenda to any section — they survive a persona switch.</p>
      <details class="l3"><summary>All the way down</summary><div class="body">
        <p><code>app/modules/persona/service.py</code>; tables <code>persona</code> (platform-wide, versioned), <code>branch_persona</code> (choice + addenda), <code>persona_favorite</code>. The persona enters the prompt directly from the branch's <code>persona_core</code> knowledge document and deliberately repeats the "never fabricate" rule — a second line of defence next to the guard.</p>
      </div></details>
    </div>
  </details>
</section>

<!-- KILL SWITCHES -->
<section class="card reveal" id="guard">
  <div class="kicker">04 · Three levels of kill switch</div>
  <h2>Who can turn the bot off, and how</h2>
  <p class="gist">The bot can be stopped surgically or wholesale — and the system always knows who outranks whom.</p>
  <div class="cols2">
    <div>
      <h3>The switches</h3>
      <ul>
        <li><b>The whole platform</b> — a global kill switch (super admin only): silences everything that writes to Instagram.</li>
        <li><b>A branch</b> — a bot toggle plus a separate sending toggle.</li>
        <li><b>One client</b> — a switch in the chat panel (manager takeover).</li>
      </ul>
    </div>
    <div>
      <h3>Humans outrank the bot</h3>
      <ul>
        <li>A manager replied by hand — the bot does not write over them.</li>
        <li>A client in the "manager/ready/handed off" stages — new messages do not wake the bot; only a human moves the funnel.</li>
        <li>A manager's note on a client overrides the model's judgement every turn.</li>
        <li>If the bot is off and the client writes — the manager gets a "client is waiting" alert.</li>
      </ul>
    </div>
  </div>
  <div class="inv"><b>Invariant:</b> in the "silent" stages (ready · handed off · dormant) the bot is unconditionally quiet; in the "human-led" stages (ready · handed off · manager) an inbound message does not switch the bot back on. Dormant is the exception: it wakes into discovery.</div>
</section>

<!-- KNOWLEDGE -->
<section class="card reveal" id="kb">
  <div class="kicker">05 · Knowledge</div>
  <h2>The knowledge base: the single source of truth</h2>
  <p class="gist">Everything Stepan says about the school lives in the branch's knowledge base: <b>documents</b> (the persona, plus the policy and market facts) and <b>course cards</b> — the only place prices come from. The base is facts-only, and the whole of it enters every prompt — no search, no "backup" paths.</p>
  <details>
    <summary>How the knowledge reaches the prompt (facts-only, whole KB)</summary>
    <div class="body">
      <p>There is no retrieval step. The knowledge base is facts-only and small enough to fit in one context window, so every reply is given the whole thing: the persona, the policy/market facts, the full card of the product in focus, and a one-line facts summary of every other product. No embeddings, no index, no reindex watcher — an edit in the KB editor is live on the next reply.</p>
      <details class="l3"><summary>All the way down</summary><div class="body">
        <ul>
          <li><code>app/modules/knowledge/service.py</code> assembles the context deterministically; the sales tactics that used to live in KB "playbook" docs now live in the reply prompt, so the KB carries only facts.</li>
          <li>A hard character budget caps the assembled context defensively (cheap JSON-mode providers stop returning valid JSON past ~30k chars); in practice the facts-only KB sits well under it.</li>
          <li>The persona never enters the search index — it is already in every prompt.</li>
        </ul>
      </div></details>
    </div>
  </details>
  <details>
    <summary>One base shared across branches, and drift protection for facts</summary>
    <div class="body">
      <ul>
        <li><b>Link:</b> a branch can read another branch's base live (one source of truth; its own base becomes read-only in this mode; one hop only — a source cannot itself be linked). That is how the test branch exercises the real production base.</li>
        <li><b>Copy:</b> a one-off clone; after that the bases live independently.</li>
        <li>Chats, clients, the funnel and settings are always the branch's own; only the knowledge base is shared.</li>
        <li>Some facts are deliberately duplicated across documents (so search finds them from any angle). The danger is editing one copy and forgetting the rest (a real case: Stepan's origin story was updated in 4 places out of 6). The control is an audit script, <code>scripts/kb_fact_audit.py</code>.</li>
      </ul>
      <details class="l3"><summary>All the way down: edits and the AI editor</summary><div class="body">
        <p>Every edit of a document or card is journaled (who, what, old → new text) with one-click restore of any version (<code>history.py</code>). There is an AI editor, "Coach": a manager writes a wish ("add instalments to the FAQ"), the model proposes a precise diff, the manager applies or rejects it (<code>coach_service.py</code>, uses the "thinking" model). There are also branch-wide directives to the bot — mandatory rules in every prompt.</p>
      </div></details>
    </div>
  </details>
</section>

<!-- LEADS -->
<section class="card reveal" id="leads">
  <div class="kicker">06 · Clients</div>
  <h2>A lead: one person, many messengers</h2>
  <p class="gist">The client card (lead) lives in the branch, not in the messenger. One lead can have several chats — Instagram and WhatsApp — all glued into one history by phone number. The card accumulates everything: name, phone, stage, temperature, audience, pains and goals, Instagram follower count, the manager's note.</p>
  <details>
    <summary>Follow-ups: how the bot chases those who went quiet</summary>
    <div class="body">
      <p>If a client goes quiet, the bot sends reminders on a schedule. The channel is chosen smartly: 24-hour window open — write there; closed — WhatsApp first, then Instagram (private APIs can write after the window). Any new client message resets the cycle and cancels the scheduled reminder. Schedule exhausted with no reply — the client goes dormant.</p>
      <details class="l3"><summary>All the way down</summary><div class="body">
        <p><code>app/modules/conversation/followup.py</code>; the channel router is <code>app/modules/leads/router.py</code>. Planning runs every 10 minutes; schedule and enablement are per channel. Quiet hours do not cancel a reminder — they postpone its sending until morning. Reminder text is generated with the lightweight contract and less knowledge.</p>
      </div></details>
    </div>
  </details>
  <details>
    <summary>Deleting without losses: a channel does not own the client</summary>
    <div class="body">
      <p>Deleting a channel cascades over its chats, messages, media and queue — but a client who still has a chat in another channel <b>survives</b> (losing only the deleted channel's chat). Only "orphans" are removed — those with no chats left anywhere. All in one transaction: a failure means a full rollback.</p>
      <details class="l3"><summary>All the way down</summary><div class="body">
        <p><code>app/modules/channels/service.py</code> (<code>purge</code>), the deletion order is foreign-key-safe; tests — <code>tests/test_channel_purge.py</code>. Separately: the stage-transition journal (<code>stage_event</code> — who moved it: bot/manager/system/CRM and why) and the chat's technical log (context cleared, course changed, notes).</p>
      </div></details>
    </div>
  </details>
</section>

<!-- CHANNELS -->
<section class="card reveal" id="channels">
  <div class="kicker">07 · Channels</div>
  <h2>Messengers: the official door and the private one</h2>
  <p class="gist">The central idea: <b>read</b> messages through the official Meta API (reliable), but <b>chase</b> the client after the 24-hour window closes through private APIs (Instagram instagrapi, WhatsApp Evolution), which officially do not allow it. That capability is paid for with a whole anti-ban arsenal.</p>
  <div class="tablewrap"><table>
    <tr><th>Channel</th><th>How it is wired</th><th>Capabilities</th></tr>
    <tr><td><b>Instagram</b></td><td>private API (instagrapi)</td><td>read (incl. message requests), write after the window, mark seen, unsend, media download, client profile</td></tr>
    <tr><td><b>WhatsApp</b></td><td>self-hosted Evolution API</td><td>read, write after the window</td></tr>
    <tr><td><b>Meta Business</b></td><td>official Graph API</td><td>canonical reading; replies only inside the 24-hour window</td></tr>
  </table></div>
  <details>
    <summary>Anti-ban: how not to lose the account</summary>
    <div class="body">
      <ul>
        <li>The same proxy and geo-locale for login and for work (a mismatch is a sure road to "suspicious activity").</li>
        <li>2–5-second pauses between private calls; a random delay before every polling cycle.</li>
        <li>Hourly and daily send caps per channel; human-like behaviour: "read" → pause → reply.</li>
        <li>Session secrets (Instagram cookies, tokens) are stored encrypted only (Fernet) and are never shown in the admin.</li>
      </ul>
      <details class="l3"><summary>All the way down: the most dangerous reading bug</summary><div class="body">
        <p>Message direction (ours/the client's) is decided by comparing the author with our own account ID. If that ID cannot be resolved, the polling cycle <b>fails outright</b> instead of guessing: once, 1,401 of our own messages were recorded as client inbound. Files: <code>app/adapters/channels/transports.py</code> (<code>_resolve_own_id</code>), client assembly with proxy/geo — <code>ig_client.py</code>.</p>
      </div></details>
    </div>
  </details>
</section>

<!-- THE CONVEYOR -->
<section class="card reveal" id="worker">
  <div class="kicker">08 · The conveyor</div>
  <h2>The background worker: 10 scheduled jobs</h2>
  <p class="gist">One shared worker runs all the routine. Every job is a "dispatcher" that hands out a separate task per branch: branches are processed in parallel and independently, one failure never touches the others.</p>
  <div class="tablewrap"><table>
    <tr><th>Job</th><th>How often</th><th>What it does</th></tr>
    <tr><td>Channel polling</td><td>2 min</td><td>collects new messages</td></tr>
    <tr><td>Reply dispatcher</td><td>1 min (at :45)</td><td>finds chats awaiting a reply</td></tr>
    <tr><td>Queue sending</td><td>20 sec</td><td>one message per chat</td></tr>
    <tr><td>Follow-ups</td><td>10 min</td><td>arms and enqueues reminders</td></tr>
    <tr><td>Deletions</td><td>1 min</td><td>unsending messages in Instagram</td></tr>
    <tr><td>CRM sync</td><td>5 min</td><td>events to the CRM + state reads</td></tr>
    <tr><td>Client profiles</td><td>30 min</td><td>followers and avatars of the active funnel</td></tr>
    <tr><td>Media backfill</td><td>3 min</td><td>downloading and recognising voice notes/images</td></tr>
    <tr><td>Needs cloud</td><td>daily</td><td>overnight analytics (Jakarta midnight)</td></tr>
    <tr><td>Log pruning</td><td>daily</td><td>broker call log older than 30 days</td></tr>
  </table></div>
  <details>
    <summary>Why nothing doubles up and nothing gets lost</summary>
    <div class="body">
      <ul>
        <li>Each branch task gets a stable ID: while the previous one is in flight, a new one is not enqueued.</li>
        <li>A per-chat lock (database advisory lock): two overlapping ticks will not call the model or send a message twice.</li>
        <li>A transaction per chat, not per whole list — a timeout mid-list does not roll back finished work (a real old bug).</li>
        <li>Schedule seconds are tuned so that intake → reply → send happens within one minute.</li>
        <li>A cap on concurrent "slow" generations — so a burst of replies cannot starve intake and sending.</li>
      </ul>
      <details class="l3"><summary>All the way down</summary><div class="body">
        <p><code>app/worker/main.py</code> (jobs and cron), <code>app/worker/wiring.py</code> (adapter assembly, queries, locks, session freezing). The engine is ARQ on top of Redis.</p>
      </div></details>
    </div>
  </details>
</section>

<!-- ACCESS -->
<section class="card reveal" id="access">
  <div class="kicker">09 · People and access</div>
  <h2>Who sees what: three roles and hard isolation</h2>
  <p class="gist">Login is via Telegram (the widget; the signature is verified cryptographically; the session is a signed 30-day cookie). Roles: <b>super admin</b> (the whole platform), <b>branch admin</b> (reads and writes in their branch), <b>viewer</b> (read-only). One person can be an admin in one branch and a viewer in another.</p>
  <details>
    <summary>Branch isolation: five layers</summary>
    <div class="body">
      <ol style="padding-left:22px">
        <li><b>Data:</b> nearly every table carries the branch id; filtering is centralised in a single wrapper class (<code>BranchScoped</code>) — modules never write filters by hand.</li>
        <li><b>Session:</b> the lists of readable and writable branches are baked into the signed cookie.</li>
        <li><b>View filter:</b> the branches picked in the UI are always intersected server-side with the permitted ones — forging the cookie does not widen access.</li>
        <li><b>Per-row checks</b> in every chat/knowledge/channel route — protection against substituting someone else's ID in the URL (IDOR).</li>
        <li><b>Two write-protection layers:</b> a coarse gate (any mutating request from a viewer → 403; none of the ~30 write routes can be forgotten) plus a precise branch check inside the route. Everything fail-closed: an old cookie without the new rights field means read-only until re-login.</li>
      </ol>
      <details class="l3"><summary>All the way down: the two admin panels</summary><div class="body">
        <p>The working admin is <code>/ui/**</code>: inbox, chat panel (five SQL queries per render, lazy translations, no LLM calls on open), knowledge, reports, settings, members, branches. The second is raw SQLAdmin at <code>/admin/**</code>: direct table access, <b>super admin only, always</b> — even with authentication off (otherwise one could raise their own role). The table with encrypted secrets is deliberately not exposed there. Files: <code>app/api/_auth.py</code> (4 middlewares), <code>app/admin/_branch.py</code> (guards), <code>app/modules/auth/rbac.py</code> (the rights table — the single source of truth, deny by default).</p>
      </div></details>
    </div>
  </details>
</section>

<!-- MONEY -->
<section class="card reveal" id="money">
  <div class="kicker">10 · Money</div>
  <h2>The budget: every cent accounted for</h2>
  <p class="gist">Every model call returns its exact price from the broker. The price lands in the branch's daily ledger. If a daily limit is set and exhausted — the branch's bot simply stays silent until the end of the day (branch-local time). Branch budgets never affect each other.</p>
  <details>
    <summary>All the way down: how this survives load</summary>
    <div class="body">
      <ul>
        <li>The write is atomic — one "insert or add" SQL statement with no races (<code>app/modules/budget/service.py</code>). The code records a real incident: an ambiguous column reference crashed the write on PostgreSQL and an already-paid reply was silently dropped (SQLite in tests swallowed it).</li>
        <li>The limit check happens <b>before</b> the model call; the charge — after a successful reply. Sandbox simulations are billed too — they run on the dedicated sandbox branch and charge its own ledger, so nothing escapes the accounting.</li>
        <li>Every broker call is a row in the <code>broker_log</code> journal: scenario (reply/followup/discovery/translation), provider, model, tokens, price, latency, success. Kept 30 days, viewed in the admin with a histogram. A journal-write failure never breaks the client's reply.</li>
      </ul>
    </div>
  </details>
</section>

<!-- CRM & ADS -->
<section class="card reveal" id="crm">
  <div class="kicker">11 · The outside world</div>
  <h2>CRM, ads and needs analytics</h2>
  <p class="gist">Stepan is the conversation; the school's CRM is the calls, contracts and money. They do not duplicate each other — they are <b>stitched together by phone number</b>. The iron rule: a CRM failure never silences the bot (fail-open).</p>
  <details>
    <summary>The two CRM links</summary>
    <div class="body">
      <ul>
        <li><b>Read ("don't get in the human's way"):</b> before sending, the bot asks the CRM for the client's state. If a manager has taken the client, a call is scheduled or a contract is signed — the verdict is "hold": the message does not go out and the client moves to the "manager" stage. State is cached for 5 minutes; a periodic sync warms the cache ahead of time.</li>
        <li><b>Write:</b> "client ready / manager needed" events go to the CRM via webhook; a failed delivery retries on the next tick.</li>
      </ul>
      <details class="l3"><summary>All the way down</summary><div class="body">
        <p><code>app/modules/crm/gate.py</code>, <code>pull.py</code>, <code>service.py</code>. All flags are off by default — the code sleeps in production until an operator enables it. There is SSRF protection: the webhook must be https and must target public addresses only (so data cannot be exfiltrated to a cloud-internal address). Managers' manual messages bypass the gate. The long-range plan — writing stages back to the CRM and purchase signals to Meta CAPI — is designed, not enabled.</p>
      </div></details>
    </div>
  </details>
  <details>
    <summary>Ads: which creative brings the clients</summary>
    <div class="body">
      <p>The ad ID a client came from is extracted from the Instagram conversation. Which course an ad maps to is set by the operator in a mapping table (automation only suggests from history and never writes on its own — that is a self-reinforcing signal). The chat of a client arriving from an ad is <b>tagged</b> with the advertised course — for attribution and as context for the bot. The conversation itself still opens with discovery: a deterministic first-turn rule forbids pitching the product, its price or schedule until the client's real need surfaces (added after a real incident where an ad click got the full pitch on turn one).</p>
      <details class="l3"><summary>All the way down: the reports</summary><div class="body">
        <p>The reports page: a funnel per ad (clicks lead to a filtered inbox); segments by temperature × audience with a success rate; a stage-flow diagram (a Sankey over the transition journal, rollbacks visible); the product source in a chat — ad/model/manager, and the model never overrides a manager's manual choice. Files: <code>app/modules/ads/mapping.py</code>, <code>app/api/_ui_panels.py</code>.</p>
        <p><b>Real spend, ad by ad.</b> The funnel now sits next to what Meta actually charged. The join is the subtle part: the ad id the private Instagram API hands us is <i>not</i> the Marketing API's ad id — it lives in a different id space and Graph answers "does not exist" for it. The media pk is the bridge: a media's shortcode IS its pk written in Instagram's base64, and Marketing API exposes that shortcode on the creative's permalink. Coverage is measured, not assumed: 93.6% of lead-bearing media resolve all the way to an ad, and the panel prints that percentage on its own face. Getting there took finding out WHY it was stuck at 45%: one ad runs in feed, stories and reels, and Meta renders a separate Instagram post per placement — adjacent shortcodes minted the same second — while the API admits to only one of them. The version a lead saw is usually another. What every variant shares is the source image it was rendered from, so its hash is the thread that ties an orphaned post back to the ad that ran it. It reports cost per <i>our</i> lead and per hand-off — not Meta's headline "cost per conversation", which prices a tap. Meta's own conversation-depth counts (reached message 3, message 5) sit alongside our stages as an independent second opinion, and blocks are surfaced as a spam signal. Files: <code>app/modules/ads/bridge.py</code>, <code>app/adapters/meta_ads.py</code>.</p>
        <p><b>One tree, not two tables.</b> Spend and funnel are grouped by campaign — the unit the money is actually budgeted in — so "what did this campaign cost and what did it bring" is one glance. Ads that could not be matched to a campaign are not dropped; they keep their own group with their funnel and no spend, because dropping them would quietly shrink the lead base and make the spend view look more complete than it is.</p>
        <p><b>Why background sync is not stale data.</b> The two datasets have opposite refresh needs. The media→ad map is <i>immutable</i> (a creative's permalink never changes), so it is never re-synced — only extended, and only when a lead arrives whose media is unmapped; a steady state costs zero Graph calls. Spend is a rolling 14-day cache at day granularity, so any date range on the page is a local SUM rather than an API call, and Meta itself revises attribution for ~7 days — chasing seconds would buy nothing. An ad account throttles account-wide after a burst of paging (hit live during development), so a throttled pull commits nothing rather than silently under-reporting spend. Coverage % and a sync timestamp are printed on the panel: a spend table that hides its own gaps reads as complete and gets trusted as such.</p>
        <p><b>Two honest corrections to the numbers.</b> The date filter is a COHORT filter: it selects clients whose conversation <i>started</i> in the window. That is the right lens for "how are this week's leads doing", but it silently answers a different question than "how much did we sell this week" — over three days the panel read 2 closed while 11 really closed, because 9 of them had first written earlier. "Closed in period", dated by the transition itself, now sits next to the cohort's "Won"; both are true, and the two together stop a good week from reading as a bad one. Second, the discovery KPI used to count clients who passed through the <i>qualifying stage</i> before a pitch — but every client crosses that stage, so it measured the plumbing and always looked healthy (87%). It now counts a real captured pain on the client's profile (65% on the same data): a smaller number that can actually go up.</p>
      </div></details>
    </div>
  </details>
  <details>
    <summary>The needs cloud: what actually worries the clients</summary>
    <div class="body">
      <p>Once a day the AI sorts the pains/goals/gains the bot has collected across all clients into stable categories ("can't find a job", "want to change careers"…). A widget on the reports page shows three frequency columns over any period — a manager sees the market picture without reading every chat.</p>
      <details class="l3"><summary>All the way down</summary><div class="body">
        <p><code>app/modules/needs_cloud/service.py</code>. The trick is category stability: the model must reuse existing ones and may add a new one only when nothing fits; only clients whose profile changed are processed (hash comparison). Categories are canonical; UI translations are cached. There is an alphabet-drift filter: the provider occasionally returned Arabic script — such labels are discarded. Daily frequency snapshots accumulate for history.</p>
      </div></details>
    </div>
  </details>
</section>

<!-- PLUMBING -->
<section class="card reveal" id="extras">
  <div class="kicker">12 · Plumbing</div>
  <h2>Notifications, voice notes, the external remote</h2>
  <details open>
    <summary>Manager notifications — in Telegram</summary>
    <div class="body">
      <p>Each branch has a Telegram group with topics (a forum): every client gets their own topic. Alert types: 🔥 ready to buy · 📆 signed up for an open house · ❓ a human is needed · 🔇 the bot is off but the client writes · "the channel needs a re-login". The alert body: a chat summary in the branch language + the same summary in the operators' language + a deep link straight into the chat panel. A delivery failure never blocks the handoff — the database record is already written.</p>
      <details class="l3"><summary>All the way down</summary><div class="body"><p><code>app/modules/notifications/</code>, <code>app/adapters/notify/telegram.py</code>. A deleted topic is recreated automatically; summaries are one model call, degrading to empty summaries on failure rather than refusing.</p></div></details>
    </div>
  </details>
  <details>
    <summary>Voice notes and images: the bot replies to the content</summary>
    <div class="body">
      <p>A voice note or photo arrives — a placeholder (🎤 / 🖼) is written to the chat at once, and the bot <b>waits</b> instead of replying to the placeholder. A background job downloads the file, transcribes the voice, describes the image in words (a screenshot, a payment receipt, a photo). Only then does the bot answer — the actual content. If recognition fails for 6 hours, the placeholder becomes "couldn't listen to this" and the bot politely asks for text.</p>
      <p><b>When there is nothing to recognise.</b> Some things can never be read: a shared reel or post Instagram refuses to hand over ("this content may have been deleted by its owner or hidden by their privacy settings"), or a bare share that carries only an account handle and no caption. The bot used to read that placeholder as if it were the client's words and either stalled or invented a topic from the account name. Now the code spots unreadable content anywhere in the client's current turn — the placeholder is often not their last message — and the bot says plainly that it doesn't open on its side, then asks the person to describe it in their own words. It never guesses.</p>
      <details class="l3"><summary>All the way down</summary><div class="body"><p><code>app/modules/media/service.py</code>; a 60 MB download cap; a transient error retries every 3 minutes, a permanent one clears the flag forever. The operator-panel translation is recomputed after recognition.</p></div></details>
    </div>
  </details>
  <details>
    <summary>MCP: an external remote control for the funnel</summary>
    <div class="body">
      <p>External systems (and Claude) can manage a client by phone number: find them, move them along the funnel, close the deal, mark "couldn't reach by phone" (then Stepan himself writes to the client: "we tried to call — let's continue here"). A separate <b>read-only</b> access exists for a reviewer: view and analyse chats with physically no way to change anything.</p>
      <details class="l3"><summary>All the way down</summary><div class="body">
        <p>Three surfaces: a local process for Claude Desktop (<code>mcp_server/stepan_mcp.py</code>), the web connector <code>/connector/mcp</code>, the reader <code>/reader/mcp</code>. Tokens are stored as hashes only (shown once), scoped to one branch or all; the access rule is a single fail-closed function: no authorisation context means deny, not "access everything". A branch token never acts on another branch's client, even if the phone resolves cross-branch. There is a sandbox, <code>sim_say</code>: a turn through the real engine (search + guard) with no Instagram — billed to the sandbox branch's own ledger and logged like every other call — the 17-scenario regression suite runs on it (<code>docs/dialogue-qa-checklist.md</code>).</p>
      </div></details>
    </div>
  </details>
</section>

<!-- QUALITY -->
<section class="card reveal" id="quality">
  <div class="kicker">13 · Quality and delivery</div>
  <h2>Tests, CI/CD and the road to production</h2>
  <p class="gist">~1,070 automated tests in 98 files cover every subsystem: branch isolation, client merging, the worker, the guard, the knowledge base, the budget, MCP. Plus a separate "live" dialogue regression suite — scenarios where the bot once broke and was fixed.</p>
  <details>
    <summary>How code reaches production</summary>
    <div class="body">
      <ol style="padding-left:22px">
        <li>Every push: linter + the full test suite (GitHub Actions).</li>
        <li>Push to main: tests → code sync to the server → container build → <b>database migrations run from the new image while the old one still serves</b> (no "new code against old schema" window) → web-container swap → health check (10 attempts).</li>
        <li>The worker restarts on deploy only if it was already running — turning sending on stays a manual decision (the Stepan-1 cutover).</li>
        <li>Rollback: revert the commit and redeploy.</li>
      </ol>
      <details class="l3"><summary>All the way down</summary><div class="body">
        <p>Tests run on in-memory SQLite (fast; order is randomised), so all SQL is written to work on both engines. The database and Redis are not exposed (docker network only); the web listens on a local port behind nginx + Cloudflare. The app runs as a non-root container user — a deliberate choice: the worker parses untrusted data from Instagram. Secrets live only in <code>.env</code> on the server. Files: <code>.github/workflows/ci.yml</code>, <code>deploy.yml</code>, <code>infra/docker-compose.yml</code>, <code>Dockerfile</code>, <code>tests/conftest.py</code>.</p>
      </div></details>
    </div>
  </details>
</section>

<!-- CRIB SHEET -->
<section class="card reveal" id="review">
  <div class="kicker">14 · For the review</div>
  <h2>Crib sheet: the strong, the debatable and the likely questions</h2>
  <details open>
    <summary>What to be proud of</summary>
    <div class="body">
      <ul>
        <li>Branch isolation is layered and centralised, with fail-closed behaviour in every contested spot.</li>
        <li>Model fabrications are genuinely intercepted before sending — not merely "forbidden by the prompt".</li>
        <li>Every dollar spent on models is counted and capped per branch.</li>
        <li>Idempotency everywhere: duplicate messages, double ticks, races — closed with locks and unique keys.</li>
        <li>The code documents real incidents right in the comments: you can see <i>why</i> each decision is what it is.</li>
        <li>Conscious trade-offs: the CRM is fail-open (its failure never silences the bot), alerts are best-effort (a missed ping loses no data).</li>
      </ul>
    </div>
  </details>
  <details open>
    <summary>Honest weak spots (better to name them yourself)</summary>
    <div class="body">
      <ul>
        <li>Everything internal sits behind authentication: only the landing, the changelog and the privacy page are public, and every other request is checked by middleware on each hit. The honest caveat: enforcement hangs on a deploy-time flag (kept off only for the very first boot, before the Telegram login bot exists, with a loud warning in the logs) — a misconfigured deployment would run open. In production it is on; the raw admin is protected always, flag or no flag.</li>
        <li>A viewer sees the write buttons — the server returns 403, but the UI does not hide them (the security boundary is server-side; hiding is cosmetics, not done yet).</li>
        <li>pgvector is installed but unused: fingerprints are JSON, similarity is computed in Python (SQLite test compatibility; fine at current volumes, a growth point at scale).</li>
        <li>The CRM integration is half plan: reading and event push are built (behind flags), stage write-back and Meta signals are designed, not implemented.</li>
      </ul>
    </div>
  </details>
  <details>
    <summary>Likely questions — short answers</summary>
    <div class="body">
      <dl class="qa">
        <dt>"Why polling instead of webhooks?"</dt>
        <dd>Private APIs (instagrapi, Evolution) offer no webhooks, and they are exactly what allows writing after the 24-hour window. Official Meta webhooks are wired as a channel, but the overall pipeline is built on polling.</dd>
        <dt>"What stops two branches' bots from mixing data?"</dt>
        <dd>Branch filtering is baked into the single data-access class; the UI additionally intersects the selection with server-side permissions and checks every row. Plus the isolation tests.</dd>
        <dt>"What if the model invents a price?"</dt>
        <dd>A deterministic check compares the price against the knowledge base; a mismatch means a regeneration on the expensive model; still bad — the safe phrase and a manager handoff. The client never sees the fabrication.</dd>
        <dt>"What happens when the broker / CRM / Telegram goes down?"</dt>
        <dd>Broker: up to 5 transient polling errors are tolerated; the chat re-answers on the next tick. CRM: fail-open, the bot continues. Telegram: the alert is lost, the database record is not.</dd>
        <dt>"How does it scale to a new branch?"</dt>
        <dd>Create the branch in the UI (the canonical knowledge skeleton is generated), connect the channels, pick a persona, optionally link the knowledge base to an existing one. No code required.</dd>
        <dt>"Why is the worker off in docker-compose?"</dt>
        <dd>Deliberate: the Stepan-1 and Stepan-2 workers must not write into the same Instagram account at once (ban). Enabling it is the manual cutover step.</dd>
      </dl>
    </div>
  </details>
</section>

<!-- GLOSSARY -->
<section class="card reveal" id="glossary">
  <div class="kicker">15 · Glossary</div>
  <h2>The terms in 30 seconds</h2>
  <div class="tablewrap"><table>
    <tr><th>Term</th><th>In plain words</th></tr>
    <tr><td><b>Branch</b></td><td>an isolated tenant of the platform: its own base, courses, bot, people, channels</td></tr>
    <tr><td><b>Lead</b></td><td>a client card inside a branch; one person = one lead, even across messengers</td></tr>
    <tr><td><b>Thread</b></td><td>one chat of a lead in one channel (a lead may have several)</td></tr>
    <tr><td><b>Funnel</b></td><td>the client's path: new → discovery → presenting → objections → ready → handed off</td></tr>
    <tr><td><b>Broker (AIbroker)</b></td><td>the external gateway to all models; returns the price of every call</td></tr>
    <tr><td><b>Knowledge base</b></td><td>facts-only: the persona, the policy/market facts and the course cards — loaded whole into every prompt, no search step</td></tr>
    <tr><td><b>Reply-guard</b></td><td>the safety layer: a fabrication check on the draft reply before sending</td></tr>
    <tr><td><b>Critic-gate</b></td><td>the last check: a strong model judges every reply against a positive rubric (grounded, responsive, sells) and fails closed to a human</td></tr>
    <tr><td><b>Outbox</b></td><td>the outgoing queue — the single door to the outside, where all limits apply</td></tr>
    <tr><td><b>Follow-up</b></td><td>a scheduled reminder to a client who went quiet</td></tr>
    <tr><td><b>24-hour window</b></td><td>the period after a client's message in which Meta officially allows a reply</td></tr>
    <tr><td><b>MCP</b></td><td>the protocol through which an external system (or Claude) drives the funnel</td></tr>
    <tr><td><b>Cutover</b></td><td>the final switch from Stepan-1 to Stepan-2 (turning the worker on)</td></tr>
  </table></div>
</section>

<footer>
  An internal team page. Compiled from the repository's code and documentation; the "all the way down" levels cite file paths so every claim can be verified.
</footer>
</main>
"""



_TITLE = "How Stepan Works — project map"


def hiw_html(lang: str = "en") -> str:
    """The complete /hiw page (English only — the Ukrainian twin was removed 2026-07-25:
    not a product language, and it doubled the maintenance of a 1.4k-line page)."""
    del lang  # accepted for URL compatibility (?lang=uk still resolves here)
    body = _BODY_EN.replace("__LANGSW__", "")
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex,nofollow">\n'
        # inline favicon: avoids the browser's automatic /favicon.ico 404
        '<link rel="icon" href="data:image/svg+xml,'
        "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E"
        "%3Crect width='100' height='100' rx='20' fill='%23ff5c35'/%3E"
        "%3Ctext x='50' y='72' font-size='62' text-anchor='middle' fill='white' "
        "font-family='Inter,sans-serif' font-weight='600'%3ES%3C/text%3E%3C/svg%3E\">\n"
        # same families as the landing / whats-new, so this page reads as part of the site
        '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600'
        '&family=Space+Grotesk:wght@500;600;700&display=swap" rel="stylesheet">\n'
        f"<title>{_TITLE}</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>"
        f"{body}"
        f"<script>{_JS}</script>\n</body>\n</html>\n"
    )
