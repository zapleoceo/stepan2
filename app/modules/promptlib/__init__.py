"""Prompt library — the three-layer prompt model.

  CRAFT     code, shared by every branch (craft.py). No market in it.
  METHOD    branch DATA, cloned from the library — how to sell HERE.
  BUSINESS  branch DATA, always its own — persona, catalogue, facts, hours, currency.

The library (models.PromptLibraryItem) holds versioned persona / method / catalogue entries;
clone.py copies one into a branch's own rows; composer.py assembles a branch's prompt out of
what the branch actually holds, with no slug list in the code.
"""
