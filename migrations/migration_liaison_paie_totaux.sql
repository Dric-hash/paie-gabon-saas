-- PaieGabon — Liaison Ameriack États Financiers : totaux annuels de paie (Note 27B)
-- Appliquée automatiquement au démarrage (liste de migrations d'app.py).
-- Idempotent : psql "$DATABASE_URL" -f migrations/migration_liaison_paie_totaux.sql
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS liaison_paie_autorisee BOOLEAN DEFAULT FALSE;
ALTER TABLE journaliers ADD COLUMN IF NOT EXISTS sexe VARCHAR(1);
