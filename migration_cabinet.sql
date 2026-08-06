-- Migration : mode cabinet (multi-entreprises)
-- Ajoute deux colonnes à la table tenants.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS est_cabinet BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS cabinet_id INTEGER REFERENCES tenants(id);
