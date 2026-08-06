-- Migration : limite d'entreprises par cabinet (paliers tarifaires)
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS limite_entreprises INTEGER;
