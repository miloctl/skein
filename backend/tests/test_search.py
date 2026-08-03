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
    from app.services import collab, search

    qid = collab.ask_question("what latency matters?", asked_by="mira")["id"]
    hits = search.search(f"question {qid}")
    assert (hits[0]["entity"], hits[0]["entity_id"]) == ("question", qid)
    # bare #N means task, so a question id without its kind word is not a hit
    assert all(h["entity"] != "question" or h["entity_id"] != qid for h in search.search(f"#{qid}"))


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
