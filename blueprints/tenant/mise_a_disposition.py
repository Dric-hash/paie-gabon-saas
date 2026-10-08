# -*- coding: utf-8 -*-
"""Mise à disposition de personnel — gestion des entreprises utilisatrices
(clients), affectation des salariés, refacturation mensuelle et marge.

Le tenant reste l'employeur juridique : la paie et les déclarations CNSS/CNAMGS
ne changent pas. Ce module ajoute, par-dessus, le volet commercial :
qui est mis à disposition de qui, à quel tarif, et combien on refacture.
"""
import calendar
from datetime import datetime, date
from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import func

from blueprints.tenant import bp, _doc_response
from core import get_tenant, parse_date
from audit import log_action
from models import (db, Salarie, BulletinPaie, PeriodePaie, Pointage,
                    ClientUtilisateur, AffectationMAD)

_MOIS = ["", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet",
         "Août", "Septembre", "Octobre", "Novembre", "Décembre"]
_STATUTS_VALIDE = ("VALIDÉ", "VALIDE", "PAYÉ", "PAYE")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _jours_ouvres(annee, mois):
    """Nombre de jours ouvrés (lun-ven) du mois — valeur par défaut éditable."""
    n = calendar.monthrange(annee, mois)[1]
    return sum(1 for j in range(1, n + 1)
               if date(annee, mois, j).weekday() < 5)


def _cout_employeur_salarie(tenant_id, salarie_id, annee, mois):
    """Coût employeur estimé pour le mois : brut + charges patronales du dernier
    bulletin disponible (celui du mois si présent, sinon le plus récent).
    Indicatif — sert au calcul de marge et au mode COEFFICIENT."""
    per = PeriodePaie.query.filter_by(tenant_id=tenant_id, annee=annee, mois=mois).first()
    b = None
    if per:
        b = BulletinPaie.query.filter_by(
            tenant_id=tenant_id, salarie_id=salarie_id, periode_id=per.id).first()
    if not b:
        b = (BulletinPaie.query.filter_by(tenant_id=tenant_id, salarie_id=salarie_id)
             .join(PeriodePaie, BulletinPaie.periode_id == PeriodePaie.id)
             .order_by(PeriodePaie.annee.desc(), PeriodePaie.mois.desc()).first())
    if not b:
        return 0.0
    f = lambda x: float(x or 0)
    return (f(b.salaire_brut) + f(b.cnss_patronale) + f(b.cnamgs_patronale)
            + f(b.fnh) + f(b.cfp))


