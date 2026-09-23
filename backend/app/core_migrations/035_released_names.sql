-- Names a rename or merge freed. activity.actor is never rewritten (the hash
-- chain covers it), so a new person or agent claiming a freed name would read
-- the earlier owner's ledger rows as their own history, and an agent actor
-- publishes them to every teammate's feed. users.refuse_released_name refuses
-- the name to every identity except `user_id`, the account whose history it
-- is (the renamed row, or a merge's target). Keyed on the folded name
-- (users.fold), the same comparison the roster uses.
CREATE TABLE released_names (
    folded text PRIMARY KEY,
    user_id bigint NOT NULL,
    released_at text NOT NULL
);
