"""HTML for the reports page: the ad funnel, its spend/segment trees, the stage flow and
the needs cloud.

Lifted out of _ui_panels.py on 2026-07-28, where it was the larger half of a 2509-line file
that also drew the leads list, the outbox, the coach chat, products, branches and every
channel form. The block referenced nothing from the rest of that file, so this is a move: the
seam was already there, drawn in the section comments, and only the filename disagreed.
"""
from __future__ import annotations

import datetime as _dt
import html as _h
from datetime import timedelta

from ._i18n import current_lang, t
from ._ui_html import (
    _STAGE_COLOR,
    _STAGE_ICON,
    _STAGES,
    _as_dt,
    fmt_dt,
    ig_post_url,
)

# ─── reports panel ────────────────────────────────────────────────────────────

def _fb_ad_url(ad_id: object, business_id: str = "", account_id: str = "") -> str:
    """Link to the ad in Meta's public Ad Library, which resolves ANY ad by id.

    Used for ads we could NOT bridge to Marketing API. Their only id is the one instagrapi
    reports (ad_context_data.ad_id), which lives in the Ad Library id space — Graph answers
    code 100 for it and an Ads Manager deep link shows 'not found'. The Ad Library keys off
    the id alone, so it still opens the real, live ad (creative, copy, status, advertiser) —
    view-only, no spend or results. business_id/account_id are unused here, kept so both
    URL builders share one signature shape."""
    return _h.escape(f"https://www.facebook.com/ads/library/?id={ad_id}")


def _ads_manager_url(
    ad_id: object, account_id: str = "", business_id: str = "",
    campaign_id: object = None, adset_id: object = None,
) -> str:
    """Deep link straight into the ad inside Ads Manager — editable, with spend and results.

    Only valid for ads resolved through the image_hash bridge (ad_creative_map.ad_id): that
    IS a Marketing API id and it lives in the configured account, so the account-scoped view
    finds it. Ads Manager filters its ad list by the selected_* ids; passing campaign and
    adset alongside the ad lands on the ad's own row instead of the account's full list.
    Without an account_id there is nothing to scope to — the caller falls back to the Ad
    Library."""
    qs = f"act={account_id}&selected_ad_ids={ad_id}"
    if business_id:
        qs += f"&business_id={business_id}"
    if campaign_id:
        qs += f"&selected_campaign_ids={campaign_id}"
    if adset_id:
        qs += f"&selected_adset_ids={adset_id}"
    return _h.escape(f"https://adsmanager.facebook.com/adsmanager/manage/ads?{qs}")


def admap_cell_inner(
    ad_id: object, mapped: str | None, suggested: str | None,
    products: list[tuple[str, str]],
) -> str:
    """Inner HTML of the product-mapping cell: a <select> (upserts the map on change) plus,
    when the ad is still unmapped, a one-click ⚡ suggestion chip from history. Shared by
    the reports table and the POST /ui/ads/{ad_id}/product response so both render identically."""
    aid = _h.escape(str(ad_id))
    opts = f'<option value="">— {_h.escape(t("rep.ad_no_product"))} —</option>'
    for slug, title in products:
        sel = " selected" if mapped == slug else ""
        opts += f'<option value="{_h.escape(slug)}"{sel}>{_h.escape(title)}</option>'
    sel_html = (
        f'<select class="admap-sel" name="product" hx-post="/ui/ads/{aid}/product"'
        f' hx-trigger="change" hx-target="#admap-{aid}" hx-swap="innerHTML">{opts}</select>'
    )
    hint = ""
    if not mapped and suggested:
        title = dict(products).get(suggested, suggested)
        hint = (
            f'<button class="admap-sug" hx-post="/ui/ads/{aid}/product"'
            f' hx-vals=\'{{"product":"{_h.escape(suggested)}"}}\' hx-trigger="click"'
            f' hx-target="#admap-{aid}" hx-swap="innerHTML"'
            f' title="{_h.escape(t("rep.ad_suggest_hint"))}">⚡ {_h.escape(title)}</button>'
        )
    return sel_html + hint


def _ad_menu_cell(
    ad_id: object, ad_media_id: object, fb_url: str, manager_url: str = "",
    extra_ids: int = 0,
) -> str:
    """Ad-id cell: a <details> menu (this ad's chats | Ads Manager | Ad Library) + IG post.

    manager_url is present only for bridged ads; the Ad Library entry stays either way, since
    it is the only view that works when the ad was published from another ad account.

    ad_id may be a comma-joined list when several Instagram ad ids resolve to one Meta ad;
    extra_ids is how many are hidden behind the first one in the summary label."""
    aid = _h.escape(str(ad_id))
    head = aid.split(",")[0] + (f' <span class="ad-more">+{extra_ids}</span>' if extra_ids else "")
    items = f'<a href="/ui/inbox?ad_id={aid}">💬 {_h.escape(t("rep.ad_open_chats"))}</a>'
    if manager_url:
        items += (
            f'<a href="{manager_url}" target="_blank" rel="noreferrer">'
            f'📊 {_h.escape(t("rep.ad_open_manager"))}</a>'
        )
    items += (
        f'<a href="{fb_url}" target="_blank" rel="noreferrer">'
        f'↗ {_h.escape(t("rep.ad_open_fb"))}</a>'
    )
    cell = (
        f'<details class="admenu"><summary title="{aid}">{head}</summary>'
        f'<div class="admenu-pop">{items}</div></details>'
    )
    for media in str(ad_media_id or "").split(",") if ad_media_id else []:
        post = ig_post_url(media)
        if post:
            cell += (
                f' <a class="ad-ig" href="{_h.escape(post)}" target="_blank" rel="noreferrer"'
                f' data-ig="{_h.escape(media)}" title="IG post">📷</a>'
            )
    return cell


# Client-side sort + per-column filter for the ad-funnel table. Inline so it ships with the
# htmx fragment; handlers are called via inline on* attrs, so redefining on each swap is a
# no-op (no listener stacking). A cell's sort/filter value is the mapping <select>'s value,
# else the ad-id <summary>, else the cell text — so the interactive product/ad cells sort too.
_AD_FUNNEL_JS = (
    "<script>"
    "function _adCellVal(td){if(!td)return'';"
    "var s=td.querySelector('select.admap-sel');if(s)return s.value;"
    "var sm=td.querySelector('summary');if(sm)return sm.textContent.trim();"
    "return td.textContent.trim();}"
    "function repSort(th){var tbl=th.closest('table');"
    "var idx=Array.prototype.indexOf.call(th.parentNode.children,th);"
    "var num=th.getAttribute('data-num')==='1';var asc=th.getAttribute('data-asc')!=='1';"
    "tbl.querySelectorAll('th.rep-sort').forEach(function(h){h.removeAttribute('data-asc');"
    "var a=h.querySelector('.rep-arr');if(a)a.textContent='';});"
    "th.setAttribute('data-asc',asc?'1':'0');"
    "var ar=th.querySelector('.rep-arr');if(ar)ar.textContent=asc?' \\u25B2':' \\u25BC';"
    "var tb=tbl.querySelector('tbody');"
    "var rs=Array.prototype.slice.call(tb.querySelectorAll('tr'));"
    "rs.sort(function(a,b){var x=_adCellVal(a.children[idx]),y=_adCellVal(b.children[idx]);"
    "if(num){x=parseFloat(x.replace(/[^0-9.\\-]/g,''))||0;"
    "y=parseFloat(y.replace(/[^0-9.\\-]/g,''))||0;return asc?x-y:y-x;}"
    "return asc?x.localeCompare(y):y.localeCompare(x);});"
    "rs.forEach(function(r){tb.appendChild(r);});}"
    "function repFilter(el){var tbl=el.closest('table');var fr=el.closest('tr');"
    "var fs=Array.prototype.slice.call(fr.querySelectorAll('.rep-f')).map(function(f){"
    "return{idx:Array.prototype.indexOf.call(f.parentNode.parentNode.children,f.parentNode),"
    "type:f.getAttribute('data-f'),val:f.value.trim().toLowerCase()};});"
    "var tb=tbl.querySelector('tbody');"
    "Array.prototype.slice.call(tb.querySelectorAll('tr')).forEach(function(r){var show=true;"
    "fs.forEach(function(f){if(!f.val)return;"
    "var cv=_adCellVal(r.children[f.idx]).toLowerCase();"
    "if(f.type==='text'){if(cv.indexOf(f.val)<0)show=false;}"
    "else if(f.type==='eq'){if(cv!==f.val)show=false;}"
    "else if(f.type==='min'){var n=parseFloat(cv.replace(/[^0-9.\\-]/g,''))||0;"
    "if(n<parseFloat(f.val))show=false;}});"
    "r.style.display=show?'':'none';});}"
    "</script>"
)


