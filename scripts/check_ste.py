#!/usr/bin/env python3
"""ASD-STE100 gate for the field guide's `how:` strings.

Ported from ~/external/SimpleEnglish/evals/ste_lint.py. Counts the
violations a regex can catch: sentence length, contractions, banned modals,
perfect tenses, "-ing" clauses, semicolons, Latin abbreviations, slop words,
trailing conditions, synonym rotation. The rotation sets carry Skein's
reserved words (CLAUDE.md "one word per concept"): check/verify/reconfirm
and delete/forget are different concepts, so one text using two of them is
drifting.

Scope is deliberately `how:` only. `pitch:` lines carry the product voice on
purpose (fieldguide/knots.yaml documents the split by key), so linting them
would enforce the standard exactly where the conventions waive it.

Known ceiling: a regex pass, not a grammar parser. It undercounts (no
passive-voice detection) and can miscount sentence bounds in unusual
markdown. A zero here is not a compliance verdict; a nonzero is always
worth reading.

Usage:
  python3 scripts/check_ste.py                 # gate backend/fieldguide/knots.yaml
  python3 scripts/check_ste.py --self-test
"""

import re
import sys
from pathlib import Path

BANNED_MODALS = re.compile(r"\b(should|would|may|might|could)\b", re.I)
PERFECT = re.compile(r"\b(has|have|had)\s+been\b|\b(has|have)\s+\w+ed\b", re.I)
CONTRACTION = re.compile(r"\b\w+(n't|'ll|'re|'ve|'d)\b|\bit's\b|\byou're\b", re.I)
ING_CLAUSE = re.compile(
    r",\s*(mak|allow|enabl|ensur|highlight|creat|provid|offer|help|reduc|improv|lead|caus|result)ing\b",
    re.I,
)
LATIN = re.compile(r"\b(e\.g\.|i\.e\.|etc\.?)(?=[\s,)]|$)", re.I)
SLOP = re.compile(
    r"\b(simply|seamlessly|effortlessly|robust|leverag\w*|utiliz\w*|"
    r"comprehensive|powerful|blazingly|streamlin\w*|facilitat\w*|"
    r"performant|plethora|myriad|delve|crucial|pivotal)\b",
    re.I,
)
TRAILING_COND = re.compile(r"\w[^.!?\n]{3,}\s\b(if|when)\b\s", re.I)
ROTATION_SETS = [
    # check = user action, verify = provenance, reconfirm = charter;
    # validate/ensure are the drift words that erode all three
    ("check-verify", re.compile(r"\b(check|verify|confirm|validate|ensure)\w*\b", re.I)),
    # delete = destruction, forget = memories
    ("delete-forget", re.compile(r"\b(delete|forget|remove|erase)\w*\b", re.I)),
    ("config-settings", re.compile(r"\b(config|configuration|settings)\b", re.I)),
]
SENTENCE_LIMIT = 20  # procedural text; `how:` is always instructions


def strip_code(text):
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`[^`\n]+`", " CODESPAN ", text)  # one word per Rule 8.6
    text = re.sub(r"^#+\s.*$", " ", text, flags=re.M)  # headings exempt (titles, 8.6)
    text = re.sub(r"https?://\S+", " URL ", text)
    # shortcut TOKENS (the ⌘K convention knots.yaml documents) and in-app
    # links read as one word each, not prose
    text = re.sub(r"[⌘⇧⌥⌃]\S*", " KEY ", text)
    text = re.sub(r"(?<=\s)/[\w/-]+", " LINK ", text)
    return text


def sentences(text):
    text = re.sub(r"^\s*([-*]|\d+\.)\s+", "", text, flags=re.M)  # list markers
    parts = re.split(r"(?<=[.!?:])\s+", text)
    return [p.strip() for p in parts if len(p.strip().split()) >= 2]


def lint(text):
    body = strip_code(text)
    sents = sentences(body)
    counts = {}
    lengths = [len(s.split()) for s in sents]
    counts["sentence_over_limit"] = sum(1 for n in lengths if n > SENTENCE_LIMIT)
    counts["contraction"] = len(CONTRACTION.findall(body))
    counts["banned_modal"] = len(BANNED_MODALS.findall(body))
    counts["perfect_tense"] = len(list(PERFECT.finditer(body)))
    counts["ing_clause"] = len(ING_CLAUSE.findall(body))
    counts["semicolon"] = body.count(";")
    counts["latin_abbrev"] = len(LATIN.findall(body))
    counts["slop_word"] = len(SLOP.findall(body))
    counts["trailing_condition"] = sum(
        1 for s in sents if TRAILING_COND.search(s) and not re.match(r"^(if|when)\b", s, re.I)
    )
    rotation = 0
    for _, rx in ROTATION_SETS:
        stems = {m.group(1).lower().rstrip("s") for m in rx.finditer(body)}
        if len(stems) > 1:
            rotation += len(stems) - 1
    counts["synonym_rotation"] = rotation
    return counts


def check_knots(path):
    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    failures = []
    for card in data.get("knots") or []:
        knot_id = card.get("id", "?")
        how = card.get("how", "")
        if not how:
            continue
        counts = lint(how)
        for kind, n in counts.items():
            if n:
                failures.append(f"{knot_id}: {kind} x{n}")
    return failures


SLOP_FIXTURE = (
    "Leveraging our robust retry mechanism, failed uploads are automatically\n"
    "reattempted, ensuring data integrity is maintained throughout the entire process which has\n"
    "been designed from the ground up to gracefully handle even the most challenging network\n"
    "interruptions. You should verify your credentials; it's also worth checking the settings,\n"
    "e.g. the timeout config. Contact support if the problem persists."
)

CLEAN_FIXTURE = (
    "The system retries a failed upload automatically. This process keeps the data correct.\n\n"
    "If failures continue, make sure that your credentials are correct."
    " If the problem continues, contact support."
)


def _expect(ok, context):
    if not ok:
        raise SystemExit(f"self-test failed: {context}")


def self_test():
    slop = lint(SLOP_FIXTURE)
    clean = lint(CLEAN_FIXTURE)
    for kind, floor in [
        ("sentence_over_limit", 1),
        ("banned_modal", 1),
        ("contraction", 1),
        ("perfect_tense", 1),
        ("ing_clause", 1),
        ("latin_abbrev", 1),
        ("slop_word", 2),
        ("trailing_condition", 1),
        ("synonym_rotation", 2),  # check-verify AND config-settings
    ]:
        _expect(slop[kind] >= floor, (kind, slop))
    _expect(slop["semicolon"] == 1, slop)
    _expect(sum(clean.values()) == 0, clean)
    print("self-test OK:", sum(slop.values()), "violations in slop fixture, 0 in clean")


def main():
    if "--self-test" in sys.argv[1:]:
        self_test()
        return
    root = Path(__file__).resolve().parent.parent
    failures = check_knots(root / "backend" / "fieldguide" / "knots.yaml")
    if failures:
        print("STE violations in knots.yaml how: strings (the STE half of the")
        print("pitch/how split — fix the wording, docs/LEXICON.md has the terms):")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    print(f"knots.yaml how: strings pass ({len(failures)} violations)")


if __name__ == "__main__":
    main()