def _pointage_reel(tenant_id, salarie_id, annee, mois):
    """Pointage réel du salarié sur le mois : (jours travaillés, heures totales).
    Renvoie (None, None) si aucun pointage n'existe pour ce salarié ce mois-là."""
    debut = date(annee, mois, 1)
    fin = date(annee + (mois // 12), (mois % 12) + 1, 1)  # 1er du mois suivant (exclu)
    pts = (Pointage.query
           .filter(Pointage.tenant_id == tenant_id,
                   Pointage.salarie_id == salarie_id,
                   Pointage.date_pointage >= debut,
                   Pointage.date_pointage < fin).all())
    if not pts:
        return None, None
    jours = 0
    heures = 0.0
    for p in pts:
        if getattr(p, "absent", False):
            continue
        if not getattr(p, "present", True):
            continue
        jours += 1
        f = lambda x: float(x or 0)
        heures += (f(p.heures_normales) + f(p.heures_sup) + f(p.heures_sup_10)
                   + f(p.heures_sup_30) + f(p.heures_sup_30b)
                   + f(p.heures_sup_40) + f(p.heures_sup_70))
    return jours, heures


def _quantite_defaut(aff, tenant_id, annee, mois):
    """Quantité facturable par défaut pour une affectation sur un mois, et sa source.
    Priorité au pointage réel ; repli sur les jours ouvrés. Renvoie (quantite, source)
    avec source ∈ {'forfait', 'pointage', 'ouvres'}."""
    if aff.mode_facturation == "FORFAIT_MENSUEL":
        return 1, "forfait"
    jours_reels, heures_reelles = _pointage_reel(tenant_id, aff.salarie_id, annee, mois)
    if aff.mode_facturation == "TAUX_HEURE":
        if heures_reelles is not None:
            return heures_reelles, "pointage"
        return _jours_ouvres(annee, mois) * 8, "ouvres"
    # TAUX_JOUR (et COEFFICIENT qui n'utilise pas la quantité)
    if jours_reels is not None:
        return jours_reels, "pointage"
    return _jours_ouvres(annee, mois), "ouvres"


def _resoudre_periode():
    now = datetime.now()
    mois = request.args.get("mois", type=int) or now.month
    annee = request.args.get("annee", type=int) or now.year
    if mois < 1 or mois > 12:
        mois = now.month
    return annee, mois


def _get_client(client_id):
    """Récupère un client du tenant courant (sécurité : cloisonnement tenant)."""
    t = get_tenant()
    if not t:
        return None, None
    c = ClientUtilisateur.query.filter_by(id=client_id, tenant_id=t.id).first()
    return t, c


# ─────────────────────────────────────────────────────────────────────────────
# Tableau de bord du module
# ─────────────────────────────────────────────────────────────────────────────
@bp.route("/mise-a-disposition")
@login_required
def mad_dashboard():
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))

    annee, mois = _resoudre_periode()
    clients = (ClientUtilisateur.query.filter_by(tenant_id=t.id)
               .order_by(ClientUtilisateur.actif.desc(), ClientUtilisateur.nom).all())

    lignes, total_facture, total_cout = [], 0.0, 0.0
    for c in clients:
        affs = [a for a in c.affectations if a.est_active_sur(annee, mois)]
        fact_c = cout_c = 0.0
        for a in affs:
            cout = _cout_employeur_salarie(t.id, a.salarie_id, annee, mois)
            q, _src = _quantite_defaut(a, t.id, annee, mois)
            fact_c += a.montant_facture(q, cout)
            cout_c += cout
        lignes.append({"client": c, "nb_affectations": len(affs),
                       "facture": fact_c, "cout": cout_c, "marge": fact_c - cout_c})
        total_facture += fact_c
        total_cout += cout_c

    nb_sal_mad = (db.session.query(func.count(func.distinct(AffectationMAD.salarie_id)))
                  .filter(AffectationMAD.tenant_id == t.id,
                          AffectationMAD.actif == True).scalar() or 0)

    return render_template("tenant/mad_dashboard.html",
        tenant=t, clients=clients, lignes=lignes,
        annee=annee, mois=mois, mois_label=_MOIS[mois], mois_fr=_MOIS,
        total_facture=total_facture, total_cout=total_cout,
        total_marge=total_facture - total_cout,
        nb_clients=len([c for c in clients if c.actif]), nb_sal_mad=nb_sal_mad)


# ─────────────────────────────────────────────────────────────────────────────
# Clients (entreprises utilisatrices)
# ─────────────────────────────────────────────────────────────────────────────
@bp.route("/mise-a-disposition/clients/nouveau", methods=["GET", "POST"])
@login_required
def mad_client_nouveau():
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        if not current_user.can_edit:
            flash("Vous n'avez pas les droits pour cette action.", "error")
            return redirect(url_for("tenant.mad_dashboard"))
        nom = (request.form.get("nom") or "").strip()
        if not nom:
            flash("La raison sociale du client est obligatoire.", "error")
            return redirect(url_for("tenant.mad_client_nouveau"))
        c = ClientUtilisateur(
            tenant_id=t.id, nom=nom,
            nif=(request.form.get("nif") or "").strip() or None,
            rccm=(request.form.get("rccm") or "").strip() or None,
            contact_nom=(request.form.get("contact_nom") or "").strip() or None,
            telephone=(request.form.get("telephone") or "").strip() or None,
            email=(request.form.get("email") or "").strip() or None,
            adresse=(request.form.get("adresse") or "").strip() or None,
            secteur=(request.form.get("secteur") or "").strip() or None,
            note=(request.form.get("note") or "").strip() or None,
        )
        db.session.add(c)
        db.session.commit()
        log_action("creation", "client_mad", c.id, f"Client MAD créé : {nom}")
        flash("Client enregistré.", "success")
        return redirect(url_for("tenant.mad_client_detail", client_id=c.id))
    return render_template("tenant/mad_client_form.html", tenant=t, client=None)


