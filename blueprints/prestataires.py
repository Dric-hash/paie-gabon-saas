"""
blueprints/prestataires.py — Module de gestion des prestataires

Couvre :
    - Fiches prestataires (freelances, sous-traitants, fournisseurs)
    - Contrats de prestation
    - Factures (avec TVA et retenue à la source)
    - Paiements
    - États récapitulatifs (annuel par prestataire)
"""
import logging
from datetime import datetime, date

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, abort, current_app)
from flask_login import login_required, current_user
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from models import (db, Prestataire, ContratPrestation, FacturePrestataire,
                    PaiementPrestataire, AvancePrestataire, Site)
from audit import log_action
from core import get_tenant, can_edit, require_permission, parse_date
from devises import (taux_xaf, convertir_en_xaf, info_taux, devises_disponibles,
                     DEVISES)

logger = logging.getLogger("paiegalon")

bp = Blueprint("prestataires", __name__)


def _guard():
    """Vérifie l'accès tenant. Retourne (tenant, redirect_or_None)."""
    if current_user.is_super_admin:
        return None, redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return None, redirect(url_for("auth.login"))
    return t, None


# ══════════════════════════════════════════════════════════════════════════════
# LISTE DES PRESTATAIRES
# ══════════════════════════════════════════════════════════════════════════════
@bp.route("/prestataires")
@login_required
def prestataires():
    t, redir = _guard()
    if redir:
        return redir
    q         = request.args.get("q", "")
    categorie = request.args.get("categorie", "")
    statut    = request.args.get("statut", "")
    page      = request.args.get("page", 1, type=int)

    query = Prestataire.query.filter_by(tenant_id=t.id)
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(
            Prestataire.raison_sociale.ilike(like),
            Prestataire.code.ilike(like),
            Prestataire.nif.ilike(like),
            Prestataire.activite.ilike(like)))
    if categorie:
        query = query.filter_by(categorie=categorie)
    if statut:
        query = query.filter_by(statut=statut)

    pagination = query.order_by(Prestataire.raison_sociale).paginate(
        page=page, per_page=25, error_out=False)

    _args = {k: v for k, v in request.args.items() if k != "page"}
    _base = request.path + "?" + "&".join(f"{k}={v}" for k, v in _args.items())
    _sep  = "&" if _args else "?"

    # Statistiques rapides
    total      = Prestataire.query.filter_by(tenant_id=t.id).count()
    actifs     = Prestataire.query.filter_by(tenant_id=t.id, statut="ACTIF").count()

    return render_template("tenant/prestataires.html",
        tenant=t, prestataires=pagination.items, pagination=pagination,
        pagination_base=_base + _sep, q=q, categorie=categorie, statut=statut,
        total=total, actifs=actifs)


# ══════════════════════════════════════════════════════════════════════════════
# FICHE PRESTATAIRE (détail)
# ══════════════════════════════════════════════════════════════════════════════
@bp.route("/prestataires/<int:id>")
@login_required
def prestataire_detail(id):
    t, redir = _guard()
    if redir:
        return redir
    p = Prestataire.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    contrats = (ContratPrestation.query.filter_by(tenant_id=t.id, prestataire_id=id)
                .order_by(ContratPrestation.date_debut.desc()).all())
    factures = (FacturePrestataire.query.filter_by(tenant_id=t.id, prestataire_id=id)
                .order_by(FacturePrestataire.date_facture.desc()).all())
    avances = (AvancePrestataire.query.filter_by(tenant_id=t.id, prestataire_id=id)
               .filter(AvancePrestataire.statut != "ANNULEE")
               .order_by(AvancePrestataire.date_avance.desc()).all())

    # Totaux
    total_facture = sum(float(f.montant_net_a_payer or 0) for f in factures
                        if f.statut != "ANNULEE")
    total_paye    = sum(float(f.montant_paye or 0) for f in factures)
    total_du      = round(total_facture - total_paye, 2)
    # Avances : on totalise en XAF (devises mélangées possibles)
    total_avances = round(sum(a.montant_en_xaf for a in avances), 2)
    total_avances_a_regul = round(sum(a.reste_a_regulariser for a in avances), 2)
    # La dernière avance en attente : à présenter au chef de chantier pour validation
    derniere_en_attente = next((a for a in avances if a.statut == "EN_ATTENTE"), None)

    return render_template("tenant/prestataire_detail.html",
        tenant=t, p=p, contrats=contrats, factures=factures, avances=avances,
        total_facture=total_facture, total_paye=total_paye, total_du=total_du,
        total_avances=total_avances, total_avances_a_regul=total_avances_a_regul,
        derniere_en_attente=derniere_en_attente,
        devises_dispo=devises_disponibles(),
        sites=Site.query.filter_by(tenant_id=t.id).all())


