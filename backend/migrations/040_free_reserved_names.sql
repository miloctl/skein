-- Roster rows holding a system-actor name predate the wall that now refuses
-- them. Left alone they are the worst of both doors: the name picker refuses
-- the person while their API key keeps writing, and every row they write is
-- visible to every viewer because the activity feed shows system actors to
-- everyone. Rename rather than delete, so their work keeps its author.
--
-- activity is untouched. Its rows are hash-chained, and rewriting an actor
-- would break verification permanently at the earliest row it touched. The
-- history keeps the old name, which is what actually happened.
UPDATE users
SET name = name || '-1'
WHERE lower(name) IN ('system', 'scheduler', 'team', 'forge')
  AND NOT EXISTS (SELECT 1 FROM users u2 WHERE u2.name = users.name || '-1');

-- the columns that attribute a row to a person, so renamed accounts keep
-- their work. Mirrors services/users.py::_ATTRIBUTION for these tables only:
-- the ones a person writes to directly.
UPDATE tasks SET assignee = assignee || '-1'
WHERE lower(assignee) IN ('system', 'scheduler', 'forge');
UPDATE api_keys SET owner = owner || '-1'
WHERE lower(owner) IN ('system', 'scheduler', 'team', 'forge');
