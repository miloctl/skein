-- Per-crew context packs (docs/VISIBILITY.md).
--
-- No 12-step table rebuild. UNIQUE(version) was a standalone index, not a
-- table constraint, so DROP INDEX plus a new expression index changes the key
-- without touching the rows. The existing packs keep crew_id NULL, which is
-- the team pack the whole roster already reads.
--
-- IFNULL(crew_id, 0), not crew_id: SQLite treats every NULL as distinct in a
-- UNIQUE index, so a bare (crew_id, version) would let two team packs share
-- version 1 and break the concurrent-publisher recovery in
-- services/context_pack.py::publish_pack, which reads the version back through
-- latest_pack.
--
-- No semicolon inside a comment. db.py::_statements splits on it with no
-- comment awareness, and the tail half becomes a statement. An apostrophe is
-- fine -- `--` runs to end of line, and SQLite opens no string literal there.

-- No IF EXISTS. 001_baseline.sql:308 creates this index, so the only way the
-- DROP fails is a database where it was removed by hand. Recreate it by hand
-- too: init_db walks migrations in filename order and stops at the first
-- failure, so a later-numbered migration never runs (CLAUDE.md says the same
-- about renames).
DROP INDEX ux_context_packs_version;

ALTER TABLE context_packs ADD COLUMN crew_id INTEGER REFERENCES crews(id);

CREATE UNIQUE INDEX ux_context_packs_crew_version
    ON context_packs (IFNULL(crew_id, 0), version);
