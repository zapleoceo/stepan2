

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
