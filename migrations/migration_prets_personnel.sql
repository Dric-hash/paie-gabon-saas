-- ═══════════════════════════════════════════════════════════════════════════
-- Migration : prêts au personnel + retenue prêt sur bulletin + quotité cessible
-- IMPORTANT : db.create_all() n'ajoute PAS de colonne à une table existante.
-- Ce script est donc NÉCESSAIRE au déploiement. Idempotent (IF NOT EXISTS).
-- ═══════════════════════════════════════════════════════════════════════════

-- Colonnes ajoutées à des tables existantes
ALTER TABLE bulletins_paie ADD COLUMN IF NOT EXISTS retenue_pret NUMERIC(15,2) DEFAULT 0;
UPDATE bulletins_paie SET retenue_pret = 0 WHERE retenue_pret IS NULL;

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS quotite_cessible_pct NUMERIC(5,2) DEFAULT 50;
UPDATE tenants SET quotite_cessible_pct = 50 WHERE quotite_cessible_pct IS NULL;

-- Nouvelles tables (créées aussi par db.create_all au redémarrage)
CREATE TABLE IF NOT EXISTS prets_personnel (
    id                SERIAL PRIMARY KEY,
    tenant_id         INTEGER NOT NULL REFERENCES tenants(id),
    salarie_id        INTEGER NOT NULL REFERENCES salaries(id),
    montant_total     NUMERIC(15,2) NOT NULL,
    date_octroi       DATE NOT NULL,
    nb_echeances      INTEGER NOT NULL DEFAULT 1,
    motif             VARCHAR(200),
    statut            VARCHAR(12) DEFAULT 'EN_COURS',
    montant_rembourse NUMERIC(15,2) DEFAULT 0,
    cree_par          INTEGER REFERENCES utilisateurs(id),
    cree_par_nom      VARCHAR(150),
    date_creation     TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_prets_tenant ON prets_personnel(tenant_id);
CREATE INDEX IF NOT EXISTS ix_prets_salarie ON prets_personnel(salarie_id);

CREATE TABLE IF NOT EXISTS remboursements_pret (
    id                 SERIAL PRIMARY KEY,
    tenant_id          INTEGER NOT NULL REFERENCES tenants(id),
    pret_id            INTEGER NOT NULL REFERENCES prets_personnel(id),
    montant            NUMERIC(15,2) NOT NULL,
    mois               INTEGER,
    annee              INTEGER,
    bulletin_id        INTEGER REFERENCES bulletins_paie(id),
    note               VARCHAR(200),
    date_remboursement DATE DEFAULT CURRENT_DATE,
    date_creation      TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_remb_pret ON remboursements_pret(pret_id);
