-- Migration : convention Hôtellerie-Restauration
-- Colonne 1 : indicateur de poste de nuit sur le salarié (prime de nuit 20%)
ALTER TABLE salaries ADD COLUMN IF NOT EXISTS travail_de_nuit BOOLEAN DEFAULT FALSE;
-- Colonne 2 : montant de la prime de nuit sur le bulletin
ALTER TABLE bulletins_paie ADD COLUMN IF NOT EXISTS prime_nuit NUMERIC(15,2) DEFAULT 0;