def _count_cell(aid: str, grp: str, n: int, color: str, no_ad: bool = False) -> str:
    """A funnel count that links to the matching chat list (ad + stage group). grp '' =
    every chat of the ad; otherwise a group from AD_FUNNEL_GROUPS (pipeline|won|dormant|deal).
    `no_ad` swaps the ad filter for "came from no ad", used by the organic row."""
    style = f' style="color:{color}"' if color else ""
    base = "/ui/inbox?no_ad=1" if no_ad else f"/ui/inbox?ad_id={aid}"
    qs = base + (f"&grp={grp}" if grp else "")
    return f'<td class="rep-n"{style}><a class="rep-lnk" href="{qs}">{n}</a></td>'


def _col_hint(key: str) -> str:
    """Hover text for a column header, from the '<key>.hint' translation.

    Silent when no hint is defined: t() echoes an unknown key back, so comparing against it
    is how we tell "no hint" from a real one — a header must never show 'rep.won.hint'."""
    hint_key = f"{key}.hint"
    hint = t(hint_key)
    return "" if hint == hint_key else f' title="{_h.escape(hint)}"'


def _ad_funnel_header(cols: list[tuple[str, bool, str, bool]],
                      products: list[tuple[str, str]]) -> str:
    """Two header rows: clickable sort headers + a per-column filter row.
    cols entries: (label_key, numeric, filter_kind[text|eq|min], align_right).

    Headers carry a hover hint: these columns mix OUR numbers with Meta's, and the two count
    different things — without saying so, "Переписок 600" next to "Наших лидов 100" reads
    like a bug rather than the point."""
    ths = ""
    for key, num, _kind, right in cols:
        style = ' style="text-align:right"' if right else ""
        ths += (
            f'<th class="rep-sort help"{style} data-num="{1 if num else 0}"'
            f'{_col_hint(key)}'
            f' onclick="repSort(this)">{_h.escape(t(key))}<span class="rep-arr"></span></th>'
        )
    fths = ""
    for _key, _num, kind, right in cols:
        style = ' style="text-align:right"' if right else ""
        if kind == "eq":  # product exact-match dropdown
            opts = f'<option value="">{_h.escape(t("rep.f_all"))}</option>' + "".join(
                f'<option value="{_h.escape(s)}">{_h.escape(tt)}</option>' for s, tt in products)
            ctrl = f'<select class="rep-f" data-f="eq" onchange="repFilter(this)">{opts}</select>'
        elif kind == "min":  # numeric ≥ threshold
            ctrl = ('<input class="rep-f" data-f="min" type="number" min="0"'
                    ' placeholder="≥" oninput="repFilter(this)">')
        else:  # substring match
            ctrl = ('<input class="rep-f" data-f="text"'
                    ' placeholder="🔍" oninput="repFilter(this)">')
        fths += f'<th{style}>{ctrl}</th>'
    return f'<thead><tr>{ths}</tr><tr class="rep-fltr">{fths}</tr></thead>'


_AD_TREE_CSS = (
    "<style>"
    ".adt{margin:.15rem 0 .5rem}"
    ".adt-c{border:1px solid #232b36;border-radius:6px;margin-bottom:.3rem;background:#151a21}"
    ".adt-c>summary{list-style:none;cursor:pointer;padding:.4rem .55rem;display:flex;"
    "align-items:center;gap:.5rem;font-size:.72rem}"
    ".adt-c>summary::-webkit-details-marker{display:none}"
    ".adt-c>summary:hover{background:#1a212a}"
    ".adt-c>summary::before{content:'\\25B8';color:#5c6b7d;font-size:.6rem;"
    "transition:transform .12s}"
    ".adt-c[open]>summary::before{transform:rotate(90deg)}"
    ".adt-nm{flex:1;color:#e8eef4;font-weight:600;overflow:hidden;text-overflow:ellipsis;"
    "white-space:nowrap}"
    ".adt-m{color:#8899aa;white-space:nowrap;font-variant-numeric:tabular-nums}"
    ".adt-m b{color:#e8eef4;font-weight:600}"
    ".adt-c table{margin:0;border-top:1px solid #232b36}"
    ".adt-orph{border-color:#3a2f22}"
    ".ad-more{color:#8899aa;font-size:.62rem}"
    # A dotted underline is the only affordance a title= tooltip has — without it nobody
    # discovers the hints, and these columns are the ones that most need explaining.
    ".help{text-decoration:underline dotted #5c6b7d 1px;text-underline-offset:3px;"
    "cursor:help}"
    "th.help{text-decoration:none}"
    "th.help:hover{text-decoration:underline dotted #5c6b7d 1px;text-underline-offset:3px}"
    "</style>"
)


def _series(daily: dict, metric: str) -> str:
    """A metric's per-day counts as 'YYYY-MM-DD:n,…', oldest first — the sparkline's input.

    Days with no events are absent from the query result and are NOT filled in here: the gap
    is filled by _sparkline, which needs the calendar span anyway to size its bars."""
    return ",".join(f"{d}:{n}" for d, n in sorted(daily.get(metric, {}).items()))


def _sparkline(series: str, color: str) -> str:
    """Per-day bars inside a KPI card: does this number come from a steady trickle or from
    one spike? A total alone cannot say, and on this panel that difference is the whole
    story — 138 hand-offs is a healthy week or a single batch depending on the shape.

    Missing days are rendered as zero-height bars so the x-axis stays calendar-true; a gap
    drawn as "no bar" would silently compress a quiet stretch and flatter the trend."""
    points = []
    for chunk in series.split(","):
        day, _, n = chunk.partition(":")
        if n.isdigit():
            points.append((day, int(n)))
    if len(points) < 2:  # one bar is not a trend, it is a rectangle
        return ""
    days = {d: n for d, n in points}
    span = _day_span(points[0][0], points[-1][0])
    bars = ""
    peak = max(days.values()) or 1
    for day in span:
        n = days.get(day, 0)
        h = round(n / peak * 100)
        bars += (
            f'<i style="height:{max(h, 2) if n else 1}%;background:{color};'
            f'opacity:{1 if n else .18}" title="{_h.escape(day)}: {n}"></i>'
        )
    return f'<div class="kpi-spark">{bars}</div>'


def _day_span(first: str, last: str) -> list[str]:
    """Every calendar day from first to last inclusive. Capped so an all-time range cannot
    render a thousand one-pixel bars nobody can read — the tail is the part anyone looks at,
    so the cap keeps the MOST RECENT days."""
    try:
        start = _dt.date.fromisoformat(first)
        end = _dt.date.fromisoformat(last)
    except ValueError:
        return []
    days = [start + _dt.timedelta(days=i) for i in range((end - start).days + 1)]
    return [d.isoformat() for d in days[-90:]]


def _money(value: float) -> str:
    return f"${value:,.2f}" if value else "—"


def _merge_ad_rows(rows: list, keys: list) -> list:
    """Collapse funnel rows that describe ONE real ad into a single row.

    fetch_ad_funnel groups by (ad_id, ad_media_id) because SQL cannot see the creative
    bridge. Instagram hands out several ad_context ids for the same Meta ad, and one ad can
    carry several creatives, so that grouping splits one ad across rows — each of which
    would then be charged the ad's FULL spend, making every per-row cost-per-lead wrong.
    Merging by `keys[i]` (the bridged Meta ad id, or the Instagram ad id when unbridged)
    fixes both: counts add up, spend is applied once.

    Returns (key, row) pairs, busiest first; row[0] is the list of Instagram ad ids and
    row[1] their comma-joined media ids, so the chats link, the product upsert and the
    IG-post icons still reach every underlying id."""
    merged: dict[object, list] = {}
    for row, key in zip(rows, keys, strict=True):
        ad_id, media_id, *counts = row
        cur = merged.get(key)
        if cur is None:
            merged[key] = [[str(ad_id)], [str(media_id)] if media_id else [],
                           *(int(c or 0) for c in counts)]
            continue
        if str(ad_id) not in cur[0]:
            cur[0].append(str(ad_id))
        if media_id and str(media_id) not in cur[1]:
            cur[1].append(str(media_id))
        for i, val in enumerate(counts, start=2):
            cur[i] += int(val or 0)
    out = [
        (key, (ids, ",".join(medias), *counts))
        for key, (ids, medias, *counts) in merged.items()
    ]
    return sorted(out, key=lambda kr: -kr[1][2])


