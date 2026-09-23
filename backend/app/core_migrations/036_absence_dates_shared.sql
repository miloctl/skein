-- The team plans around a private absence only when the person away chose
-- "the team sees that I am away" (services/absences.py::TEAM_SEES_DATES).
-- FALSE for every existing row: a private window's dates reached capacity,
-- planning and the weekly draft although nobody chose to share them.
ALTER TABLE absences ADD COLUMN dates_shared boolean NOT NULL DEFAULT FALSE;
