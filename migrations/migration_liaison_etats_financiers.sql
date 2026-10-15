-- ════════════════════════════════════════════════════════════════════════════
-- Liaison Ameriack États Financiers (15/10/2026)
-- Clé de LECTURE SEULE de la fiche d'identité de l'entreprise
-- (GET /api/v1/identite). Stockée hachée (SHA-256), jamais en clair.
-- Idempotent : peut être relancé sans risque. Également appliqué
-- automatiquement au démarrage (run_migrations).
--   psql "$DATABASE_URL" -f migrations/migration_liaison_etats_financiers.sql
-- ════════════════════════════════════════════════════════════════════════════
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS cle_liaison_hash VARCHAR(64);
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS cle_liaison_prefixe VARCHAR(16);
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS cle_liaison_date TIMESTAMP;
CREATE INDEX IF NOT EXISTS idx_tenants_cle_liaison_hash ON tenants (cle_liaison_hash);
