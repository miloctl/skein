-- New writes must use one canonical row shape. NOT VALID keeps startup possible
-- when an older database already contains a row that the verifier must report.
ALTER TABLE activity ADD CONSTRAINT activity_positive_seq
    CHECK (seq IS NULL OR seq > 0) NOT VALID;
ALTER TABLE activity ADD CONSTRAINT activity_detail_present
    CHECK (detail IS NOT NULL) NOT VALID;
ALTER TABLE activity ADD CONSTRAINT activity_chain_shape
    CHECK ((
        (seq IS NULL AND hash IS NULL AND prev_hash IS NULL)
        OR (seq = 1 AND hash ~ '^[0-9a-f]{64}$' AND prev_hash IS NULL)
        OR (
            seq > 1
            AND hash ~ '^[0-9a-f]{64}$'
            AND prev_hash ~ '^[0-9a-f]{64}$'
        )
    ) IS TRUE) NOT VALID;

-- These marks belonged to the verifier before this migration. Replace a
-- sequence that is absent or not past the current tail. Keep a larger old
-- sequence as truncation evidence, but remove its unpaired digest.
DELETE FROM app_settings
WHERE key = 'activity_chain_high_hash'
   OR (
       key = 'activity_chain_high_seq'
       AND CASE
           WHEN value ~ '^(0|[1-9][0-9]{0,17})$' THEN
               value::bigint <= (SELECT COALESCE(MAX(seq), 0) FROM activity)
           WHEN value ~ '^[1-9][0-9]{18}$' AND value <= '9223372036854775807' THEN
               value::bigint <= (SELECT COALESCE(MAX(seq), 0) FROM activity)
           ELSE FALSE
       END
   );

-- These marks now belong to the append path. A verifier reads them but never
-- creates or advances them, so a direct activity write cannot bless itself.
INSERT INTO app_settings (key, value, updated_at)
SELECT
    'activity_chain_high_seq',
    seq::text,
    to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS') || '+00:00'
FROM activity
WHERE seq = (SELECT MAX(seq) FROM activity WHERE seq IS NOT NULL)
  AND hash ~ '^[0-9a-f]{64}$'
  AND (
      (seq = 1 AND prev_hash IS NULL)
      OR (seq > 1 AND prev_hash ~ '^[0-9a-f]{64}$')
  )
ON CONFLICT (key) DO NOTHING;

INSERT INTO app_settings (key, value, updated_at)
SELECT
    'activity_chain_high_hash',
    hash,
    to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS') || '+00:00'
FROM activity
WHERE seq = (SELECT MAX(seq) FROM activity WHERE seq IS NOT NULL)
  AND hash ~ '^[0-9a-f]{64}$'
  AND (
      (seq = 1 AND prev_hash IS NULL)
      OR (seq > 1 AND prev_hash ~ '^[0-9a-f]{64}$')
  )
ON CONFLICT (key) DO NOTHING;

INSERT INTO app_settings (key, value, updated_at)
SELECT
    'activity_chain_legacy',
    COUNT(*)::text,
    to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS') || '+00:00'
FROM activity
WHERE seq IS NULL
ON CONFLICT (key) DO NOTHING;
