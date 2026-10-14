-- ════════════════════════════════════════════════════════════════════════════
-- Migration : correctifs de l'audit du 14/10/2026
-- ════════════════════════════════════════════════════════════════════════════
-- La date de paiement d'un bulletin était saisie mais jamais enregistrée
-- (colonne absente). Également ajoutée automatiquement au démarrage de l'app
-- (run_migrations) — ce script permet de l'appliquer explicitement.
-- Idempotente. Sur le VPS :
--   psql "$DATABASE_URL" -f migrations/migration_audit_2026_10.sql
-- ════════════════════════════════════════════════════════════════════════════
ALTER TABLE bulletins_paie ADD COLUMN IF NOT EXISTS date_paiement DATE;
