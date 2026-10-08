-- ═══════════════════════════════════════════════════════════════════════════
-- Migration : Mise à disposition de personnel
-- Les tables sont aussi créées par db.create_all() au redémarrage ;
-- ce script permet de les créer manuellement si besoin. Idempotent.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS clients_utilisateurs (
    id            SERIAL PRIMARY KEY,
    tenant_id     INTEGER NOT NULL REFERENCES tenants(id),
    nom           VARCHAR(200) NOT NULL,
    nif           VARCHAR(30),
    rccm          VARCHAR(50),
    contact_nom   VARCHAR(150),
    telephone     VARCHAR(30),
    email         VARCHAR(200),
    adresse       VARCHAR(300),
    secteur       VARCHAR(120),
    note          TEXT,
    actif         BOOLEAN DEFAULT TRUE,
    date_creation TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_clients_util_tenant ON clients_utilisateurs(tenant_id);

CREATE TABLE IF NOT EXISTS affectations_mad (
    id            SERIAL PRIMARY KEY,
    tenant_id     INTEGER NOT NULL REFERENCES tenants(id),
    client_id     INTEGER NOT NULL REFERENCES clients_utilisateurs(id),
    salarie_id    INTEGER NOT NULL REFERENCES salaries(id),
    poste         VARCHAR(200),
    date_debut    DATE NOT NULL,
    date_fin      DATE,
    mode_facturation VARCHAR(20) DEFAULT 'TAUX_JOUR',
    valeur        NUMERIC(14,4) DEFAULT 0,
    actif         BOOLEAN DEFAULT TRUE,
    note          VARCHAR(300),
    date_creation TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_affect_mad_tenant ON affectations_mad(tenant_id);
CREATE INDEX IF NOT EXISTS ix_affect_mad_client ON affectations_mad(client_id);
CREATE INDEX IF NOT EXISTS ix_affect_mad_salarie ON affectations_mad(salarie_id);
