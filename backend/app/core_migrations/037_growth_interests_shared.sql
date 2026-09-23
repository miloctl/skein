-- Growth interests reach a teammate only after their person shares them
-- (users.share_growth_interests). FALSE for every existing row: the
-- interests were written for "staffing what-ifs" and reached every caller.
ALTER TABLE users ADD COLUMN growth_shared boolean NOT NULL DEFAULT FALSE;
