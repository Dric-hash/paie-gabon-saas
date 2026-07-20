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

from models import (db, utcnow, Prestataire, ContratPrestation, FacturePrestataire,
                    PaiementPrestataire, AvancePrestataire, LigneFacturePrestataire,
                    Site)
from audit import log_action
from core import get_tenant, can_edit, require_permission, parse_date, admin_only
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


def _imputer_avances(factures, avances):
    """Impute (déduit) automatiquement les avances sur les factures, par chantier.

    Règle métier :
      • Une avance rattachée à un chantier est déduite des factures de CE chantier.
      • Une avance sans chantier alimente un pool « général » déduit des factures
        sans chantier.
      • Allocation chronologique : la facture la plus ancienne du chantier est
        soldée en premier.
    Tout est converti en **XAF** (via montant_en_xaf / taux_change), de sorte que
    le solde est correct même si les avances sont en EUR, USD, MAD…

    Renvoie un dict :
      avance_xaf[fid]  : avance imputée sur la facture (XAF)
      solde_xaf[fid]   : solde restant après paiements ET avances (XAF, ≥ 0)
      reste_paye_xaf[fid] : reste après paiements seuls (XAF)
      total_facture_xaf, total_paye_xaf, total_avances_xaf,
      total_avances_imputees_xaf, avance_disponible_xaf, solde_net_xaf
    """
    from collections import defaultdict

    actives = [a for a in avances if a.statut != "ANNULEE"]
    pools = defaultdict(float)
    total_avances_xaf = 0.0
    for a in actives:
        pools[a.site_id] += a.montant_en_xaf
        total_avances_xaf += a.montant_en_xaf

    facts = [f for f in factures if f.statut != "ANNULEE"]
    facts_ordre = sorted(facts, key=lambda f: (f.date_facture, f.id))

    par_site = defaultdict(list)
    for f in facts_ordre:
        par_site[f.site_id].append(f)

    avance_xaf, solde_xaf, reste_paye_xaf = {}, {}, {}
    # Mêmes montants, mais exprimés dans la DEVISE de chaque facture
    # (l'avance imputée, en XAF, est reconvertie dans la devise de la facture).
    avance_dev, solde_dev, reste_paye_dev = {}, {}, {}
    total_facture_xaf = total_paye_xaf = 0.0
    for site_id, fs in par_site.items():
        pool = pools.get(site_id, 0.0)
        for f in fs:
            net   = f.montant_net_en_xaf
            taux  = float(f.taux_change or 1) or 1
            paye  = round(float(f.montant_paye or 0) * taux, 2)
            reste = max(0.0, round(net - paye, 2))
            imput = round(min(pool, reste), 2)
            pool  = round(pool - imput, 2)
            total_facture_xaf += net
            total_paye_xaf    += paye
            avance_xaf[f.id]     = imput
            reste_paye_xaf[f.id] = reste
            solde_xaf[f.id]      = round(reste - imput, 2)
            # ── Conversion dans la devise de la facture ──────────────────────
            net_d   = round(float(f.montant_net_a_payer or 0), 2)
            paye_d  = round(float(f.montant_paye or 0), 2)
            reste_d = max(0.0, round(net_d - paye_d, 2))
            imput_d = round(imput / taux, 2)              # XAF → devise facture
            imput_d = min(imput_d, reste_d)               # garde-fou arrondi
            avance_dev[f.id]     = imput_d
            reste_paye_dev[f.id] = reste_d
            solde_dev[f.id]      = max(0.0, round(reste_d - imput_d, 2))
        pools[site_id] = pool

    avance_disponible_xaf = round(sum(max(0.0, v) for v in pools.values()), 2)
    total_avances_imputees_xaf = round(total_avances_xaf - avance_disponible_xaf, 2)
    solde_net_xaf = round(sum(solde_xaf.values()), 2)

    return {
        "avance_xaf": avance_xaf, "solde_xaf": solde_xaf,
        "reste_paye_xaf": reste_paye_xaf,
        "avance_dev": avance_dev, "solde_dev": solde_dev,
        "reste_paye_dev": reste_paye_dev,
        "total_facture_xaf": round(total_facture_xaf, 2),
        "total_paye_xaf": round(total_paye_xaf, 2),
        "total_avances_xaf": round(total_avances_xaf, 2),
        "total_avances_imputees_xaf": total_avances_imputees_xaf,
        "avance_disponible_xaf": avance_disponible_xaf,
        "solde_net_xaf": solde_net_xaf,
    }


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

    # Imputation automatique des avances sur les factures (par chantier, en XAF)
    imp = _imputer_avances(factures, avances)
    total_facture = imp["total_facture_xaf"]
    total_paye    = imp["total_paye_xaf"]
    total_avances = imp["total_avances_xaf"]
    avances_imputees   = imp["total_avances_imputees_xaf"]
    avance_disponible  = imp["avance_disponible_xaf"]
    solde_net          = imp["solde_net_xaf"]
    # Soldes par facture (après paiements ET avances), DANS LA DEVISE de la facture
    # (l'avance imputée est convertie XAF → devise de la facture).
    solde_par_facture  = imp["solde_dev"]
    avance_par_facture = imp["avance_dev"]
    # La dernière avance en attente : à présenter au chef de chantier pour validation
    derniere_en_attente = next((a for a in avances if a.statut == "EN_ATTENTE"), None)

    # Données JSON des factures (pour préremplir la modale d'édition côté JS)
    factures_data = {
        f.id: {"f": f.to_dict(), "lignes": [l.to_dict() for l in f.lignes]}
        for f in factures
    }

    return render_template("tenant/prestataire_detail.html",
        tenant=t, p=p, contrats=contrats, factures=factures, avances=avances,
        factures_data=factures_data,
        total_facture=total_facture, total_paye=total_paye,
        total_avances=total_avances, avances_imputees=avances_imputees,
        avance_disponible=avance_disponible, solde_net=solde_net,
        solde_par_facture=solde_par_facture, avance_par_facture=avance_par_facture,
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
        pourcentage_realisation=request.form.get("pourcentage_realisation", type=float) or 0,
        montant_ht=float(request.form.get("montant_ht", 0) or 0),
        taux_tva=float(request.form.get("taux_tva", taux_tva) or 0),
        taux_retenue=float(request.form.get("taux_retenue", taux_retenue) or 0),
        devise=devise, taux_change=taux_ch,
        statut="BROUILLON",
    )
    _appliquer_lignes(f, t.id)
    f.calculer()
    db.session.add(f)
    db.session.commit()
    log_action("CREATE", "facture_prestataire", f.id,
               f"Facture {f.numero} — {p.raison_sociale} — {f.montant_net_a_payer} {devise}",
               user_id=current_user.id, tenant_id=t.id)
    db.session.commit()
    flash(f"Facture {f.numero} enregistrée en brouillon. Validez-la avant paiement.", "success")
    return redirect(url_for("prestataires.prestataire_detail", id=id))


