-- Migration : 2 cases d'heures sup pour les jours fériés chômés payés (jour/nuit)
ALTER TABLE bulletins_paie ADD COLUMN IF NOT EXISTS heures_sup_fj NUMERIC(15,2) DEFAULT 0;
ALTER TABLE bulletins_paie ADD COLUMN IF NOT EXISTS heures_sup_fn NUMERIC(15,2) DEFAULT 0;
ALTER TABLE bulletins_paie ADD COLUMN IF NOT EXISTS base_heures_sup_fj NUMERIC(15,2) DEFAULT 0;
ALTER TABLE bulletins_paie ADD COLUMN IF NOT EXISTS taux_heures_sup_fj VARCHAR(20) DEFAULT '';
ALTER TABLE bulletins_paie ADD COLUMN IF NOT EXISTS base_heures_sup_fn NUMERIC(15,2) DEFAULT 0;
ALTER TABLE bulletins_paie ADD COLUMN IF NOT EXISTS taux_heures_sup_fn VARCHAR(20) DEFAULT '';
