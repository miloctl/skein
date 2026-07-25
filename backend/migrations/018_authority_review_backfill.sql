UPDATE agent_authority SET review_by = date(updated_at, '+90 days')
    WHERE level IN ('autonomous', 'notify') AND review_by IS NULL;
