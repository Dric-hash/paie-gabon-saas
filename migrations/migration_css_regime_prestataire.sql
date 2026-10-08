-- ═══════════════════════════════════════════════════════════════════════════
-- Migration : régime fiscal prestataire + CSS (Contribution Spéciale de Solidarité)
-- IMPORTANT : db.create_all() n'ajoute PAS de colonne à une table existante.
-- Ce script est donc NÉCESSAIRE au déploiement. Idempotent (IF NOT EXISTS).
-- ═══════════════════════════════════════════════════════════════════════════

-- Prestataire : régime fiscal + assujettissement CSS
ALTER TABLE prestataires ADD COLUMN IF NOT EXISTS regime_fiscal VARCHAR(12) DEFAULT 'TVA_CSS';
ALTER TABLE prestataires ADD COLUMN IF NOT EXISTS assujetti_css BOOLEAN DEFAULT TRUE;

-- Reclassement des prestataires existants selon leurs réglages actuels
UPDATE prestataires SET regime_fiscal = 'TVA_CSS'   WHERE assujetti_tva = TRUE  AND (regime_fiscal IS NULL OR regime_fiscal = '');
UPDATE prestataires SET regime_fiscal = 'PRECOMPTE' WHERE assujetti_tva = FALSE AND COALESCE(taux_retenue_source,0) > 0 AND (regime_fiscal IS NULL OR regime_fiscal = '');
UPDATE prestataires SET regime_fiscal = 'CSS'       WHERE assujetti_tva = FALSE AND COALESCE(taux_retenue_source,0) = 0 AND (regime_fiscal IS NULL OR regime_fiscal = '');
UPDATE prestataires SET regime_fiscal = 'TVA_CSS'   WHERE regime_fiscal IS NULL OR regime_fiscal = '';
UPDATE prestataires SET assujetti_css = TRUE WHERE assujetti_css IS NULL;

-- Factures prestataire : taux et montant CSS
ALTER TABLE factures_prestataire ADD COLUMN IF NOT EXISTS taux_css NUMERIC(5,2) DEFAULT 1;
ALTER TABLE factures_prestataire ADD COLUMN IF NOT EXISTS montant_css NUMERIC(15,2) DEFAULT 0;
-- Les factures déjà saisies gardent CSS = 0 (ne pas recalculer rétroactivement).
UPDATE factures_prestataire SET taux_css = 0 WHERE taux_css IS NULL;
UPDATE factures_prestataire SET montant_css = 0 WHERE montant_css IS NULL;