@bp.route("/mise-a-disposition/clients/<int:client_id>")
@login_required
def mad_client_detail(client_id):
    t, c = _get_client(client_id)
    if not c:
        flash("Client introuvable.", "error")
        return redirect(url_for("tenant.mad_dashboard"))
    annee, mois = _resoudre_periode()

    affectations = (AffectationMAD.query.filter_by(tenant_id=t.id, client_id=c.id)
                    .order_by(AffectationMAD.actif.desc(),
                              AffectationMAD.date_debut.desc()).all())
    # salariés disponibles pour une nouvelle affectation
    salaries = (Salarie.query.filter_by(tenant_id=t.id, statut="ACTIF")
                .order_by(Salarie.nom, Salarie.prenom).all())

    # aperçu facturation du mois en cours
    apercu = []
    tot_f = tot_c = 0.0
    for a in affectations:
        if not a.est_active_sur(annee, mois):
            continue
        cout = _cout_employeur_salarie(t.id, a.salarie_id, annee, mois)
        q, src = _quantite_defaut(a, t.id, annee, mois)
        f = a.montant_facture(q, cout)
        apercu.append({"aff": a, "cout": cout, "facture": f, "marge": f - cout,
                       "quantite": q, "source": src})
        tot_f += f
        tot_c += cout

    return render_template("tenant/mad_client_detail.html",
        tenant=t, client=c, affectations=affectations, salaries=salaries,
        annee=annee, mois=mois, mois_label=_MOIS[mois], mois_fr=_MOIS,
        apercu=apercu, total_facture=tot_f, total_cout=tot_c,
        total_marge=tot_f - tot_c, modes=AffectationMAD.MODES)


@bp.route("/mise-a-disposition/clients/<int:client_id>/modifier", methods=["POST"])
@login_required
def mad_client_modifier(client_id):
    t, c = _get_client(client_id)
    if not c:
        flash("Client introuvable.", "error")
        return redirect(url_for("tenant.mad_dashboard"))
    if not current_user.can_edit:
        flash("Vous n'avez pas les droits pour cette action.", "error")
        return redirect(url_for("tenant.mad_client_detail", client_id=c.id))
    c.nom = (request.form.get("nom") or c.nom).strip()
    c.nif = (request.form.get("nif") or "").strip() or None
    c.rccm = (request.form.get("rccm") or "").strip() or None
    c.contact_nom = (request.form.get("contact_nom") or "").strip() or None
    c.telephone = (request.form.get("telephone") or "").strip() or None
    c.email = (request.form.get("email") or "").strip() or None
    c.adresse = (request.form.get("adresse") or "").strip() or None
    c.secteur = (request.form.get("secteur") or "").strip() or None
    c.note = (request.form.get("note") or "").strip() or None
    c.actif = request.form.get("actif") == "on"
    db.session.commit()
    log_action("modification", "client_mad", c.id, f"Client MAD modifié : {c.nom}")
    flash("Client mis à jour.", "success")
    return redirect(url_for("tenant.mad_client_detail", client_id=c.id))


@bp.route("/mise-a-disposition/clients/<int:client_id>/supprimer", methods=["POST"])
@login_required
def mad_client_supprimer(client_id):
    t, c = _get_client(client_id)
    if not c:
        flash("Client introuvable.", "error")
        return redirect(url_for("tenant.mad_dashboard"))
    if not current_user.can_edit:
        flash("Vous n'avez pas les droits pour cette action.", "error")
        return redirect(url_for("tenant.mad_client_detail", client_id=c.id))
    if c.affectations:
        flash("Impossible de supprimer : ce client a des affectations. "
              "Désactivez-le plutôt.", "error")
        return redirect(url_for("tenant.mad_client_detail", client_id=c.id))
    nom = c.nom
    db.session.delete(c)
    db.session.commit()
    log_action("suppression", "client_mad", client_id, f"Client MAD supprimé : {nom}")
    flash("Client supprimé.", "success")
    return redirect(url_for("tenant.mad_dashboard"))


