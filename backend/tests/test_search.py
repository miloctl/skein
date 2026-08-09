"""FTS indexing rules and the short-id fast path."""


def test_fts_entity_word_not_indexed(fresh_db):
    from app.services import search, work

    work.create_task("Optimize queries")
    assert search.search("task") == []  # entity name is not searchable
    assert search.search("optimize") != []


def test_short_id_jumps_to_the_row(fresh_db):
    from app.services import search, work

    tid = work.create_task("Optimize queries")["id"]
    for q in (f"#{tid}", f"task {tid}", f"Task #{tid}", f"task#{tid}"):
        hits = search.search(q)
        assert (hits[0]["entity"], hits[0]["entity_id"]) == ("task", tid), q


def test_short_id_names_the_entity_kind(fresh_db):
    from app.services import collab, search, work

    qid = collab.ask_question("what latency matters?", asked_by="mira")["id"]
    hits = search.search(f"question {qid}")
    assert (hits[0]["entity"], hits[0]["entity_id"]) == ("question", qid)
    # bare #N means task. Assert it against a REAL collision — a task and a
    # question that share an id — or the negative iterates an empty list and
    # proves nothing
    tid = work.create_task("Fix login")["id"]
    assert tid == qid
    bare = search.search(f"#{qid}")
    assert (bare[0]["entity"], bare[0]["entity_id"]) == ("task", tid)


def test_the_short_id_hit_ranks_first_and_counts_against_the_limit(fresh_db):
    from app.services import search, work

    target = work.create_task("dashboard rebuild final")["id"]
    # decoys whose TEXT contains the same phrase, so FTS returns real
    # competition for the direct hit to be ranked above and counted with
    for n in range(6):
        work.create_task(f"follow-up {n} for task {target}")
    hits = search.search(f"task {target}", limit=3)
    assert len(hits) == 3
    assert (hits[0]["entity"], hits[0]["entity_id"]) == ("task", target)


def test_reindexing_a_record_replaces_its_row(fresh_db):
    from app.services import search, work

    tid = work.create_task("dashboard rebuild")["id"]
    work.update_task(tid, description="dashboard rebuild continues", actor="mira")
    hits = [h for h in search.search("dashboard") if h["entity_id"] == tid]
    assert len(hits) == 1


def test_short_id_unknown_kind_or_missing_id_falls_through(fresh_db):
    from app.services import search, work

    tid = work.create_task("Optimize queries")["id"]
    assert search.search(f"task {tid + 900}") == []
    assert search.search(f"banana {tid}") == []
    # separator required: a literal token like "task42" is not a ref
    assert search.search(f"task{tid}") == []
    # oversized id is an FTS miss, never an integer error
    assert search.search("#" + "9" * 25) == []


def test_short_id_dedupes_against_fts(fresh_db):
    from app.services import search, work

    tid = work.create_task("Optimize queries")["id"]
    work.update_task(tid, description=f"task {tid} tracks the slow dashboard")
    hits = search.search(f"task {tid}")
    assert [(h["entity"], h["entity_id"]) for h in hits].count(("task", tid)) == 1


def test_deindex_removes_the_row_from_search_results(fresh_db):
    from app.services import collab, search

    n = collab.save_note(topic="ghost", content="ectoplasm findings", author="a")
    assert search.search("ectoplasm")
    collab.delete_note(n["id"], actor="a")
    # the docstring's promise: search must never cite a record that no
    # longer exists — the FTS row goes with the record, not only the vector
    assert search.search("ectoplasm") == []


def test_ask_falls_back_to_word_overlap_when_the_phrase_misses(fresh_db):
    from app.services import blockers, search

    blockers.raise_blocker("Vendor contract unsigned", detail="blocks the integration")
    # the phrase matches nothing, so the natural question would come back
    # empty without the fallback — this IS what the `?` prefix buys
    assert search.search("why is the vendor contract blocked") == []
    answer = search.ask("why is the vendor contract blocked")
    assert [c["ref"] for c in answer["citations"]] == ["blocker #1"]
    assert "word overlap" in answer["note"]


def test_ask_does_not_widen_a_question_on_its_function_words(fresh_db):
    from app.services import search, work

    # every one of these carries "the" and nothing else in common with the
    # question. Before the stopword filter they all came back, because `the`
    # was OR'd in and matches most rows anyone ever writes.
    for title in ("Interview the requester", "Build the happy path", "Rebuild the wiki"):
        work.create_task(title)
    answer = search.ask("why is the pager quiet")
    assert answer["citations"] == []
    assert answer["note"] == "nothing indexed matches — try different words"


def test_ask_keeps_a_status_word_the_team_actually_searches_for(fresh_db):
    from app.services import search, work

    # `done` reads like a function word and is not: it is a task status.
    # Stopwording it would make one of the commonest questions unanswerable.
    work.create_task("Ship the done-list export")
    answer = search.ask("what is done here")
    assert [c["ref"] for c in answer["citations"]] == ["task #1"]


def test_ask_tries_the_one_meaningful_word_left_in_a_question(fresh_db):
    from app.services import collab, search

    collab.save_note(topic="skein", content="many strands, one formation", author="a")
    # "what is skein" strips to a single word. The old guard needed two and
    # skipped the fallback entirely, so a three-word question answered nothing.
    answer = search.ask("what is skein")
    assert [c["ref"] for c in answer["citations"]] == ["note #1"]
