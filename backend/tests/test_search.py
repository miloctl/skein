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
