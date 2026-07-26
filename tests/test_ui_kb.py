

def test_the_save_button_is_reachable_without_scrolling_past_the_textarea() -> None:
    """It was always in the markup, and always invisible. Two reasons, both from the accordion:
    a collapsed document shows nothing at all, and in the OPEN one the button sat under a
    textarea sized from the document — facts_policy is 15.7k chars, so 48 rows, so ~1500px
    below the fold. Stuck to the bottom of the scrolling panel instead."""
    from app.api._ui_html import _CSS
    from app.api._ui_kb import kb_editor_html

    html = kb_editor_html(7, "facts_policy", "Политика", "x" * 15000, "dima")
    assert 'class="kb-save-bar"' in html
    assert 'id="kb-save-7"' in html
    # …and the rule that actually pins it, in the stylesheet that ships with the page.
    assert ".kb-save-bar{position:sticky" in _CSS


def _docs(n: int = 3) -> list:
    # (id, slug, title, content, category, sort_order, updated_by, branch_name)
    return [(i, f"doc_{i}", f"Doc {i}", "x" * 15000, "playbook", i, "dima", "Jakarta")
            for i in range(1, n + 1)]


def test_the_all_documents_view_has_exactly_one_scroller() -> None:
    """.pnl-body is the page's only scroller — flex:1 + overflow-y:auto as a direct flex child
    of #main, which is itself overflow:hidden. The first accordion version left every editor
    carrying its own .pnl-body INSIDE its <details>, which took them out of that flex chain:
    nothing scrolled, and #main clipped everything below the first textarea. The save button
    was not merely low on the page, it was cut off, along with documents two onwards."""
    from app.api._ui_kb import kb_all_html

    html = kb_all_html(_docs())
    assert html.count("pnl-body") == 1
    assert html.startswith('<div class="pnl-body">')
    assert html.endswith("</div>")
    # every document still has its own form and its own save button
    for i in (1, 2, 3):
        assert f'id="kb-form-{i}"' in html
        assert f'id="kb-save-{i}"' in html


def test_the_single_document_view_keeps_its_own_scroller() -> None:
    """/ui/knowledge/<id>/edit renders one editor straight into #main, so there it must still
    supply the .pnl-body itself."""
    from app.api._ui_kb import kb_editor_html

    assert 'class="pnl-body"' in kb_editor_html(1, "s", "T", "body")
    assert "pnl-body" not in kb_editor_html(1, "s", "T", "body", nested=True)
