# -*- coding: utf-8 -*-
"""Gestion de carrière (Phase 1) : frise de carrière d'un salarié, et promotion
/ avancement qui crée un avenant de contrat. N'altère jamais la paie : le
nouveau salaire est porté par l'avenant (Contrat), repris normalement au
prochain bulletin — un bulletin validé n'est jamais modifié."""
from datetime import datetime, date

from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from blueprints.tenant import bp, _doc_response
from core import get_tenant, _parse_date, id_du_tenant
from audit import log_action
from models import (db, Salarie, Contrat, CategorieEmploi, EvenementCarriere,
                    HistoriqueSalarie, Sanction, Formation, Evaluation,
                    CompetenceSalarie)


def _frise(t, s):
    """Construit la frise de carrière : agrège l'embauche, les événements de
    carrière saisis, les avenants de contrat, les sanctions et l'historique des
    modifications — en lignes uniformes triées de la plus récente à la plus ancienne."""
    lignes = []

    # Embauche (virtuel, depuis la fiche salarié)
    if s.date_embauche:
        lignes.append({"date": s.date_embauche, "type": "EMBAUCHE", "icone": "🎉",
                       "titre": "Embauche", "details": s.emploi or "", "evt_id": None})

    # Événements de carrière saisis
    evts = (EvenementCarriere.query.filter_by(tenant_id=t.id, salarie_id=s.id).all())
    contrats_lies = {e.contrat_id for e in evts if e.contrat_id}
    for e in evts:
        det = e.details or ""
        if e.ancienne_valeur or e.nouvelle_valeur:
            det = (det + ("\n" if det else "") +
                   f"{e.ancienne_valeur or '—'} → {e.nouvelle_valeur or '—'}")
        lignes.append({"date": e.date_effet, "type": e.type, "icone": e.icone,
                       "titre": e.titre or e.type_libelle, "details": det, "evt_id": e.id})

    # Avenants de contrat non déjà représentés par un événement
    contrats = (Contrat.query.filter_by(tenant_id=t.id, salarie_id=s.id)
                .order_by(Contrat.date_debut).all())
    for c in contrats:
        if c.id in contrats_lies:
            continue
        if s.date_embauche and c.date_debut == s.date_embauche:
            continue  # le contrat initial = déjà couvert par l'embauche
        lignes.append({"date": c.date_debut, "type": "AVENANT", "icone": "📄",
                       "titre": f"Avenant — {c.type_contrat}",
                       "details": (f"{c.poste or ''} · {int(float(c.salaire_base or 0)):,} FCFA"
                                   .replace(",", " ")), "evt_id": None})

    # Sanctions
    for sa in Sanction.query.filter_by(tenant_id=t.id, salarie_id=s.id).all():
        lignes.append({"date": sa.date_sanction or date.today(),
                       "type": "SANCTION", "icone": "⚠️",
                       "titre": "Sanction" + (f" — {sa.type}" if sa.type else ""),
                       "details": sa.motif or "", "evt_id": None})

    # Formations (développement RH — Phase 3)
    for f in Formation.query.filter_by(tenant_id=t.id, salarie_id=s.id).all():
        det = f.type_libelle + (f" · {f.organisme}" if f.organisme else "")
        det += f" · {f.statut_libelle}"
        if f.certifiante and f.date_expiration:
            det += f"\nCertification valide jusqu'au {f.date_expiration.strftime('%d/%m/%Y')}"
        lignes.append({"date": f.date_debut or (f.date_creation.date() if f.date_creation else date.today()),
                       "type": "FORMATION", "icone": "🎓",
                       "titre": f"Formation — {f.intitule}", "details": det,
                       "evt_id": None, "formation_id": f.id})

    # Évaluations / entretiens (développement RH — Phase 3)
    for e in Evaluation.query.filter_by(tenant_id=t.id, salarie_id=s.id).all():
        det = e.type_libelle
        if e.note_globale:
            det += f" · Appréciation {e.note_globale} ({e.note_libelle})"
        lignes.append({"date": e.date_evaluation or date.today(),
                       "type": "EVALUATION", "icone": "📋",
                       "titre": "Évaluation" + (f" {e.periode}" if e.periode else ""),
                       "details": det, "evt_id": None, "evaluation_id": e.id})

    # Historique des modifications (fonction / date d'embauche)
    for h in HistoriqueSalarie.query.filter_by(tenant_id=t.id, salarie_id=s.id).all():
        lignes.append({"date": (h.date_modification.date() if h.date_modification else date.today()),
                       "type": "MODIF", "icone": "✏️",
                       "titre": f"Modification — {h.champ_libelle}",
                       "details": f"{h.ancienne_valeur or '—'} → {h.nouvelle_valeur or '—'}",
                       "evt_id": None})

    lignes.sort(key=lambda x: x["date"] or date.min, reverse=True)
    return lignes


