-- Theme prefs follow the person, not the browser: pack, colorway,
-- appearance, and custom hues as a small JSON blob on the user row.
ALTER TABLE users ADD COLUMN theme TEXT NOT NULL DEFAULT '';
