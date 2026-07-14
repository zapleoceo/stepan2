"""Internal "How it works" page — an interactive, top-down map of the whole system.

Served at /hiw for the team (technical reviews, onboarding new members). NOT in the
public allowlist (app/api/_auth.py), so with auth enabled it requires a session like
the rest of the app. Self-contained HTML (own <!doctype> + inline CSS/JS, no CDN).
Content drills down in three levels per topic: plain-language gist → mechanics →
file paths and production incidents.
"""
# ruff: noqa: E501 — inline CSS/HTML string; long lines are inherent, not code smell
from __future__ import annotations

_HTML = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Как устроен Степан — карта проекта</title>
<style>
:root{
  --bg:#F6F8F5; --surface:#FFFFFF; --ink:#212930; --muted:#5D6B70;
  --accent:#0E7A6D; --accent-ink:#0A5D53; --accent-soft:rgba(14,122,109,.09);
  --warn:#9A5B10; --warn-soft:rgba(180,83,9,.10);
  --line:#E0E6E1; --code-bg:#EDF1EC;
  --shadow:0 1px 2px rgba(25,40,35,.06),0 6px 20px rgba(25,40,35,.05);
  --hero-ink:#EAF2EE; --hero-muted:#9AB0AA;
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#121A1C; --surface:#192326; --ink:#E4EBE7; --muted:#93A3A2;
    --accent:#3BCDB8; --accent-ink:#66DECC; --accent-soft:rgba(59,205,184,.12);
    --warn:#E0A65E; --warn-soft:rgba(224,166,94,.12);
    --line:#28353A; --code-bg:#202C30;
    --shadow:0 1px 2px rgba(0,0,0,.3),0 8px 24px rgba(0,0,0,.25);
  }
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
@media (prefers-reduced-motion: reduce){ html{scroll-behavior:auto} *{transition:none!important;animation:none!important} }
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.62 -apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:920px;margin:0 auto;padding:0 20px 96px}
h1,h2,h3{font-family:Georgia,"Times New Roman",serif;line-height:1.25;text-wrap:balance}
h1{font-size:clamp(30px,5vw,46px);margin:.2em 0 .3em;font-weight:700}
h2{font-size:26px;margin:0 0 6px}
h3{font-size:19px;margin:20px 0 8px}
p{margin:.5em 0}
a{color:var(--accent-ink)}
code{font:.86em ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--code-bg);padding:1px 6px;border-radius:5px;word-break:break-all}

