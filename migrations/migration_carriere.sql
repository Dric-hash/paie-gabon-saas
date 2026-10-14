-- ═══════════════════════════════════════════════════════════════════════════
-- Migration : gestion de carrière (Phase 1)
-- IMPORTANT : db.create_all() n'ajoute PAS de colonne à une table existante.
-- Ce script est donc NÉCESSAIRE au déploiement. Idempotent (IF NOT EXISTS).
-- N'altère pas la paie : le salaire évolue via l'avenant de contrat.
-- ═══════════════════════════════════════════════════════════════════════════

-- Échelon (texte libre) dans la catégorie, pour la gestion de carrière.
ALTER TABLE salaries ADD COLUMN IF NOT EXISTS echelon VARCHAR(20);

-- Événements de carrière (frise) — créée aussi par db.create_all au redémarrage.
CREATE TABLE IF NOT EXISTS evenements_carriere (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id),
    salarie_id      INTEGER NOT NULL REFERENCES salaries(id),
    type            VARCHAR(20) NOT NULL,
    date_effet      DATE NOT NULL,
    titre           VARCHAR(200),
    details         TEXT,
    ancienne_valeur VARCHAR(255),
    nouvelle_valeur VARCHAR(255),
    contrat_id      INTEGER REFERENCES contrats(id),
    cree_par        INTEGER REFERENCES utilisateurs(id),
    cree_par_nom    VARCHAR(150),
    date_creation   TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_evt_carriere_salarie
    ON evenements_carriere(tenant_id, salarie_id);
