CREATE INDEX forge_receipts_fingerprint ON forge_receipts(namespace, event, payload_sha256);