/* hero — deep committed panel on both themes, keynote feel */
.hero{background:linear-gradient(160deg,#0D1517 0%,#10201F 55%,#0D1B23 100%);color:var(--hero-ink);margin:0 -20px;padding:64px 20px 46px;position:relative;overflow:hidden}
.hero::after{content:"";position:absolute;inset:auto -20% -60% -20%;height:80%;background:radial-gradient(ellipse at 50% 100%,rgba(59,205,184,.18),transparent 70%);pointer-events:none}
.hero-in{max-width:880px;margin:0 auto;position:relative}
.eyebrow{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:#5FD6C3;font-weight:700}
.hero .lede{font-size:19px;color:var(--hero-muted);max-width:64ch}
.hero a.back{color:#5FD6C3;font-size:13.5px;text-decoration:none;border:1px solid rgba(95,214,195,.35);border-radius:999px;padding:6px 14px;display:inline-block;margin-bottom:26px}
.hero a.back:hover{background:rgba(95,214,195,.12)}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin:24px 0 4px}
.stat{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.12);border-radius:10px;padding:10px 14px;backdrop-filter:blur(2px)}
.stat b{display:block;font-size:20px;font-variant-numeric:tabular-nums;color:#5FD6C3}
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
</style>
</head>
<body>
<div class="hero">
  <div class="hero-in">
    <a class="back" href="/ui/inbox">← в админку</a>
    <div class="eyebrow">Карта проекта · от общего к частному</div>
    <h1>Как устроен Степан</h1>
    <p class="lede">Платформа, где ИИ сам переписывается с клиентами в Instagram и WhatsApp: выясняет, что человеку нужно, подбирает курс, отвечает на возражения и передаёт «горячего» клиента живому менеджеру. Каждый филиал — изолированный «жилец» со своей базой знаний, ботом и людьми.</p>
    <div class="stats">
      <div class="stat"><b>146</b><span>файлов кода (Python)</span></div>
      <div class="stat"><b>~1 034</b><span>автотеста в 97 файлах</span></div>
      <div class="stat"><b>11</b><span>фоновых задач по расписанию</span></div>
      <div class="stat"><b>3</b><span>канала: Instagram · WhatsApp · Meta</span></div>
      <div class="stat"><b>0–5</b><span>фазы развития — все закрыты</span></div>
    </div>
    <p class="hint">Каждая тема раскрывается на три уровня: <b>суть</b> → <b>как это работает</b> → <b>до самого дна</b> (файлы, нюансы, реальные инциденты). Пользуйтесь поиском и кнопкой «Раскрыть всё».</p>
  </div>
</div>

<main>
<nav class="toc" aria-label="Оглавление">
  <a href="#idea">Замысел</a>
  <a href="#journey">Путь сообщения</a>
  <a href="#brain">Мозг</a>
  <a href="#guard">Стоп-краны</a>
  <a href="#kb">Знания</a>
  <a href="#leads">Лиды</a>
  <a href="#channels">Каналы</a>
  <a href="#worker">Конвейер</a>
  <a href="#access">Доступ</a>
  <a href="#money">Деньги</a>
  <a href="#crm">CRM и реклама</a>
  <a href="#extras">Обвязка</a>
  <a href="#quality">Качество</a>
  <a href="#review">Шпаргалка</a>
  <a href="#glossary">Словарик</a>
</nav>

<div class="controls">
  <input id="q" type="search" placeholder="Поиск по документу… (например: телефон, бюджет, guard)" aria-label="Поиск по документу">
  <button class="ctl" id="openAll" type="button">Раскрыть всё</button>
  <button class="ctl" id="closeAll" type="button">Свернуть всё</button>
</div>

<!-- ЗАМЫСЕЛ -->
<section class="card reveal" id="idea">
  <div class="kicker">01 · Замысел</div>
  <h2>Что это и зачем</h2>
  <p class="gist">Степан-2 — «отель для ботов-продавцов». Одна платформа обслуживает много <b>филиалов</b>: у каждого — своя база знаний, свои курсы и цены, свой характер бота, свой язык и свои сотрудники. Данные филиалов никогда не пересекаются — это главное правило безопасности всего проекта.</p>
  <details>
    <summary>Чем вторая версия отличается от первой</summary>
    <div class="body">
      <ul>
        <li><b>Степан-1</b> — бот для одного клиента: один аккаунт, одна база, всё зашито под Индонезию.</li>
        <li><b>Степан-2</b> — платформа: филиалы добавляются без переписывания кода. «Филиала по умолчанию» нет — каждый живёт под своим номером.</li>
        <li>Все обращения к нейросетям идут через единый внешний шлюз (<b>AIbroker</b>): в проекте нет ни одного ключа от провайдеров моделей, а каждый вызов возвращает точную цену в долларах — по ней ведётся бюджет каждого филиала.</li>
        <li>Мессенджеры спрятаны за общим «переходником» (адаптером): можно заменить способ работы с Instagram, не трогая мозг бота.</li>
        <li>Полная независимость от первой версии: отдельный репозиторий, отдельная база, отдельные контейнеры. Степан-1 продолжает работать, переключение — по готовности.</li>
      </ul>
      <details class="l3">
        <summary>До дна: бизнес-логика денег и статус</summary>
        <div class="body">
          <ul>
            <li>Экономия на моделях: дорогая нейросеть включается только в «денежные» моменты (цена, оплата, готовность), дешёвая — на всём остальном объёме. Границу помогает подбирать офлайн-скрипт <code>scripts/bakeoff_capability.py</code>: гоняет реальные диалоги через обе модели и сравнивает решения.</li>
            <li>Дальний план — замкнуть рекламную петлю: реклама → лид → договор в CRM → сигнал обратно в Meta, чтобы реклама оптимизировалась на покупателей.</li>
            <li>Статус: фазы 0–5 закрыты, платформа в проде. Отправляющий «конвейер» намеренно выключен до финального переключения со Степана-1 — два бота не должны писать в один Instagram-аккаунт одновременно (риск бана).</li>
            <li>Документация: <code>README.md</code>, <code>docs/multitenant-design.md</code> (ключевой документ), 19 тематических файлов в <code>docs/</code>.</li>
          </ul>
        </div>
      </details>
    </div>
  </details>
  <details>
    <summary>Из чего построено (технологии — одним абзацем)</summary>
    <div class="body">
      <p>Python 3.12. Веб-часть — FastAPI (страницы рисуются на сервере, оживают через HTMX — без тяжёлого фронтенда). База — PostgreSQL, очередь фоновых задач — Redis + ARQ. Схема базы меняется миграциями Alembic. Всё упаковано в 4 docker-контейнера: база, Redis, веб-приложение, воркер.</p>
      <details class="l3">
        <summary>До дна: архитектурный стиль</summary>
        <div class="body">
          <p>«Порты и адаптеры» (гексагональная архитектура): ядро (<code>app/domain/</code>, <code>app/modules/</code>) не знает про Instagram, нейросети и Telegram — оно разговаривает с абстрактными интерфейсами-«портами» (<code>app/ports/</code>: канал, LLM, уведомления). Конкретные реализации — «адаптеры» (<code>app/adapters/</code>). Плюс модульный монолит: каждый модуль (<code>auth</code>, <code>conversation</code>, <code>knowledge</code>, <code>leads</code>…) — своя зона ответственности. Любой кусок инфраструктуры можно заменить, не перепиливая мозг.</p>
        </div>
      </details>
    </div>
  </details>
</section>

<!-- ПУТЬ СООБЩЕНИЯ -->
<section class="card reveal" id="journey">
  <div class="kicker">02 · Главный сюжет</div>
  <h2>Путь одного сообщения</h2>
  <p class="gist">Всё, что делает платформа, — это шесть шагов между «клиент написал» и «клиент получил ответ». Нажмите на шаг, чтобы раскрыть его.</p>

  <div class="pipe" role="tablist" aria-label="Шаги пути сообщения">
    <button role="tab" aria-selected="true" data-panel="p1"><span class="n">ШАГ 1</span><span class="t">Клиент пишет</span><span class="s">Instagram / WhatsApp</span></button>
    <button role="tab" aria-selected="false" data-panel="p2"><span class="n">ШАГ 2</span><span class="t">Приём</span><span class="s">кто это и что нового</span></button>
    <button role="tab" aria-selected="false" data-panel="p3"><span class="n">ШАГ 3</span><span class="t">Решение</span><span class="s">ИИ думает над ответом</span></button>
    <button role="tab" aria-selected="false" data-panel="p4"><span class="n">ШАГ 4</span><span class="t">Проверка</span><span class="s">ловим выдумки</span></button>
    <button role="tab" aria-selected="false" data-panel="p5"><span class="n">ШАГ 5</span><span class="t">Очередь</span><span class="s">по-человечески</span></button>
    <button role="tab" aria-selected="false" data-panel="p6"><span class="n">ШАГ 6</span><span class="t">Отправка</span><span class="s">лимиты и вежливость</span></button>
  </div>

  <div class="pipe-panel" id="p1" role="tabpanel">
    <h3 style="margin-top:0">Клиент пишет — а платформа сама спрашивает «что нового?»</h3>
    <p>Мессенджеры не присылают уведомлений сами (у приватных API их просто нет). Поэтому каждые <b>2 минуты</b> платформа опрашивает все активные каналы всех филиалов: «появились новые сообщения?». Перед каждым опросом — случайная пауза, чтобы не стучаться в Instagram в одну и ту же секунду (защита от бана).</p>
    <details class="l3"><summary>До дна</summary><div class="body">
      <p>Задача <code>ingest_active_channels</code> в <code>app/worker/main.py</code> раскидывает по отдельному заданию на филиал; каждый канал опрашивается в своей транзакции — сбой одного не ломает остальные. Instagram читается через приватный API (instagrapi), причём разбирается сырой JSON — штатный разборщик библиотеки падает на пересланных постах (<code>app/adapters/channels/ig_parse.py</code>). Читаются и «запросы на переписку» (pending inbox) — там живут холодные лиды с рекламы. Если сессия канала «заболела» (Instagram требует подтверждение) — канал замораживается во всех циклах и менеджеру летит алерт «нужен повторный вход».</p>
    </div></details>
  </div>

  <div class="pipe-panel" id="p2" role="tabpanel" hidden>
    <h3 style="margin-top:0">Приём: понять, кто написал, и ничего не задвоить</h3>
    <p>Каждое новое сообщение проходит фильтры: не видели ли мы его раньше (защита от дублей), кто автор — клиент или наш же менеджер, ответивший вручную с телефона. Дальше главный фокус: <b>один человек в разных мессенджерах = один клиент</b>. Склейка — по номеру телефона внутри филиала. Свежее сообщение продлевает 24-часовое «окно ответа», отменяет запланированные напоминания и будит бота, если тот спал.</p>
    <details class="l3"><summary>До дна</summary><div class="body">
      <ul>
        <li><code>app/modules/leads/ingest.py</code> — единственная точка записи входящих; идемпотентность по паре (канал, внешний id сообщения).</li>
        <li>Телефон достаётся прямо из текста (<code>phone.py</code>) с учётом страны филиала: «0812…», «62812…» и «+62 812…» дают один ключ; цена «Rp 1.200.000» номером не считается.</li>
        <li>Защита от «захвата» чата: склейка по телефону работает только для совершенно нового диалога. Если человек напечатал чужой номер в существующем чате — его переписка не переедет к владельцу номера (<code>identity.py</code>).</li>
        <li>Ручной ответ менеджера из приложения Instagram тоже попадает в базу и сдвигает отметку «мы ответили» — бот никогда не пишет поверх человека.</li>
        <li>Спящий клиент от нового сообщения «просыпается» в стадию выявления потребности; но если клиента уже ведёт человек (стадии «готов», «передан», «менеджер») — бот не включается.</li>
      </ul>
    </div></details>
  </div>

  <div class="pipe-panel" id="p3" role="tabpanel" hidden>
    <h3 style="margin-top:0">Решение: ИИ читает всё и отвечает строго по форме</h3>
    <p>Раз в минуту платформа находит чаты, где клиент написал последним, и для каждого собирает «досье»: характер бота, нужные куски базы знаний, карточку обсуждаемого курса, историю переписки, накопленные потребности клиента и свод правил продаж. Нейросеть возвращает не просто текст, а <b>структурированное решение</b>: что ответить, на какой стадии воронки клиент, какие у него боли и цели, нужен ли живой менеджер.</p>
    <details class="l3"><summary>До дна</summary><div class="body">
      <ul>
        <li>Диспетчер <code>reply_pending</code> стартует на 45-й секунде минуты — сразу после цикла приёма, чтобы ответить в ту же минуту. Каждый чат — отдельное задание с блокировкой (двойной вызов нейросети для одного чата исключён — реальный случай двойного биллинга, закрыт advisory-lock'ом).</li>
        <li>Сердце — <code>ReplyService.decide</code> (<code>app/modules/conversation/reply.py</code>) и <code>DecisionEngine</code> (<code>engine.py</code>). Перед вызовом: проверка дневного бюджета филиала, ожидание расшифровки голосовых, определение языка ответа.</li>
        <li>Выбор модели (<code>routing.py</code>): дешёвая «fast» — на объём, дорогая «smart» — на денежные стадии и на клиентов, у которых страховка уже ловила выдумки; «deep» (думает до 8 минут) — только для внутреннего ИИ-редактора базы знаний.</li>
        <li>Если дешёвая модель вернула битый ответ — один повтор на дорогой. Если ответ слишком похож на предыдущий — регенерация с поправкой «не повторяйся».</li>
        <li>Все вызовы идут через шлюз AIbroker в асинхронном режиме «поставил задание — опрашивай готовность» (<code>app/adapters/llm/broker.py</code>): медленная модель не обрывает соединение по таймаутам прокси.</li>
      </ul>
    </div></details>
  </div>

  <div class="pipe-panel" id="p4" role="tabpanel" hidden>
    <h3 style="margin-top:0">Проверка: выдумка никогда не уходит клиенту</h3>
    <p>Между «ИИ придумал ответ» и «ответ ушёл» стоит страховочный слой (reply-guard). Сначала — мгновенные бесплатные проверки: ссылка, которой нет в базе знаний; фраза «уже отправил вам файл» (бот не умеет отправлять файлы); два вопроса в одном сообщении; предложение созвониться (бот только пишет). Потом — выборочная проверка второй нейросетью, только для «рискованных» ответов. Поймали проблему → одна перегенерация; не помогло → безопасная фраза «уточню у команды» и передача менеджеру.</p>
    <details class="l3"><summary>До дна</summary><div class="body">
      <ul>
        <li>Появился после реального случая: бот выдумал ссылку на «лабораторию», бесплатный доступ и сертификат Cisco. Файлы: <code>app/modules/conversation/guard.py</code>, оркестровка в <code>reply.py</code>, документ <code>docs/reply-guard.md</code>.</li>
        <li>Хитрая экономия: самый частый «риск» — цена. Если цена в ответе дословно совпадает с базой знаний, платная LLM-проверка пропускается (это сотни ответов в день).</li>
        <li>Отдельно ловятся «ложные эскалации»: модель хочет позвать менеджера на вопрос о цене, ответ на который уже есть в контексте — вместо этого перегенерация.</li>
        <li>Каждая перегенерация увеличивает счётчик у клиента — после двух такой клиент навсегда переводится на дорогую модель.</li>
        <li>Режимы на филиал: полный / только ссылки / выключен. Текст промпта-проверяльщика редактируется как обычный документ базы знаний.</li>
      </ul>
    </div></details>
  </div>

  <div class="pipe-panel" id="p5" role="tabpanel" hidden>
    <h3 style="margin-top:0">Очередь: единственная дверь наружу</h3>
    <p>Готовый ответ не отправляется напрямую — он кладётся в исходящую очередь (outbox). Это единственный путь любого сообщения наружу, поэтому все лимиты и правила применяются ровно один раз. Длинный ответ режется на «пузыри» (до трёх коротких сообщений), между ними — паузы, а сам ответ уходит с небольшой случайной задержкой — как будто печатает человек.</p>
    <details class="l3"><summary>До дна</summary><div class="body">
      <p>Таблица <code>outbox</code>; источники строк: ответ бота, ручное сообщение менеджера из панели, напоминание (follow-up). Разделитель пузырей — <code>|||</code> в ответе модели. Перед постановкой — контрольная проверка «а не ответил ли уже параллельный процесс» (<code>enqueue_reply</code> в <code>reply.py</code>). Вместе с постановкой применяется решение модели: сдвиг стадии воронки, алерт менеджеру, фиксация потребностей клиента (<code>needs.py</code> — слияние без дублей).</p>
    </div></details>
  </div>

  <div class="pipe-panel" id="p6" role="tabpanel" hidden>
    <h3 style="margin-top:0">Отправка: лимиты, тихие часы и вежливость к CRM</h3>
    <p>Каждые 20 секунд отправщик берёт по одному готовому сообщению на чат и проверяет цепочку правил: включена ли отправка вообще; не тихие ли сейчас часы (ночные напоминания ждут утра, живые ответы идут всегда); не исчерпан ли лимит сообщений в час/день (защита от бана); открыто ли 24-часовое окно Meta; и не занят ли клиентом живой менеджер в CRM — тогда бот вежливо молчит. Перед отправкой бот «читает» сообщение клиента и выдерживает паузу — совсем как человек.</p>
    <details class="l3"><summary>До дна</summary><div class="body">
      <ul>
        <li><code>OutboxSender.send_next</code> (<code>app/modules/conversation/outbox.py</code>); приоритет — настоящий ответ раньше напоминания, ручные сообщения менеджера идут вне всех лимитов.</li>
        <li>Мягкая блокировка Instagram (challenge / rate limit) → повтор с нарастающей паузой; постоянная ошибка → чат усыпляется. Закрытое окно Meta → строка помечается «пропущено», чат спит до нового входящего (иначе бот жёг бы деньги на перегенерацию каждый тик — реальный инцидент «Meta 400 loop»).</li>
        <li>После успешной отправки планируется следующее напоминание. Расписание напоминаний — по каждому каналу отдельно; выбор канала: открытое окно → WhatsApp → Instagram.</li>
        <li>Если клиент попросил не беспокоить («jangan ganggu», «diem») — напоминания отменяются навсегда. Если напоминание получилось повтором старого — «сжигается» целый шаг расписания, а не попытка (раньше это был крупнейший пожиратель токенов: ~1 300 генераций в день).</li>
      </ul>
    </div></details>
  </div>
</section>

<!-- МОЗГ -->
<section class="card reveal" id="brain">
  <div class="kicker">03 · Мозг</div>
  <h2>Как Степан продаёт: методология</h2>
  <p class="gist">Главное правило: <b>сначала выясни, потом предлагай</b>. Даже если человек с порога спросил цену, Степан сперва задаст один вопрос о ситуации — и только поняв боль, презентует курс. Это классические техники продаж (SPIN + Value Proposition Canvas), зашитые в неизменяемый «контракт» для нейросети.</p>
  <div class="stagechips" aria-label="Стадии воронки">
    <span class="chip">новый</span><span class="arrow">→</span>
    <span class="chip">прогрев</span><span class="arrow">→</span>
    <span class="chip hot">выявление</span><span class="arrow">→</span>
    <span class="chip">презентация</span><span class="arrow">→</span>
    <span class="chip">возражения</span><span class="arrow">→</span>
    <span class="chip hot">готов</span><span class="arrow">→</span>
    <span class="chip">передан менеджеру</span>
  </div>
  <div class="legend"><i>Плюс две особые стадии: <b>спит</b> (не отвечает, разбудит новое сообщение) и <b>менеджер</b> (взял человек — бот молчит).</i></div>

  <details>
    <summary>Правила, которые модель не может нарушить</summary>
    <div class="body">
      <ul>
        <li><b>Один вопрос за ход.</b> Два знака вопроса — страховка режет до первого.</li>
        <li><b>Факты только из базы знаний.</b> Цены, даты, кейсы выпускников — ничего «из головы».</li>
        <li><b>Телефон до передачи.</b> Нельзя отдать клиента менеджеру без номера телефона: если модель рвётся эскалировать, а телефона нет — бот сперва просит WhatsApp. (Появилось после клиента, уехавшего к менеджеру с пустым номером.)</li>
        <li><b>Презентация только после боли.</b> Двойная защита: правило в промпте + гейт в коде — если модель просит стадию «презентация», но боль клиента не зафиксирована, код откатывает её в «выявление». После 4 ходов расспросов гейт отпускает — чтобы не устраивать допрос.</li>
        <li><b>Готовность решает система.</b> Модель не может сама поставить стадию «готов» — только поднять флаг, решение принимает код.</li>
      </ul>
      <details class="l3"><summary>До дна: что именно возвращает модель</summary><div class="body">
        <p>Ответ — строгий JSON (контракт <code>_DECISION_CONTRACT</code> в <code>app/modules/conversation/prompt.py</code>): текст ответа, стадия и её причина, обсуждаемый курс, флаги «готов»/«нужен менеджер», телефон, язык ответа, две оси классификации клиента — температура (горячий/тёплый/холодный/без бюджета/нецелевой) и аудитория (взрослый/школьник — школьник не отказ: скидка 10% и оплата родителями), плюс работы/боли/выгоды клиента (копятся в профиле и подкладываются в каждый следующий промпт). Для напоминаний есть облегчённый контракт в треть размера.</p>
      </div></details>
    </div>
  </details>

  <details>
    <summary>Из чего собирается промпт (по порядку)</summary>
    <div class="body">
      <ol style="padding-left:22px">
        <li><b>Персона</b> — характер и голос Степана (всегда целиком, первым блоком).</li>
        <li><b>Карточка обсуждаемого курса</b> — но урезанная до ядра: суть, цена, расписание, формат, результат. Объёмная программа уезжает в поиск по знаниям.</li>
        <li><b>Обязательные документы</b> — правила оплаты и список запретов (каждый ход, слишком важны, чтобы доверять поиску).</li>
        <li><b>Каталог курсов</b> — список активных продуктов.</li>
        <li><b>Найденные знания</b> — куски базы, релевантные последним репликам (умный поиск, см. «Знания»).</li>
        <li><b>Сегодняшняя дата</b> по времени филиала — чтобы не предлагать прошедшие занятия.</li>
        <li><b>Заметки менеджера</b>: правила на весь филиал + личная пометка на клиента («проверено, ещё не готов»).</li>
        <li><b>Известные потребности клиента</b> — всё, что накопили прошлые ходы.</li>
        <li><b>Контракт продаж</b> + история переписки.</li>
      </ol>
      <details class="l3"><summary>До дна: ограничения и кеш</summary><div class="body">
        <p>Общий бюджет контекста ограничен (~30 тыс. символов): дальше дешёвые модели перестают возвращать валидный JSON. При переполнении выбрасываются наименее релевантные куски знаний — персона, карточка курса и каталог не трогаются никогда. Сборка контекста кешируется на один ход клиента, чтобы перегенерации (страховка, дедуп, эскалация) не пересчитывали дорогой поиск. Файлы: <code>prompt.py</code> (чистая функция, без обращений к базе), <code>app/modules/knowledge/service.py</code> (<code>knowledge_context</code>).</p>
      </div></details>
    </div>
  </details>

  <details>
    <summary>Характер: библиотека персон</summary>
    <div class="body">
      <p>Методология («что делать») одинакова для всех филиалов и зашита в код. А вот <b>как звучать</b> — настраивается: в библиотеке лежат версионированные персоны («Консультативный закрыватель», «Тёплый советник», «Быстрый»), каждая описана секциями: голос и тон, стиль расспросов, работа с возражениями, стиль закрытия, границы. Филиал выбирает персону и может дописать свои приписки к любой секции — они переживают смену персоны.</p>
      <details class="l3"><summary>До дна</summary><div class="body">
        <p><code>app/modules/persona/service.py</code>; таблицы <code>persona</code> (общеплатформенная, версии), <code>branch_persona</code> (выбор + приписки), <code>persona_favorite</code>. Персона попадает в промпт напрямую из документа <code>persona_core</code> базы знаний филиала и намеренно дублирует правило «никогда не выдумывай» — вторая линия обороны к страховке.</p>
      </div></details>
    </div>
  </details>
</section>

<!-- СТОП-КРАНЫ -->
<section class="card reveal" id="guard">
  <div class="kicker">04 · Три уровня «стоп-крана»</div>
  <h2>Кто и как может выключить бота</h2>
  <p class="gist">Бот можно остановить точечно или целиком — и система всегда знает, кто главнее.</p>
  <div class="cols2">
    <div>
      <h3>Выключатели</h3>
      <ul>
        <li><b>Вся платформа</b> — общий стоп-кран (только суперадмин): гасит всё, что пишет в Instagram.</li>
        <li><b>Филиал</b> — тумблер бота и отдельно тумблер отправки.</li>
        <li><b>Один клиент</b> — переключатель в панели чата (перехват менеджером).</li>
      </ul>
    </div>
    <div>
      <h3>Приоритет человека</h3>
      <ul>
        <li>Менеджер ответил вручную — бот не пишет поверх.</li>
        <li>Клиент в стадии «менеджер/готов/передан» — новые сообщения бота не будят; воронку двигает только человек.</li>
        <li>Пометка менеджера на клиенте перекрывает суждение модели каждый ход.</li>
        <li>Если бот выключен, а клиент написал — менеджеру летит алерт «клиент ждёт».</li>
      </ul>
    </div>
  </div>
  <div class="inv"><b>Инвариант:</b> «тихие» стадии (готов · передан · спит) — бот молчит безусловно; «человеческие» стадии (готов · передан · менеджер) — входящее сообщение не включает бота обратно. Спящий — исключение: он просыпается в «выявление».</div>
</section>

<!-- ЗНАНИЯ -->
<section class="card reveal" id="kb">
  <div class="kicker">05 · Знания</div>
  <h2>База знаний: единственный источник правды</h2>
  <p class="gist">Всё, что Степан говорит о школе, лежит в базе знаний филиала: <b>документы</b> (характер, сценарии продаж, справочник) и <b>карточки курсов</b> — единственное место, откуда берутся цены. В промпт знания попадают только через умный поиск — никаких «запасных» путей.</p>
  <details>
    <summary>Как работает умный поиск (RAG)</summary>
    <div class="body">
      <p>Каждый документ режется на куски по заголовкам (~1 400 символов), каждый кусок превращается в числовой «отпечаток смысла» (эмбеддинг через брокер). Когда клиент пишет, последние реплики диалога тоже получают отпечаток, и в промпт подкладываются самые близкие по смыслу куски. Сторож раз в 5 минут замечает изменённые документы и перестраивает индекс филиала.</p>
      <details class="l3"><summary>До дна</summary><div class="body">
        <ul>
          <li><code>app/modules/knowledge/rag.py</code>, <code>chunking.py</code>, <code>reindex.py</code>. Отпечатки хранятся JSON-массивами, близость считается в Python — чтобы одинаково работало и на боевом PostgreSQL, и на SQLite в тестах (расширение pgvector установлено, но не используется — честная тема для ревью).</li>
          <li>Индекс пересобирается целиком (удалить + вставить). Если часть кусков не получила отпечаток — «водяной знак» не двигается, чтобы не зафиксировать частичный индекс.</li>
          <li>Смена модели эмбеддингов требует полного переиндекса всех филиалов: <code>scripts/reembed_all_branches.py</code>.</li>
          <li>Персона в поисковый индекс не попадает — она и так всегда в промпте.</li>
        </ul>
      </div></details>
    </div>
  </details>
  <details>
    <summary>Общая база на несколько филиалов и защита от «расползания» фактов</summary>
    <div class="body">
      <ul>
        <li><b>Связать:</b> филиал может читать базу другого филиала вживую (один источник правды, своя база в этом режиме только для чтения, один «прыжок» — источник сам не может быть связан). Так тестовый филиал проверяет актуальную боевую базу.</li>
        <li><b>Скопировать:</b> разовый клон, дальше базы живут независимо.</li>
        <li>Чаты, клиенты, воронка и настройки — всегда свои; общая только база знаний.</li>
        <li>Некоторые факты намеренно продублированы в нескольких документах (чтобы поиск нашёл их из любого угла). Опасность — правка в одном месте и забытые копии (реальный случай: историю Степана обновили в 4 местах из 6). Контроль — аудит-скрипт <code>scripts/kb_fact_audit.py</code>.</li>
      </ul>
      <details class="l3"><summary>До дна: правки и ИИ-редактор</summary><div class="body">
        <p>Каждая правка документа или карточки журналируется (кто, что, старый → новый текст) с восстановлением любой версии в один клик (<code>history.py</code>). Есть ИИ-редактор «Коуч»: менеджер пишет пожелание («добавь рассрочку в FAQ»), нейросеть предлагает точечный дифф, менеджер применяет или отклоняет (<code>coach_service.py</code>, использует «думающую» модель). Также есть директивы боту на весь филиал — обязательные правила в каждый промпт.</p>
      </div></details>
    </div>
  </details>
</section>

<!-- ЛИДЫ -->
<section class="card reveal" id="leads">
  <div class="kicker">06 · Клиенты</div>
  <h2>Лид: один человек, много мессенджеров</h2>
  <p class="gist">Карточка клиента (лид) живёт в филиале, а не в мессенджере. У одного лида может быть несколько чатов — Instagram и WhatsApp — и все они склеены в одну историю по номеру телефона. В карточке копится всё: имя, телефон, стадия, температура, аудитория, боли и цели, счётчик подписчиков в Instagram, пометка менеджера.</p>
  <details>
    <summary>Напоминания (follow-up): как бот «догоняет» замолчавших</summary>
    <div class="body">
      <p>Если клиент замолчал, бот по расписанию отправляет напоминания. Канал выбирается умно: открыто 24-часовое окно — пишем там же; закрыто — через WhatsApp, потом Instagram (приватные API умеют писать после окна). Любое новое сообщение клиента сбрасывает цикл и отменяет запланированное напоминание. Исчерпал расписание без ответа — клиент засыпает.</p>
      <details class="l3"><summary>До дна</summary><div class="body">
        <p><code>app/modules/conversation/followup.py</code>, роутер каналов — <code>app/modules/leads/router.py</code>. Планирование каждые 10 минут; расписание и включённость — на каждый канал отдельно. Тихие часы не отменяют напоминание, а откладывают отправку до утра. Текст напоминания генерится по облегчённому контракту с меньшим объёмом знаний.</p>
      </div></details>
    </div>
  </details>
  <details>
    <summary>Удаление без потерь: канал не владеет клиентом</summary>
    <div class="body">
      <p>При удалении канала каскадом чистятся его чаты, сообщения, медиа и очередь — но клиент, у которого остался чат в другом канале, <b>выживает</b> (теряет только чат удалённого канала). Удаляются лишь «сироты» — те, у кого чатов больше нигде нет. Всё в одной транзакции: сбой — полный откат.</p>
      <details class="l3"><summary>До дна</summary><div class="body">
        <p><code>app/modules/channels/service.py</code> (<code>purge</code>), порядок удаления безопасен для внешних ключей; тесты — <code>tests/test_channel_purge.py</code>. Отдельно: журнал переходов стадий (<code>stage_event</code> — кто двинул: бот/менеджер/система/CRM и почему) и технический лог чата (очистка контекста, смена курса, пометки).</p>
      </div></details>
    </div>
  </details>
</section>

<!-- КАНАЛЫ -->
<section class="card reveal" id="channels">
  <div class="kicker">07 · Каналы</div>
  <h2>Мессенджеры: официальная дверь и приватная</h2>
  <p class="gist">Центральная идея: <b>читать</b> сообщения — через официальный API Meta (надёжно), а <b>догонять</b> клиента после закрытия 24-часового окна — через приватные API (Instagram instagrapi, WhatsApp Evolution), которые официально это не разрешают. Ради этой возможности — целый арсенал «анти-бан»-мер.</p>
  <div class="tablewrap"><table>
    <tr><th>Канал</th><th>Как подключён</th><th>Умеет</th></tr>
    <tr><td><b>Instagram</b></td><td>приватный API (instagrapi)</td><td>читать (вкл. запросы переписки), писать после окна, «прочитано», отзыв сообщений, скачивание медиа, профиль клиента</td></tr>
    <tr><td><b>WhatsApp</b></td><td>свой сервер Evolution API</td><td>читать, писать после окна</td></tr>
    <tr><td><b>Meta Business</b></td><td>официальный Graph API</td><td>каноничное чтение; ответ только внутри 24-часового окна</td></tr>
  </table></div>
  <details>
    <summary>Анти-бан: как не потерять аккаунт</summary>
    <div class="body">
      <ul>
        <li>Один и тот же прокси и гео-локаль для входа и для работы (несовпадение — верный путь к «подозрительной активности»).</li>
        <li>Паузы 2–5 секунд между приватными вызовами; случайная задержка перед каждым циклом опроса.</li>
        <li>Лимиты отправки в час и в день на каждый канал; человекоподобное поведение: «прочитать» → пауза → ответ.</li>
        <li>Секреты сессий (куки Instagram, токены) хранятся только в зашифрованном виде (Fernet) и никогда не показываются в админке.</li>
      </ul>
      <details class="l3"><summary>До дна: самый опасный баг чтения</summary><div class="body">
        <p>Направление сообщения (наше/клиента) определяется сравнением автора с собственным ID аккаунта. Если ID не удалось определить — цикл опроса <b>падает целиком</b>, а не продолжает вслепую: однажды 1 401 наше сообщение было помечено как входящие от клиентов. Файлы: <code>app/adapters/channels/transports.py</code> (<code>_resolve_own_id</code>), сборка клиента с прокси/гео — <code>ig_client.py</code>.</p>
      </div></details>
    </div>
  </details>
</section>

<!-- КОНВЕЙЕР -->
<section class="card reveal" id="worker">
  <div class="kicker">08 · Конвейер</div>
  <h2>Фоновый воркер: 11 задач по расписанию</h2>
  <p class="gist">Всю рутину крутит один общий воркер. Каждая задача — «диспетчер», который раздаёт по отдельному заданию на филиал: филиалы обрабатываются параллельно и независимо, сбой одного не трогает других.</p>
  <div class="tablewrap"><table>
    <tr><th>Задача</th><th>Как часто</th><th>Что делает</th></tr>
    <tr><td>Опрос каналов</td><td>2 мин</td><td>забирает новые сообщения</td></tr>
    <tr><td>Диспетчер ответов</td><td>1 мин (на :45)</td><td>находит чаты, ждущие ответа</td></tr>
    <tr><td>Отправка очереди</td><td>20 сек</td><td>по одному сообщению на чат</td></tr>
    <tr><td>Напоминания</td><td>10 мин</td><td>взводит и ставит follow-up</td></tr>
    <tr><td>Удаления</td><td>1 мин</td><td>отзыв сообщений в Instagram</td></tr>
    <tr><td>Синк CRM</td><td>5 мин</td><td>события в CRM + чтение состояния</td></tr>
    <tr><td>Профили клиентов</td><td>30 мин</td><td>подписчики и аватарки активной воронки</td></tr>
    <tr><td>Догрузка медиа</td><td>3 мин</td><td>скачивание и распознавание голосовых/картинок</td></tr>
    <tr><td>Переиндексация знаний</td><td>5 мин</td><td>ловит изменённые базы</td></tr>
    <tr><td>Облако потребностей</td><td>1 р/сутки</td><td>ночная аналитика (полночь Джакарты)</td></tr>
    <tr><td>Чистка логов</td><td>1 р/сутки</td><td>журнал вызовов брокера старше 30 дней</td></tr>
  </table></div>
  <details>
    <summary>Почему ничего не задваивается и не теряется</summary>
    <div class="body">
      <ul>
        <li>Задание на филиал получает стабильный ID: пока предыдущее летит, новое не ставится.</li>
        <li>Блокировка на уровне чата (advisory-lock базы): два пересёкшихся тика не вызовут нейросеть и не отправят сообщение дважды.</li>
        <li>Транзакция на каждый чат, а не на весь список — таймаут посередине не откатывает уже сделанное (реальный старый баг).</li>
        <li>Секунды расписаний подобраны так, чтобы в пределах минуты шло «приём → ответ → отправка».</li>
        <li>Лимит одновременных «медленных» генераций — чтобы всплеск ответов не заморил приём и отправку.</li>
      </ul>
      <details class="l3"><summary>До дна</summary><div class="body">
        <p><code>app/worker/main.py</code> (задачи и cron), <code>app/worker/wiring.py</code> (сборка адаптеров, выборки, блокировки, заморозка сессий). Движок — ARQ поверх Redis.</p>
      </div></details>
    </div>
  </details>
</section>

<!-- ДОСТУП -->
<section class="card reveal" id="access">
  <div class="kicker">09 · Люди и доступ</div>
  <h2>Кто что видит: три роли и жёсткая изоляция</h2>
  <p class="gist">Вход — через Telegram (виджет, подпись проверяется криптографически, сессия — подписанная cookie на 30 дней). Роли: <b>суперадмин</b> (вся платформа), <b>админ филиала</b> (читает и пишет в своём), <b>наблюдатель</b> (только читает). Один человек может быть админом в одном филиале и наблюдателем в другом.</p>
  <details>
    <summary>Изоляция филиалов: пять слоёв</summary>
    <div class="body">
      <ol style="padding-left:22px">
        <li><b>Данные:</b> почти каждая таблица несёт номер филиала; фильтрация — централизованно в одном классе-обёртке (<code>BranchScoped</code>), модули не пишут фильтры вручную.</li>
        <li><b>Сессия:</b> список доступных и записываемых филиалов зашит в подписанную cookie.</li>
        <li><b>Фильтр вида:</b> выбранные в интерфейсе филиалы всегда пересекаются на сервере с разрешёнными — подделка cookie не расширяет доступ.</li>
        <li><b>Построчная проверка</b> в каждом маршруте чатов/знаний/каналов — защита от подстановки чужого ID в адрес (IDOR).</li>
        <li><b>Два слоя защиты записи:</b> общий шлагбаум (любой изменяющий запрос от «наблюдателя» — 403; ни один из ~30 маршрутов записи нельзя забыть закрыть) + точная проверка филиала в самом маршруте. Всё fail-closed: старая cookie без нового поля прав = только чтение до перелогина.</li>
      </ol>
      <details class="l3"><summary>До дна: две админки</summary><div class="body">
        <p>Рабочая админка — <code>/ui/**</code>: инбокс, панель чата (пять SQL-запросов на отрисовку, ленивые переводы, без обращений к нейросети при открытии), знания, отчёты, настройки, участники, филиалы. Вторая — сырой SQLAdmin на <code>/admin/**</code>: прямой доступ к таблицам, <b>только суперадмин и всегда</b>, даже при выключенной аутентификации (иначе можно было бы поднять себе роль). Таблица с шифрованными секретами в неё намеренно не выведена. Файлы: <code>app/api/_auth.py</code> (4 middleware), <code>app/admin/_branch.py</code> (гварды), <code>app/modules/auth/rbac.py</code> (таблица прав — единственный источник правды, «запрещено по умолчанию»).</p>
      </div></details>
    </div>
  </details>
</section>

<!-- ДЕНЬГИ -->
<section class="card reveal" id="money">
  <div class="kicker">10 · Деньги</div>
  <h2>Бюджет: каждый цент под учётом</h2>
  <p class="gist">Каждый вызов нейросети возвращает точную цену от брокера. Цена складывается в дневную «кассовую книгу» филиала. Задан дневной лимит и он исчерпан — бот филиала просто молчит до конца дня (по местному времени филиала). Бюджеты филиалов не влияют друг на друга.</p>
  <details>
    <summary>До дна: как это не ломается под нагрузкой</summary>
    <div class="body">
      <ul>
        <li>Запись атомарная — одна SQL-команда «вставить или дополнить» без гонок (<code>app/modules/budget/service.py</code>). В коде зафиксирован реальный инцидент: неоднозначная ссылка на колонку роняла запись в PostgreSQL, и уже оплаченный ответ тихо терялся (SQLite в тестах это глотал).</li>
        <li>Проверка лимита — <b>до</b> вызова нейросети; списание — после успешного ответа. Симуляции не тарифицируются.</li>
        <li>Каждый вызов брокера — строка в журнале <code>broker_log</code>: сценарий (ответ/напоминание/страховка/перевод/поиск), провайдер, модель, токены, цена, задержка, успех. Хранится 30 дней, смотрится в админке с гистограммой. Ошибка записи журнала никогда не роняет ответ клиенту.</li>
      </ul>
    </div>
  </details>
</section>

<!-- CRM И РЕКЛАМА -->
<section class="card reveal" id="crm">
  <div class="kicker">11 · Внешний мир</div>
  <h2>CRM, реклама и аналитика потребностей</h2>
  <p class="gist">Степан — это переписка; CRM школы — это звонки, договоры и деньги. Они не дублируют друг друга, а <b>сшиваются по номеру телефона</b>. Железный принцип: сбой CRM никогда не заставит бота замолчать (fail-open).</p>
  <details>
    <summary>Две связки с CRM</summary>
    <div class="body">
      <ul>
        <li><b>Чтение («не мешай человеку»):</b> перед отправкой бот спрашивает состояние клиента в CRM. Если клиентом занялся менеджер, назначен звонок или подписан договор — вердикт «стоп»: сообщение не уходит, клиент переводится в стадию «менеджер». Состояние кешируется на 5 минут; периодический синк греет кеш заранее.</li>
        <li><b>Запись:</b> события «клиент готов / нужен менеджер» уходят в CRM вебхуком; неудачная отправка повторяется следующим тиком.</li>
      </ul>
      <details class="l3"><summary>До дна</summary><div class="body">
        <p><code>app/modules/crm/gate.py</code>, <code>pull.py</code>, <code>service.py</code>. Все флаги по умолчанию выключены — код в проде спит, пока оператор не включит. Есть защита от SSRF: вебхук — только https и только на публичные адреса (чтобы нельзя было слить данные на внутренний адрес облака). Ручные сообщения менеджера гейт не трогает. Дальний план: запись стадий в CRM и сигналы покупок в Meta CAPI — спроектировано, не включено.</p>
      </div></details>
    </div>
  </details>
  <details>
    <summary>Реклама: какое объявление приводит клиентов</summary>
    <div class="body">
      <p>Из Instagram-переписки достаётся ID рекламного объявления, с которого пришёл клиент. Какому курсу соответствует объявление — задаёт оператор в табличке соответствий (автоматика лишь подсказывает по истории, но никогда не записывает сама — это самоусиливающийся сигнал). Клиент с рекламы сразу получает нужный курс в чате, ещё до первого вопроса.</p>
      <details class="l3"><summary>До дна: отчёты</summary><div class="body">
        <p>Страница отчётов: воронка по каждому объявлению (клики ведут в отфильтрованный инбокс; ссылка «Открыть в FB» ведёт в публичную Библиотеку рекламы — лид-объявления крутятся под агентским кабинетом и в своём Ads Manager не видны); сегменты по температуре × аудитории с процентом успеха; диаграмма потока по стадиям (санкей по журналу переходов, видны откаты назад); источник продукта в чате — реклама/модель/менеджер, и модель никогда не перебивает ручной выбор менеджера. Файлы: <code>app/modules/ads/mapping.py</code>, <code>app/api/_ui_panels.py</code>.</p>
      </div></details>
    </div>
  </details>
  <details>
    <summary>Облако потребностей: что на самом деле волнует клиентов</summary>
    <div class="body">
      <p>Раз в сутки ИИ раскладывает собранные ботом боли/цели/выгоды всех клиентов по устойчивым категориям («не могу найти работу», «хочу сменить профессию»…). Виджет в отчётах показывает три колонки с частотами за любой период — менеджер видит картину рынка, не читая каждый чат.</p>
      <details class="l3"><summary>До дна</summary><div class="body">
        <p><code>app/modules/needs_cloud/service.py</code>. Хитрость — стабильность категорий: модель обязана переиспользовать существующие, новые заводит только при необходимости; обрабатываются лишь клиенты с изменившимся профилем (сравнение по хешу). Категории — по-русски, переводы для интерфейса кешируются. Есть фильтр «дрейфа алфавита»: провайдер иногда возвращал арабскую вязь — такие метки отбрасываются. Ежедневные снимки частот копятся для истории.</p>
      </div></details>
    </div>
  </details>
</section>

<!-- ОБВЯЗКА -->
<section class="card reveal" id="extras">
  <div class="kicker">12 · Обвязка</div>
  <h2>Уведомления, голосовые, внешний пульт</h2>
  <details open>
    <summary>Уведомления менеджерам — в Telegram</summary>
    <div class="body">
      <p>У каждого филиала — Telegram-группа с темами (форум): на каждого клиента заводится своя тема. Типы алертов: 🔥 готов к сделке · 📆 записался на день открытых дверей · ❓ нужен человек · 🔇 бот выключен, а клиент пишет · «канал требует повторного входа». Тело алерта: сводка чата на языке филиала + та же сводка по-русски + ссылка прямо в панель чата. Сбой отправки никогда не блокирует передачу клиента — запись в базе уже сделана.</p>
      <details class="l3"><summary>До дна</summary><div class="body"><p><code>app/modules/notifications/</code>, <code>app/adapters/notify/telegram.py</code>. Удалённая тема пересоздаётся автоматически; сводки — один вызов нейросети, при сбое деградация до пустых сводок, а не отказ.</p></div></details>
    </div>
  </details>
  <details>
    <summary>Голосовые и картинки: бот отвечает на содержание</summary>
    <div class="body">
      <p>Пришло голосовое или фото — в чат сразу пишется заглушка (🎤 / 🖼), и бот <b>ждёт</b>, не отвечая на заглушку. Фоновая задача скачивает файл, голос расшифровывает в текст, картинку описывает словами (скриншот, чек об оплате, фото). Только после этого бот отвечает — уже на содержание. Не удалось распознать за 6 часов — заглушка меняется на «не смог прослушать», и бот вежливо просит написать текстом.</p>
      <details class="l3"><summary>До дна</summary><div class="body"><p><code>app/modules/media/service.py</code>; лимит скачивания 60 МБ; временная ошибка — повтор каждые 3 минуты, постоянная — снятие флага навсегда. Перевод для операторской панели пересчитывается после распознавания.</p></div></details>
    </div>
  </details>
  <details>
    <summary>MCP: внешний пульт управления воронкой</summary>
    <div class="body">
      <p>Внешние системы (и Claude) могут управлять клиентом по номеру телефона: найти, передвинуть по стадии, закрыть сделку, отметить «не дозвонились» (тогда Степан сам напишет клиенту «пытались дозвониться — давайте здесь»). Отдельный <b>только читающий</b> доступ — для ревьюера: смотреть и анализировать чаты, физически без возможности что-то менять.</p>
      <details class="l3"><summary>До дна</summary><div class="body">
        <p>Три поверхности: локальный процесс для Claude Desktop (<code>mcp_server/stepan_mcp.py</code>), веб-коннектор <code>/connector/mcp</code>, читалка <code>/reader/mcp</code>. Токены хранятся только хешами (показываются один раз), бывают на один филиал или на все; правило доступа — единственная функция, fail-closed: нет контекста авторизации — отказ, а не «доступ ко всему». Токен филиала не действует на чужого клиента, даже если телефон нашёлся в другом филиале. Есть песочница <code>sim_say</code>: реплика через настоящий движок (поиск + страховка), но без Instagram и без списания бюджета — на ней гоняется регрессионный набор из 17 сценариев (<code>docs/dialogue-qa-checklist.md</code>).</p>
      </div></details>
    </div>
  </details>
</section>

<!-- КАЧЕСТВО -->
<section class="card reveal" id="quality">
  <div class="kicker">13 · Качество и доставка</div>
  <h2>Тесты, CI/CD и путь в прод</h2>
  <p class="gist">~1 034 автотеста в 97 файлах покрывают все подсистемы: изоляцию филиалов, склейку клиентов, воркер, страховку, поиск по знаниям, бюджет, MCP. Плюс отдельный «живой» регрессионный набор диалогов — сценарии, на которых бот когда-то ломался и был починен.</p>
  <details>
    <summary>Как код попадает в прод</summary>
    <div class="body">
      <ol style="padding-left:22px">
        <li>Каждый push: линтер + все тесты (GitHub Actions).</li>
        <li>Push в main: тесты → синхронизация кода на сервер → сборка контейнера → <b>миграции базы применяются новым образом, пока старый ещё обслуживает</b> (нет окна «новый код против старой схемы») → замена веб-контейнера → проверка живости (10 попыток).</li>
        <li>Воркер при деплое перезапускается только если уже был запущен — включение отправки остаётся ручным решением (переключение со Степаном-1).</li>
        <li>Откат — revert коммита и повторный деплой.</li>
      </ol>
      <details class="l3"><summary>До дна</summary><div class="body">
        <p>Тесты бегут на SQLite в памяти (быстро; порядок рандомизируется), поэтому весь SQL написан совместимо с обеими базами. База и Redis наружу не торчат (только внутри docker-сети), веб — на локальный порт за nginx + Cloudflare. Приложение в контейнере работает не от root — осознанное решение: воркер разбирает недоверенные данные из Instagram. Секреты — только в <code>.env</code> на сервере. Файлы: <code>.github/workflows/ci.yml</code>, <code>deploy.yml</code>, <code>infra/docker-compose.yml</code>, <code>Dockerfile</code>, <code>tests/conftest.py</code>.</p>
      </div></details>
    </div>
  </details>
</section>

<!-- ШПАРГАЛКА -->
<section class="card reveal" id="review">
  <div class="kicker">14 · К ревью</div>
  <h2>Шпаргалка: сильное, спорное и вероятные вопросы</h2>
  <details open>
    <summary>Чем гордиться</summary>
    <div class="body">
      <ul>
        <li>Изоляция филиалов — многослойная и централизованная, с fail-closed-поведением во всех спорных местах.</li>
        <li>Выдумки нейросети реально перехватываются до отправки, а не «запрещены промптом».</li>
        <li>Каждый рубль на нейросети посчитан и ограничен по филиалам.</li>
        <li>Идемпотентность везде: дубли сообщений, двойные тики, гонки — закрыты блокировками и уникальными ключами.</li>
        <li>Код документирует реальные инциденты прямо в комментариях: видно, <i>почему</i> решение именно такое.</li>
        <li>Осознанные компромиссы: CRM — fail-open (сбой не глушит бота), алерты — best-effort (пропуск пинга не теряет данные).</li>
      </ul>
    </div>
  </details>
  <details open>
    <summary>Честные слабые места (лучше назвать самим)</summary>
    <div class="body">
      <ul>
        <li>Аутентификация — <b>опциональна</b> и по умолчанию выключена (дизайн «выкатить тёмным»); в проде включена, но публичный деплой без неё оставил бы интерфейс открытым. Сырая админка при этом защищена всегда.</li>
        <li>Наблюдатель видит кнопки записи — сервер вернёт 403, но интерфейс их не прячет (граница безопасности серверная, скрытие — косметика, пока не сделано).</li>
        <li>pgvector установлен, но не используется: отпечатки — JSON, близость считается в Python (совместимость с тестами на SQLite; на текущих объёмах ок, на больших — точка роста).</li>
        <li>Документация местами отстаёт от кода (таблица задач воркера); один скрипт бэкофилла написан под устаревшую модель классификации — помечено в доках.</li>
        <li>Два «ручных» SQL-файла миграций живут вне Alembic.</li>
        <li>CRM-интеграция наполовину план: чтение и push событий готовы (выключены флагами), запись стадий и сигналы в Meta — спроектированы, не реализованы.</li>
      </ul>
    </div>
  </details>
  <details>
    <summary>Вероятные вопросы — короткие ответы</summary>
    <div class="body">
      <dl class="qa">
        <dt>«Почему поллинг, а не вебхуки?»</dt>
        <dd>Приватные API (instagrapi, Evolution) вебхуков не дают, а именно они позволяют писать после 24-часового окна. Официальные вебхуки Meta подключены как канал, но общий пайплайн построен на опросе.</dd>
        <dt>«Что мешает боту двух филиалов перепутать данные?»</dt>
        <dd>Фильтрация по филиалу зашита в единственный класс доступа к данным; интерфейс дополнительно пересекает выбор с правами на сервере и проверяет каждую строку. Плюс тесты изоляции.</dd>
        <dt>«Что будет, если нейросеть выдумает цену?»</dt>
        <dd>Детерминированная проверка сверит цену с базой знаний; несовпадение — перегенерация на дорогой модели; снова плохо — безопасная фраза и передача менеджеру. Клиент выдумку не увидит.</dd>
        <dt>«Что при падении брокера / CRM / Telegram?»</dt>
        <dd>Брокер: до 5 временных ошибок опроса терпится, чат переотвечает следующим тиком. CRM: fail-open, бот продолжает. Telegram: алерт теряется, запись в базе — нет.</dd>
        <dt>«Как масштабируется на новый филиал?»</dt>
        <dd>Создать филиал в интерфейсе (канонический скелет базы знаний создаётся сам), подключить каналы, выбрать персону, при желании — связать базу знаний с существующей. Кода писать не нужно.</dd>
        <dt>«Почему воркер выключен в docker-compose?»</dt>
        <dd>Намеренно: воркеры Степана-1 и Степана-2 не должны писать в один Instagram-аккаунт одновременно (бан). Включение — ручной шаг переключения.</dd>
      </dl>
    </div>
  </details>
</section>

<!-- СЛОВАРИК -->
<section class="card reveal" id="glossary">
  <div class="kicker">15 · Словарик</div>
  <h2>Термины за 30 секунд</h2>
  <div class="tablewrap"><table>
    <tr><th>Термин</th><th>По-человечески</th></tr>
    <tr><td><b>Филиал (branch)</b></td><td>изолированный «жилец» платформы: своя база, курсы, бот, люди, каналы</td></tr>
    <tr><td><b>Лид</b></td><td>карточка клиента в филиале; один человек = один лид, даже в разных мессенджерах</td></tr>
    <tr><td><b>Тред</b></td><td>один чат лида в одном канале (у лида их может быть несколько)</td></tr>
    <tr><td><b>Воронка</b></td><td>путь клиента: новый → выявление → презентация → возражения → готов → передан</td></tr>
    <tr><td><b>Брокер (AIbroker)</b></td><td>внешний шлюз ко всем нейросетям; возвращает цену каждого вызова</td></tr>
    <tr><td><b>RAG</b></td><td>умный поиск: в промпт попадают куски базы знаний, близкие по смыслу к диалогу</td></tr>
    <tr><td><b>Reply-guard</b></td><td>страховка: проверка черновика ответа на выдумки до отправки</td></tr>
    <tr><td><b>Outbox</b></td><td>исходящая очередь — единственная дверь наружу, где применяются все лимиты</td></tr>
    <tr><td><b>Follow-up</b></td><td>напоминание замолчавшему клиенту по расписанию</td></tr>
    <tr><td><b>24-часовое окно</b></td><td>срок после сообщения клиента, в который Meta официально разрешает ответить</td></tr>
    <tr><td><b>MCP</b></td><td>протокол, по которому внешняя система (или Claude) управляет воронкой</td></tr>
    <tr><td><b>Cutover</b></td><td>финальное переключение со Степана-1 на Степана-2 (включение воркера)</td></tr>
  </table></div>
</section>

<footer>
  Внутренняя страница команды. Составлено по коду и документации репозитория; уровни «до дна» содержат пути к файлам — по ним можно проверить каждое утверждение.
</footer>
</main>

<script>
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
</script>
</body>
</html>
"""


def hiw_html() -> str:
    """The complete /hiw page (static content, no per-request state)."""
    return _HTML
