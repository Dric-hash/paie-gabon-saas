-- ═══════════════════════════════════════════════════════════════════════════
-- Migration : profil d'entreprise (type_compte) + mise en relation par NIF
-- Idempotent. Les tables manquantes sont aussi créées par db.create_all().
-- ═══════════════════════════════════════════════════════════════════════════

-- 1) type_compte sur les tenants
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS type_compte VARCHAR(20) DEFAULT 'ENTREPRISE';
UPDATE tenants SET type_compte = 'CABINET' WHERE est_cabinet = TRUE AND (type_compte IS NULL OR type_compte = 'ENTREPRISE');
UPDATE tenants SET type_compte = 'ENTREPRISE' WHERE type_compte IS NULL;

-- 2) table des mises en relation
CREATE TABLE IF NOT EXISTS mises_en_relation (
    id                  SERIAL PRIMARY KEY,
    tenant_demandeur_id INTEGER NOT NULL REFERENCES tenants(id),
    tenant_cible_id     INTEGER REFERENCES tenants(id),
    nif_recherche       VARCHAR(50) NOT NULL,
    statut              VARCHAR(12) DEFAULT 'EN_ATTENTE',
    message             TEXT,
    client_cree_id      INTEGER REFERENCES clients_utilisateurs(id),
    date_creation       TIMESTAMP DEFAULT NOW(),
    date_reponse        TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_mer_demandeur ON mises_en_relation(tenant_demandeur_id);
CREATE INDEX IF NOT EXISTS ix_mer_cible ON mises_en_relation(tenant_cible_id);