# ══════════════════════════════════════════════════════════════════════════════
# CRÉER / MODIFIER UN PRESTATAIRE
# ══════════════════════════════════════════════════════════════════════════════
@bp.route("/prestataires/nouveau", methods=["GET", "POST"])
@login_required
@can_edit
def prestataire_nouveau():
    t, redir = _guard()
    if redir:
        return redir
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        if not code:
            # Générer un code automatique
            n = Prestataire.query.filter_by(tenant_id=t.id).count() + 1
            code = f"PRE{n:04d}"
        if Prestataire.query.filter_by(tenant_id=t.id, code=code).first():
            flash("Ce code prestataire existe déjà.", "error")
            return render_template("tenant/prestataire_form.html", tenant=t,
                                   p=None, sites=Site.query.filter_by(tenant_id=t.id).all())
        p = Prestataire(
            tenant_id=t.id, code=code,
            type_personne=request.form.get("type_personne", "MORALE"),
            categorie=request.form.get("categorie", "FREELANCE"),
            raison_sociale=request.form.get("raison_sociale", "").strip(),
            sigle=request.form.get("sigle", "").strip(),
            nom_contact=request.form.get("nom_contact", "").strip(),
            telephone=request.form.get("telephone", "").strip(),
            email=request.form.get("email", "").strip(),
            adresse=request.form.get("adresse", "").strip(),
            ville=request.form.get("ville", "Libreville"),
            nif=request.form.get("nif", "").strip(),
            rccm=request.form.get("rccm", "").strip(),
            activite=request.form.get("activite", "").strip(),
            mode_paiement=request.form.get("mode_paiement", "VIREMENT"),
            rib=request.form.get("rib", "").strip(),
            banque=request.form.get("banque", "").strip(),
            numero_mobile_money=request.form.get("numero_mobile_money", "").strip(),
            resident=request.form.get("resident") == "on",
            assujetti_tva=request.form.get("assujetti_tva") == "on",
            taux_retenue_source=float(request.form.get("taux_retenue_source", 0) or 0),
            notes=request.form.get("notes", "").strip(),
        )
        db.session.add(p)
        db.session.commit()
        log_action("CREATE", "prestataire", p.id,
                   f"Création prestataire {p.raison_sociale}",
                   user_id=current_user.id, tenant_id=t.id)
        db.session.commit()
        flash(f"Prestataire « {p.raison_sociale} » créé.", "success")
        return redirect(url_for("prestataires.prestataire_detail", id=p.id))
    return render_template("tenant/prestataire_form.html", tenant=t, p=None,
                           sites=Site.query.filter_by(tenant_id=t.id).all())


@bp.route("/prestataires/<int:id>/modifier", methods=["GET", "POST"])
@login_required
@can_edit
def prestataire_modifier(id):
    t, redir = _guard()
    if redir:
        return redir
    p = Prestataire.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    if request.method == "POST":
        p.type_personne  = request.form.get("type_personne", p.type_personne)
        p.categorie      = request.form.get("categorie", p.categorie)
        p.raison_sociale = request.form.get("raison_sociale", "").strip()
        p.sigle          = request.form.get("sigle", "").strip()
        p.nom_contact    = request.form.get("nom_contact", "").strip()
        p.telephone      = request.form.get("telephone", "").strip()
        p.email          = request.form.get("email", "").strip()
        p.adresse        = request.form.get("adresse", "").strip()
        p.ville          = request.form.get("ville", "Libreville")
        p.nif            = request.form.get("nif", "").strip()
        p.rccm           = request.form.get("rccm", "").strip()
        p.activite       = request.form.get("activite", "").strip()
        p.mode_paiement  = request.form.get("mode_paiement", "VIREMENT")
        p.rib            = request.form.get("rib", "").strip()
        p.banque         = request.form.get("banque", "").strip()
        p.numero_mobile_money = request.form.get("numero_mobile_money", "").strip()
        p.resident       = request.form.get("resident") == "on"
        p.assujetti_tva  = request.form.get("assujetti_tva") == "on"
        p.taux_retenue_source = float(request.form.get("taux_retenue_source", 0) or 0)
        p.statut         = request.form.get("statut", p.statut)
        p.notes          = request.form.get("notes", "").strip()
        db.session.commit()
        log_action("UPDATE", "prestataire", p.id,
                   f"Modification prestataire {p.raison_sociale}",
                   user_id=current_user.id, tenant_id=t.id)
        db.session.commit()
        flash("Prestataire mis à jour.", "success")
        return redirect(url_for("prestataires.prestataire_detail", id=p.id))
    return render_template("tenant/prestataire_form.html", tenant=t, p=p,
                           sites=Site.query.filter_by(tenant_id=t.id).all())