def _ad_tree_html(
    rows: list, media_to_ad: dict[str, dict], ad_spend: dict[str, dict],
    synced_at: object = None, business_id: str = "", account_id: str = "", *,
    mappings: dict[str, str] | None = None,
    suggestions: dict[str, str] | None = None,
    products: list[tuple[str, str]] | None = None,
    organic: tuple[int, int, int, int, int] | None = None,
) -> str:
    """Campaign → ad tree: Meta's spend and our funnel on the same line, grouped the way the
    money is actually budgeted (per campaign), so "what did this campaign cost and what did it
    bring" is one glance instead of two tables and a manual join.

    Two numbers deliberately do NOT reconcile and are shown side by side: Meta counts taps it
    attributed, we count leads we hold. Cost-per-OUR-lead is the number Meta's headline
    cost-per-conversation hides.

    Ads we could not map to a campaign are NOT dropped — they get their own group at the
    bottom with their funnel and no spend. Dropping them would silently shrink the lead base
    and make the spend table look more complete than it is."""
    if not rows and not (organic and organic[0]):
        return ""
    mappings = mappings or {}
    suggestions = suggestions or {}
    show_map = bool(products)
    groups: dict[str, dict] = {}
    orphans: list = []
    for row in rows:
        mapped = media_to_ad.get(str(row[1] or ""))
        if mapped is None:
            orphans.append(row)
            continue
        key = mapped.get("campaign_name") or "—"
        grp = groups.setdefault(key, {"rows": [], "keys": [], "by_ad": {}, "spend": 0.0,
                                      "leads": 0, "won": 0, "deals": 0, "started": 0,
                                      "d5": 0, "blocks": 0, "seen": set()})
        grp["rows"].append(row)
        grp["keys"].append(mapped["ad_id"])
        grp["by_ad"][mapped["ad_id"]] = mapped
        grp["leads"] += int(row[2] or 0)
        grp["won"] += int(row[4] or 0)
        grp["deals"] += int(row[6] or 0)
        # Several media can resolve to ONE ad — count its spend once, not per medium.
        ad_id = mapped["ad_id"]
        if ad_id not in grp["seen"]:
            grp["seen"].add(ad_id)
            m = ad_spend.get(ad_id) or {}
            grp["spend"] += float(m.get("spend") or 0.0)
            grp["started"] += int(m.get("conv_started") or 0)
            grp["d5"] += int(m.get("conv_depth_5") or 0)
            grp["blocks"] += int(m.get("blocks") or 0)

    def _grp_items(grp: dict) -> list:
        """Merged rows of a campaign, each paired with its bridged-ad record."""
        return [
            (row, grp["by_ad"][key])
            for key, row in _merge_ad_rows(grp["rows"], grp["keys"])
        ]

    def _orphan_items(rows: list) -> list:
        """Unbridged ads merge by their Instagram ad id — the split there is per creative."""
        return [(row, {}) for _, row in _merge_ad_rows(rows, [str(r[0]) for r in rows])]

    def _ad_rows(items: list, with_spend: bool) -> str:
        out = ""
        for row, mapped in items:
            ad_ids, ad_media_id, total, pipeline, won, dormant, deals = row
            total, won, deals = int(total or 0), int(won or 0), int(deals or 0)
            conv = round(won / total * 100, 1) if total else 0.0
            ad_id = ",".join(ad_ids)
            aid = _h.escape(ad_id)
            # A merged row carries several Instagram ad ids; the product map is keyed by each
            # of them, so show the first one that is mapped and write back to all of them.
            cur = next((mappings.get(a) for a in ad_ids if mappings.get(a)), None)
            sug = next((suggestions.get(a) for a in ad_ids if suggestions.get(a)), None)
            cell = admap_cell_inner(ad_id, cur, sug, products or [])
            map_cell = f'<td class="admap" id="admap-{aid}">{cell}</td>' if show_map else ""
            spend_cells = ""
            if with_spend:
                m = ad_spend.get(mapped["ad_id"]) or {}
                spend = float(m.get("spend") or 0.0)
                cpl = spend / total if total else 0.0
                # Cost per SALE, from the CRM — not per hand-off. Those differ by however
                # many managers' conversations end without money.
                cpw = spend / deals if deals else 0.0
                spend_cells = (
                    f'<td class="rep-n">{_money(spend)}</td>'
                    f'<td class="rep-n">{int(m.get("conv_started") or 0) or "—"}</td>'
                    f'<td class="rep-n" style="color:#51cf66">'
                    f'{int(m.get("conv_depth_5") or 0) or "—"}</td>'
                    f'<td class="rep-n" style="font-weight:600">{_money(cpl)}</td>'
                    f'<td class="rep-n">{_money(cpw)}</td>'
                )
            fb = _fb_ad_url(mapped["ad_id"] if with_spend else ad_id, business_id, account_id)
            # Ads Manager can only find a bridged ad (a real Marketing API id in OUR account);
            # an unbridged row keeps the Ad Library link alone.
            mgr = ""
            if with_spend and account_id:
                mgr = _ads_manager_url(
                    mapped["ad_id"], account_id, business_id,
                    mapped.get("campaign_id"), mapped.get("adset_id"))
            out += (
                f'<tr><td>{_ad_menu_cell(ad_id, ad_media_id, fb, mgr, len(ad_ids) - 1)}</td>'
                f'{map_cell}{spend_cells}'
                f'{_count_cell(aid, "", total, "")}'
                f'{_count_cell(aid, "pipeline", int(pipeline or 0), "#9b7aff")}'
                f'{_count_cell(aid, "won", won, "#51cf66")}'
                f'{_count_cell(aid, "deal", deals, "#ffd43b")}'
                f'{_count_cell(aid, "dormant", int(dormant or 0), "#868e96")}'
                f'<td class="rep-n" style="color:#ffa94d">{conv}%</td></tr>'
            )
        return out

    def _head(with_spend: bool) -> str:
        """Sort + per-column filters survive the regrouping: campaigns answer "where did the
        money go", but finding one ad inside a big campaign still needs them.
        Entries: (label_key, numeric, filter_kind[text|eq|min], align_right)."""
        cols: list[tuple[str, bool, str, bool]] = [("rep.ad", False, "text", False)]
        if show_map:
            cols.append(("rep.ad_product", False, "eq", False))
        if with_spend:
            cols += [
                ("rep.ads_spend", True, "min", True), ("rep.ads_started", True, "min", True),
                ("rep.ads_d5", True, "min", True), ("rep.ads_cpl", True, "min", True),
                ("rep.ads_cpw", True, "min", True),
            ]
        cols += [
            ("rep.total", True, "min", True), ("rep.pipeline", True, "min", True),
            ("rep.won", True, "min", True), ("rep.deal", True, "min", True),
            ("rep.dormant", True, "min", True),
            ("rep.conv", True, "min", True),
        ]
        return _ad_funnel_header(cols, products or [])

    body = ""
    for name, grp in sorted(groups.items(), key=lambda kv: -kv[1]["spend"]):
        cpl = grp["spend"] / grp["leads"] if grp["leads"] else 0.0
        blocks = (
            f' · <span style="color:#ff6b6b">{grp["blocks"]}</span>' if grp["blocks"] else "")
        body += (
            f'<details class="adt-c"><summary>'
            f'<span class="adt-nm" title="{_h.escape(name)}">{_h.escape(name[:60])}</span>'
            f'<span class="adt-m help" title="{_h.escape(t("rep.ads_row.hint"))}">'
            f'<b>{_money(grp["spend"])}</b> · '
            f'{_h.escape(t("rep.ads_leads"))} <b>{grp["leads"]}</b> · '
            f'{_h.escape(t("rep.ads_won"))} <b>{grp["won"]}</b> · '
            f'{_h.escape(t("rep.deal"))} '
            f'<b style="color:#ffd43b">{grp["deals"]}</b> · '
            f'{_h.escape(t("rep.ads_cpl"))} <b>{_money(cpl)}</b>{blocks}</span>'
            f'</summary><table class="rep-tbl rep-sortable">{_head(True)}'
            f'<tbody>{_ad_rows(_grp_items(grp), True)}</tbody></table></details>'
        )
    if orphans:
        n = sum(int(r[2] or 0) for r in orphans)
        body += (
            f'<details class="adt-c adt-orph"><summary>'
            f'<span class="adt-nm">{_h.escape(t("rep.ads_unmatched"))}</span>'
            f'<span class="adt-m">{_h.escape(t("rep.ads_leads"))} <b>{n}</b> · '
            f'{_h.escape(t("rep.ads_no_spend"))}</span></summary>'
            f'<table class="rep-tbl rep-sortable">{_head(False)}'
            f'<tbody>{_ad_rows(_orphan_items(orphans), False)}</tbody></table></details>'
        )
    # Leads that came from no ad at all. They were absent from this whole section, which made
    # it read as the full base — and put the one confirmed sale somewhere no table listed.
    if organic and organic[0]:
        o_total, o_pipe, o_won, o_dorm, o_deals = organic
        body += (
            f'<details class="adt-c adt-org"><summary>'
            f'<span class="adt-nm">{_h.escape(t("rep.ads_organic"))}</span>'
            f'<span class="adt-m">{_h.escape(t("rep.ads_leads"))} <b>{o_total}</b> · '
            f'{_h.escape(t("rep.deal"))} <b style="color:#ffd43b">{o_deals}</b> · '
            f'{_h.escape(t("rep.ads_no_spend"))}</span></summary>'
            f'<table class="rep-tbl"><tbody><tr><td>'
            f'<a class="rep-lnk" href="/ui/inbox?no_ad=1">'
            f'{_h.escape(t("rep.ads_organic"))}</a></td>'
            f'{_count_cell("", "", o_total, "", no_ad=True)}'
            f'{_count_cell("", "pipeline", o_pipe, "#9b7aff", no_ad=True)}'
            f'{_count_cell("", "won", o_won, "#51cf66", no_ad=True)}'
            f'{_count_cell("", "deal", o_deals, "#ffd43b", no_ad=True)}'
            f'{_count_cell("", "dormant", o_dorm, "#868e96", no_ad=True)}'
            f'</tr></tbody></table></details>'
        )
    if not body:
        return ""
    tot_leads = sum(g["leads"] for g in groups.values())
    tot_spend = sum(g["spend"] for g in groups.values())
    seen_leads = tot_leads + sum(int(r[2] or 0) for r in orphans)
    covered = tot_leads / seen_leads * 100 if seen_leads else 0.0
    when = fmt_dt(synced_at, "%d.%m %H:%M")
    stamp = f' · {_h.escape(t("rep.ads_synced"))} {when}' if when else ""
    # Coverage on the face of the panel, not in a tooltip: a spend view that silently omits
    # part of the leads reads as complete and would be trusted as such.
    note = (
        f'<div style="font-size:.62rem;color:#8899aa;margin:.15rem 0 .4rem">'
        f'<span class="help" title="{_h.escape(t("rep.ads_coverage.hint"))}">'
        f'{_h.escape(t("rep.ads_coverage"))}: {covered:.0f}% ({tot_leads}/{seen_leads})</span>'
        f' · {_h.escape(t("rep.ads_total"))} {_money(tot_spend)}{stamp}</div>'
    )
    hdr = (
        f'<h3 style="font-size:.78rem;color:#8899aa;margin:1rem 0 .35rem">'
        f'{_h.escape(t("rep.ad_tree"))}</h3>'
    )
    return f'{hdr}{note}<div class="adt">{body}</div>{_AD_TREE_CSS}{_AD_FUNNEL_JS}'


