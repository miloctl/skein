CREATE TABLE chat_folders (
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (owner, name)
);
