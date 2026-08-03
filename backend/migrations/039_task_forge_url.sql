-- Where the code for this task lives: the branch or pull request the forge
-- webhook matched. Written by the forge service only, so it stays empty on
-- every task nobody pushed for, and a person never has to retype the link.
ALTER TABLE tasks ADD COLUMN forge_url TEXT NOT NULL DEFAULT '';
