-- ═══════════════════════════════════════════════════════════════════════════
-- Migration : signatures du pointage mobile (feuille de présence)
-- IMPORTANT : db.create_all() n'ajoute PAS de colonne à une table existante.
-- Ce script est donc NÉCESSAIRE au déploiement. Idempotent (IF NOT EXISTS).
-- ═══════════════════════════════════════════════════════════════════════════

-- Signature tactile individuelle du travailleur (optionnelle), sur le pointage.
ALTER TABLE pointages ADD COLUMN IF NOT EXISTS signature TEXT;

-- Feuille de présence : signature du chef de chantier pour une date + un chantier.
CREATE TABLE IF NOT EXISTS feuilles_presence (
    id             SERIAL PRIMARY KEY,
    tenant_id      INTEGER NOT NULL REFERENCES tenants(id),
    pointeur_id    INTEGER REFERENCES pointeurs_chantier(id),
    site_id        INTEGER REFERENCES sites(id),
    date_pointage  DATE NOT NULL,
    signature_chef TEXT,
    chef_nom       VARCHAR(150),
    date_creation  TIMESTAMP DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uix_feuille_presence
    ON feuilles_presence(tenant_id, pointeur_id, date_pointage, site_id);
CREATE INDEX IF NOT EXISTS idx_feuille_tenant_date
    ON feuilles_presence(tenant_id, date_pointage);
