ALTER TABLE artifacts
ADD COLUMN content_sha256 text,
ADD CONSTRAINT artifacts_content_sha256_shape
CHECK (content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$');
