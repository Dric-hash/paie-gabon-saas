-- ═══════════════════════════════════════════════════════════════════════════
-- Migration : pointage mobile (PWA chantier)
-- IMPORTANT : db.create_all() n'ajoute PAS de colonne à une table existante.
-- Ce script est donc NÉCESSAIRE au déploiement. Idempotent (IF NOT EXISTS).
--
-- Sécurité paie : les pointages mobiles arrivent en statut 'SOUMIS' (quarantaine)
-- et ne sont comptés ni en paie ni en facturation tant qu'ils ne sont pas
-- validés par le RH (statut 'VALIDE'). Les pointages web existants sont migrés
-- en 'VALIDE' pour continuer d'être comptés normalement.
-- ═══════════════════════════════════════════════════════════════════════════

-- ── Colonnes ajoutées à la table pointages ────────────────────────────────
ALTER TABLE pointages ADD COLUMN IF NOT EXISTS uuid_client   VARCHAR(36);
ALTER TABLE pointages ADD COLUMN IF NOT EXISTS source        VARCHAR(10) DEFAULT 'WEB';
ALTER TABLE pointages ADD COLUMN IF NOT EXISTS statut        VARCHAR(10) DEFAULT 'VALIDE';
ALTER TABLE pointages ADD COLUMN IF NOT EXISTS pointeur_id   INTEGER;
ALTER TABLE pointages ADD COLUMN IF NOT EXISTS latitude      NUMERIC(9,6);
ALTER TABLE pointages ADD COLUMN IF NOT EXISTS longitude     NUMERIC(9,6);
ALTER TABLE pointages ADD COLUMN IF NOT EXISTS geo_hors_zone BOOLEAN DEFAULT FALSE;
ALTER TABLE pointages ADD COLUMN IF NOT EXISTS heure_serveur TIMESTAMP;

-- Les pointages existants (saisis sur le web) restent comptés en paie.
UPDATE pointages SET source = 'WEB'    WHERE source IS NULL;
UPDATE pointages SET statut = 'VALIDE' WHERE statut IS NULL;

-- ── Colonnes de géolocalisation du site/chantier ──────────────────────────
ALTER TABLE sites ADD COLUMN IF NOT EXISTS latitude  NUMERIC(9,6);
ALTER TABLE sites ADD COLUMN IF NOT EXISTS longitude NUMERIC(9,6);
ALTER TABLE sites ADD COLUMN IF NOT EXISTS rayon_m   INTEGER DEFAULT 200;

-- ── Table des pointeurs de chantier (créée aussi par db.create_all) ────────
CREATE TABLE IF NOT EXISTS pointeurs_chantier (
    id                 SERIAL PRIMARY KEY,
    tenant_id          INTEGER NOT NULL REFERENCES tenants(id),
    site_id            INTEGER REFERENCES sites(id),
    nom                VARCHAR(150) NOT NULL,
    code               VARCHAR(20) NOT NULL UNIQUE,
    pin_hash           VARCHAR(256) NOT NULL,
    actif              BOOLEAN DEFAULT TRUE,
    derniere_connexion TIMESTAMP,
    cree_par           VARCHAR(200),
    date_creation      TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pointeurs_tenant ON pointeurs_chantier(tenant_id);

-- Lien du pointeur vers sa propre fiche travailleur (salarié OU journalier),
-- pour qu'il figure dans son équipe et se pointe lui-même.
ALTER TABLE pointeurs_chantier ADD COLUMN IF NOT EXISTS salarie_id    INTEGER REFERENCES salaries(id);
ALTER TABLE pointeurs_chantier ADD COLUMN IF NOT EXISTS journalier_id INTEGER REFERENCES journaliers(id);

-- FK pointeur sur pointages (ajoutée après création de la table pointeurs)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'fk_pointages_pointeur'
    ) THEN
        ALTER TABLE pointages
            ADD CONSTRAINT fk_pointages_pointeur
            FOREIGN KEY (pointeur_id) REFERENCES pointeurs_chantier(id);
    END IF;
END $$;

-- Idempotence de la synchronisation : un même uuid_client ne crée qu'une ligne.
CREATE UNIQUE INDEX IF NOT EXISTS uix_pointages_tenant_uuid
    ON pointages(tenant_id, uuid_client)
    WHERE uuid_client IS NOT NULL;
