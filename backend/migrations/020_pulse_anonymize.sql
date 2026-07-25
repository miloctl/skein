UPDATE feedback SET created_by = '' WHERE kind = 'pulse';
UPDATE activity SET actor = 'team',
    detail = 'pulse ' || substr(detail, 1, instr(detail, ' ') - 1)
    WHERE action = 'record_feedback' AND detail LIKE '%pulse/%';
