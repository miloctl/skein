-- Names a rename or merge freed. activity.actor is never rewritten (the hash
-- chain covers it), so a new person claiming a freed name would read the
-- earlier owner's ledger rows as their own history. users.refuse_released_name
-- refuses a new human row for any name here. Keyed on the folded name
-- (users.fold), the same comparison the roster uses.
CREATE TABLE released_names (
    folded text PRIMARY KEY,
    released_at text NOT NULL
);