@bp.route("/prestataires/<int:id>/supprimer", methods=["POST"])
@login_required
@can_edit
def prestataire_supprimer(id):
    t, redir = _guard()
    if redir:
        return redir
    p = Prestataire.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    # Empêcher la suppression si des factures existent
    if FacturePrestataire.query.filter_by(tenant_id=t.id, prestataire_id=id).count() > 0:
        flash("Impossible de supprimer : ce prestataire a des factures. "
              "Passez-le plutôt en statut « Inactif ».", "error")
        return redirect(url_for("prestataires.prestataire_detail", id=id))
    nom = p.raison_sociale
    ContratPrestation.query.filter_by(tenant_id=t.id, prestataire_id=id).delete()
    db.session.delete(p)
    db.session.commit()
    log_action("DELETE", "prestataire", id, f"Suppression prestataire {nom}",
               user_id=current_user.id, tenant_id=t.id)
    db.session.commit()
    flash(f"Prestataire « {nom} » supprimé.", "success")
    return redirect(url_for("prestataires.prestataires"))


# ══════════════════════════════════════════════════════════════════════════════
# CONTRATS DE PRESTATION
# ══════════════════════════════════════════════════════════════════════════════
@bp.route("/prestataires/<int:id>/contrats/nouveau", methods=["POST"])
@login_required
@can_edit
def contrat_prestation_nouveau(id):
    t, redir = _guard()
    if redir:
        return redir
    p = Prestataire.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    c = ContratPrestation(
        tenant_id=t.id, prestataire_id=id,
        reference=request.form.get("reference", "").strip(),
        objet=request.form.get("objet", "").strip(),
        type_remuneration=request.form.get("type_remuneration", "FORFAIT"),
        montant=float(request.form.get("montant", 0) or 0),
        date_debut=parse_date(request.form.get("date_debut")) or date.today(),
        date_fin=parse_date(request.form.get("date_fin")),
        site_id=request.form.get("site_id", type=int) or None,
        conditions=request.form.get("conditions", "").strip(),
    )
    db.session.add(c)
    db.session.commit()
    log_action("CREATE", "contrat_prestation", c.id,
               f"Contrat prestation {p.raison_sociale} : {c.objet}",
               user_id=current_user.id, tenant_id=t.id)
    db.session.commit()
    flash("Contrat de prestation ajouté.", "success")
    return redirect(url_for("prestataires.prestataire_detail", id=id))


@bp.route("/prestataires/contrats/<int:cid>/statut", methods=["POST"])
@login_required
@can_edit
def contrat_prestation_statut(cid):
    t, redir = _guard()
    if redir:
        return redir
    c = ContratPrestation.query.filter_by(id=cid, tenant_id=t.id).first_or_404()
    c.statut = request.form.get("statut", c.statut)
    db.session.commit()
    flash("Statut du contrat mis à jour.", "success")
    return redirect(url_for("prestataires.prestataire_detail", id=c.prestataire_id))