# Pipeline order for the one-line funnel (side stages shown separately below). Verified
# against 7 days of live bot-driven transitions (2026-07-23): a fresh lead reaches discovery
# directly 657 times against 8 that land in nurturing first, and 84% of entries into
# nurturing come from an already-active stage going quiet, not from "new" — it's a side
# state entered from (and returned to) any active stage, not a step in the sequence.
# objection left the line on 2026-07-25 for the same reason nurturing did: over 30 days it was
# entered from qualifying (39%), presenting (24%), new (14%) and even ready, and left back to
# all of them. In the line it made the report show doubt as a mandatory station between
# presenting and ready — an ordinary moment of any sale rendered as a bottleneck.
_FUNNEL_PIPELINE = ("new", "qualifying", "presenting", "ready")
_FUNNEL_SIDE = ("objection", "nurturing", "handed_off", "dormant", "manager")

# Flow diagram: pipeline spine on the top lane, side branches on the bottom lane.
_FLOW_SPINE = ("new", "qualifying", "presenting", "ready", "handed_off")
_FLOW_EXITS = ("objection", "nurturing", "dormant", "manager")


def _funnel_flow_html(
    flow: list, reach: dict[str, int] | None = None, total_leads: int = 0,
) -> str:
    """The whole funnel as a server-rendered SVG flow (Sankey-style): each lead's real path
    from first message (entry) through every stage transition to an exit, reconstructed from the
    stage_event audit log. Link thickness ∝ distinct leads on that transition; node bar/label =
    distinct leads that passed through the stage (`reach`) — a real headcount ≤ total leads, so
    the entry bar never reads higher than the lead base. Falls back to edge-derived throughput
    when `reach` is absent. `total_leads` (all leads in the window) drives the standalone
    "no movement" bucket = leads that entered but have no transition yet, so entry + no-movement
    reconcile to the whole base. Each node has a hover <title> explaining how the stage is
    determined. Back-edges (e.g. presenting→qualifying) curve, so churn and drop-off are visible,
    not just the happy path. Empty (→ caller falls back to the line funnel) when there's no
    transition history for the window."""
    edges = [(str(a), str(b), int(c)) for a, b, c in flow if int(c) > 0 and str(a) != str(b)]
    if not edges:
        return ""
    out_sum: dict[str, int] = {}
    in_sum: dict[str, int] = {}
    for a, b, c in edges:
        out_sum[a] = out_sum.get(a, 0) + c
        in_sum[b] = in_sum.get(b, 0) + c
    edge_tp = {s: max(out_sum.get(s, 0), in_sum.get(s, 0)) for s in set(out_sum) | set(in_sum)}
    tp = {s: (reach.get(s, edge_tp[s]) if reach else edge_tp[s]) for s in edge_tp}
    max_tp = max(tp.values(), default=1) or 1
    max_c = max((c for _, _, c in edges), default=1) or 1

    vw, vh, bar_w = 700, 210, 16
    left, right, top_y, bot_y = 46, vw - 46, 66, 168
    step = (right - left) / (len(_FLOW_SPINE) - 1)
    pos: dict[str, tuple[float, float]] = {
        s: (left + i * step, top_y) for i, s in enumerate(_FLOW_SPINE)
    }
    # Bottom lane, spread across the width: nurturing sits early (its traffic is heaviest
    # around qualifying/presenting), dormant and manager later, mirroring where in a real
    # conversation a lead is most likely to fall into each.
    pos["nurturing"] = (left + 1.5 * step, bot_y)
    pos["dormant"] = (left + 3 * step, bot_y)
    pos["manager"] = (left + 4.5 * step, bot_y)

    def bar_h(s: str) -> float:
        return max(10.0, min(96.0, tp.get(s, 0) / max_tp * 96))

    links = ""
    for a, b, c in sorted(edges, key=lambda e: e[2]):  # thin first so thick links draw on top
        if a not in pos or b not in pos:
            continue
        x0, y0 = pos[a]
        x1, y1 = pos[b]
        fwd = x1 >= x0
        sx = x0 + bar_w / 2 if fwd else x0 - bar_w / 2
        tx = x1 - bar_w / 2 if fwd else x1 + bar_w / 2
        w = max(1.0, min(22.0, c / max_c * 22))
        mx = (sx + tx) / 2
        links += (
            f'<path d="M{sx:.0f},{y0:.0f} C{mx:.0f},{y0:.0f} {mx:.0f},{y1:.0f} {tx:.0f},{y1:.0f}"'
            f' fill="none" stroke="{_STAGE_COLOR.get(b, "#4a5568")}" stroke-width="{w:.1f}"'
            f' opacity="0.3"/>'
        )
    nodes = ""
    for s, (x, y) in pos.items():
        t_s = tp.get(s, 0)
        if t_s <= 0:
            continue
        h = bar_h(s)
        col = _STAGE_COLOR.get(s, "#4a5568")
        # 'new' is the entry point (every lead's first message), not a "still-new" bucket —
        # label it "entry" so its throughput doesn't read as current occupancy of stage new.
        lbl = _h.escape(t("flow.entry") if s == "new" else t(f"stage.{s}"))
        below = s in _FLOW_EXITS
        ty = y + h / 2 + 12 if below else y - h / 2 - 5
        desc = _h.escape(t("flow.entry_desc") if s == "new" else t(f"sdesc.{s}"))
        nodes += (
            f'<a href="/ui/inbox?stage={s}" style="cursor:pointer">'
            f'<g><title>{lbl}: {t_s} — {desc}</title>'
            f'<rect x="{x - bar_w / 2:.0f}" y="{y - h / 2:.0f}" width="{bar_w}" height="{h:.0f}"'
            f' rx="3" fill="{col}"/>'
            f'<text x="{x:.0f}" y="{ty:.0f}" text-anchor="middle" fill="#9aa7b5"'
            f' font-size="9">{lbl} · {t_s}</text></g></a>'
        )
    # "no movement": leads that entered (first message) but have no transition logged yet, so
    # entry + this = the whole base. A standalone dashed node under the entry (no links flow to
    # it — that is the point: they never moved).
    moved = reach.get("*", 0) if reach else 0
    stuck = max(0, total_leads - moved)
    if stuck > 0:
        sx, sy = left, bot_y
        sh = max(10.0, min(96.0, stuck / max_tp * 96))
        slbl = _h.escape(t("flow.stuck"))
        sdesc = _h.escape(t("flow.stuck_desc"))
        nodes += (
            f'<g><title>{slbl}: {stuck} — {sdesc}</title>'
            f'<rect x="{sx - bar_w / 2:.0f}" y="{sy - sh / 2:.0f}" width="{bar_w}"'
            f' height="{sh:.0f}" rx="3" fill="#3a4250" stroke="#4a5568"'
            f' stroke-dasharray="3 2"/>'
            f'<text x="{sx:.0f}" y="{sy + sh / 2 + 12:.0f}" text-anchor="middle" fill="#6b7685"'
            f' font-size="9">{slbl} · {stuck}</text></g>'
        )
    svg = (
        f'<svg viewBox="0 0 {vw} {vh}" style="width:100%;max-width:720px;height:auto"'
        f' xmlns="http://www.w3.org/2000/svg">{links}{nodes}</svg>'
    )
    return (
        f'<h3 style="font-size:.78rem;color:#8899aa;margin:.9rem 0 .35rem">'
        f'{_h.escape(t("rep.funnel"))}</h3><div class="seg-tree">{svg}</div>'
    )