@bp.route("/salaries/<int:id>/carriere")
@login_required
def carriere(id):
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    s = Salarie.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    contrat = Contrat.query.filter_by(salarie_id=s.id, tenant_id=t.id, actif=True).first()
    anc_jours = (date.today() - s.date_embauche).days if s.date_embauche else 0
    nb_competences = CompetenceSalarie.query.filter_by(tenant_id=t.id, salarie_id=s.id).count()
    return render_template("tenant/carriere.html", tenant=t, salarie=s,
                           frise=_frise(t, s), contrat=contrat,
                           anciennete_ans=anc_jours // 365,
                           nb_competences=nb_competences,
                           TYPES=EvenementCarriere.TYPES, now=datetime.now())


@bp.route("/salaries/<int:id>/promouvoir", methods=["GET", "POST"])
@login_required
def promouvoir(id):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        abort(403)
    s = Salarie.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    cats = CategorieEmploi.query.filter_by(tenant_id=t.id).order_by(CategorieEmploi.code).all()
    contrat = Contrat.query.filter_by(salarie_id=s.id, tenant_id=t.id, actif=True).first()

    if request.method == "POST":
        type_evt = request.form.get("type_evt", "PROMOTION")
        if type_evt not in ("PROMOTION", "AUGMENTATION", "CHANGEMENT_POSTE"):
            type_evt = "PROMOTION"
        date_effet = _parse_date(request.form.get("date_effet")) or date.today()
        nouveau_poste = (request.form.get("poste") or "").strip() or s.emploi
        nouvel_echelon = (request.form.get("echelon") or "").strip() or None
        # Catégorie validée contre le tenant (jamais celle d'une autre entreprise)
        cat_id = id_du_tenant(CategorieEmploi, request.form.get("categorie_id"), t)
        motif = (request.form.get("motif") or "").strip() or None
        try:
            nouveau_salaire = float((request.form.get("salaire_base") or "0").replace(" ", "").replace(",", "."))
        except ValueError:
            nouveau_salaire = 0
        # Valeur de départ (pour tracer l'avant → après)
        ancien_salaire = float(contrat.salaire_base) if contrat else 0
        ancienne_cat = s.categorie.code if s.categorie else None
        nouvelle_cat = CategorieEmploi.query.filter_by(id=cat_id, tenant_id=t.id).first() if cat_id else None

        if nouveau_salaire <= 0:
            flash("Le nouveau salaire de base doit être positif.", "error")
            return render_template("tenant/promouvoir.html", tenant=t, salarie=s,
                                   categories=cats, contrat=contrat, now=datetime.now())

        # ── Avenant : désactive le contrat courant, en crée un nouveau ──────────
        Contrat.query.filter_by(salarie_id=s.id, tenant_id=t.id, actif=True).update({"actif": False})
        c = Contrat(
            tenant_id=t.id, salarie_id=s.id,
            type_contrat=(contrat.type_contrat if contrat else "CDI"),
            date_debut=date_effet,
            date_fin=(contrat.date_fin if contrat else None),
            salaire_base=nouveau_salaire,
            poste=nouveau_poste,
            categorie_id=(cat_id or (s.categorie_id if s.categorie_id else None)),
            actif=True,
        )
        db.session.add(c)
        # Mise à jour de la fiche salarié
        s.emploi = nouveau_poste
        if cat_id:
            s.categorie_id = cat_id
        if nouvel_echelon is not None:
            s.echelon = nouvel_echelon
        db.session.flush()

        # Détail de l'évolution
        chg = []
        if nouvelle_cat and nouvelle_cat.code != ancienne_cat:
            chg.append(f"catégorie {ancienne_cat or '—'} → {nouvelle_cat.code}")
        if nouvel_echelon:
            chg.append(f"échelon {nouvel_echelon}")
        if nouveau_salaire != ancien_salaire:
            chg.append(f"salaire {int(ancien_salaire):,} → {int(nouveau_salaire):,} FCFA".replace(",", " "))
        detail = (motif + "\n" if motif else "") + " · ".join(chg)

        evt = EvenementCarriere(
            tenant_id=t.id, salarie_id=s.id, type=type_evt, date_effet=date_effet,
            titre=EvenementCarriere.TYPES.get(type_evt, type_evt),
            details=detail.strip() or None,
            ancienne_valeur=(f"{ancienne_cat or '—'} · {int(ancien_salaire):,} FCFA".replace(",", " ")),
            nouvelle_valeur=(f"{(nouvelle_cat.code if nouvelle_cat else ancienne_cat) or '—'} · "
                             f"{int(nouveau_salaire):,} FCFA".replace(",", " ")),
            contrat_id=c.id,
            cree_par=current_user.id,
            cree_par_nom=getattr(current_user, "nom_complet", None) or current_user.email,
        )
        db.session.add(evt)
        db.session.commit()
        log_action("CREATE", "carriere", evt.id,
                   f"{evt.type_libelle} — {s.nom_complet} (effet {date_effet.strftime('%d/%m/%Y')})")
        flash(f"{evt.type_libelle} enregistrée. Le nouveau salaire s'applique à partir "
              f"de la paie de {date_effet.strftime('%m/%Y')} (avenant créé).", "success")
        return redirect(url_for("tenant.carriere", id=s.id))

    return render_template("tenant/promouvoir.html", tenant=t, salarie=s,
                           categories=cats, contrat=contrat, now=datetime.now())


