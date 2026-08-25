ALTER TABLE chat_threads
    ADD COLUMN kind text NOT NULL DEFAULT 'solo'
        CHECK (kind IN ('solo', 'shared')),
    ADD COLUMN created_by text NOT NULL DEFAULT '',
    ADD COLUMN archived_at text;

UPDATE chat_threads SET created_by = owner WHERE created_by = '';

ALTER TABLE chat_messages
    ADD COLUMN author_kind text NOT NULL DEFAULT 'legacy'
        CHECK (author_kind IN ('legacy', 'human', 'agent', 'system')),
    ADD COLUMN author text NOT NULL DEFAULT '',
    ADD COLUMN turn_id text NOT NULL DEFAULT '',
    ADD COLUMN reply_to_message_id bigint REFERENCES chat_messages(id) ON DELETE SET NULL,
    ADD COLUMN client_key text NOT NULL DEFAULT '';

UPDATE chat_messages AS message
SET author_kind = 'human', author = thread.owner
FROM chat_threads AS thread
WHERE message.thread_id = thread.id AND message.role = 'user';

CREATE UNIQUE INDEX uq_chat_messages_client_key
    ON chat_messages (thread_id, author, client_key)
    WHERE client_key != '';

CREATE TABLE chat_members (
    thread_id text NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    person text NOT NULL,
    role text NOT NULL DEFAULT 'member'
        CHECK (role IN ('steward', 'member')),
    joined_at text NOT NULL,
    left_at text,
    added_by text NOT NULL,
    last_read_message_id bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (thread_id, person)
);

CREATE INDEX idx_chat_members_person_active
    ON chat_members (person, thread_id)
    WHERE left_at IS NULL;

CREATE TABLE chat_invitations (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    thread_id text NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    person text NOT NULL,
    invited_by text NOT NULL,
    share_history boolean NOT NULL DEFAULT TRUE CHECK (share_history),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'accepted', 'declined', 'revoked')),
    created_at text NOT NULL,
    responded_at text
);

CREATE UNIQUE INDEX uq_chat_invitations_pending
    ON chat_invitations (thread_id, person)
    WHERE status = 'pending';

CREATE INDEX idx_chat_invitations_person_pending
    ON chat_invitations (person, id)
    WHERE status = 'pending';
