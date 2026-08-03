-- The forge knot briefly used a team-wide predicate, so one person's push
-- tied the card for everyone and the zero-adoption rule read 100% adoption
-- off a single push. The predicate is None now, but a predicate cannot undo
-- rows it already wrote. Untie them. Whoever uses the feature ties it again.
--
-- feature_unlocks is per-person discovery state, not the provenance ledger,
-- so deleting from it breaks no hash chain.
DELETE FROM feature_unlocks WHERE knot = 'forge';
