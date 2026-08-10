-- Which engagement a memory belongs to, and where it came from.
--
-- A memory is injected into every future conversation's system prompt, which
-- makes it the highest-leverage write in the app. Until now it carried no
-- subject: a fact learned while working one engagement was recalled while
-- working every other one, so the useful ones were diluted by the irrelevant
-- ones and nobody could tell which was which.
--
-- `source_kind` and `source_id` record what the memory was FILED FROM — a chat
-- thread, an artifact, a decision. Not for display: for the reader who asks
-- "where did this come from" six weeks later, which is the question that
-- decides whether a memory still holds. `provenance.py` already answers that
-- for every other row from its own columns. A memory's answer lives here
-- because its source is not a row it was converted from.
--
-- NULL engagement means what it has always meant: a fact about the team, not
-- about one piece of work. `memory.recall` keeps returning those to every
-- conversation, and an engagement's own memories are added to that, never
-- substituted for it.
ALTER TABLE memories ADD COLUMN engagement_id INTEGER REFERENCES engagements(id);
ALTER TABLE memories ADD COLUMN source_kind TEXT NOT NULL DEFAULT '';
ALTER TABLE memories ADD COLUMN source_id TEXT NOT NULL DEFAULT '';

-- recall reads by engagement on every turn of a linked chat, so the lookup is
-- by that column. Partial, because most memories carry no engagement and an
-- index over their NULLs would be most of the table for no reader.
CREATE INDEX IF NOT EXISTS idx_memories_engagement
    ON memories (engagement_id) WHERE engagement_id IS NOT NULL;
