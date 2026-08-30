CREATE TABLE oidc_identities (
    issuer text NOT NULL CHECK (issuer <> ''),
    subject text NOT NULL CHECK (char_length(subject) BETWEEN 1 AND 255),
    user_id bigint NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    display_name text NOT NULL CHECK (char_length(display_name) BETWEEN 1 AND 64),
    origin text NOT NULL DEFAULT 'human'
        CHECK (origin IN ('human', 'agent', 'agent_verified')),
    created_by text NOT NULL,
    created_at text NOT NULL,
    PRIMARY KEY (issuer, subject),
    UNIQUE (issuer, user_id)
);
