-- PaieGabon — Portail des employés (consultation des bulletins), 22/10/2026
-- La colonne est ajoutée au démarrage (liste de migrations d'app.py) et les
-- tables par create_all. Ce fichier permet de tout créer à la main ; idempotent :
--   psql "$DATABASE_URL" -f migrations/migration_portail_salaries.sql
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS portail_salaries_actif BOOLEAN DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS acces_salaries (
    id                 SERIAL PRIMARY KEY,
    tenant_id          INTEGER NOT NULL REFERENCES tenants(id),
    salarie_id         INTEGER NOT NULL UNIQUE REFERENCES salaries(id),
    pin_hash           VARCHAR(256),
    code_hash          VARCHAR(256),
    code_expire        TIMESTAMP,
    actif              BOOLEAN NOT NULL DEFAULT TRUE,
    version            INTEGER NOT NULL DEFAULT 1,
    echecs             INTEGER NOT NULL DEFAULT 0,
    verrou_jusqua      TIMESTAMP,
    consentement_le    TIMESTAMP,
    active_le          TIMESTAMP,
    derniere_connexion TIMESTAMP,
    invite_le          TIMESTAMP,
    invite_par         VARCHAR(150),
    date_creation      TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_acces_salaries_tenant_id ON acces_salaries (tenant_id);

CREATE TABLE IF NOT EXISTS consultations_bulletins (
    id          SERIAL PRIMARY KEY,
    tenant_id   INTEGER NOT NULL REFERENCES tenants(id),
    salarie_id  INTEGER NOT NULL REFERENCES salaries(id),
    bulletin_id INTEGER NOT NULL REFERENCES bulletins_paie(id),
    date        TIMESTAMP NOT NULL,
    ip          VARCHAR(45)
);
CREATE INDEX IF NOT EXISTS ix_consultations_bulletins_tenant_id ON consultations_bulletins (tenant_id);
CREATE INDEX IF NOT EXISTS ix_consultations_bulletins_salarie_id ON consultations_bulletins (salarie_id);
CREATE INDEX IF NOT EXISTS ix_consultations_bulletins_bulletin_id ON consultations_bulletins (bulletin_id);
