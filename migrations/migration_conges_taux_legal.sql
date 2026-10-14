-- ════════════════════════════════════════════════════════════════════════════
-- Migration UNIQUE : taux légal d'acquisition des congés (14/10/2026)
-- ════════════════════════════════════════════════════════════════════════════
-- Code du travail, Loi n° 022/2021 du 19/11/2021, Art. 222 :
--   « Sauf dispositions contractuelles plus favorables, le travailleur acquiert
--     droit au congé […] à raison de deux (2) jours ouvrables par mois de service
--     effectif. Les travailleurs âgés de moins de dix-huit (18) ans ont droit à
--     deux jours et demi (2,5) ouvrables. »
--
-- Jusqu'ici toutes les entreprises étaient à 2,5 par DÉFAUT (valeur non choisie,
-- aucun écran ne permettait de la modifier). On les ramène au taux légal 2,0,
-- SAUF la convention Hydrocarbures (2,5 j/mois, Art. 42 de la convention).
-- Les moins de 18 ans gardent 2,5 : c'est calculé par l'application.
--
-- ⚠️ À exécuter UNE SEULE FOIS. Ensuite, une entreprise au contrat plus favorable
--    remet 2,5 dans Paramètres → ce script ne doit plus être relancé.
--   psql "$DATABASE_URL" -f migrations/migration_conges_taux_legal.sql
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

\echo '== Avant : répartition des taux'
SELECT COALESCE(UPPER(convention), '—') AS convention, jours_conge_par_mois, COUNT(*) AS entreprises
FROM tenants GROUP BY 1, 2 ORDER BY 1, 2;

-- Valeur par défaut des nouvelles entreprises
ALTER TABLE tenants ALTER COLUMN jours_conge_par_mois SET DEFAULT 2.0;

-- Taux légal pour toutes, sauf Hydrocarbures
UPDATE tenants SET jours_conge_par_mois = 2.0
WHERE UPPER(COALESCE(convention, '')) <> 'HYDROCARBURES'
  AND (jours_conge_par_mois IS NULL OR jours_conge_par_mois <> 2.0);

-- Hydrocarbures : 2,5 (convention plus favorable)
UPDATE tenants SET jours_conge_par_mois = 2.5
WHERE UPPER(COALESCE(convention, '')) = 'HYDROCARBURES'
  AND (jours_conge_par_mois IS NULL OR jours_conge_par_mois < 2.5);

-- Lignes de solde annuel de l'année en cours (affichage fiche salarié + alerte
-- « congés à poser ») : recalcul au nouveau taux pour les ADULTES des entreprises
-- passées à 2,0. Les mineurs (< 18 ans au 31/12) et Hydrocarbures sont inchangés.
UPDATE conges c
SET jours_acquis = ROUND(LEAST(12, GREATEST(1,
        (CURRENT_DATE - s.date_embauche) / 30)) * 2.0, 1)
FROM salaries s, tenants t
WHERE c.salarie_id = s.id AND t.id = c.tenant_id
  AND c.date_depart IS NULL
  AND c.annee = EXTRACT(YEAR FROM CURRENT_DATE)
  AND UPPER(COALESCE(t.convention, '')) <> 'HYDROCARBURES'
  AND s.date_embauche IS NOT NULL
  AND (s.date_naissance IS NULL
       OR s.date_naissance <= make_date(EXTRACT(YEAR FROM CURRENT_DATE)::int - 18, 12, 31));

\echo '== Après : répartition des taux'
SELECT COALESCE(UPPER(convention), '—') AS convention, jours_conge_par_mois, COUNT(*) AS entreprises
FROM tenants GROUP BY 1, 2 ORDER BY 1, 2;

COMMIT;
