"""FTS indexing rules."""


def test_fts_entity_word_not_indexed(fresh_db):
    from app.services import search, work

    work.create_task("Optimize queries")
    assert search.search("task") == []  # entity name is not searchable
    assert search.search("optimize") != []
