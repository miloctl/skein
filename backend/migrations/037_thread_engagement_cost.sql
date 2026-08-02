-- Chat threads can be linked to the engagement they serve, and every model
-- turn can carry an estimated cost. The link is what turns token accounting
-- into engagement accounting: usage_log joins through chat_threads to land
-- spend next to engagement health.
--
-- cost_usd is NULL when the model has no configured price (SKEIN_MODEL_PRICES)
-- -- honest, not zero: zero would silently understate spend.
ALTER TABLE chat_threads ADD COLUMN engagement_id INTEGER REFERENCES engagements(id);
ALTER TABLE usage_log ADD COLUMN cost_usd REAL