def _funnel_line_html(stage_counts: dict[str, int]) -> str:
    """One-line sales funnel: each pipeline stage as a step (count + % of total), a tooltip
    describing HOW the stage is determined, in order — extensible by editing the tuples."""
    total = sum(stage_counts.values()) or 1
    steps = ""
    for s in _FUNNEL_PIPELINE:
        n = stage_counts.get(s, 0)
        pct = round(n / total * 100)
        color = _STAGE_COLOR.get(s, "#4a5568")
        steps += (
            f'<div class="fnl-step" title="{_h.escape(t(f"sdesc.{s}"))}">'
            f'<div class="fnl-bar" style="background:{color}"></div>'
            f'<div class="fnl-num">{n}</div>'
            f'<div class="fnl-nm">{_h.escape(t(f"stage.{s}"))}</div>'
            f'<div class="fnl-pct">{pct}%</div></div>'
        )
    side = ""
    for s in _FUNNEL_SIDE:
        n = stage_counts.get(s, 0)
        if not n:
            continue
        side += (
            f'<span class="fnl-side" title="{_h.escape(t(f"sdesc.{s}"))}"'
            f' style="border-color:{_STAGE_COLOR.get(s,"#4a5568")}">'
            f'{_h.escape(t(f"stage.{s}"))} {n}</span>'
        )
    side_row = f'<div class="fnl-side-row">{side}</div>' if side else ""
    return (
        f'<h3 style="font-size:.78rem;color:#8899aa;margin:.9rem 0 .4rem">'
        f'{_h.escape(t("rep.funnel"))}</h3>'
        f'<div class="fnl-line">{steps}</div>{side_row}'
    )


_QUICK_RANGES = (
    ("1h", "rep.range_1h"), ("2h", "rep.range_2h"), ("4h", "rep.range_4h"),
    ("8h", "rep.range_8h"), ("12h", "rep.range_12h"), ("24h", "rep.range_24h"),
    ("7d", "rep.range_7d"), ("30d", "rep.range_30d"),
    ("60d", "rep.range_60d"), ("90d", "rep.range_90d"), ("", "rep.range_all"),
)


def _quick_range_html(active_range: str) -> str:
    """One-click preset buttons — each fires its own htmx GET immediately (no Apply
    click, no date typing) and clears the manual date pickers since the two are
    mutually exclusive filters."""
    chips = "".join(
        f'<a class="rep-preset{" on" if key == active_range else ""}"'
        f' hx-get="/ui/reports/panel{f"?range={key}" if key else ""}" hx-target="#main"'
        f' hx-push-url="true">{_h.escape(t(label))}</a>'
        for key, label in _QUICK_RANGES
    )
    return f'<div class="rep-presets">{chips}</div>'


def _date_range_form_html(date_from: str, date_to: str, active_range: str = "") -> str:
    """Quick-range presets plus From/To date pickers filtering the whole report by the
    lead's conversation-start date.

    Auto-applies on change of EITHER date (no Apply click needed, no full reload) — htmx's
    hx-trigger="change" listens on the form and fires from either input independently."""
    return (
        f'{_quick_range_html(active_range)}'
        f'<form class="rep-dates" hx-get="/ui/reports/panel" hx-target="#main"'
        f' hx-push-url="true" hx-trigger="change">'
        f'<label>{_h.escape(t("rep.from"))}'
        f'<input type="date" name="date_from" value="{_h.escape(date_from)}"></label>'
        f'<label>{_h.escape(t("rep.to"))}'
        f'<input type="date" name="date_to" value="{_h.escape(date_to)}"></label>'
        f'<span class="rep-dhint">{_h.escape(t("rep.date_hint"))}</span>'
        f'</form>'
    )


# Classifier/intent palette — a temperature scale, deliberately using DIFFERENT hexes than
# _STAGE_COLOR's funnel/pipeline palette above, so the same color never means two different
# things when a segment card and a stage box sit side by side (they used to share exact hex
# values — hot==manager, warm==ready, cold==new, no_budget==qualifying — which is why the
# reports panel read as one undifferentiated wall of color). 'student' is an audience, not a
# segment here — see _AUD_ORDER.
_SEG_META = (  # (key, colour, i18n label) — this tuple order IS the display order everywhere
    ("hot", "#f06595", "seg.hot"),
    ("warm", "#ffd43b", "seg.warm"),
    ("cold", "#748ffc", "seg.cold"),
    ("no_budget", "#be4bdb", "seg.no_budget"),
    ("non_target", "#5c636a", "seg.non_target"),
    ("unclear", "#4a5568", "seg.unclear"),
)
# Fixed rank by temperature (hottest first, unclassified last) — the SAME order in every
# audience block, regardless of win-rate or volume, so "hot/warm/cold/..." always reads
# top-to-bottom the same way instead of reshuffling per block (that's what made the reports
# panel look inconsistent — win-rate sort put segments in a different order per audience).
_SEG_RANK = {k: i for i, (k, _c, _l) in enumerate(_SEG_META)}
_AUD_ORDER = ("adult", "unknown", "student")  # sub-tree order; 'unknown' = not yet classified


def _segment_subtree_svg(
    rows: list, root_label: str, aud_key: str = "", seg_stage_map: dict | None = None,
) -> str:
    """One audience's segment tree: a root node (its total) branching into each intent
    segment, link thickness ∝ volume, in the fixed temperature order (_SEG_RANK) — the same
    order in every audience block. To the RIGHT of each segment node, a row of small stage
    boxes (the funnel inside that segment): one box per non-empty stage with its count,
    clickable to that audience+segment+stage's chats. `seg_stage_map` = {lead_type:
    {stage: count}}."""
    meta = {k: (c, lbl) for k, c, lbl in _SEG_META}
    leaves = []
    for key, n, won in rows:
        if n <= 0:
            continue
        color, lbl = meta.get(key, ("#4a5568", "seg.unclear"))
        leaves.append((color, _h.escape(t(lbl)), n, won, key))
    if not leaves:
        return ""
    leaves.sort(key=lambda r: _SEG_RANK.get(r[4], len(_SEG_META)))
    total = sum(r[2] for r in leaves)
    n_seg = len(leaves)
    ssm = seg_stage_map or {}
    row_h, top, node_x, node_w, node_h = 46, 14, 372, 236, 34
    link_x0, mid_x = 128, 250
    bx0, bw, bh, bgap = node_x + node_w + 12, 46, 34, 6  # stage boxes right of each node
    max_boxes = max(
        (sum(1 for st in _STAGES if int(ssm.get(k, {}).get(st, 0) or 0) > 0)
         for _c, _l, _n, _w, k in leaves), default=0)
    w = bx0 + max_boxes * (bw + bgap) if max_boxes else node_x + node_w + 10
    height = top * 2 + n_seg * row_h
    root_cy = height // 2
    links, nodes = "", ""
    for i, (color, label, cnt, won, key) in enumerate(leaves):
        cy = top + row_h // 2 + i * row_h
        thick = max(2, round(cnt / total * 34))
        links += (
            f'<path d="M{link_x0},{root_cy} C{mid_x},{root_cy} {mid_x},{cy} {node_x},{cy}"'
            f' fill="none" stroke="{color}" stroke-width="{thick}" opacity="0.5"/>'
        )
        pct = round(cnt / total * 100)
        won_pct = round(won / cnt * 100) if cnt else 0
        y = cy - node_h // 2
        desc = _h.escape(t(f"segdesc.{key}"))
        tip = t("seg.tip", label=label, cnt=cnt, pct=pct, won_pct=won_pct, desc=desc)
        aud_q = f"&audience={aud_key}" if aud_key else ""
        nodes += (
            f'<a href="/ui/inbox?lead_type={key}{aud_q}"'
            f' style="cursor:pointer">'
            f'<g><title>{tip}</title>'
            f'<rect x="{node_x}" y="{y}" width="{node_w}" height="{node_h}" rx="6"'
            f' fill="#141925" stroke="#2d3748"/>'
            f'<rect x="{node_x}" y="{y}" width="4" height="{node_h}" rx="2" fill="{color}"/>'
            f'<text x="{node_x + 14}" y="{cy - 2}" fill="{color}" font-size="12"'
            f' font-weight="600">{label}</text>'
            f'<text x="{node_x + node_w - 10}" y="{cy - 1}" text-anchor="end" fill="#e8eef4"'
            f' font-size="14" font-weight="700">{cnt}</text>'
            f'<text x="{node_x + 14}" y="{cy + 11}" fill="#6b7685" font-size="9">'
            f'{pct}% · won {won_pct}%</text></g></a>'
        )
        # stage boxes to the right — the funnel inside this segment
        by, j = cy - bh // 2, 0
        for st in _STAGES:
            c = int(ssm.get(key, {}).get(st, 0) or 0)
            if c <= 0:
                continue
            bx = bx0 + j * (bw + bgap)
            j += 1
            scol = _STAGE_COLOR.get(st, "#868e96")
            sicon = _STAGE_ICON.get(st, "•")
            slabel = _h.escape(t(f"stage.{st}"))
            stip = f"{slabel}: {c}"
            nodes += (
                f'<a href="/ui/inbox?lead_type={key}{aud_q}&stage={st}" style="cursor:pointer">'
                f'<g><title>{stip}</title>'
                f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="5"'
                f' fill="#141925" stroke="#2d3748"/>'
                f'<rect x="{bx}" y="{by}" width="{bw}" height="3" rx="1.5" fill="{scol}"/>'
                # icon caption above the count so each box reads on its own, not just on hover —
                # same glyph the main inbox funnel uses for this stage, tying the two views
                # together visually.
                f'<text x="{bx + bw / 2:.0f}" y="{by + 15}" text-anchor="middle"'
                f' font-size="10">{sicon}</text>'
                f'<text x="{bx + bw / 2:.0f}" y="{by + 28}" text-anchor="middle" fill="#e8eef4"'
                f' font-size="12" font-weight="700">{c}</text></g></a>'
            )
    root = (
        f'<rect x="6" y="{root_cy - 30}" width="122" height="60" rx="8" fill="#1a2230"'
        f' stroke="#2d3748"/>'
        f'<text x="67" y="{root_cy - 7}" text-anchor="middle" fill="#8899aa"'
        f' font-size="10">{root_label}</text>'
        f'<text x="67" y="{root_cy + 16}" text-anchor="middle" fill="#e8eef4"'
        f' font-size="22" font-weight="700">{total}</text>'
    )
    return (
        f'<svg viewBox="0 0 {w} {height}" style="width:100%;max-width:{w}px;height:auto"'
        f' xmlns="http://www.w3.org/2000/svg">{links}{root}{nodes}</svg>'
    )