# ══════════════════════════════════════════════════════════════════════════════
# FACTURES
# ══════════════════════════════════════════════════════════════════════════════
@bp.route("/prestataires/<int:id>/factures/nouvelle", methods=["POST"])
@login_required
@can_edit
def facture_nouvelle(id):
    t, redir = _guard()
    if redir:
        return redir
    p = Prestataire.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    numero = request.form.get("numero", "").strip()
    if FacturePrestataire.query.filter_by(tenant_id=t.id, prestataire_id=id, numero=numero).first():
        flash("Une facture avec ce numéro existe déjà pour ce prestataire.", "error")
        return redirect(url_for("prestataires.prestataire_detail", id=id))

    # Taux par défaut depuis la fiche prestataire
    taux_tva     = 18 if p.assujetti_tva else 0
    taux_retenue = float(p.taux_retenue_source or 0)

    devise = (request.form.get("devise") or "XAF").upper()
    if devise not in DEVISES:
        devise = "XAF"
    taux_ch = request.form.get("taux_change", type=float)
    if not taux_ch or taux_ch <= 0:
        taux_ch = taux_xaf(devise, parse_date(request.form.get("date_facture")))

    f = FacturePrestataire(
        tenant_id=t.id, prestataire_id=id,
        contrat_id=request.form.get("contrat_id", type=int) or None,
        site_id=request.form.get("site_id", type=int) or None,
        numero=numero,
        date_facture=parse_date(request.form.get("date_facture")) or date.today(),
        date_echeance=parse_date(request.form.get("date_echeance")),
        description=request.form.get("description", "").strip(),
        surface_m2=request.form.get("surface_m2", type=float) or None,
        prix_unitaire_m2=request.form.get("prix_unitaire_m2", type=float) or None,
        pourcentage_realisation=request.form.get("pourcentage_realisation", type=float) or 0,
        montant_ht=float(request.form.get("montant_ht", 0) or 0),
        taux_tva=float(request.form.get("taux_tva", taux_tva) or 0),
        taux_retenue=float(request.form.get("taux_retenue", taux_retenue) or 0),
        devise=devise, taux_change=taux_ch,
    )
    f.calculer()
    db.session.add(f)
    db.session.commit()
    log_action("CREATE", "facture_prestataire", f.id,
               f"Facture {f.numero} — {p.raison_sociale} — {f.montant_net_a_payer} XAF",
               user_id=current_user.id, tenant_id=t.id)
    db.session.commit()
    flash(f"Facture {f.numero} enregistrée (net à payer : {int(f.montant_net_a_payer):,} XAF).".replace(",", " "), "success")
    return redirect(url_for("prestataires.prestataire_detail", id=id))


@bp.route("/prestataires/factures/<int:fid>/payer", methods=["POST"])
@login_required
@can_edit
def facture_payer(fid):
    t, redir = _guard()
    if redir:
        return redir
    f = FacturePrestataire.query.filter_by(id=fid, tenant_id=t.id).first_or_404()
    montant = float(request.form.get("montant", 0) or 0)
    if montant <= 0:
        flash("Le montant du paiement doit être positif.", "error")
        return redirect(url_for("prestataires.prestataire_detail", id=f.prestataire_id))
    if montant > f.reste_a_payer + 0.01:
        flash(f"Le montant dépasse le reste à payer ({int(f.reste_a_payer):,} XAF).".replace(",", " "), "error")
        return redirect(url_for("prestataires.prestataire_detail", id=f.prestataire_id))

    paie = PaiementPrestataire(
        tenant_id=t.id, facture_id=fid, montant=montant,
        date_paiement=parse_date(request.form.get("date_paiement")) or date.today(),
        mode_paiement=request.form.get("mode_paiement", "VIREMENT"),
        pourcentage_realisation=request.form.get("pourcentage_realisation", type=float) or 0,
        reference=request.form.get("reference", "").strip(),
        notes=request.form.get("notes", "").strip(),
    )
    db.session.add(paie)
    # Mettre à jour la facture
    f.montant_paye = round(float(f.montant_paye or 0) + montant, 2)
    if f.montant_paye >= float(f.montant_net_a_payer) - 0.01:
        f.statut = "PAYEE"
    elif f.montant_paye > 0:
        f.statut = "PARTIELLE"
    db.session.commit()
    log_action("CREATE", "paiement_prestataire", paie.id,
               f"Paiement {montant} XAF sur facture {f.numero}",
               user_id=current_user.id, tenant_id=t.id)
    db.session.commit()
    flash(f"Paiement de {int(montant):,} XAF enregistré.".replace(",", " "), "success")
    return redirect(url_for("prestataires.prestataire_detail", id=f.prestataire_id))


