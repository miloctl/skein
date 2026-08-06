-- Crews: durable groups of people, the membership a visibility tier reads
-- (docs/VISIBILITY.md). Nothing in this migration grants or restricts
-- anything -- the tier columns land in a later file, and until they do a
-- crew is a roster with a name.
--
-- Not engagements, which already join people to work: an engagement reaches
-- `closed`, allocates a percent, and carries a date window, so access built
-- on one expires the moment the work ships. Membership here is durable and
-- binary.
--
-- Not named `team` either. That word is a SYSTEM_ACTORS entry
-- (services/activity.py), the notifications broadcast address whose rows the
-- first reader clears for everyone (services/notifications.py), and a
-- promises.audience value. A table of things people can name would collide
-- with all three.
--
-- No semicolons in this file, comments included -- db.py::_statements splits
-- on them and the tail half is a syntax error at startup.
CREATE TABLE crews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    summary TEXT NOT NULL DEFAULT '',
    -- deactivate rather than delete, the same word and the same shape as
    -- users.active: a crew that
    -- scoped rows must keep resolving after it stops being used, or those
    -- rows name a crew id that no longer exists
    active INTEGER NOT NULL DEFAULT 1,
    origin TEXT NOT NULL DEFAULT 'human',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- The engagements precedent (ux_engagements_name_nocase in 001_baseline).
-- services/crews.py serializes its own pre-check and insert inside one
-- BEGIN IMMEDIATE, so this index is the backstop for a caller that inserts
-- outside a transaction, not the guard against that race. NOCASE is
-- ASCII-only, so it does not catch `Café` against `CAFÉ` -- crews.py folds
-- the name in Python the way users.py::refuse_fold_collision does.
CREATE UNIQUE INDEX ux_crews_name_nocase ON crews (name COLLATE NOCASE);

CREATE TABLE crew_members (
    crew_id INTEGER NOT NULL REFERENCES crews(id) ON DELETE CASCADE,
    -- a roster name, matching every other person column in the schema. There
    -- are no foreign keys to users(name) anywhere here, so services/users.py
    -- ::_ATTRIBUTION carries this column and rename_user moves it -- left out
    -- of that map, a rename orphans the membership and the person silently
    -- loses every crew row they could see.
    person TEXT NOT NULL,
    -- a steward edits this crew's membership. It is NOT an authorization tier
    -- of its own: AdminUser stays deployment-wide, and a steward administers
    -- nothing outside these rows.
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('member', 'steward')),
    -- provenance, like the allocations row this most resembles: membership
    -- decides what a person reads, so "who put them here, through which
    -- path" is the question asked after an incident
    origin TEXT NOT NULL DEFAULT 'human',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (crew_id, person)
);

-- "which crews is this person in" runs on every scoped read once the tier
-- lands, and the primary key above leads with crew_id, so it cannot serve it.
-- (person, crew_id) rather than (person): crews_of orders by crew_id, and the
-- single-column form makes SQLite build a temp b-tree for that sort. The pair
-- is covering, so the query never touches a row.
CREATE INDEX idx_crew_members_person ON crew_members (person, crew_id);