def _segment_tree_html(segments: list, seg_stage_by_aud: dict | None = None) -> str:
    """Two-axis lead breakdown: one segment sub-tree per audience (adults, then students),
    each root showing that audience's total, branching into intent segments by win rate, with
    the stage funnel drawn as boxes to the right of each segment. Rows: (audience, lead_type,
    total, won); a legacy 3-tuple is audience 'adult'. `seg_stage_by_aud` =
    {aud: {lead_type: {stage: count}}}. Server-rendered SVG — no client JS."""
    by_aud: dict[str, list] = {}
    for s in segments:
        if len(s) >= 4:
            aud, key, n, won = str(s[0]), str(s[1]), int(s[2]), int(s[3] or 0)
        else:  # legacy (lead_type, total, won) with no audience axis
            aud, key, n, won = "adult", str(s[0]), int(s[1]), int(s[2] or 0)
        if n <= 0:
            continue
        by_aud.setdefault(aud, []).append((key, n, won))
    if not by_aud:
        return ""
    auds = [a for a in _AUD_ORDER if a in by_aud] + [a for a in by_aud if a not in _AUD_ORDER]
    single = len(auds) == 1
    blocks = ""
    for aud in auds:
        # With one audience, keep the familiar "Total leads" root; else name each audience.
        root_label = _h.escape(t("rep.total") if single else t(f"aud.{aud}"))
        svg = _segment_subtree_svg(by_aud[aud], root_label, aud_key=aud,
                                   seg_stage_map=(seg_stage_by_aud or {}).get(aud, {}))
        if not svg:
            continue
        # No caption above the block: the root card already carries the audience name, and
        # printing it twice made three headings compete with three identical-looking cards.
        blocks += f'<div class="seg-tree">{svg}</div>'
    if not blocks:
        return ""
    return (
        f'<h3 style="font-size:.78rem;color:#8899aa;margin:1rem 0 .35rem">'
        f'{_h.escape(t("seg.title"))}</h3>{blocks}'
    )


_CLOUD_COLS = (
    ("pains", "cloud.pains", "#ff8787"),   # Боли
    ("jobs", "cloud.jobs", "#74c0fc"),     # Цели
    ("gains", "cloud.gains", "#69db7c"),   # Выгоды
)


def _needs_cloud_html(clouds: dict | None) -> str:
    """Three-column need cloud (Боли · Цели · Выгоды), AI-grouped, most frequent first, each
    entity with a weight bar. Empty until the nightly aggregation has run for the branch."""
    if not clouds:
        return ""
    cols = ""
    for kind, title_key, color in _CLOUD_COLS:
        entries = clouds.get(kind) or []
        rows = ""
        for e in entries:
            pct = max(6, round(e.weight * 100))  # keep a sliver visible even for the rarest
            rows += (
                f'<div class="ncl-row" title="{_h.escape(e.label)} · {e.count}">'
                f'<div class="ncl-bar" style="width:{pct}%;background:{color}"></div>'
                f'<span class="ncl-lbl">{_h.escape(e.label)}</span>'
                f'<span class="ncl-n">{e.count}</span></div>'
            )
        if not rows:
            rows = f'<div class="ncl-empty">{_h.escape(t("cloud.empty"))}</div>'
        cols += (
            f'<div class="ncl-col">'
            f'<div class="ncl-hd" style="color:{color}">{_h.escape(t(title_key))}</div>'
            f'{rows}</div>'
        )
    return (
        f'<div class="ncl-wrap">'
        f'<h3 class="ncl-title">{_h.escape(t("cloud.title"))}</h3>'
        f'<div class="ncl-cols">{cols}</div></div>'
    )


