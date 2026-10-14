-- ════════════════════════════════════════════════════════════════════════════
-- Migration : Médaille du travail (gestion de carrière — Phase 2)
-- ════════════════════════════════════════════════════════════════════════════
-- Crée la table des paliers de la médaille du travail, PARAMÉTRABLES par
-- entreprise. Les valeurs par défaut (bronze 10 ans, argent 20 ans, or 30 ans)
-- reposent sur la Loi N° 021/2010 du 27/07/2010 (modifiant le Code du Travail,
-- art. 11 : service continu dans la même entreprise). Elles sont semées
-- automatiquement par l'application au premier accès au module (pas ici),
-- afin de couvrir aussi les tenants existants.
--
-- Aucune colonne n'est ajoutée aux tables existantes : l'attribution d'une
-- médaille est tracée comme un EvenementCarriere de type DISTINCTION
-- (le code du palier est stocké dans nouvelle_valeur).
--
-- Idempotente (IF NOT EXISTS). À exécuter sur le VPS :
--   psql "$DATABASE_URL" -f migrations/migration_medailles.sql
-- ════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS paliers_medaille (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id),
    code            VARCHAR(30)  NOT NULL,
    libelle         VARCHAR(100) NOT NULL,
    annees_requises INTEGER      NOT NULL,
    ordre           INTEGER      DEFAULT 0,
    actif           BOOLEAN      DEFAULT TRUE,
    date_creation   TIMESTAMP    DEFAULT NOW(),
    CONSTRAINT uq_palier_medaille_tenant_code UNIQUE (tenant_id, code)
);

CREATE INDEX IF NOT EXISTS idx_paliers_medaille_tenant
    ON paliers_medaille (tenant_id, actif);
