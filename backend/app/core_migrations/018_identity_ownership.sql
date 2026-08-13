-- Agent kind is not enough to prove who owns a machine identity. A persona,
-- delegated helper, specialist, service, and MCP actor can all be agents but
-- must not silently exchange prompts, authority, or provenance.
--
-- Existing rows stay blank for one compatibility classification at startup.
-- New reservations always write an explicit owner.
ALTER TABLE users ADD COLUMN identity_owner TEXT NOT NULL DEFAULT '';

UPDATE users SET identity_owner = 'human' WHERE kind = 'human';
UPDATE users SET identity_owner = 'generic-agent' WHERE kind = 'agent';
UPDATE users SET identity_owner = 'core' WHERE name = 'agent' AND kind = 'agent';

-- These names are owned by the stock content shipped with this core. Private
-- overlay ownership is not inferable from an old agent row and must remain
-- explicit, so it is not guessed here.
UPDATE users SET identity_owner = 'content'
WHERE kind = 'agent' AND name IN (
    'backend-architect',
    'code-reviewer',
    'delivery',
    'engineering',
    'growth-mentor',
    'incident-commander',
    'meeting-notes',
    'minimal-change-engineer',
    'onboarding-guide',
    'project-shepherd',
    'sprint-prioritizer',
    'training-designer'
);
