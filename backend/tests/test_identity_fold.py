"""fold_identity is the ONE normalization every ownership comparison uses
(services/users.py::fold, crews, the skfold SQL function). These properties
hold for all input, not chosen examples: a string class that folded
unstably would let two spellings of one name hold two identities."""

import unicodedata

from hypothesis import given
from hypothesis import strategies as st

from app.identity_names import fold_identity

_names = st.text(max_size=60)

# every format character (category Cf): ZWJ, ZWNJ, bidi controls, soft hyphen
_format_chars = st.characters(categories=["Cf"])


@given(_names)
def test_folding_is_idempotent(name):
    once = fold_identity(name)
    assert fold_identity(once) == once


@given(_names)
def test_folding_ignores_case(name):
    assert fold_identity(name.upper()) == fold_identity(name.lower())


@given(_names)
def test_folding_ignores_compatibility_forms(name):
    # a fullwidth or ligature spelling renders as the plain one, so it must
    # BE the plain one — this is the fullwidth-TEAM case from the docstring
    for form in ("NFC", "NFD", "NFKC", "NFKD"):
        assert fold_identity(unicodedata.normalize(form, name)) == fold_identity(name)


@given(name=_names, zw=_format_chars, at=st.integers(min_value=0, max_value=60))
def test_an_invisible_character_never_makes_a_new_identity(name, zw, at):
    pos = min(at, len(name))
    smuggled = name[:pos] + zw + name[pos:]
    assert fold_identity(smuggled) == fold_identity(name)


@given(_names)
def test_surrounding_whitespace_is_not_identity(name):
    assert fold_identity(f"  {name}\t") == fold_identity(name)
