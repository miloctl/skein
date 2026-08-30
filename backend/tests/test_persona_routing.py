"""Routing eval: does a realistic ask rank the right persona first?

consult_specialist and the @-picker route on bench descriptions alone, so
description quality IS routing quality. Each bench persona owns a case file
in tests/eval_routing/ with positive prompts (asks it must win) and negative
prompts (asks a named other persona must beat it on). The scorer is
deterministic TF-IDF over the live descriptions — keyless, so this runs in
the normal suite. Ported from the agent-skills evals runner (JS), Python so
one toolchain runs it.

The rank-1 ratchet holds the floor. When a description edit drops the rate,
fix the description, not the ratchet; raise the ratchet when the corpus
comfortably clears it.
"""

import json
import math
import re
from pathlib import Path

from app.services import personas

CASES_DIR = Path(__file__).parent / "eval_routing"
RANK1_FLOOR = 1.0

_SUFFIXES = ("ing", "ers", "er", "es", "ed", "s")

# the validator's stopword floor plus the filler that dominates typed asks —
# without this, "what" and "before" outscore every domain term
_STOPWORDS = personas._DESCRIPTION_STOPWORDS | frozenset(
    [
        "what",
        "before",
        "after",
        "must",
        "can",
        "help",
        "here",
        "there",
        "this",
        "that",
        "these",
        "those",
        "our",
        "your",
        "you",
        "them",
        "they",
        "was",
        "were",
        "will",
        "would",
        "out",
        "over",
        "under",
        "between",
        "into",
        "from",
        "does",
        "did",
        "doing",
        "make",
        "makes",
        "keep",
        "keeps",
        "walk",
        "through",
    ]
)


def _stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z]+", text.lower())
    return [_stem(w) for w in words if w not in _STOPWORDS and len(w) > 2]


def _rank(prompt: str, docs: dict[str, list[str]]) -> list[str]:
    """Slugs best-first by TF-IDF cosine of the prompt against each
    description. Ties break alphabetically so the eval is stable."""
    n = len(docs)
    df: dict[str, int] = {}
    for terms in docs.values():
        for t in set(terms):
            df[t] = df.get(t, 0) + 1
    idf = {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}
    query = _tokens(prompt)
    scores = {}
    for slug, terms in docs.items():
        tf = {t: terms.count(t) for t in set(terms)}
        norm = math.sqrt(sum((f * idf[t]) ** 2 for t, f in tf.items())) or 1.0
        scores[slug] = sum(tf.get(t, 0) * idf.get(t, 0.0) for t in query) / norm
    return sorted(scores, key=lambda s: (-scores[s], s))


def _bench_docs() -> dict[str, list[str]]:
    return {p["slug"]: _tokens(p["description"]) for p in personas.list_personas()}


def _load_cases() -> dict[str, dict]:
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(CASES_DIR.glob("*.json"))
    }


def test_every_bench_persona_has_a_case_file():
    """A persona without cases is a persona whose routing nobody measured —
    adding to the bench means adding to this corpus."""
    bench = {p["slug"] for p in personas.list_personas()}
    cases = set(_load_cases())
    assert bench == cases, (
        f"missing cases: {sorted(bench - cases)}; orphaned: {sorted(cases - bench)}"
    )


def test_positive_prompts_hold_the_rank1_floor():
    docs = _bench_docs()
    total, won, losses = 0, 0, []
    for slug, case in _load_cases().items():
        for prompt in case["positive"]:
            total += 1
            ranking = _rank(prompt, docs)
            if ranking[0] == slug:
                won += 1
            else:
                losses.append(f"{slug}: {prompt!r} went to {ranking[0]}")
    rate = won / total
    detail = "\n".join(losses)
    assert rate >= RANK1_FLOOR, f"rank-1 rate {rate:.2f} under {RANK1_FLOOR}:\n{detail}"


def test_negative_prompts_route_to_their_owner():
    """A negative is an ask that sounds adjacent. Its declared owner must rank
    first, or an unrelated third persona can win while this check stays green."""
    docs = _bench_docs()
    misses = []
    for slug, case in _load_cases().items():
        for neg in case.get("negative", []):
            ranking = _rank(neg["prompt"], docs)
            if ranking[0] != neg["owner"]:
                misses.append(
                    f"{slug}: expected {neg['owner']} on {neg['prompt']!r}, got {ranking[0]}"
                )
    assert not misses, "\n".join(misses)