def reports_panel_html(
    stage_counts: dict[str, int],
    hour_in: dict[int, int],
    hour_out: dict[int, int],
    ad_funnel: list | None = None,
    discovery: dict | None = None,
    fb_business_id: str = "",
    fb_account_id: str = "",
    date_from: str = "",
    date_to: str = "",
    active_range: str = "",
    ad_mappings: dict[str, str] | None = None,
    ad_suggestions: dict[str, str] | None = None,
    products: list[tuple[str, str]] | None = None,
    segments: list | None = None,
    segment_stages: dict | None = None,
    stage_flow: list | None = None,
    stage_reach: dict[str, int] | None = None,
    total_leads: int = 0,
    needs_cloud: dict | None = None,
    closed_in_period: int | None = None,
    deals: int | None = None,
    daily_kpis: dict[str, dict[str, int]] | None = None,
    organic: tuple[int, int, int, int, int] | None = None,
    media_to_ad: dict[str, dict] | None = None,
    ad_spend: dict[str, dict] | None = None,
    ads_synced_at: object = None,
) -> str:
    total = sum(stage_counts.values())

    def _kpi(
        label: str, value: str, color: str = "#e8eef4", series: str = "", href: str = "",
    ) -> str:
        """A tile, optionally linking to the chats behind it. Without the link a number like
        "Сделка 1" is unanswerable: the buyer may have come from no ad at all, so no table
        further down the page contains them."""
        body = (
            f'<div class="kpi-n" style="color:{color}">{_h.escape(value)}</div>'
            f'<div class="kpi-l">{_h.escape(t(label))}</div>'
            f'{_sparkline(series, color) if series else ""}'
        )
        cls = "kpi kpi-lnk" if href else "kpi"
        tip = _h.escape(t(f"{label}.hint"))
        if href:
            return f'<a class="{cls}" href="{_h.escape(href)}" title="{tip}">{body}</a>'
        return f'<div class="{cls}" title="{tip}">{body}</div>'

    daily = daily_kpis or {}
    # Every tile below is an EVENT counted inside the window: a lead arriving, a hand-off, a
    # CRM close, a lead going quiet, a message. The old panel mixed those with cohort reads
    # ("of the leads that arrived, how many are NOW in stage X"), so two tiles could both be
    # right and still disagree — "Продано из пришедших 53" next to "Продано за период 138".
    # The current-stage snapshot did not disappear: it is the funnel line further down, which
    # is the honest place for it. Reworked 2026-07-27.
    total_in = sum(hour_in.values())
    total_out = sum(hour_out.values())
    kpis = (
        _kpi("rep.total", str(total), series=_series(daily, "leads"))
        + _kpi("rep.closed_period", str(closed_in_period or 0), "#51cf66",
               _series(daily, "handoff"))
        # The only tile that means money — everything else counts conversations. Clickable
        # because a buyer can come from no ad at all, in which case NO table on this page
        # lists them: the ad tree only holds threads that carry an ad_id.
        + _kpi("rep.deal", str(deals or 0), "#ffd43b", _series(daily, "deal"),
               href="/ui/inbox?grp=deal")
        + _kpi("rep.dormant_period", str(sum(daily.get("dormant", {}).values())), "#868e96",
               _series(daily, "dormant"))
        + _kpi("rep.msgs_tile", f"{total_out}↑ / {total_in}↓", "#63c5ff",
               _series(daily, "messages"))
    )
    if discovery is not None:  # rates, not events — a daily series of a ratio is noise
        kpis += (
            _kpi("rep.discovered", f"{discovery.get('pct', 0):g}%", "#4da6ff")
            + _kpi("rep.disc_len", f"{discovery.get('avg_msgs', 0):g}", "#4da6ff")
        )

    # compact hourly-activity mini-chart placed high in the panel — grouped in/out bars per
    # hour-of-day, scaled to the busiest in/out count so the two directions compare directly;
    # the header carries the period totals + the peak hour so bar heights have a magnitude.
    max_val = max(max(hour_in.values(), default=0), max(hour_out.values(), default=0), 1)
    hour_totals = {h: hour_in.get(h, 0) + hour_out.get(h, 0) for h in range(24)}
    peak_h = max(hour_totals, key=lambda h: hour_totals[h])
    peak_val = hour_totals[peak_h]
    in_lbl = _h.escape(t("rep.msgs_in"))
    out_lbl = _h.escape(t("rep.msgs_out"))
    hour_bars = ""
    for h in range(24):
        n_in = hour_in.get(h, 0)
        n_out = hour_out.get(h, 0)
        h_in = round(n_in / max_val * 100)
        h_out = round(n_out / max_val * 100)
        hour_bars += (
            f'<div class="hbar" title="{h:02d}:00 · {in_lbl} {n_in} · {out_lbl} {n_out}">'
            f'<div class="hbar-g">'
            f'<div class="hbar-in" style="height:{h_in}%"></div>'
            f'<div class="hbar-out" style="height:{h_out}%"></div>'
            f'</div>'
            f'<div class="hbar-l">{f"{h:02d}" if h % 6 == 0 else ""}</div>'
            f'</div>'
        )
    peak_txt = _h.escape(t("rep.peak", n=peak_val, h=f"{peak_h:02d}"))
    mini_act = (
        f'<div class="mini-act">'
        f'<div class="mini-act-hd">'
        f'<span class="mini-act-t">{_h.escape(t("rep.activity"))} · '
        f'{_h.escape(t("rep.by_hour"))}</span>'
        f'<span class="mini-act-s"><b style="color:#4da6ff">{total_in}</b> {in_lbl} · '
        f'<b style="color:#51cf66">{total_out}</b> {out_lbl} · {peak_txt}</span></div>'
        f'<div class="hchart hchart-mini">{hour_bars}</div>'
        f'</div>'
    )

    title_lbl = _h.escape(t("rep.title"))
    return (
        f'<div class="ch"><span class="ch-n" data-help="{_h.escape(t("help.reports"))}">'
        f'{title_lbl}</span></div>'
        f'<div class="pnl-body">'
        # Date controls and the hour histogram sit side by side: both answer "which slice am
        # I looking at", and the histogram was previously stranded below the funnel, far from
        # the filter that scopes it.
        f'<div class="rep-top">'
        f'<div class="rep-top-dates">'
        f'{_date_range_form_html(date_from, date_to, active_range)}</div>'
        f'{mini_act}'
        f'</div>'
        f'<div class="kpi-row">{kpis}</div>'
        f'{_segment_tree_html(segments or [], segment_stages)}'
        f'<div class="rep-fc">'
        f'{_needs_cloud_html(needs_cloud)}'
        f'<div class="rep-fc-funnel">{_funnel_flow_html(stage_flow or [], stage_reach, total_leads) or _funnel_line_html(stage_counts)}</div>'  # noqa: E501
        f'</div>'
        f'{_ad_tree_html(ad_funnel or [], media_to_ad or {}, ad_spend or {}, ads_synced_at, fb_business_id, fb_account_id, mappings=ad_mappings, suggestions=ad_suggestions, products=products, organic=organic)}'  # noqa: E501
        f'</div>'
    )


# ─── broker log page ──────────────────────────────────────────────────────────

_LOG_KIND = {
    "reply": {"ru": "ответ", "en": "reply"},
    "followup": {"ru": "follow-up", "en": "follow-up"},
    "translate": {"ru": "перевод", "en": "translate"},
    "alert": {"ru": "саммари алерта", "en": "alert summary"},
    "embed": {"ru": "эмбеддинг", "en": "embedding"},
    "embed:query": {"ru": "эмбед: поиск по базе (ответ боту)",
                    "en": "embed: KB search (per reply)"},
    "coach": {"ru": "правка базы (Coach)", "en": "KB edit (Coach)"},
    "suggest": {"ru": "черновик менеджеру", "en": "draft for manager"},
    "chat": {"ru": "chat", "en": "chat"},
}


def _log_kind_label(kind: str | None) -> str:
    if not kind:
        return "—"
    row = _LOG_KIND.get(kind)
    return row.get(current_lang(), row.get("en", kind)) if row else kind


def _group_broker_rows(rows: list) -> list[list]:
    """Cluster consecutive broker calls of the SAME thread within a short window into one
    'turn' (one reply/followup = an embed + the chat + guard verify + any regens). Rows are
    newest-first; a same-thread gap over the window (or a rows-without-thread call) starts a
    new cluster. Threads interleave in time, so a cluster pulls its calls together visually."""
    clusters: list[list] = []
    seen: dict[int, tuple[int, object]] = {}  # thread_id -> (cluster index, its last dt)
    window = timedelta(seconds=300)
    for r in rows:
        tid, dt = r.thread_id, _as_dt(r.created_at)
        prev = seen.get(tid) if tid is not None else None
        if prev is not None and dt is not None and prev[1] is not None \
                and timedelta() <= (prev[1] - dt) <= window:
            clusters[prev[0]].append(r)
        else:
            clusters.append([r])
            prev = (len(clusters) - 1, dt)
        if tid is not None:
            seen[tid] = (prev[0], dt)
    return clusters


def _log_group_header(cluster: list, tz_by_branch: dict[int, int]) -> str:
    """Summary band above a multi-call turn: thread, call count, END-TO-END wall-clock across
    all the calls, total tokens/cost, and a fail count — the per-reply view the flat rows lack."""
    tid = cluster[0].thread_id
    dts = [d for d in (_as_dt(r.created_at) for r in cluster) if d is not None]
    ends = [d + timedelta(milliseconds=int(r.latency_ms or 0))
            for r, d in zip(cluster, dts, strict=False)]
    span = (max(ends) - min(dts)).total_seconds() if dts else 0.0
    tok = sum(int(r.tokens_in or 0) + int(r.tokens_out or 0) for r in cluster)
    cost = sum(float(r.cost_usd or 0) for r in cluster)
    fails = sum(1 for r in cluster if not r.ok)
    cost_s = "free" if not cost else f"${cost:.4f}"
    fail_s = (f' · <span class="st-pill s-fail">{fails} fail</span>') if fails else ""
    chat = (f'<a class="oq-chat" hx-get="/ui/chat/{tid}" hx-target="#main" hx-push-url="true"'
            f' href="/ui/inbox" onclick="setOpenThread({tid})">#{tid}</a>')
    return (
        f'<tr style="background:rgba(120,140,170,.10)">'
        f'<td colspan="9" style="font-size:.72rem;color:#6b7685;padding:.25rem .5rem">'
        f'🧵 {chat} · {len(cluster)} calls · end-to-end '
        f'<b style="color:#3a4657">{span:.1f}s</b> · {tok} tok · {_h.escape(cost_s)}{fail_s}'
        f'</td></tr>'
    )


def _log_row(r: object, tz_by_branch: dict[int, int], grouped: bool = False) -> str:
    req, tid, kind, cap = r.request_id, r.thread_id, r.kind, r.capability
    model, ti, to, cost = r.model, r.tokens_in, r.tokens_out, r.cost_usd
    lat, ok, err, created = r.latency_ms, r.ok, r.error, r.created_at
    when = fmt_dt(created, "%m-%d %H:%M:%S", empty="—")  # MM-DD HH:MM:SS, viewer-local
    rid = f'#{_h.escape(str(req))}' if req else "—"
    chat = (f'<a class="oq-chat" hx-get="/ui/chat/{tid}" hx-target="#main"'
            f' hx-push-url="true" href="/ui/inbox" onclick="setOpenThread({tid})">#{tid}</a>'
            if tid else '<span style="color:#4a5568">—</span>')
    tok = int(ti or 0) + int(to or 0)
    cost_s = "free" if not cost else f"${float(cost):.4f}"
    lat_s = f"{int(lat) / 1000:.1f}s" if lat else "—"
    model_s = _h.escape((model or "—").split("/")[-1])
    fail = "" if ok else ' <span class="st-pill s-fail">fail</span>'
    title = f' title="{_h.escape(str(err)[:300])}"' if err else ""
    styles = "" if ok else "opacity:.6;"
    # a member of a multi-call turn gets a left accent so the group reads as one block
    if grouped:
        styles += "border-left:3px solid rgba(120,140,170,.5);"
    dim = f' style="{styles}"' if styles else ""
    return (
        f'<tr{dim}{title}>'
        f'<td style="font-family:ui-monospace,monospace;font-size:.68rem">{rid}</td>'
        f'<td style="color:#6b7685;font-size:.7rem;white-space:nowrap">{_h.escape(when)}</td>'
        f'<td style="font-size:.74rem">{_h.escape(_log_kind_label(kind))}{fail}</td>'
        f'<td style="font-size:.74rem">{chat}</td>'
        f'<td style="font-family:ui-monospace,monospace;font-size:.68rem;color:#8899aa">'
        f'{_h.escape(cap or "—")}</td>'
        f'<td style="font-family:ui-monospace,monospace;font-size:.68rem">{model_s}</td>'
        f'<td style="text-align:right;font-size:.7rem;color:#8899aa">{tok}</td>'
        f'<td style="text-align:right;font-size:.7rem">{_h.escape(cost_s)}</td>'
        f'<td style="text-align:right;font-size:.7rem;color:#6b7685">{_h.escape(lat_s)}</td>'
        f'</tr>'
    )


