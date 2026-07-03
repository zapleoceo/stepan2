"""Raw IG thread-item parsing — the resilient content extractor (S1 parity)."""
from __future__ import annotations

from app.adapters.channels.ig_parse import (
    canonical_ig_media,
    clean_url,
    item_content,
    media_url,
)


def test_text_item_passes_through() -> None:
    c = item_content({"item_type": "text", "item_id": "1", "user_id": "42", "text": "hi"})
    assert c == {"text": "hi", "link_url": None, "preview_url": None,
                 "media_url": None, "media_kind": None}


def test_system_event_and_empty_text_are_dropped() -> None:
    assert item_content({"item_type": "action_log"}) is None
    assert item_content({"item_type": "text", "text": "  "}) is None


def test_media_photo_video_voice_gif() -> None:
    photo = item_content({"item_type": "media",
                          "media": {"image_versions2": {"candidates": [{"url": "http://c/p.jpg"}]}}})
    assert photo["media_kind"] == "image" and photo["media_url"] == "http://c/p.jpg"
    assert photo["text"] == "🖼 media"

    video = item_content({"item_type": "media",
                          "media": {"video_versions": [{"url": "http://c/v.mp4"}]}})
    assert video["media_kind"] == "video" and video["media_url"] == "http://c/v.mp4"

    voice = item_content({"item_type": "voice_media",
                          "voice_media": {"media": {"audio": {"audio_src": "http://c/a.m4a"}}}})
    assert voice["media_kind"] == "audio" and voice["text"] == "🎤 voice"

    gif = item_content({"item_type": "animated_media",
                        "animated_media": {"images": {"fixed_height": {"url": "http://c/g.gif"}}}})
    assert gif["media_kind"] == "image" and gif["text"] == "GIF"


def test_shared_link_unwraps_and_captions() -> None:
    c = item_content({"item_type": "xma_link", "xma_link": [{
        "target_url": "https://l.instagram.com/?u=https%3A%2F%2Fexample.com%2Fx&e=1",
        "link_context": {"link_image_url": "http://c/prev.jpg"}}]})
    assert c["link_url"] == "https://example.com/x"      # wrapper stripped
    assert c["preview_url"] == "http://c/prev.jpg"
    assert c["text"] == "🔗 https://example.com/x"        # no card text → the url itself


def test_shared_reel_canonicalized_with_media_placeholder() -> None:
    c = item_content({"item_type": "xma_clip", "xma_clip": [{
        "target_url": "https://www.instagram.com/reel/ABC123/?igshid=zzz"}]})
    assert c["link_url"] == "https://www.instagram.com/reel/ABC123/"
    assert c["text"] == "🎬 reel"


def test_xma_card_title_becomes_text() -> None:
    c = item_content({"item_type": "xma_media_share",
                      "xma_media_share": [{"header_title_text": "Cool Post"}]})
    assert c["text"] == "📷 Cool Post"


def test_canonical_media_uses_item_type_not_path() -> None:
    # a sponsored post shared under a /reel/ URL must canonicalize to /p/ (path lies)
    assert canonical_ig_media(
        "https://www.instagram.com/reel/CODE/", "xma_media_share"
    ) == "https://www.instagram.com/p/CODE/"
    # a genuine clip share stays /reel/
    assert canonical_ig_media(
        "https://www.instagram.com/reel/CODE/", "xma_clip"
    ) == "https://www.instagram.com/reel/CODE/"
    # non-IG url untouched
    assert canonical_ig_media("https://youtu.be/x") == "https://youtu.be/x"


def test_clean_url_handles_none_and_wrapper() -> None:
    assert clean_url(None) is None
    assert clean_url("") is None
    assert clean_url("https://l.instagram.com/?u=https%3A%2F%2Fa.com%2Fp") == "https://a.com/p"


def test_media_url_none_for_shared_post() -> None:
    assert media_url({"item_type": "xma_media_share", "xma_media_share": [{}]}) is None