def _appliquer_lignes(f, tenant_id):
    """(Re)construit les lignes de détail d'une facture à partir du formulaire.

    Champs répétés : ligne_designation[], ligne_quantite[], ligne_unite[],
    ligne_prix[]. Le HT découle de la somme des lignes si au moins une existe.
    """
    designations = request.form.getlist("ligne_designation")
    quantites    = request.form.getlist("ligne_quantite")
    quantites_tot = request.form.getlist("ligne_quantite_totale")
    unites       = request.form.getlist("ligne_unite")
    prix         = request.form.getlist("ligne_prix")

    # Vider les lignes existantes (édition)
    if f.id:
        LigneFacturePrestataire.query.filter_by(facture_id=f.id).delete()
    f.lignes = []

    ordre = 0
    for i, des in enumerate(designations):
        des = (des or "").strip()
        if not des:
            continue
        def _num(lst, idx, defaut=0.0):
            try:
                return float(lst[idx]) if idx < len(lst) and lst[idx] not in (None, "") else defaut
            except (ValueError, TypeError):
                return defaut
        q  = _num(quantites, i, 1.0)
        qt = _num(quantites_tot, i, 0.0)
        pu = _num(prix, i, 0.0)
        u  = (unites[i].strip() if i < len(unites) and unites[i] else "u")
        ordre += 1
        ligne = LigneFacturePrestataire(
            tenant_id=tenant_id, designation=des, quantite=q,
            quantite_totale=(qt if qt > 0 else None), unite=u,
            prix_unitaire=pu, ordre=ordre)
        ligne.calculer()
        f.lignes.append(ligne)


