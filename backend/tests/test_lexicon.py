"""The reader-facing name of every agent write, pinned against the registry."""

import pytest

from app.services import lexicon
from app.services.review import _registry


def _pairs():
    return {(e, a) for e, handlers in _registry().items() for a in handlers}


def test_every_registry_pair_has_a_phrase():
    """A new entity must not reach a person as a schema word. This is the
    guard the whole module exists for — without it the fallback quietly
    renders "update note_delete" in the authority card again."""
    missing = sorted(_pairs() - set(lexicon.CAPABILITY))
    assert missing == [], f"no reader-facing phrase for: {missing}"


def test_no_phrase_outlives_its_registry_pair():
    """The other direction: a phrase for a pair that no longer applies is a
    name for a capability the system does not have."""
    stale = sorted(set(lexicon.CAPABILITY) - _pairs())
    assert stale == [], f"phrase for a pair the registry dropped: {stale}"


def test_every_phrase_leads_with_a_verb():
    """A noun cannot express a residual — `blocker` registers create AND
    resolve, and "a blocker" hid the resolve. Articles are how nouns start,
    so they are what this rejects."""
    offenders = [p for p in lexicon.CAPABILITY.values() if p.split()[0] in {"a", "an", "the"}]
    assert offenders == []


def test_a_multi_action_entity_enumerates_its_verbs():
    """The authority matrix grants per ENTITY, so its row must show every
    capability the grant carries."""
    assert lexicon.entity_label("blocker") == "blockers (raise, resolve)"
    assert lexicon.entity_label("question") == "questions (answer, ask)"
    # a single-action entity reads as the capability itself
    assert lexicon.entity_label("note_delete") == "delete a note"


def test_every_multi_action_entity_has_a_plural():
    """Without one, entity_label falls back to the schema word and the raw
    identifier is back on the card."""
    multi = {e for e, _ in lexicon.CAPABILITY} & {
        e for e, handlers in _registry().items() if len(handlers) > 1
    }
    assert multi <= set(lexicon.PLURAL), f"no plural for: {sorted(multi - set(lexicon.PLURAL))}"


def test_one_function_keeps_one_name():
    """promise/update and promise_settle/update are the same function. Two
    names for one power let a reader forbid one and believe both closed."""
    assert lexicon.phrase("promise", "update") == lexicon.phrase("promise_settle", "update")


@pytest.mark.parametrize("entity", ["note_delete", "memory_forget", "event_cancel"])
def test_destructive_phrases_use_the_reserved_word(entity):
    """CLAUDE.md reserves `delete` for destruction and `forget` for memories.
    event_cancel is a hard DELETE, so "cancel" understated it."""
    p = lexicon.phrase(entity, "update")
    assert "delete" in p or "forget" in p, p
