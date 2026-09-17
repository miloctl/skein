-- The model a person picked for one solo chat with /model. Empty = the team
-- pick or the deployment default (agents/team_agent.py::model_in_force).
ALTER TABLE chat_threads ADD COLUMN model_id TEXT NOT NULL DEFAULT '';