# ─────────────────────────────────────────────────────────────────────────────
# Affectations
# ─────────────────────────────────────────────────────────────────────────────
@bp.route("/mise-a-disposition/clients/<int:client_id>/affecter", methods=["POST"])
@login_required
def mad_affecter(client_id):
    t, c = _get_client(client_id)
    if not c:
        flash("Client introuvable.", "error")
        return redirect(url_for("tenant.mad_dashboard"))
    if not current_user.can_edit:
        flash("Vous n'avez pas les droits pour cette action.", "error")
        return redirect(url_for("tenant.mad_client_detail", client_id=c.id))

    sal_id = request.form.get("salarie_id", type=int)
    sal = Salarie.query.filter_by(id=sal_id, tenant_id=t.id).first() if sal_id else None
    if not sal:
        flash("Salarié invalide.", "error")
        return redirect(url_for("tenant.mad_client_detail", client_id=c.id))

    mode = request.form.get("mode_facturation") or "TAUX_JOUR"
    if mode not in AffectationMAD.MODES:
        mode = "TAUX_JOUR"
    try:
        valeur = float((request.form.get("valeur") or "0").replace(" ", "").replace(",", "."))
    except ValueError:
        valeur = 0.0
    d_debut = parse_date(request.form.get("date_debut")) or date.today()
    d_fin = parse_date(request.form.get("date_fin"))

    a = AffectationMAD(
        tenant_id=t.id, client_id=c.id, salarie_id=sal.id,
        poste=(request.form.get("poste") or "").strip() or None,
        date_debut=d_debut, date_fin=d_fin,
        mode_facturation=mode, valeur=valeur,
        note=(request.form.get("note") or "").strip() or None,
        actif=(d_fin is None or d_fin >= date.today()),
    )
    db.session.add(a)
    db.session.commit()
    log_action("creation", "affectation_mad", a.id,
               f"{sal.nom} {sal.prenom} mis à disposition de {c.nom}")
    flash(f"{sal.prenom} {sal.nom} affecté(e) à {c.nom}.", "success")
    return redirect(url_for("tenant.mad_client_detail", client_id=c.id))


@bp.route("/mise-a-disposition/affectations/<int:aff_id>/modifier", methods=["POST"])
@login_required
def mad_affectation_modifier(aff_id):
    t = get_tenant()
    a = AffectationMAD.query.filter_by(id=aff_id, tenant_id=t.id).first() if t else None
    if not a:
        flash("Affectation introuvable.", "error")
        return redirect(url_for("tenant.mad_dashboard"))
    if not current_user.can_edit:
        flash("Vous n'avez pas les droits pour cette action.", "error")
        return redirect(url_for("tenant.mad_client_detail", client_id=a.client_id))
    mode = request.form.get("mode_facturation") or a.mode_facturation
    if mode in AffectationMAD.MODES:
        a.mode_facturation = mode
    try:
        a.valeur = float((request.form.get("valeur") or str(a.valeur)).replace(" ", "").replace(",", "."))
    except ValueError:
        pass
    a.poste = (request.form.get("poste") or "").strip() or None
    nd = parse_date(request.form.get("date_debut"))
    if nd:
        a.date_debut = nd
    a.date_fin = parse_date(request.form.get("date_fin"))
    a.note = (request.form.get("note") or "").strip() or None
    a.actif = (a.date_fin is None or a.date_fin >= date.today())
    db.session.commit()
    log_action("modification", "affectation_mad", a.id, "Affectation MAD modifiée")
    flash("Affectation mise à jour.", "success")
    return redirect(url_for("tenant.mad_client_detail", client_id=a.client_id))


@bp.route("/mise-a-disposition/affectations/<int:aff_id>/terminer", methods=["POST"])
@login_required
def mad_affectation_terminer(aff_id):
    t = get_tenant()
    a = AffectationMAD.query.filter_by(id=aff_id, tenant_id=t.id).first() if t else None
    if not a:
        flash("Affectation introuvable.", "error")
        return redirect(url_for("tenant.mad_dashboard"))
    if not current_user.can_edit:
        flash("Vous n'avez pas les droits pour cette action.", "error")
        return redirect(url_for("tenant.mad_client_detail", client_id=a.client_id))
    a.date_fin = parse_date(request.form.get("date_fin")) or date.today()
    a.actif = False
    db.session.commit()
    log_action("modification", "affectation_mad", a.id, "Affectation MAD terminée")
    flash("Affectation terminée.", "success")
    return redirect(url_for("tenant.mad_client_detail", client_id=a.client_id))


@bp.route("/mise-a-disposition/affectations/<int:aff_id>/supprimer", methods=["POST"])
@login_required
def mad_affectation_supprimer(aff_id):
    t = get_tenant()
    a = AffectationMAD.query.filter_by(id=aff_id, tenant_id=t.id).first() if t else None
    if not a:
        flash("Affectation introuvable.", "error")
        return redirect(url_for("tenant.mad_dashboard"))
    if not current_user.can_edit:
        flash("Vous n'avez pas les droits pour cette action.", "error")
        return redirect(url_for("tenant.mad_client_detail", client_id=a.client_id))
    cid = a.client_id
    db.session.delete(a)
    db.session.commit()
    log_action("suppression", "affectation_mad", aff_id, "Affectation MAD supprimée")
    flash("Affectation supprimée.", "success")
    return redirect(url_for("tenant.mad_client_detail", client_id=cid))