@bp.route("/prestataires/factures/<int:fid>/annuler", methods=["POST"])
@login_required
@can_edit
def facture_annuler(fid):
    t, redir = _guard()
    if redir:
        return redir
    f = FacturePrestataire.query.filter_by(id=fid, tenant_id=t.id).first_or_404()
    f.statut = "ANNULEE"
    db.session.commit()
    flash(f"Facture {f.numero} annulée.", "success")
    return redirect(url_for("prestataires.prestataire_detail", id=f.prestataire_id))


# ══════════════════════════════════════════════════════════════════════════════
# AVANCES (versées hors facture, fréquentes avec les sous-traitants)
# ══════════════════════════════════════════════════════════════════════════════
@bp.route("/prestataires/<int:id>/avances/nouvelle", methods=["POST"])
@login_required
@can_edit
def avance_nouvelle(id):
    t, redir = _guard()
    if redir:
        return redir
    p = Prestataire.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    montant = float(request.form.get("montant", 0) or 0)
    if montant <= 0:
        flash("Le montant de l'avance doit être positif.", "error")
        return redirect(url_for("prestataires.prestataire_detail", id=id))

    devise = (request.form.get("devise") or "XAF").upper()
    if devise not in DEVISES:
        devise = "XAF"
    # Taux : valeur saisie (modifiable) sinon taux du jour
    taux = request.form.get("taux_change", type=float)
    if not taux or taux <= 0:
        taux = taux_xaf(devise, parse_date(request.form.get("date_avance")))

    a = AvancePrestataire(
        tenant_id=t.id, prestataire_id=id,
        contrat_id=request.form.get("contrat_id", type=int) or None,
        site_id=request.form.get("site_id", type=int) or None,
        montant=montant,
        devise=devise, taux_change=taux,
        montant_xaf=round(montant * taux, 2),
        date_avance=parse_date(request.form.get("date_avance")) or date.today(),
        mode_paiement=request.form.get("mode_paiement", "VIREMENT"),
        reference=request.form.get("reference", "").strip(),
        motif=request.form.get("motif", "").strip(),
        statut="EN_ATTENTE",
    )
    db.session.add(a)
    db.session.commit()
    log_action("CREATE", "avance_prestataire", a.id,
               f"Avance {int(a.montant_en_xaf):,} XAF — {p.raison_sociale}".replace(",", " "),
               user_id=current_user.id, tenant_id=t.id)
    db.session.commit()
    flash(f"Avance de {int(montant):,} {devise} enregistrée.".replace(",", " "), "success")
    return redirect(url_for("prestataires.prestataire_detail", id=id))


@bp.route("/prestataires/avances/<int:aid>/modifier", methods=["POST"])
@login_required
@can_edit
def avance_modifier(aid):
    t, redir = _guard()
    if redir:
        return redir
    a = AvancePrestataire.query.filter_by(id=aid, tenant_id=t.id).first_or_404()
    if not a.est_modifiable:
        flash("Cette avance est validée : elle ne peut plus être modifiée.", "error")
        return redirect(url_for("prestataires.prestataire_detail", id=a.prestataire_id))

    montant = float(request.form.get("montant", a.montant) or 0)
    if montant <= 0:
        flash("Le montant de l'avance doit être positif.", "error")
        return redirect(url_for("prestataires.prestataire_detail", id=a.prestataire_id))
    devise = (request.form.get("devise") or a.devise or "XAF").upper()
    if devise not in DEVISES:
        devise = "XAF"
    taux = request.form.get("taux_change", type=float)
    if not taux or taux <= 0:
        taux = taux_xaf(devise, parse_date(request.form.get("date_avance")))

    a.montant       = montant
    a.devise        = devise
    a.taux_change   = taux
    a.montant_xaf   = round(montant * taux, 2)
    a.site_id       = request.form.get("site_id", type=int) or None
    a.contrat_id    = request.form.get("contrat_id", type=int) or None
    a.date_avance   = parse_date(request.form.get("date_avance")) or a.date_avance
    a.mode_paiement = request.form.get("mode_paiement", a.mode_paiement)
    a.reference     = request.form.get("reference", "").strip()
    a.motif         = request.form.get("motif", "").strip()
    db.session.commit()
    log_action("UPDATE", "avance_prestataire", a.id, "Modification avance",
               user_id=current_user.id, tenant_id=t.id)
    db.session.commit()
    flash("Avance modifiée.", "success")
    return redirect(url_for("prestataires.prestataire_detail", id=a.prestataire_id))


