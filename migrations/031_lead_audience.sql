-- Split the lead classification into two orthogonal axes:
--   lead_type = intent/temperature (hot|warm|cold|no_budget|non_target|unclear)
--   audience  = who they are (adult|student)
-- Previously 'student' occupied the single lead_type slot, so a school-age lead could never
-- also be marked hot/warm — hiding their real intent and forcing their won% to 0.
ALTER TABLE lead ADD COLUMN IF NOT EXISTS audience VARCHAR(16);

-- Migrate existing school-age leads onto the audience axis. Their intent was never captured
-- (the slot held 'student'), so reset lead_type to 'unclear' — the live classifier re-derives
-- a real temperature on their next reply. Run scripts/reclassify_lead_types.py to backfill.
UPDATE lead SET audience = 'student', lead_type = 'unclear' WHERE lead_type = 'student';
