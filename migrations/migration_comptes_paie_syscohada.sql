-- PaieGabon — Comptes du journal de paie paramétrables (SYSCOHADA révisé), 20/10/2026
-- Appliquée automatiquement au démarrage (liste de migrations d'app.py).
-- Idempotent : psql "$DATABASE_URL" -f migrations/migration_comptes_paie_syscohada.sql
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS comptes_paie TEXT;