# ─────────────────────────────────────────────────────────────────────────────
# Facturation mensuelle
# ─────────────────────────────────────────────────────────────────────────────
@bp.route("/mise-a-disposition/clients/<int:client_id>/facture")
@login_required
def mad_facture(client_id):
    t, c = _get_client(client_id)
    if not c:
        flash("Client introuvable.", "error")
        return redirect(url_for("tenant.mad_dashboard"))
    annee, mois = _resoudre_periode()
    jo = _jours_ouvres(annee, mois)

    lignes = []
    for a in c.affectations:
        if not a.est_active_sur(annee, mois):
            continue
        cout = _cout_employeur_salarie(t.id, a.salarie_id, annee, mois)
        q, src = _quantite_defaut(a, t.id, annee, mois)
        lignes.append({
            "aff": a, "salarie": a.salarie, "quantite": q, "source": src,
            "cout": cout, "facture": a.montant_facture(q, cout),
        })
    total = sum(l["facture"] for l in lignes)
    total_cout = sum(l["cout"] for l in lignes)

    return render_template("tenant/mad_facture.html",
        tenant=t, client=c, lignes=lignes, annee=annee, mois=mois,
        mois_label=_MOIS[mois], mois_fr=_MOIS, jours_ouvres=jo,
        total=total, total_cout=total_cout, marge=total - total_cout)


@bp.route("/mise-a-disposition/clients/<int:client_id>/facture/pdf", methods=["POST"])
@login_required
def mad_facture_pdf(client_id):
    t, c = _get_client(client_id)
    if not c:
        flash("Client introuvable.", "error")
        return redirect(url_for("tenant.mad_dashboard"))
    annee = request.form.get("annee", type=int) or datetime.now().year
    mois = request.form.get("mois", type=int) or datetime.now().month

    # Les quantités facturées proviennent du formulaire (éditables).
    lignes = []
    for a in c.affectations:
        if not a.est_active_sur(annee, mois):
            continue
        cout = _cout_employeur_salarie(t.id, a.salarie_id, annee, mois)
        raw = request.form.get(f"q_{a.id}")
        try:
            q = float((raw or "0").replace(" ", "").replace(",", "."))
        except ValueError:
            q = 0.0
        montant = a.montant_facture(q, cout)
        lignes.append({
            "poste": a.poste or (a.salarie.emploi if a.salarie else ""),
            "salarie": f"{a.salarie.prenom} {a.salarie.nom}" if a.salarie else "",
            "mode": a.mode_libelle, "unite": a.unite,
            "quantite": q, "valeur": float(a.valeur or 0), "montant": montant,
        })

    numero = f"MAD-{annee}{mois:02d}-{c.id:03d}"
    from mise_a_disposition_pdf import generer_facture_mad_pdf
    pdf = generer_facture_mad_pdf(t, c, lignes, mois, annee, numero, _MOIS[mois])
    log_action("edition", "facture_mad", c.id, f"Facture MAD {numero}")
    return _doc_response(pdf, f"facture_{numero}.pdf")


# ─────────────────────────────────────────────────────────────────────────────
# Contrat de mise à disposition (PDF)
# ─────────────────────────────────────────────────────────────────────────────
@bp.route("/mise-a-disposition/affectations/<int:aff_id>/contrat")
@login_required
def mad_contrat(aff_id):
    t = get_tenant()
    a = AffectationMAD.query.filter_by(id=aff_id, tenant_id=t.id).first() if t else None
    if not a:
        flash("Affectation introuvable.", "error")
        return redirect(url_for("tenant.mad_dashboard"))
    client = ClientUtilisateur.query.filter_by(id=a.client_id, tenant_id=t.id).first()
    if not client:
        flash("Client introuvable.", "error")
        return redirect(url_for("tenant.mad_dashboard"))
    # Trame personnalisée du tenant si elle existe (ModeleContrat MISE_A_DISPOSITION)
    modele = None
    try:
        from models import ModeleContrat
        modele = (ModeleContrat.query
                  .filter_by(tenant_id=t.id, type_contrat="MISE_A_DISPOSITION", actif=True)
                  .order_by(ModeleContrat.id.desc()).first())
    except Exception:
        modele = None
    from documents_rh import generer_contrat_mad_pdf
    pdf = generer_contrat_mad_pdf(t, client, a, modele=modele)
    log_action("edition", "contrat_mad", a.id,
               f"Contrat de mise à disposition — {client.nom}")
    nom = f"contrat_mad_{client.nom[:20].replace(' ', '_')}_{a.id}.pdf"
    return _doc_response(pdf, nom)
