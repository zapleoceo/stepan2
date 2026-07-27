"""Who we may greet by name, and who is just "Kak".

An IG display name is whatever the account owner typed. Two shapes reached live chats before
this guard existed: a non-Latin script ("Halo Kak 福安祥!", 27.07) and a run-together English
phrase ("Kak YourFriends", "Kak PusatSkincareLG"). Both pass `.isalpha()` and carry no digit,
underscore or dot — and neither is anyone's given name. Being greeted by your own handle is
the fastest way to learn you are talking to a bot.
"""
from __future__ import annotations

import pytest

from app.modules.conversation.prompt import clean_first_name, lead_name_hint


@pytest.mark.parametrize("name", [
    "Wildan", "Khansa", "Marcella", "Farhan", "Vira",
    "Nurhasanah Putri",           # only the first word is used
    "José",                       # accented Latin still Latin
])
def test_real_given_names_are_used(name: str) -> None:
    assert clean_first_name(name) == name.split()[0]
    assert lead_name_hint(name) is not None


@pytest.mark.parametrize("handle", [
    "user8842", "vibecoding_id", "budi.santoso", "@someone",   # the original digit/punct tells
    "福安祥",                                                    # went out live on 27.07
    "Марина",                                                  # any non-Latin script
    "YourFriends", "PusatSkincareLG",                          # run-together brand/phrase
    "A",                                                       # too short to be a name
])
def test_handles_and_brands_are_not_names(handle: str) -> None:
    assert clean_first_name(handle) is None
    assert lead_name_hint(handle) is None


def test_no_name_at_all() -> None:
    assert clean_first_name(None) is None
    assert clean_first_name("   ") is None


def test_an_all_caps_name_is_still_a_name() -> None:
    """Shouty is not the same as fake — plenty of real profiles are typed in caps, and the
    CamelCase rule must not reach them."""
    assert clean_first_name("BUDI") == "BUDI"
