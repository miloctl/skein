-- The reasoning level a person picked for one solo chat with /reasoning.
-- Empty = the team level or the model default (services/settings.py::turn_reasoning).
ALTER TABLE chat_threads ADD COLUMN reasoning TEXT NOT NULL DEFAULT '';
