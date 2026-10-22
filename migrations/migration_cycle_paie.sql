-- PaieGabon — Cycle de paie : obligations et échéances légales, 22/10/2026
-- Tables créées automatiquement au démarrage (create_all). Ce fichier permet
-- de les créer à la main ; idempotent :
--   psql "$DATABASE_URL" -f migrations/migration_cycle_paie.sql
CREATE TABLE IF NOT EXISTS obligations_paie (
    id                SERIAL PRIMARY KEY,
    tenant_id         INTEGER NOT NULL REFERENCES tenants(id),
    code              VARCHAR(20) NOT NULL,
    annee             INTEGER NOT NULL,
    periode           INTEGER NOT NULL DEFAULT 0,
    statut            VARCHAR(20) NOT NULL DEFAULT 'A_FAIRE',
    date_declaration  DATE,
    date_paiement     DATE,
    montant_paye      NUMERIC(15,2),
    reference         VARCHAR(100),
    commentaire       VARCHAR(500),
    saisi_par         VARCHAR(150),
    date_modification TIMESTAMP,
    CONSTRAINT uq_obligation_paie UNIQUE (tenant_id, code, annee, periode)
);
CREATE INDEX IF NOT EXISTS ix_obligations_paie_tenant_id ON obligations_paie (tenant_id);

CREATE TABLE IF NOT EXISTS echeances_legales (
    id                SERIAL PRIMARY KEY,
    code              VARCHAR(20) NOT NULL UNIQUE,
    jour              INTEGER,
    decalage_mois     INTEGER,
    source            VARCHAR(300),
    verifie_le        DATE,
    date_modification TIMESTAMP
);

-- Objectifs internes propres à chaque entreprise (ajout du 22/10/2026)
CREATE TABLE IF NOT EXISTS echeances_tenant (
    id                SERIAL PRIMARY KEY,
    tenant_id         INTEGER NOT NULL REFERENCES tenants(id),
    code              VARCHAR(20) NOT NULL,
    jour              INTEGER NOT NULL,
    decalage_mois     INTEGER NOT NULL,
    note              VARCHAR(300),
    date_modification TIMESTAMP,
    CONSTRAINT uq_echeance_tenant UNIQUE (tenant_id, code)
);
CREATE INDEX IF NOT EXISTS ix_echeances_tenant_tenant_id ON echeances_tenant (tenant_id);