@bp.route("/prestataires/factures/<int:fid>/modifier", methods=["POST"])
@login_required
@can_edit
def facture_modifier(fid):
    t, redir = _guard()
    if redir:
        return redir
    f = FacturePrestataire.query.filter_by(id=fid, tenant_id=t.id).first_or_404()
    if not f.est_modifiable and not current_user.is_tenant_admin:
        flash("Cette facture est validée : seul l'administrateur du compte peut la modifier.", "error")
        return redirect(url_for("prestataires.prestataire_detail", id=f.prestataire_id))

    numero = request.form.get("numero", "").strip()
    doublon = (FacturePrestataire.query
               .filter_by(tenant_id=t.id, prestataire_id=f.prestataire_id, numero=numero)
               .filter(FacturePrestataire.id != fid).first())
    if doublon:
        flash("Une autre facture porte déjà ce numéro.", "error")
        return redirect(url_for("prestataires.prestataire_detail", id=f.prestataire_id))

    devise = (request.form.get("devise") or f.devise or "XAF").upper()
    if devise not in DEVISES:
        devise = "XAF"
    taux_ch = request.form.get("taux_change", type=float)
    if not taux_ch or taux_ch <= 0:
        taux_ch = taux_xaf(devise, parse_date(request.form.get("date_facture")))

    f.numero        = numero or f.numero
    f.date_facture  = parse_date(request.form.get("date_facture")) or f.date_facture
    f.date_echeance = parse_date(request.form.get("date_echeance"))
    f.description   = request.form.get("description", "").strip()
    f.site_id       = request.form.get("site_id", type=int) or None
    f.contrat_id    = request.form.get("contrat_id", type=int) or None
    f.pourcentage_realisation = request.form.get("pourcentage_realisation", type=float) or 0
    f.montant_ht    = float(request.form.get("montant_ht", f.montant_ht) or 0)
    f.taux_tva      = float(request.form.get("taux_tva", f.taux_tva) or 0)
    f.taux_retenue  = float(request.form.get("taux_retenue", f.taux_retenue) or 0)
    f.devise        = devise
    f.taux_change   = taux_ch
    _appliquer_lignes(f, t.id)
    f.calculer()
    # Si l'admin modifie une facture déjà validée/payée, on garde un statut cohérent
    if f.statut not in ("BROUILLON", "ANNULEE"):
        paye = float(f.montant_paye or 0)
        if paye >= float(f.montant_net_a_payer) - 0.01 and paye > 0:
            f.statut = "PAYEE"
        elif paye > 0:
            f.statut = "PARTIELLE"
        else:
            f.statut = "VALIDEE"
    db.session.commit()
    log_action("UPDATE", "facture_prestataire", f.id, f"Modification facture {f.numero}",
               user_id=current_user.id, tenant_id=t.id)
    db.session.commit()
    flash(f"Facture {f.numero} modifiée.", "success")
    return redirect(url_for("prestataires.prestataire_detail", id=f.prestataire_id))


