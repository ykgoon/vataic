-- 011_outcome_hop_mass.sql
-- Records how much of an outcome's movement came from each causal hop.
--
-- outcomes."order" is decided from this: the order is the shallowest hop that
-- carries a majority of the movement. Keeping the distribution rather than only
-- the resulting order means a reader (or a test) can check the judgement instead
-- of trusting a bare integer, and an outcome can say "mostly direct, with a
-- second-order tail" rather than being forced into one bucket.
ALTER TABLE outcomes ADD COLUMN hop_mass TEXT NOT NULL DEFAULT '{}';
