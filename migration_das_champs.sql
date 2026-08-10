-- Migration : champs salarié pour la Déclaration Annuelle des Salaires (DGI)
ALTER TABLE salaries ADD COLUMN IF NOT EXISTS nif VARCHAR(30);
ALTER TABLE salaries ADD COLUMN IF NOT EXISTS niveau VARCHAR(10);
ALTER TABLE salaries ADD COLUMN IF NOT EXISTS code_emploi VARCHAR(10);
