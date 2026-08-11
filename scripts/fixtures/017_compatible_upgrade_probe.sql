-- The upgrade rehearsal copies this additive migration into the simulated
-- next compatible core release. It proves code and schema drift, not only a
-- package version change.
CREATE TABLE compatible_upgrade_probe (
    installed_at TEXT NOT NULL
);
