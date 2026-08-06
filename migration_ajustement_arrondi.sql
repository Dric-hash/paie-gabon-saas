-- Migration : ajustement d'arrondi du net à payer sur les bulletins
ALTER TABLE bulletins_paie ADD COLUMN IF NOT EXISTS ajustement_arrondi NUMERIC(15,2) DEFAULT 0;