@bp.route("/salaries/<int:id>/carriere/evenement", methods=["POST"])
@login_required
def carriere_evenement(id):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        abort(403)
    s = Salarie.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    type_evt = request.form.get("type", "AUTRE")
    if type_evt not in EvenementCarriere.TYPES:
        type_evt = "AUTRE"
    evt = EvenementCarriere(
        tenant_id=t.id, salarie_id=s.id, type=type_evt,
        date_effet=_parse_date(request.form.get("date_effet")) or date.today(),
        titre=(request.form.get("titre") or "").strip() or EvenementCarriere.TYPES[type_evt],
        details=(request.form.get("details") or "").strip() or None,
        cree_par=current_user.id,
        cree_par_nom=getattr(current_user, "nom_complet", None) or current_user.email,
    )
    db.session.add(evt)
    db.session.commit()
    log_action("CREATE", "carriere", evt.id, f"{evt.type_libelle} — {s.nom_complet}")
    flash("Événement de carrière ajouté.", "success")
    return redirect(url_for("tenant.carriere", id=s.id))


@bp.route("/carriere/evenement/<int:id>/supprimer", methods=["POST"])
@login_required
def carriere_evenement_supprimer(id):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        abort(403)
    evt = EvenementCarriere.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    sid = evt.salarie_id
    # On ne supprime pas l'avenant de contrat lié (document de paie) — juste la trace carrière.
    db.session.delete(evt)
    db.session.commit()
    log_action("DELETE", "carriere", id, "Événement de carrière supprimé")
    flash("Événement supprimé (l'avenant de contrat, s'il existe, est conservé).", "success")
    return redirect(url_for("tenant.carriere", id=sid))


@bp.route("/carriere/evenement/<int:id>/attestation")
@login_required
def carriere_attestation(id):
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    evt = EvenementCarriere.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    s = evt.salarie
    from documents_rh import generer_attestation_carriere_pdf
    pdf = generer_attestation_carriere_pdf(t, s, evt)
    log_action("EXPORT", "carriere", evt.id, "Attestation de carrière éditée")
    nom = f"attestation_{evt.type.lower()}_{(s.nom if s else 'salarie')}_{evt.id}.pdf"
    return _doc_response(pdf, nom)
