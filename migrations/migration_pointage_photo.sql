-- ═══════════════════════════════════════════════════════════════════════════
-- Migration : photo du travailleur pointé (pointage mobile)
-- IMPORTANT : db.create_all() n'ajoute PAS de colonne à une table existante.
-- Ce script est donc NÉCESSAIRE au déploiement. Idempotent (IF NOT EXISTS).
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE pointages ADD COLUMN IF NOT EXISTS photo TEXT;