@bp.route("/prestataires/avances/<int:aid>/valider", methods=["POST"])
@login_required
@can_edit
def avance_valider(aid):
    """Validation par le chef de chantier : fige l'avance (plus de modif/suppression)."""
    t, redir = _guard()
    if redir:
        return redir
    a = AvancePrestataire.query.filter_by(id=aid, tenant_id=t.id).first_or_404()
    if a.statut == "VALIDEE":
        flash("Cette avance est déjà validée.", "info")
        return redirect(url_for("prestataires.prestataire_detail", id=a.prestataire_id))
    chef = request.form.get("valide_par_nom", "").strip()
    if not chef:
        flash("Indiquez le nom du chef de chantier qui valide l'avance.", "error")
        return redirect(url_for("prestataires.prestataire_detail", id=a.prestataire_id))
    a.statut = "VALIDEE"
    a.valide_par_nom = chef
    a.valide_par_user_id = current_user.id
    a.date_validation = datetime.utcnow()
    db.session.commit()
    log_action("VALIDATE", "avance_prestataire", a.id,
               f"Avance validée par {chef}", user_id=current_user.id, tenant_id=t.id)
    db.session.commit()
    flash(f"Avance validée par {chef}. Elle est désormais figée.", "success")
    return redirect(url_for("prestataires.prestataire_detail", id=a.prestataire_id))


@bp.route("/prestataires/avances/<int:aid>/supprimer", methods=["POST"])
@login_required
@can_edit
def avance_supprimer(aid):
    t, redir = _guard()
    if redir:
        return redir
    a = AvancePrestataire.query.filter_by(id=aid, tenant_id=t.id).first_or_404()
    pid = a.prestataire_id
    if a.statut == "VALIDEE":
        flash("Cette avance est validée : elle ne peut plus être supprimée.", "error")
        return redirect(url_for("prestataires.prestataire_detail", id=pid))
    db.session.delete(a)
    db.session.commit()
    log_action("DELETE", "avance_prestataire", aid, "Suppression avance",
               user_id=current_user.id, tenant_id=t.id)
    db.session.commit()
    flash("Avance supprimée.", "success")
    return redirect(url_for("prestataires.prestataire_detail", id=pid))


# ══════════════════════════════════════════════════════════════════════════════
# RELEVÉ IMPRIMABLE PAR PRESTATAIRE / SOUS-TRAITANT
# ══════════════════════════════════════════════════════════════════════════════
@bp.route("/prestataires/<int:id>/releve")
@login_required
def prestataire_releve(id):
    """Relevé imprimable d'un prestataire : identité, factures, et la liste des
    avances déjà perçues, avec une synthèse financière (facturé / payé / avances /
    solde). Imprimable directement (window.print)."""
    t, redir = _guard()
    if redir:
        return redir
    p = Prestataire.query.filter_by(id=id, tenant_id=t.id).first_or_404()

    factures = (FacturePrestataire.query.filter_by(tenant_id=t.id, prestataire_id=id)
                .filter(FacturePrestataire.statut != "ANNULEE")
                .order_by(FacturePrestataire.date_facture).all())
    avances = (AvancePrestataire.query.filter_by(tenant_id=t.id, prestataire_id=id)
               .filter(AvancePrestataire.statut != "ANNULEE")
               .order_by(AvancePrestataire.date_avance).all())

    total_facture = sum(float(f.montant_net_a_payer or 0) for f in factures)
    total_paye    = sum(float(f.montant_paye or 0) for f in factures)
    total_avances = round(sum(a.montant_en_xaf for a in avances), 2)
    total_avances_regul = sum(float(a.montant_regularise or 0) for a in avances)
    # Solde net dû = net facturé - déjà payé - avances non encore régularisées.
    avances_non_regul = round(total_avances - total_avances_regul, 2)
    solde = round(total_facture - total_paye - avances_non_regul, 2)
    # Y a-t-il au moins une devise étrangère à afficher ?
    multi_devises = any((a.devise or "XAF") != "XAF" for a in avances) or \
                    any((f.devise or "XAF") != "XAF" for f in factures)

    return render_template("tenant/prestataire_releve_print.html",
        tenant=t, p=p, factures=factures, avances=avances,
        total_facture=total_facture, total_paye=total_paye,
        total_avances=total_avances, avances_non_regul=avances_non_regul,
        solde=solde, multi_devises=multi_devises, DEVISES=DEVISES,
        now=datetime.now())