def _log_pager(page: int, size: int, total: int) -> str:
    pages = max(1, (total + size - 1) // size)
    cur = page + 1
    prev = (f'<button class="btn-sm" hx-get="/ui/settings/log?page={page - 1}"'
            f' hx-target="#main">← {_h.escape(t("log.prev"))}</button>' if page > 0
            else f'<span class="btn-sm" style="opacity:.35">← {_h.escape(t("log.prev"))}</span>')
    nxt = (f'<button class="btn-sm" hx-get="/ui/settings/log?page={page + 1}"'
           f' hx-target="#main">{_h.escape(t("log.next"))} →</button>' if cur < pages
           else f'<span class="btn-sm" style="opacity:.35">{_h.escape(t("log.next"))} →</span>')
    return (
        f'<div style="display:flex;gap:.6rem;align-items:center;margin:.6rem 0">'
        f'{prev}<span style="color:#6b7685;font-size:.72rem">{_h.escape(t("log.page"))} '
        f'{cur} / {pages} · {total} {_h.escape(t("log.total"))}</span>{nxt}</div>'
    )


def _log_histogram_html(
    buckets: list[float], turns: int, window: str, windows: list[str],
    since: object = None, bucket_span_s: float = 0.0, tz_off_h: int = 0,
) -> str:
    """Micro period-buttons (1h/4h/12h/24h/7d) + a COMPACT mini bar histogram of total
    end-to-end seconds per time bucket — a load/slowness sparkline. Each bar tooltips its
    time range + value; the axis is labelled at both ends (oldest → now)."""
    btns = "".join(
        (f'<span class="btn-sm" style="background:#3a4657;color:#fff;cursor:default">{w}</span>'
         if w == window else
         f'<button class="btn-sm" hx-get="/ui/settings/log?page=0&window={w}"'
         f' hx-target="#main">{w}</button>')
        for w in windows
    )
    start_dt = _as_dt(since)
    tz = timedelta(hours=tz_off_h)
    fmt = "%H:%M" if window in ("1h", "4h", "12h", "24h") else "%m-%d %H:%M"

    def _label(i: int) -> str:
        if start_dt is None:
            return "—"
        return (start_dt + tz + timedelta(seconds=bucket_span_s * i)).strftime(fmt)

    peak = max(buckets) if buckets else 0.0
    total = sum(buckets)
    span_min = bucket_span_s / 60
    # multi-line native tooltips: escape, then a literal &#10; per line break
    def _tip(text: str) -> str:
        return _h.escape(text).replace("\n", "&#10;")

    chart_tip = _tip(
        "Гистограмма нагрузки на брокер (генерация ответов).\n"
        f"Каждый столбик — интервал {span_min:.0f} мин; высота = суммарное end-to-end время "
        "всех ходов, начатых в этом интервале.\n"
        "Ход = все запросы одного чата к брокеру в пределах 5 минут "
        "(поиск по базе + генерация + проверка guard + перегенерации).\n"
        "Цвет: красный ≥66% пика, синий ≥33%, серо-голубой — низкая нагрузка.\n"
        "Высокие красные столбики = брокер отвечал медленно или было много ретраев.")
    if peak <= 0:
        chart = (f'<span title="{chart_tip}" style="color:#6b7685;font-size:.72rem'
                 f';cursor:help">нет данных за период</span>')
    else:
        def _bar(i: int, v: float) -> str:
            h = max(2, v / peak * 34)
            if v >= peak * 0.66:
                color, level = "#c0563a", "пиковая нагрузка"
            elif v >= peak * 0.33:
                color, level = "#5b7fa6", "средняя нагрузка"
            else:
                color, level = "#8aa0b8", "низкая нагрузка"
            tip = _tip(
                f"{_label(i)}–{_label(i + 1)}\n"
                f"Σ end-to-end: {v:.0f}s ({level})\n"
                "Суммарное время генерации ответов, начатых в этом интервале: от первого "
                "запроса к брокеру до конца последнего, включая ретраи и перегенерации.")
            return (f'<div title="{tip}" style="width:5px;height:{h:.0f}px;cursor:help'
                    f';background:{color};border-radius:1px 1px 0 0"></div>')
        bars = "".join(_bar(i, v) for i, v in enumerate(buckets))
        axis = (f'<div style="display:flex;justify-content:space-between;font-size:.6rem'
                f';color:#8899aa;margin-top:1px"><span>{_h.escape(_label(0))}</span>'
                f'<span>сейчас</span></div>')
        chart = (f'<div style="width:fit-content" title="{chart_tip}">'
                 f'<div style="display:flex;align-items:flex-end;gap:1px;height:38px">{bars}</div>'
                 f'{axis}</div>')
    info = (f'<span title="{chart_tip}" style="cursor:help;color:#8899aa;font-size:.66rem'
            f';border:1px solid #8899aa;border-radius:50%;width:13px;height:13px'
            f';display:inline-flex;align-items:center;justify-content:center'
            f';flex:none">?</span>')
    summary_tip = _tip(
        "Σ end-to-end — сумма всех столбиков за выбранный период.\n"
        "Ходов — сколько ответов бот сгенерировал за период.\n"
        "Пик — самый нагруженный интервал графика.")
    summary = (f'<span title="{summary_tip}" style="color:#6b7685;font-size:.72rem'
               f';white-space:nowrap;cursor:help">'
               f'Σ end-to-end <b style="color:#3a4657">{total:.0f}s</b> · {turns} ходов · '
               f'пик {peak:.0f}s</span>')
    return (
        f'<div style="display:flex;align-items:flex-end;gap:.7rem;margin:.4rem 0 .7rem'
        f';flex-wrap:wrap">'
        f'<div style="display:flex;gap:.25rem">{btns}</div>'
        f'{chart}{info}{summary}</div>'
    )


def broker_log_panel_html(
    rows: list, page: int, size: int, total: int, tz_by_branch: dict[int, int] | None = None,
    hist: tuple | None = None,
) -> str:
    title = _h.escape(t("log.title"))
    intro = _h.escape(t("log.intro"))
    tz = tz_by_branch or {}
    parts: list[str] = []
    for cluster in _group_broker_rows(rows):
        if len(cluster) > 1:
            parts.append(_log_group_header(cluster, tz))
            parts.extend(_log_row(r, tz, grouped=True) for r in cluster)
        else:
            parts.append(_log_row(cluster[0], tz))
    body = "".join(parts) or (
        f'<tr><td colspan="9" style="color:#4a5568">{_h.escape(t("log.empty"))}</td></tr>')
    def _th(label: str, hint_key: str, right: bool = False) -> str:
        style = ' style="text-align:right"' if right else ""
        return f'<th{style} data-help="{_h.escape(t(hint_key))}">{_h.escape(label)}</th>'

    head = (
        "<tr>"
        + _th("ID", "log.h.id")
        + _th(t("log.when"), "log.h.when")
        + _th(t("log.kind"), "log.h.kind")
        + _th(t("log.chat"), "log.h.chat")
        + _th("cap", "log.h.cap")
        + _th(t("log.model"), "log.h.model")
        + _th("tok", "log.h.tok", right=True)
        + _th(t("log.cost"), "log.h.cost", right=True)
        + _th(t("log.dur"), "log.h.dur", right=True)
        + "</tr>"
    )
    hist_html = _log_histogram_html(*hist) if hist is not None else ""
    return (
        f'<div class="ch"><span class="ch-n" data-help="{_h.escape(intro)}">{title}</span></div>'
        f'<div class="pnl-body">'
        f'<div class="hint">{intro}</div>'
        f'{hist_html}'
        f'{_log_pager(page, size, total)}'
        f'<table class="tbl"><thead>{head}</thead><tbody>{body}</tbody></table>'
        f'{_log_pager(page, size, total)}'
        f'</div>'
    )
