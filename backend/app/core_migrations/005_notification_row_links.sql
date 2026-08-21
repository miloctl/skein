-- Notification links are stored at write time, so rows written before the
-- row-level deep links (2026-08-20) still point at a page top — a question
-- notice pointed at "/", which is My Day itself. Retarget the two kinds
-- whose rows now carry ids. Safe to rewrite: notifications are not
-- hash-chained (only activity rows with a seq are), and the WHERE keeps this
-- idempotent and away from links a service wrote deliberately.
UPDATE notifications
   SET link = '/dashboard#question-' || source_id
 WHERE source_entity = 'question' AND source_id IS NOT NULL
   AND link IN ('/', '/dashboard');
UPDATE notifications
   SET link = '/dashboard#blocker-' || source_id
 WHERE source_entity = 'blocker' AND source_id IS NOT NULL
   AND link IN ('/', '/dashboard');
