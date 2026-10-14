-- ════════════════════════════════════════════════════════════════════════════
-- DIAGNOSTIC (LECTURE SEULE) — Audit sécurité & bugs du 14/10/2026
-- ════════════════════════════════════════════════════════════════════════════
-- Ce script ne modifie RIEN : il liste les données déjà touchées par les bugs
-- corrigés, pour décider au cas par cas. À lancer sur le VPS :
--   psql "$DATABASE_URL" -f migrations/diagnostic_audit_2026_10.sql
-- Chaque bloc doit idéalement renvoyer 0 ligne.
-- ════════════════════════════════════════════════════════════════════════════

\echo '== 1. Congés APPROUVÉS dont les jours pris ≠ durée réelle (double approbation / écrasement)'
SELECT c.id, c.tenant_id, s.matricule, s.nom, s.prenom, c.date_depart, c.date_retour,
       c.jours_pris, (c.date_retour - c.date_depart + 1) AS jours_reels
FROM conges c JOIN salaries s ON s.id = c.salarie_id
WHERE c.statut IN ('APPROUVÉ', 'APPROUVE') AND c.date_depart IS NOT NULL
  AND c.date_retour IS NOT NULL
  AND c.jours_pris <> (c.date_retour - c.date_depart + 1)
ORDER BY c.tenant_id, c.date_depart;

\echo '== 2. Demandes NON approuvées portant des jours pris hérités (écrasement d''une demande approuvée)'
SELECT c.id, c.tenant_id, s.matricule, s.nom, c.statut, c.date_depart, c.date_retour, c.jours_pris
FROM conges c JOIN salaries s ON s.id = c.salarie_id
WHERE c.statut NOT IN ('APPROUVÉ', 'APPROUVE') AND c.date_depart IS NOT NULL
  AND COALESCE(c.jours_pris, 0) > 0
ORDER BY c.tenant_id, c.date_depart;

\echo '== 3. Bulletins à vérifier : mois où un congé du bloc 1 a démarré (allocation de congé possiblement gonflée)'
SELECT b.id AS bulletin_id, b.numero, b.statut, b.tenant_id, s.matricule, s.nom,
       p.mois, p.annee, c.id AS conge_id, c.jours_pris,
       (c.date_retour - c.date_depart + 1) AS jours_reels
FROM conges c
JOIN salaries s       ON s.id = c.salarie_id
JOIN bulletins_paie b ON b.salarie_id = c.salarie_id AND b.tenant_id = c.tenant_id
JOIN periodes_paie p  ON p.id = b.periode_id
WHERE c.statut IN ('APPROUVÉ', 'APPROUVE') AND c.date_depart IS NOT NULL
  AND c.date_retour IS NOT NULL
  AND c.jours_pris <> (c.date_retour - c.date_depart + 1)
  AND p.annee = EXTRACT(YEAR FROM c.date_depart) AND p.mois = EXTRACT(MONTH FROM c.date_depart)
ORDER BY b.tenant_id, p.annee, p.mois;

\echo '== 4. Bulletins PAYÉS sans numéro officiel (payés sans validation préalable)'
SELECT b.id, b.tenant_id, b.salarie_id, b.periode_id, b.net_a_payer, b.mode_paiement
FROM bulletins_paie b
WHERE b.statut = 'PAYÉ' AND b.numero IS NULL
ORDER BY b.tenant_id, b.periode_id;

\echo '== 5. Références croisées entre entreprises (catégorie / chantier / contrat d''un autre tenant)'
SELECT 'salarie.categorie' AS objet, s.id, s.tenant_id, c.tenant_id AS tenant_reference
FROM salaries s JOIN categories_emploi c ON c.id = s.categorie_id WHERE c.tenant_id <> s.tenant_id
UNION ALL
SELECT 'contrat.categorie', k.id, k.tenant_id, c.tenant_id
FROM contrats k JOIN categories_emploi c ON c.id = k.categorie_id WHERE c.tenant_id <> k.tenant_id
UNION ALL
SELECT 'affectation.site', a.id, a.tenant_id, si.tenant_id
FROM affectations_sites a JOIN sites si ON si.id = a.site_id WHERE si.tenant_id <> a.tenant_id
UNION ALL
SELECT 'contrat_prestation.site', cp.id, cp.tenant_id, si.tenant_id
FROM contrats_prestation cp JOIN sites si ON si.id = cp.site_id WHERE si.tenant_id <> cp.tenant_id
UNION ALL
SELECT 'facture_prestataire.site', f.id, f.tenant_id, si.tenant_id
FROM factures_prestataire f JOIN sites si ON si.id = f.site_id WHERE si.tenant_id <> f.tenant_id
UNION ALL
SELECT 'facture_prestataire.contrat', f.id, f.tenant_id, cp.tenant_id
FROM factures_prestataire f JOIN contrats_prestation cp ON cp.id = f.contrat_id WHERE cp.tenant_id <> f.tenant_id
UNION ALL
SELECT 'avance_prestataire.site', a.id, a.tenant_id, si.tenant_id
FROM avances_prestataire a JOIN sites si ON si.id = a.site_id WHERE si.tenant_id <> a.tenant_id
UNION ALL
SELECT 'avance_prestataire.contrat', a.id, a.tenant_id, cp.tenant_id
FROM avances_prestataire a JOIN contrats_prestation cp ON cp.id = a.contrat_id WHERE cp.tenant_id <> a.tenant_id;

\echo '== 6. Noms piégés (tentative de XSS déjà en base) — à nettoyer si présents'
SELECT 'salarie' AS objet, id, tenant_id, nom || ' ' || prenom AS valeur FROM salaries
WHERE nom ~ '[<>''"`]' OR prenom ~ '[<>''"`]'
UNION ALL
SELECT 'tenant', id, id, denomination FROM tenants WHERE denomination ~ '[<>"`]|''\)';