@bp.route("/api/prestataire/taux-devise")
@login_required
def api_taux_devise():
    """Renvoie le taux du jour (XAF pour 1 unité) pour la conversion en direct."""
    t = get_tenant()
    if not t:
        return jsonify({"erreur": "non connecté"}), 401
    devise = (request.args.get("devise") or "XAF").upper()
    if devise not in DEVISES:
        return jsonify({"erreur": "devise inconnue"}), 400
    jour = parse_date(request.args.get("date"))
    return jsonify(info_taux(devise, jour))


# ══════════════════════════════════════════════════════════════════════════════
# API : calcul facture en temps réel
# ══════════════════════════════════════════════════════════════════════════════
@bp.route("/api/prestataire/calculer-facture", methods=["POST"])
@login_required
def api_calculer_facture():
    """Calcule TVA, retenue et net à payer en temps réel pour le formulaire."""
    t = get_tenant()
    if not t:
        return jsonify({"erreur": "non connecté"}), 401
    data = request.get_json(force=True) or {}
    ht = float(data.get("montant_ht", 0) or 0)
    taux_tva = float(data.get("taux_tva", 18) or 0)
    taux_retenue = float(data.get("taux_retenue", 0) or 0)
    tva = round(ht * taux_tva / 100, 2)
    ttc = round(ht + tva, 2)
    retenue = round(ht * taux_retenue / 100, 2)
    net = round(ttc - retenue, 2)
    return jsonify({
        "montant_tva": tva, "montant_ttc": ttc,
        "montant_retenue": retenue, "montant_net_a_payer": net,
    })


# ══════════════════════════════════════════════════════════════════════════════
# ÉTAT RÉCAPITULATIF ANNUEL (états fiscaux)
# ══════════════════════════════════════════════════════════════════════════════
@bp.route("/prestataires/etat-annuel")
@login_required
def etat_annuel():
    """
    État récapitulatif annuel des sommes versées par prestataire.
    Utile pour les déclarations fiscales et le suivi des retenues à la source.
    """
    t, redir = _guard()
    if redir:
        return redir
    annee = request.args.get("annee", datetime.now().year, type=int)

    # Agréger les factures par prestataire sur l'année
    factures = (FacturePrestataire.query
                .filter_by(tenant_id=t.id)
                .filter(func.extract("year", FacturePrestataire.date_facture) == annee)
                .filter(FacturePrestataire.statut != "ANNULEE")
                .options(joinedload(FacturePrestataire.prestataire))
                .all())

    recap = {}
    for f in factures:
        pid = f.prestataire_id
        if pid not in recap:
            recap[pid] = {
                "prestataire": f.prestataire,
                "nb_factures": 0,
                "total_ht": 0.0, "total_tva": 0.0,
                "total_retenue": 0.0, "total_ttc": 0.0,
                "total_net": 0.0, "total_paye": 0.0,
            }
        r = recap[pid]
        r["nb_factures"]   += 1
        r["total_ht"]      += float(f.montant_ht or 0)
        r["total_tva"]     += float(f.montant_tva or 0)
        r["total_retenue"] += float(f.montant_retenue or 0)
        r["total_ttc"]     += float(f.montant_ttc or 0)
        r["total_net"]     += float(f.montant_net_a_payer or 0)
        r["total_paye"]    += float(f.montant_paye or 0)

    lignes = sorted(recap.values(),
                    key=lambda x: x["prestataire"].raison_sociale if x["prestataire"] else "")

    totaux = {
        "ht":      sum(l["total_ht"] for l in lignes),
        "tva":     sum(l["total_tva"] for l in lignes),
        "retenue": sum(l["total_retenue"] for l in lignes),
        "net":     sum(l["total_net"] for l in lignes),
        "paye":    sum(l["total_paye"] for l in lignes),
    }
    annees_dispo = list(range(datetime.now().year - 2, datetime.now().year + 1))

    return render_template("tenant/prestataires_etat_annuel.html",
        tenant=t, annee=annee, lignes=lignes, totaux=totaux,
        annees_dispo=annees_dispo)