@bp.route("/prestataires/factures/<int:fid>/supprimer", methods=["POST"])
@login_required
@admin_only
def facture_supprimer(fid):
    """Suppression définitive d'une facture — réservée à l'administrateur du compte.

    Supprime aussi ses paiements et ses lignes de détail (même si la facture est
    validée ou payée). Action enregistrée dans le journal d'audit.
    """
    t, redir = _guard()
    if redir:
        return redir
    f = FacturePrestataire.query.filter_by(id=fid, tenant_id=t.id).first_or_404()
    pid, numero = f.prestataire_id, f.numero
    # Retirer d'abord les paiements liés (pas de cascade sur cette relation)
    PaiementPrestataire.query.filter_by(tenant_id=t.id, facture_id=fid).delete()
    db.session.delete(f)   # les lignes partent par cascade delete-orphan
    db.session.commit()
    log_action("DELETE", "facture_prestataire", fid,
               f"Suppression facture {numero} (admin)",
               user_id=current_user.id, tenant_id=t.id)
    db.session.commit()
    flash(f"Facture {numero} supprimée définitivement.", "success")
    return redirect(url_for("prestataires.prestataire_detail", id=pid))


@bp.route("/prestataires/factures/<int:fid>/valider", methods=["POST"])
@login_required
@can_edit
def facture_valider(fid):
    """Validation de la facture : la rend payable et la fige (plus de modif)."""
    t, redir = _guard()
    if redir:
        return redir
    f = FacturePrestataire.query.filter_by(id=fid, tenant_id=t.id).first_or_404()
    if f.statut != "BROUILLON":
        flash("Seule une facture en brouillon peut être validée.", "info")
        return redirect(url_for("prestataires.prestataire_detail", id=f.prestataire_id))
    if float(f.montant_net_a_payer or 0) <= 0:
        flash("Impossible de valider une facture à 0. Ajoutez au moins une ligne.", "error")
        return redirect(url_for("prestataires.prestataire_detail", id=f.prestataire_id))
    f.statut = "VALIDEE"
    f.valide_par_nom = (request.form.get("valide_par_nom", "").strip() or None)
    f.valide_par_user_id = current_user.id
    f.date_validation = utcnow()
    db.session.commit()
    log_action("VALIDATE", "facture_prestataire", f.id, f"Facture {f.numero} validée",
               user_id=current_user.id, tenant_id=t.id)
    db.session.commit()
    flash(f"Facture {f.numero} validée. Elle peut maintenant être payée.", "success")
    return redirect(url_for("prestataires.prestataire_detail", id=f.prestataire_id))


@bp.route("/prestataires/factures/<int:fid>/payer", methods=["POST"])
@login_required
@can_edit
def facture_payer(fid):
    t, redir = _guard()
    if redir:
        return redir
    f = FacturePrestataire.query.filter_by(id=fid, tenant_id=t.id).first_or_404()
    if not f.est_payable:
        flash("La facture doit être validée avant d'enregistrer un paiement.", "error")
        return redirect(url_for("prestataires.prestataire_detail", id=f.prestataire_id))
    montant = float(request.form.get("montant", 0) or 0)
    if montant <= 0:
        flash("Le montant du paiement doit être positif.", "error")
        return redirect(url_for("prestataires.prestataire_detail", id=f.prestataire_id))
    if montant > f.reste_a_payer + 0.01:
        flash(f"Le montant dépasse le reste à payer ({int(f.reste_a_payer):,} {f.devise}).".replace(",", " "), "error")
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
    f.montant_paye = round(float(f.montant_paye or 0) + montant, 2)
    if f.montant_paye >= float(f.montant_net_a_payer) - 0.01:
        f.statut = "PAYEE"
    elif f.montant_paye > 0:
        f.statut = "PARTIELLE"
    db.session.commit()
    log_action("CREATE", "paiement_prestataire", paie.id,
               f"Paiement {montant} {f.devise} sur facture {f.numero}",
               user_id=current_user.id, tenant_id=t.id)
    db.session.commit()
    flash(f"Paiement de {int(montant):,} {f.devise} enregistré.".replace(",", " "), "success")
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


