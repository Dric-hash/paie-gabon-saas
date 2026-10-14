-- ════════════════════════════════════════════════════════════════════════════
-- Migration : Développement RH (gestion de carrière — Phase 3)
-- ════════════════════════════════════════════════════════════════════════════
-- Crée les tables formations, competences, competences_salarie, evaluations.
-- Aucune colonne n'est ajoutée aux tables existantes. Aucune n'intervient dans
-- le calcul de la paie.
--
-- Idempotente (IF NOT EXISTS). À exécuter sur le VPS :
--   psql "$DATABASE_URL" -f migrations/migration_phase3.sql
-- ════════════════════════════════════════════════════════════════════════════

-- ── Formations ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS formations (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id),
    salarie_id      INTEGER NOT NULL REFERENCES salaries(id),
    intitule        VARCHAR(200) NOT NULL,
    organisme       VARCHAR(200),
    type            VARCHAR(30)  DEFAULT 'EXTERNE',
    date_debut      DATE,
    date_fin        DATE,
    duree_heures    NUMERIC(8,2),
    cout            NUMERIC(15,2),
    statut          VARCHAR(20)  DEFAULT 'PLANIFIEE',
    certifiante     BOOLEAN      DEFAULT FALSE,
    date_expiration DATE,
    resultat        VARCHAR(200),
    details         TEXT,
    cree_par        INTEGER REFERENCES utilisateurs(id),
    cree_par_nom    VARCHAR(150),
    date_creation   TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_formations_tenant_salarie ON formations (tenant_id, salarie_id);
CREATE INDEX IF NOT EXISTS idx_formations_expiration     ON formations (tenant_id, date_expiration);

-- ── Référentiel de compétences ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS competences (
    id            SERIAL PRIMARY KEY,
    tenant_id     INTEGER NOT NULL REFERENCES tenants(id),
    libelle       VARCHAR(150) NOT NULL,
    categorie     VARCHAR(50),
    actif         BOOLEAN DEFAULT TRUE,
    date_creation TIMESTAMP DEFAULT NOW(),
    CONSTRAINT uq_competence_tenant_libelle UNIQUE (tenant_id, libelle)
);
CREATE INDEX IF NOT EXISTS idx_competences_tenant ON competences (tenant_id, actif);

-- ── Niveau de compétence par salarié ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS competences_salarie (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id),
    salarie_id      INTEGER NOT NULL REFERENCES salaries(id),
    competence_id   INTEGER NOT NULL REFERENCES competences(id),
    niveau          INTEGER NOT NULL,
    date_evaluation DATE,
    evaluateur_nom  VARCHAR(150),
    commentaire     VARCHAR(255),
    CONSTRAINT uq_comp_sal_tenant_salarie_competence UNIQUE (tenant_id, salarie_id, competence_id)
);
CREATE INDEX IF NOT EXISTS idx_comp_sal_salarie ON competences_salarie (tenant_id, salarie_id);

-- ── Évaluations / entretiens ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS evaluations (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id),
    salarie_id          INTEGER NOT NULL REFERENCES salaries(id),
    type                VARCHAR(30) DEFAULT 'ANNUELLE',
    date_evaluation     DATE,
    periode             VARCHAR(20),
    note_globale        VARCHAR(2),
    evaluateur_id       INTEGER REFERENCES utilisateurs(id),
    evaluateur_nom      VARCHAR(150),
    points_forts        TEXT,
    axes_amelioration   TEXT,
    objectifs           TEXT,
    commentaire_salarie TEXT,
    statut              VARCHAR(20) DEFAULT 'PLANIFIEE',
    cree_par            INTEGER REFERENCES utilisateurs(id),
    cree_par_nom        VARCHAR(150),
    date_creation       TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_evaluations_tenant_salarie ON evaluations (tenant_id, salarie_id);