@bp.route("/prestataires/factures/<int:fid>/imprimer")
@login_required
def facture_imprimer(fid):
    """Facture imprimable (lignes de détail, totaux, devise + équivalent XAF,
    avances du chantier déduites et solde net automatiquement calculé)."""
    t, redir = _guard()
    if redir:
        return redir
    f = FacturePrestataire.query.filter_by(id=fid, tenant_id=t.id).first_or_404()
    p = Prestataire.query.filter_by(id=f.prestataire_id, tenant_id=t.id).first_or_404()
    site = Site.query.filter_by(id=f.site_id, tenant_id=t.id).first() if f.site_id else None

    # Imputation des avances sur l'ensemble des factures du prestataire (par chantier)
    factures = (FacturePrestataire.query.filter_by(tenant_id=t.id, prestataire_id=f.prestataire_id)
                .order_by(FacturePrestataire.date_facture).all())
    avances = (AvancePrestataire.query.filter_by(tenant_id=t.id, prestataire_id=f.prestataire_id)
               .filter(AvancePrestataire.statut != "ANNULEE").all())
    imp = _imputer_avances(factures, avances)
    avance_imputee = imp["avance_xaf"].get(f.id, 0.0)      # XAF déduits sur CETTE facture
    solde_xaf      = imp["solde_xaf"].get(f.id, 0.0)        # solde après paiements + avances
    # Mêmes montants dans la DEVISE de la facture (avance convertie XAF → devise)
    avance_imputee_dev = imp["avance_dev"].get(f.id, 0.0)
    solde_dev          = imp["solde_dev"].get(f.id, f.reste_a_payer)
    # Avances du chantier de cette facture (pour le détail imprimé)
    avances_site = [a for a in avances if a.site_id == f.site_id]

    return render_template("tenant/facture_print.html",
        tenant=t, p=p, f=f, site=site, now=datetime.now(),
        avance_imputee=avance_imputee, solde_xaf=solde_xaf,
        avance_imputee_dev=avance_imputee_dev, solde_dev=solde_dev,
        avances_site=avances_site)


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
    a.date_validation = utcnow()
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

    # Une avance validée ne peut être supprimée QUE par un administrateur du tenant.
    if a.statut == "VALIDEE" and not current_user.is_tenant_admin:
        flash("Cette avance est validée : seul un administrateur peut la supprimer.", "error")
        return redirect(url_for("prestataires.prestataire_detail", id=pid))

    # Journaliser spécialement la suppression d'une avance validée (et régularisée).
    if a.statut == "VALIDEE":
        detail = "Suppression ADMIN d'une avance VALIDEE"
        if (a.montant_regularise or 0) > 0:
            detail += f" et REGULARISEE (montant regularise : {a.montant_regularise})"
        log_action("DELETE", "avance_prestataire", aid, detail,
                   user_id=current_user.id, tenant_id=t.id)

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

    imp = _imputer_avances(factures, avances)

    total_facture = imp["total_facture_xaf"]
    total_paye    = imp["total_paye_xaf"]
    total_avances = imp["total_avances_xaf"]
    # Solde net dû (XAF) = net facturé − déjà payé − avances imputées.
    avances_non_regul = imp["total_avances_imputees_xaf"]
    solde = imp["solde_net_xaf"]
    # Y a-t-il au moins une devise étrangère à afficher ?
    multi_devises = any((a.devise or "XAF") != "XAF" for a in avances) or \
                    any((f.devise or "XAF") != "XAF" for f in factures)

    return render_template("tenant/prestataire_releve_print.html",
        tenant=t, p=p, factures=factures, avances=avances, imp=imp,
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
