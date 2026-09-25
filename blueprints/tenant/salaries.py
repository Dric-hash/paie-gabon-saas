# -*- coding: utf-8 -*-
"""Salariés, contrats, documents du dossier, modèles de contrat, solde de tout compte,
imports/pointage salariés — extrait de tenant.py (même blueprint « tenant »)."""
from datetime import datetime, date, timedelta
from flask import (render_template, request, redirect, url_for, flash, session,
                   current_app, abort, send_file)
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from sqlalchemy import desc, or_, func
from blueprints.tenant import (bp, logger, _doc_response, _build_elements_recurrents,
    _grille_tenant, _lire_pointage_salaries, _sal_periode_demandee,
    _pointages_mois_contexte, _resoudre_mois_annee)
from core import tenant_required, get_tenant, can_edit, _pd, _parse_date, parse_date, _cache_delete
from audit import log_action
from models import (db, Salarie, Contrat, CategorieEmploi, DocumentSalarie, ModeleContrat,
                    BulletinPaie, PeriodePaie, Conge, Acompte, ComposantPaie,
                    Pointage, Site, AffectationSite)


@bp.route("/salaries")
@login_required
def salaries():
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    q      = request.args.get("q", "")
    statut = request.args.get("statut", "")
    page   = request.args.get("page", 1, type=int)
    query  = Salarie.query.filter_by(tenant_id=t.id)
    if q:      query = query.filter(db.or_(Salarie.nom.ilike(f"%{q}%"), Salarie.prenom.ilike(f"%{q}%"), Salarie.matricule.ilike(f"%{q}%")))
    if statut: query = query.filter_by(statut=statut)
    query = query.options(joinedload(Salarie.categorie), joinedload(Salarie.contrats))
    pagination = query.order_by(Salarie.nom).paginate(page=page, per_page=25, error_out=False)
    _args  = {k: v for k, v in request.args.items() if k != 'page'}
    _base  = request.path + '?' + '&'.join(f'{k}={v}' for k, v in _args.items())
    _sep   = '&' if _args else '?'
    return render_template("tenant/salaries.html",
        salaries=pagination.items, pagination=pagination,
        categories=CategorieEmploi.query.filter_by(tenant_id=t.id).all(),
        q=q, statut=statut, tenant=t,
        pagination_base=_base + _sep)









@bp.route("/salaries/import", methods=["GET","POST"])
@login_required
def salaries_import():
    """Import en masse de salariés depuis un fichier Excel."""
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))

    if request.method == "GET":
        categories = CategorieEmploi.query.filter_by(tenant_id=t.id).all()
        return render_template("tenant/salaries_import.html",
            tenant=t, categories=categories)

    # ── POST : traitement du fichier ─────────────────────────────────────────
    fichier = request.files.get("fichier")
    if not fichier or not fichier.filename.endswith((".xlsx", ".xls")):
        flash("❌ Fichier invalide. Utilisez le modèle Excel fourni (.xlsx).", "error")
        return redirect(url_for("tenant.salaries_import"))

    # Garde anti-DoS (zip-bomb / fichier surdimensionné) : 5 Mo max.
    fichier.seek(0, 2); _taille = fichier.tell(); fichier.seek(0)
    if _taille > 5_000_000:
        flash("❌ Fichier trop volumineux (max 5 Mo).", "error")
        return redirect(url_for("tenant.salaries_import"))

    mode = request.form.get("mode", "ignorer")  # ignorer | ecraser

    import openpyxl
    from datetime import date as date_type

    def parse_date(val):
        if not val: return None
        if isinstance(val, (date_type, datetime)): return val if isinstance(val, date_type) else val.date()
        s = str(val).strip()
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y"):
            try: return datetime.strptime(s, fmt).date()
            except: pass
        return None

    def clean(val): return str(val).strip() if val not in (None, "") else None

    try:
        wb = openpyxl.load_workbook(fichier, data_only=True)
        ws = wb.active
    except Exception as e:
        flash(f"❌ Erreur lecture fichier : {e}", "error")
        return redirect(url_for("tenant.salaries_import"))

    # Trouver la ligne d'en-tête (ligne avec "MATRICULE")
    header_row = None
    for row_idx in range(1, 10):
        row_vals = [str(ws.cell(row_idx, c).value or "").upper().strip() for c in range(1, 20)]
        if "MATRICULE" in row_vals:
            header_row = row_idx
            break

    if not header_row:
        flash("❌ Entête non trouvée. Utilisez le modèle Excel fourni.", "error")
        return redirect(url_for("tenant.salaries_import"))

    # Mapper les colonnes
    headers = {}
    for col in range(1, ws.max_column + 1):
        val = str(ws.cell(header_row, col).value or "").upper().strip()
        if val: headers[val] = col

    required = ["MATRICULE", "NOM", "PRENOM", "EMPLOI", "DATE_EMBAUCHE"]
    for req in required:
        if req not in headers:
            flash(f"❌ Colonne obligatoire manquante : {req}", "error")
            return redirect(url_for("tenant.salaries_import"))

    # Charger les catégories du tenant
    cats = {c.code.upper(): c for c in CategorieEmploi.query.filter_by(tenant_id=t.id).all()}

    # ── Vérifier quota ────────────────────────────────────────────────────────
    nb_existants = Salarie.query.filter_by(tenant_id=t.id, statut="ACTIF").count()
    quota = t.quota_employes_info

    # Traiter les lignes de données
    nb_crees = nb_maj = nb_erreurs = nb_ignores = 0
    erreurs   = []
    avertissements = []

    for row_idx in range(header_row + 1, ws.max_row + 1):
        def get(col_name):
            c = headers.get(col_name)
            return ws.cell(row_idx, c).value if c else None

        matricule = clean(get("MATRICULE"))
        if not matricule: continue  # ligne vide

        nom    = clean(get("NOM"))
        prenom = clean(get("PRENOM"))
        emploi = clean(get("EMPLOI"))
        date_e = parse_date(get("DATE_EMBAUCHE"))

        # Validation champs obligatoires
        if not all([nom, prenom, emploi, date_e]):
            erreurs.append(f"Ligne {row_idx} ({matricule}) : champs obligatoires manquants")
            nb_erreurs += 1
            continue

        # Vérifier si matricule existe
        existant = Salarie.query.filter_by(tenant_id=t.id, matricule=matricule).first()

        if existant and mode == "ignorer":
            nb_ignores += 1
            continue

        # Récupérer catégorie
        cat_code = clean(get("CATEGORIE"))
        cat = cats.get(cat_code.upper()) if cat_code else None

        # Salaire de base → créer/maj contrat
        salaire_raw = get("SALAIRE_BASE")
        salaire_base = None
        if salaire_raw:
            try: salaire_base = float(str(salaire_raw).replace(" ","").replace(",","."))
            except: pass

        # Quota check pour nouvelles créations
        if not existant and quota.get("max"):
            if nb_existants + nb_crees >= quota["max"]:
                erreurs.append(f"Quota atteint ({quota['max']} employés). Import arrêté à la ligne {row_idx}.")
                break

        data = dict(
            tenant_id              = t.id,
            matricule              = matricule.upper(),
            nom                    = nom.upper(),
            prenom                 = prenom,
            emploi                 = emploi.upper(),
            date_embauche          = date_e,
            categorie_id           = cat.id if cat else None,
            telephone              = clean(get("TELEPHONE")),
            sexe                   = clean(get("SEXE")),
            date_naissance         = parse_date(get("DATE_NAISSANCE")),
            situation_matrimoniale = clean(get("SITUATION_MAT")),
            nationalite            = clean(get("NATIONALITE")) or "GABONAISE",
            numero_cnss            = clean(get("NUMERO_CNSS")),
            numero_cnamgs          = clean(get("NUMERO_CNAMGS")),
            email                  = clean(get("EMAIL")),
            adresse                = clean(get("ADRESSE")),
            statut                 = "ACTIF",
        )
        try: data["nb_enfants"]   = int(float(str(get("NB_ENFANTS") or 0)))
        except: data["nb_enfants"] = 0
        try: data["nombre_parts"] = float(str(get("NOMBRE_PARTS") or 1).replace(",","."))
        except: data["nombre_parts"] = 1

        try:
            if existant:
                for k, v in data.items():
                    if v is not None: setattr(existant, k, v)
                s_obj = existant
                nb_maj += 1
            else:
                s_obj = Salarie(**data)
                db.session.add(s_obj)
                db.session.flush()
                nb_crees += 1

            # Créer/maj contrat si salaire fourni
            if salaire_base and salaire_base > 0:
                contrat = next((c for c in (s_obj.contrats if s_obj.id else [])), None)
                if not contrat:
                    contrat = Contrat(tenant_id=t.id, salarie_id=s_obj.id,
                                      date_debut=date_e, actif=True)
                    db.session.add(contrat)
                contrat.salaire_base = salaire_base
                contrat.type_contrat = "CDI"
                contrat.actif        = True

        except Exception as e:
            db.session.rollback()
            erreurs.append(f"Ligne {row_idx} ({matricule}) : {str(e)[:80]}")
            nb_erreurs += 1
            continue

    db.session.commit()
    _cache_delete(f"{t.id}:")  # Invalider cache

    # Message résumé
    msg_parts = []
    if nb_crees:   msg_parts.append(f"✅ {nb_crees} salarié(s) créé(s)")
    if nb_maj:     msg_parts.append(f"🔄 {nb_maj} mis à jour")
    if nb_ignores: msg_parts.append(f"⏭️ {nb_ignores} ignoré(s) (déjà existants)")
    if nb_erreurs: msg_parts.append(f"❌ {nb_erreurs} erreur(s)")
    flash(" · ".join(msg_parts) or "Aucune donnée importée.", "success" if not nb_erreurs else "error")

    for err in erreurs[:5]:
        flash(f"⚠️ {err}", "error")

    return redirect(url_for("tenant.salaries"))


@bp.route("/salaries/import/modele")
@login_required
def salaries_import_modele():
    """Génère et télécharge le modèle Excel vierge d'import des salariés.

    Le fichier est produit à la volée (openpyxl) pour rester toujours disponible
    et toujours aligné sur les colonnes réellement attendues par l'import.
    """
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))

    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from io import BytesIO
    from flask import send_file

    # Colonnes : les 5 premières sont OBLIGATOIRES, les suivantes optionnelles.
    colonnes = [
        ("MATRICULE", True), ("NOM", True), ("PRENOM", True),
        ("EMPLOI", True), ("DATE_EMBAUCHE", True),
        ("SEXE", False), ("DATE_NAISSANCE", False), ("NATIONALITE", False),
        ("SITUATION_MAT", False), ("NB_ENFANTS", False), ("NOMBRE_PARTS", False),
        ("ADRESSE", False), ("TELEPHONE", False), ("EMAIL", False),
        ("CATEGORIE", False), ("SALAIRE_BASE", False),
        ("NUMERO_CNSS", False), ("NUMERO_CNAMGS", False),
    ]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Salariés"

    entete_oblig = Font(bold=True, color="FFFFFF")
    entete_opt   = Font(bold=True, color="0F3D36")
    fill_oblig   = PatternFill("solid", fgColor="0F3D36")
    fill_opt     = PatternFill("solid", fgColor="E8EFEC")

    for idx, (nom, oblig) in enumerate(colonnes, start=1):
        cell = ws.cell(row=1, column=idx, value=nom)
        cell.alignment = Alignment(horizontal="center")
        cell.font = entete_oblig if oblig else entete_opt
        cell.fill = fill_oblig if oblig else fill_opt
        ws.column_dimensions[cell.column_letter].width = max(14, len(nom) + 2)

    # Ligne d'exemple (sera ignorée si supprimée ; sert de guide de format)
    exemple = {
        "MATRICULE": "0001", "NOM": "NDONG", "PRENOM": "Jean",
        "EMPLOI": "Maçon", "DATE_EMBAUCHE": "15/01/2026",
        "SEXE": "M", "DATE_NAISSANCE": "10/06/1990", "NATIONALITE": "Gabonaise",
        "SITUATION_MAT": "MARIE", "NB_ENFANTS": 2, "NOMBRE_PARTS": 2.5,
        "ADRESSE": "Libreville", "TELEPHONE": "077000000", "EMAIL": "",
        "CATEGORIE": "C1", "SALAIRE_BASE": 200000,
        "NUMERO_CNSS": "", "NUMERO_CNAMGS": "",
    }
    for idx, (nom, _) in enumerate(colonnes, start=1):
        ws.cell(row=2, column=idx, value=exemple.get(nom, ""))
    for c in range(1, len(colonnes) + 1):
        ws.cell(row=2, column=c).font = Font(italic=True, color="9CA3AF")

    # Feuille d'instructions, avec les catégories réellement définies pour ce tenant.
    ws2 = wb.create_sheet("Instructions")
    cats = CategorieEmploi.query.filter_by(tenant_id=t.id).all()
    lignes_info = [
        ("Modèle d'import des salariés — PaieGabon", True),
        ("", False),
        ("Colonnes OBLIGATOIRES : MATRICULE, NOM, PRENOM, EMPLOI, DATE_EMBAUCHE", False),
        ("Les autres colonnes sont facultatives.", False),
        ("Format des dates : JJ/MM/AAAA (ex. 15/01/2026).", False),
        ("SEXE : M ou F.", False),
        ("SITUATION_MAT : CELIBATAIRE, MARIE, DIVORCE, VEUF.", False),
        ("SALAIRE_BASE : montant entier en FCFA, sans espaces (ex. 200000).", False),
        ("Supprimez la ligne d'exemple avant l'import.", False),
        ("", False),
        ("Codes CATEGORIE disponibles pour votre entreprise :", True),
    ]
    if cats:
        for c in cats:
            lignes_info.append((f"   {c.code} — {c.libelle}", False))
    else:
        lignes_info.append(("   (aucune catégorie définie — laissez la colonne vide)", False))
    for i, (txt, gras) in enumerate(lignes_info, start=1):
        cell = ws2.cell(row=i, column=1, value=txt)
        if gras:
            cell.font = Font(bold=True, color="0F3D36")
    ws2.column_dimensions["A"].width = 70

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name="modele_import_salaries.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@bp.route("/salaries/nouveau", methods=["GET","POST"])
@login_required
def salarie_nouveau():
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t=get_tenant()
    if not t: return redirect(url_for("auth.login"))
    if not current_user.can_edit: abort(403)
    cats=CategorieEmploi.query.filter_by(tenant_id=t.id).all()
    # Vérifier quota dès le GET (bloquer accès au formulaire)
    q = t.quota_employes_info
    if q["max"] and q["plein"]:
        flash(
            f"Limite atteinte — Plan « {t.plan.nom} » : {q['max']} employé(s) maximum "
            f"({q['salaries']} salarié(s) + {q['journaliers']} journalier(s)). "
            f"Passez au plan supérieur.", "error"
        )
        return redirect(url_for("tenant.salaries"))
    if request.method=="POST":
        if not t.peut_ajouter_employe:
            flash(f"Limite atteinte ({t.plan.max_salaries} employés). Passez au plan supérieur.","error")
            return redirect(url_for("tenant.salaries"))
        s=Salarie(tenant_id=t.id,
            matricule=request.form["matricule"].strip().upper(),
            categorie_id=request.form.get("categorie_id") or None,
            nom=request.form["nom"].strip().upper(), prenom=request.form["prenom"].strip(),
            telephone=request.form.get("telephone"), email=request.form.get("email","").strip() or None,
            nationalite=request.form.get("nationalite","GABONAISE"),
            sexe=request.form.get("sexe"),
            date_naissance=_pd(request.form.get("date_naissance")),
            date_prochaine_visite_medicale=_pd(request.form.get("date_prochaine_visite_medicale")),
            date_embauche=_pd(request.form["date_embauche"]),
            situation_matrimoniale=request.form.get("situation_matrimoniale"),
            nb_enfants=int(request.form.get("nb_enfants") or 0),
            nb_enfants_moins_16ans=int(request.form.get("nb_enfants_moins_16ans") or 0),
            nombre_parts=float(request.form.get("nombre_parts") or 1),
            numero_cnss=request.form.get("numero_cnss"), numero_cnamgs=request.form.get("numero_cnamgs"),
            emploi=request.form.get("emploi"), assujetti_cnamgs=request.form.get("assujetti_cnamgs")=="OUI", travail_de_nuit=request.form.get("travail_de_nuit")=="OUI",
            nif=request.form.get("nif"), niveau=request.form.get("niveau"), code_emploi=request.form.get("code_emploi"), statut="ACTIF")
        db.session.add(s)
        sb=float(request.form.get("salaire_base") or 0)
        if sb: db.session.add(Contrat(tenant_id=t.id,salarie=s,type_contrat=request.form.get("type_contrat","CDI"),date_debut=s.date_embauche,salaire_base=sb,poste=s.emploi,actif=True))
        db.session.flush()
        log_action("CREATE", "salarie", s.id,
                   f"Nouveau salarié : {s.nom_complet} (matricule {s.matricule})",
                   apres={"nom": s.nom, "prenom": s.prenom, "matricule": s.matricule,
                          "emploi": s.emploi, "salaire_base": sb})
        db.session.commit(); flash(f"Salarié {s.nom_complet} créé.","success")
        return redirect(url_for("tenant.salarie_detail",id=s.id))
    sites = Site.query.filter_by(tenant_id=t.id, actif=True).order_by(Site.nom).all()
    return render_template("tenant/salarie_form.html", salarie=None, categories=cats,
        action="nouveau", tenant=t, sites=sites, aff_actuelle=None,
        grille_salaires=_grille_tenant(t))

@bp.route("/salaries/<int:id>")
@login_required
def salarie_detail(id):
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    s = Salarie.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    bulletins = BulletinPaie.query.filter_by(salarie_id=id, tenant_id=t.id)        .order_by(BulletinPaie.date_creation.desc()).all()
    contrat = Contrat.query.filter_by(salarie_id=id, tenant_id=t.id, actif=True).first()
    conge   = Conge.query.filter_by(salarie_id=id, tenant_id=t.id,
                                     annee=datetime.now().year).first()
    total_brut = sum(float(b.salaire_brut or 0) for b in bulletins)
    total_net  = sum(float(b.net_a_payer  or 0) for b in bulletins)
    total_cnss = sum(float(b.cnss_salarie or 0) for b in bulletins)
    total_irpp = sum(float(b.irpp         or 0) for b in bulletins)
    nb_mois    = len(bulletins)
    anciennete_jours = (datetime.now().date() - s.date_embauche).days if s.date_embauche else 0
    anciennete_ans   = anciennete_jours // 365
    anciennete_mois  = (anciennete_jours % 365) // 30

    # ── Historique des pointages (30 derniers jours) ──────────────────────────
    nb_jours = request.args.get("nb_jours", type=int, default=30)
    nb_jours = min(max(nb_jours, 7), 90)          # borne 7-90 jours
    date_fin   = datetime.now().date()
    date_debut = date_fin - timedelta(days=nb_jours - 1)

    pts_hist = Pointage.query.filter_by(tenant_id=t.id, salarie_id=id)        .filter(Pointage.date_pointage >= date_debut,
                Pointage.date_pointage <= date_fin)        .order_by(Pointage.date_pointage.desc()).all()

    # Stats synthèse
    nb_presences  = sum(1 for p in pts_hist if p.present)
    nb_absences   = sum(1 for p in pts_hist if p.absent)
    nb_non_pointes = nb_jours - len(pts_hist)
    h_normales_tot = round(sum(float(p.heures_normales or 0) for p in pts_hist if p.present), 1)
    h_sup_tot      = round(sum(
        float(p.heures_sup_10 or 0) + float(p.heures_sup_30 or 0) +
        float(p.heures_sup_40 or 0) + float(p.heures_sup_70 or 0)
        for p in pts_hist if p.present), 1)
    taux_presence = round(nb_presences / (nb_presences + nb_absences) * 100
                          ) if (nb_presences + nb_absences) > 0 else 0

    from models import DocumentSalarie
    documents = (DocumentSalarie.query.filter_by(tenant_id=t.id, salarie_id=id)
                 .order_by(DocumentSalarie.date_creation.desc()).all())
    return render_template("tenant/salarie_detail.html",
        salarie=s, tenant=t, bulletins=bulletins, contrat=contrat, conge=conge,
        documents=documents,
        modeles_contrat=ModeleContrat.query.filter_by(tenant_id=t.id, actif=True).order_by(ModeleContrat.nom).all(),
        total_brut=total_brut, total_net=total_net, total_cnss=total_cnss,
        total_irpp=total_irpp, nb_mois=nb_mois,
        anciennete_ans=anciennete_ans, anciennete_mois=anciennete_mois,
        # Historique pointage
        pts_hist=pts_hist, nb_jours=nb_jours,
        nb_presences=nb_presences, nb_absences=nb_absences,
        nb_non_pointes=nb_non_pointes,
        h_normales_tot=h_normales_tot, h_sup_tot=h_sup_tot,
        taux_presence=taux_presence,
        date_debut_hist=date_debut, date_fin_hist=date_fin)

@bp.route("/salaries/<int:id>/modifier", methods=["GET","POST"])
@login_required
def salarie_modifier(id):
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t=get_tenant()
    if not t: return redirect(url_for("auth.login"))
    if not current_user.can_edit: abort(403)
    s = Salarie.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    cats = CategorieEmploi.query.filter_by(tenant_id=t.id).all()
    if request.method=="POST":
        for f,v in [("nom",request.form["nom"].strip().upper()),("prenom",request.form["prenom"].strip()),
            ("telephone",request.form.get("telephone")),
            ("email",request.form.get("email","").strip() or None),
            ("nationalite",request.form.get("nationalite")),
            ("sexe",request.form.get("sexe")),("date_naissance",_pd(request.form.get("date_naissance"))),
            ("date_prochaine_visite_medicale",_pd(request.form.get("date_prochaine_visite_medicale"))),
            ("situation_matrimoniale",request.form.get("situation_matrimoniale")),
            ("nb_enfants",int(request.form.get("nb_enfants") or 0)),
            ("nombre_parts",calculer_parts_irpp(request.form.get("situation_matrimoniale",""),int(request.form.get("nb_enfants",0) or 0))),
            ("numero_cnss",request.form.get("numero_cnss")),("numero_cnamgs",request.form.get("numero_cnamgs")),
            ("emploi",request.form.get("emploi")),("categorie_id",request.form.get("categorie_id") or None),
            ("mode_paiement",(request.form.get("mode_paiement","ESPECES") or "ESPECES").strip()),
            ("statut",request.form.get("statut","ACTIF")),("date_modification",utcnow())]:
            setattr(s,f,v)

        # ── Salaire de base : mise à jour du contrat actif ────────────────
        sb_raw = request.form.get("salaire_base")
        if sb_raw:
            try:
                nouveau_sb = float(str(sb_raw).replace(" ", "").replace(",", "."))
            except (ValueError, TypeError):
                nouveau_sb = None
            if nouveau_sb and nouveau_sb > 0:
                contrat = Contrat.query.filter_by(
                    salarie_id=s.id, tenant_id=t.id, actif=True).first()
                if contrat:
                    ancien_sb = float(contrat.salaire_base or 0)
                    if abs(nouveau_sb - ancien_sb) >= 0.01:
                        contrat.salaire_base = nouveau_sb
                        log_action("UPDATE", "contrat", contrat.id,
                            "Salaire de base {} : {} → {} FCFA".format(
                                s.nom_complet, f"{int(ancien_sb):,}".replace(",", " "),
                                f"{int(nouveau_sb):,}".replace(",", " ")))
                else:
                    # Aucun contrat actif : on en crée un pour porter le salaire
                    db.session.add(Contrat(
                        tenant_id=t.id, salarie_id=s.id, type_contrat="CDI",
                        date_debut=s.date_embauche or date.today(),
                        salaire_base=nouveau_sb, poste=s.emploi, actif=True))
                    log_action("CREATE", "contrat", None,
                        f"Contrat créé pour {s.nom_complet} — salaire "
                        + f"{int(nouveau_sb):,}".replace(",", " ") + " FCFA")

        db.session.commit()
        # ── Affectation site ──────────────────────────────────────────────
        site_id = request.form.get("site_id", type=int)
        if site_id:
            aff_prev = AffectationSite.query.filter_by(
                salarie_id=s.id, tenant_id=t.id, actif=True).first()
            if aff_prev and aff_prev.site_id != site_id:
                aff_prev.actif    = False
                aff_prev.date_fin = date.today()
                aff_prev.motif    = "Réaffecté via formulaire salarié"
            if not aff_prev or aff_prev.site_id != site_id:
                db.session.add(AffectationSite(
                    tenant_id=t.id, site_id=site_id, salarie_id=s.id,
                    date_debut=date.today(), actif=True,
                    cree_par=current_user.email))
            db.session.commit()
        elif request.form.get("retirer_site"):
            aff = AffectationSite.query.filter_by(
                salarie_id=s.id, tenant_id=t.id, actif=True).first()
            if aff:
                aff.actif    = False
                aff.date_fin = date.today()
                aff.motif    = "Retiré via formulaire salarié"
                db.session.commit()
        flash("Fiche mise à jour.", "success")
        log_action("UPDATE", "salarie", s.id, f"Modification fiche salarié {s.nom_complet}")
        db.session.commit()
        return redirect(url_for("tenant.salarie_detail", id=s.id))
    # Récupérer site actuel + liste des sites
    aff_actuelle = AffectationSite.query.filter_by(
        salarie_id=id, tenant_id=t.id, actif=True).first()
    sites = Site.query.filter_by(tenant_id=t.id, actif=True).order_by(Site.nom).all()
    contrat_actif = Contrat.query.filter_by(
        salarie_id=id, tenant_id=t.id, actif=True).first()
    salaire_actuel = int(float(contrat_actif.salaire_base)) if contrat_actif and contrat_actif.salaire_base else ""
    return render_template("tenant/salarie_form.html", salarie=s, categories=cats,
        action="modifier", tenant=t, sites=sites, aff_actuelle=aff_actuelle,
        salaire_actuel=salaire_actuel, grille_salaires=_grille_tenant(t))




@bp.route("/salaries/<int:sal_id>/contrats")
@login_required
@tenant_required
def contrats_salarie(sal_id):
    """Liste de tous les contrats d'un salarié avec historique."""
    t = get_tenant()
    s = Salarie.query.filter_by(id=sal_id, tenant_id=t.id).first_or_404()
    contrats = Contrat.query.filter_by(
        salarie_id=sal_id, tenant_id=t.id
    ).order_by(Contrat.date_debut.desc()).all()
    cats = CategorieEmploi.query.filter_by(tenant_id=t.id).order_by(CategorieEmploi.code).all()
    return render_template("tenant/contrats_salarie.html",
                           salarie=s, contrats=contrats, tenant=t,
                           categories=cats)


@bp.route("/salaries/<int:sal_id>/contrats/nouveau", methods=["GET","POST"])
@login_required
@tenant_required
@can_edit
def contrat_nouveau(sal_id):
    """Créer un nouveau contrat pour un salarié."""
    t = get_tenant()
    s = Salarie.query.filter_by(id=sal_id, tenant_id=t.id).first_or_404()
    cats = CategorieEmploi.query.filter_by(tenant_id=t.id).all()

    if request.method == "POST":
        type_c  = request.form.get("type_contrat", "CDI")
        salaire = float(request.form.get("salaire_base", 0) or 0)
        poste   = request.form.get("poste", "").strip() or s.emploi
        cat_id  = request.form.get("categorie_id", type=int)

        try:
            date_debut = datetime.strptime(request.form.get("date_debut",""), "%Y-%m-%d").date()
        except ValueError:
            flash("Date de début invalide.", "error")
            return render_template("tenant/contrat_form.html",
                                   salarie=s, tenant=t, categories=cats, contrat=None)

        date_fin = None
        if request.form.get("date_fin"):
            try:
                date_fin = datetime.strptime(request.form.get("date_fin"), "%Y-%m-%d").date()
            except ValueError:
                flash("Date de fin invalide.", "error")
                return render_template("tenant/contrat_form.html",
                                       salarie=s, tenant=t, categories=cats, contrat=None)

        if not salaire or salaire <= 0:
            flash("Le salaire de base doit être positif.", "error")
            return render_template("tenant/contrat_form.html",
                                   salarie=s, tenant=t, categories=cats, contrat=None)

        # Désactiver l'ancien contrat actif
        Contrat.query.filter_by(
            salarie_id=sal_id, tenant_id=t.id, actif=True
        ).update({"actif": False})

        # Créer le nouveau contrat
        c = Contrat(
            tenant_id    = t.id,
            salarie_id   = sal_id,
            type_contrat = type_c,
            date_debut   = date_debut,
            date_fin     = date_fin,
            date_fin_essai = _parse_date(request.form.get("date_fin_essai")),
            salaire_base = salaire,
            elements_recurrents = _build_elements_recurrents(t.id),
            poste        = poste,
            categorie_id = cat_id,
            actif        = True,
        )
        # Mettre à jour le salarié
        s.emploi = poste
        s.travail_de_nuit = request.form.get("travail_de_nuit") == "OUI"
        s.nif = request.form.get("nif")
        s.niveau = request.form.get("niveau")
        s.code_emploi = request.form.get("code_emploi")
        if cat_id:
            s.categorie_id = cat_id

        db.session.add(c)
        db.session.flush()
        log_action("CREATE", "contrat", c.id,
                   f"Nouveau contrat {type_c} pour {s.nom_complet} — "
                   f"salaire {int(salaire):,} FCFA à partir du {date_debut.strftime('%d/%m/%Y')}")
        db.session.commit()
        _cache_delete(f"{t.id}:")
        flash(f"Contrat {type_c} créé pour {s.nom_complet}.", "success")
        return redirect(url_for("tenant.contrats_salarie", sal_id=sal_id))

    return render_template("tenant/contrat_form.html",
                           salarie=s, tenant=t, categories=cats, contrat=None,
                           composants=ComposantPaie.query.filter_by(tenant_id=t.id, actif=True).order_by(ComposantPaie.libelle).all(),
                           elements_rec={})


@bp.route("/contrats/<int:id>/modifier", methods=["GET","POST"])
@login_required
@tenant_required
@can_edit
def contrat_modifier(id):
    """Modifier un contrat existant."""
    t = get_tenant()
    c = Contrat.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    s = c.salarie
    cats = CategorieEmploi.query.filter_by(tenant_id=t.id).all()

    if request.method == "POST":
        avant = c.to_dict()
        c.type_contrat = request.form.get("type_contrat", c.type_contrat)
        c.poste        = request.form.get("poste", c.poste or "").strip()
        c.categorie_id = request.form.get("categorie_id", type=int) or c.categorie_id

        try:
            c.salaire_base = float(request.form.get("salaire_base", c.salaire_base) or 0)
        except ValueError:
            pass

        if request.form.get("date_fin"):
            try:
                c.date_fin = datetime.strptime(request.form.get("date_fin"), "%Y-%m-%d").date()
            except ValueError:
                flash("Date de fin invalide.", "error")
                return render_template("tenant/contrat_form.html",
                                       salarie=s, tenant=t, categories=cats, contrat=c)
        else:
            c.date_fin = None
        c.date_fin_essai = _parse_date(request.form.get("date_fin_essai"))
        c.elements_recurrents = _build_elements_recurrents(t.id)

        db.session.flush()
        log_action("UPDATE", "contrat", c.id,
                   f"Modification contrat {s.nom_complet}",
                   avant=avant, apres=c.to_dict())
        db.session.commit()
        flash("Contrat mis à jour.", "success")
        return redirect(url_for("tenant.contrats_salarie", sal_id=s.id))

    import json as _json
    try: _er = _json.loads(c.elements_recurrents) if c.elements_recurrents else {}
    except Exception: _er = {}
    return render_template("tenant/contrat_form.html",
                           salarie=s, tenant=t, categories=cats, contrat=c,
                           composants=ComposantPaie.query.filter_by(tenant_id=t.id, actif=True).order_by(ComposantPaie.libelle).all(),
                           elements_rec=_er)


@bp.route("/contrats/<int:id>/terminer", methods=["POST"])
@login_required
@tenant_required
@can_edit
def contrat_terminer(id):
    """Marquer un contrat comme terminé : motif de rupture + date d'arrêt,
    répercutés sur le salarié pour le calcul du solde de tout compte."""
    t  = get_tenant()
    c  = Contrat.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    s  = c.salarie
    type_rupture = (request.form.get("type_rupture", "").strip().upper()
                    or request.form.get("motif", "").strip().upper() or "LICENCIEMENT")
    date_arret = _parse_date(request.form.get("date_arret")) or date.today()

    c.date_fin = date_arret
    c.actif    = False
    # Répercussion sur le salarié (utilisés par le solde de tout compte)
    s.date_cessation = date_arret
    s.type_rupture   = type_rupture
    s.statut         = "INACTIF"

    log_action("UPDATE", "contrat", c.id,
               f"Fin de contrat {s.nom_complet} — {type_rupture} au {date_arret.strftime('%d/%m/%Y')}",
               user_id=current_user.id, tenant_id=t.id)
    db.session.commit()
    flash(f"Contrat de {s.nom_complet} terminé ({type_rupture.title()}, au {date_arret.strftime('%d/%m/%Y')}).", "success")
    return redirect(url_for("tenant.contrats_salarie", sal_id=s.id))


@bp.route("/contrats/<int:id>/supprimer", methods=["POST"])
@login_required
@tenant_required
@can_edit
def contrat_supprimer(id):
    """Supprimer un contrat (uniquement si inactif ou aucun bulletin associé)."""
    t = get_tenant()
    c = Contrat.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    s = c.salarie

    if c.actif:
        flash("Impossible de supprimer le contrat actif. Terminez-le d'abord.", "error")
        return redirect(url_for("tenant.contrats_salarie", sal_id=s.id))

    log_action("DELETE", "contrat", c.id,
               f"Suppression contrat {c.type_contrat} de {s.nom_complet} "
               f"(du {c.date_debut} au {c.date_fin or 'en cours'})")
    db.session.delete(c)
    db.session.commit()
    flash("Contrat supprimé.", "success")
    return redirect(url_for("tenant.contrats_salarie", sal_id=s.id))


@bp.route("/salaries/<int:id>/supprimer", methods=["POST"])
@login_required
def salarie_supprimer(id):
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    if not current_user.is_tenant_admin:
        flash("Seul l'administrateur peut supprimer un salarié.", "error")
        return redirect(url_for("tenant.salaries"))
    s = Salarie.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    nom = s.nom_complet
    bulletins_actifs = BulletinPaie.query.filter_by(salarie_id=id).filter(
        BulletinPaie.statut.in_(["VALIDÉ","PAYÉ"])).count()
    if bulletins_actifs > 0:
        flash(f"Impossible de supprimer {nom} : {bulletins_actifs} bulletin(s) validé(s). Passez-le en INACTIF.", "error")
        return redirect(url_for("tenant.salarie_detail", id=id))
    try:
        BulletinPaie.query.filter_by(salarie_id=id).delete()
        Contrat.query.filter_by(salarie_id=id).delete()
        Pointage.query.filter_by(salarie_id=id).delete()
        Acompte.query.filter_by(salarie_id=id).delete()
        Conge.query.filter_by(salarie_id=id).delete()
        from models import DocumentSalarie, AffectationSite
        DocumentSalarie.query.filter_by(salarie_id=id).delete()
        AffectationSite.query.filter_by(salarie_id=id).delete()
        db.session.delete(s); db.session.commit()
        log_action("DELETE", "salarie", id, f"Suppression salarié {nom}")
        db.session.commit()
        flash(f"Salarié {nom} supprimé.", "success")
    except Exception as e:
        db.session.rollback(); flash(f"Erreur: {str(e)}", "error")
        return redirect(url_for("tenant.salarie_detail", id=id))
    return redirect(url_for("tenant.salaries"))

# ── Bulletins ─────────────────────────────────────────────────────────────────


@bp.route("/salaries/<int:sal_id>/solde-tout-compte")
@tenant_required
def solde_tout_compte(sal_id):
    """Calcul du solde de tout compte (indemnité congés + licenciement)."""
    t = get_tenant()
    s = Salarie.query.filter_by(id=sal_id, tenant_id=t.id)\
        .options(joinedload(Salarie.conges),
                 joinedload(Salarie.contrats)).first_or_404()

    date_cessation_str = request.args.get("date_cessation", "")
    try:
        date_cessation = datetime.strptime(date_cessation_str, "%Y-%m-%d").date() \
            if date_cessation_str else (s.date_cessation or date.today())
    except ValueError:
        date_cessation = s.date_cessation or date.today()

    # 12 derniers bulletins
    bulletins_12 = BulletinPaie.query.filter_by(
        tenant_id=t.id, salarie_id=sal_id
    ).filter(
        BulletinPaie.statut.in_(["VALIDÉ","VALIDE","PAYÉ"])
    ).order_by(BulletinPaie.date_creation.desc()).limit(12).all()

    from conges_avance import calculer_solde_tout_compte
    cause = request.args.get("cause") or (s.type_rupture or "LICENCIEMENT")
    solde = calculer_solde_tout_compte(
        s, bulletins_12, date_cessation, convention=t.convention,
        cause=cause, jours_conge_par_mois=t.jours_conge_par_mois
    )

    return render_template("tenant/solde_tout_compte.html",
        tenant=t, salarie=s, solde=solde, date_cessation=date_cessation,
    )




@bp.route("/salaries/<int:sal_id>/document/<type_doc>")
@login_required
def salarie_document(sal_id, type_doc):
    """
    Génère un document RH PDF pour un salarié.
    type_doc : attestation-travail | certificat-travail | attestation-salaire | solde-tout-compte
    """
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    s = (Salarie.query.filter_by(id=sal_id, tenant_id=t.id)
         .options(joinedload(Salarie.conges), joinedload(Salarie.contrats))
         .first_or_404())

    from documents_rh import (attestation_travail, certificat_travail,
                              attestation_salaire, solde_tout_compte_pdf)
    base_nom = f"{s.nom}_{s.prenom}".replace(" ", "_")

    try:
        if type_doc == "attestation-travail":
            pdf = attestation_travail(s, t)
            nom = f"attestation_travail_{base_nom}.pdf"

        elif type_doc == "certificat-travail":
            pdf = certificat_travail(s, t)
            nom = f"certificat_travail_{base_nom}.pdf"

        elif type_doc == "attestation-salaire":
            # Récupérer le dernier bulletin pour les montants
            dernier = (BulletinPaie.query.filter_by(tenant_id=t.id, salarie_id=sal_id)
                       .order_by(BulletinPaie.date_creation.desc()).first())
            brut = float(dernier.salaire_brut) if dernier else None
            net  = float(dernier.net_a_payer) if dernier else None
            if brut is None:
                contrat = next((c for c in s.contrats if c.actif), None)
                brut = float(contrat.salaire_base) if contrat else None
            pdf = attestation_salaire(s, t, brut, net)
            nom = f"attestation_salaire_{base_nom}.pdf"

        elif type_doc == "solde-tout-compte":
            date_cess_str = request.args.get("date_cessation", "")
            date_cess = parse_date(date_cess_str) or s.date_cessation or date.today()
            bulletins_12 = (BulletinPaie.query.filter_by(tenant_id=t.id, salarie_id=sal_id)
                            .filter(BulletinPaie.statut.in_(["VALIDÉ", "VALIDE", "PAYÉ"]))
                            .order_by(BulletinPaie.date_creation.desc()).limit(12).all())
            from conges_avance import calculer_solde_tout_compte
            cause = request.args.get("cause") or (s.type_rupture or "LICENCIEMENT")
            solde = calculer_solde_tout_compte(
                s, bulletins_12, date_cess, convention=t.convention,
                cause=cause, jours_conge_par_mois=t.jours_conge_par_mois
            )
            pdf = solde_tout_compte_pdf(s, t, solde, date_cess)
            nom = f"solde_tout_compte_{base_nom}.pdf"

        else:
            flash("Type de document inconnu.", "error")
            return redirect(url_for("tenant.salarie_detail", id=sal_id))

        log_action("EXPORT", "salarie", sal_id,
                   f"Document {type_doc} généré pour {s.nom_complet}",
                   user_id=current_user.id, tenant_id=t.id)
        db.session.commit()
        return _doc_response(pdf, nom)

    except Exception as e:
        logger.error(f"Erreur génération document {type_doc} : {e}")
        flash(f"Erreur lors de la génération du document : {e}", "error")
        return redirect(url_for("tenant.salarie_detail", id=sal_id))


# ── DOSSIER SALARIÉ : pièces jointes (contrat signé, CNI, diplômes…) ───────────
_DOC_MIMES = {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}

@bp.route("/salaries/<int:id>/documents", methods=["POST"])
@login_required
def salarie_document_upload(id):
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        flash("Accès refusé.", "error"); return redirect(url_for("tenant.salarie_detail", id=id))
    s = Salarie.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    from models import DocumentSalarie
    f = request.files.get("fichier")
    if not f or not f.filename:
        flash("Aucun fichier sélectionné.", "error"); return redirect(url_for("tenant.salarie_detail", id=id))
    f.seek(0, 2); taille = f.tell(); f.seek(0)
    if taille > 4 * 1024 * 1024:
        flash("Fichier trop volumineux (max 4 Mo).", "error"); return redirect(url_for("tenant.salarie_detail", id=id))
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in _DOC_MIMES:
        flash("Format non accepté (PDF, JPG ou PNG uniquement).", "error"); return redirect(url_for("tenant.salarie_detail", id=id))
    import base64
    data_uri = f"data:{_DOC_MIMES[ext]};base64," + base64.b64encode(f.read()).decode()
    doc = DocumentSalarie(tenant_id=t.id, salarie_id=s.id,
        type_document=(request.form.get("type_document") or "Autre")[:60],
        nom_fichier=f.filename[:255], mime=_DOC_MIMES[ext], taille=taille, contenu=data_uri)
    db.session.add(doc); db.session.commit()
    log_action("CREATE", "document_salarie", doc.id,
               f"Document « {doc.type_document} » ajouté au dossier de {s.nom_complet}",
               user_id=current_user.id, tenant_id=t.id)
    db.session.commit()
    flash("📎 Document ajouté au dossier.", "success")
    return redirect(url_for("tenant.salarie_detail", id=id))


@bp.route("/salaries/<int:id>/documents/<int:doc_id>")
@login_required
def salarie_document_download(id, doc_id):
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    from models import DocumentSalarie
    doc = DocumentSalarie.query.filter_by(id=doc_id, salarie_id=id, tenant_id=t.id).first_or_404()
    import base64, io
    try:
        raw = base64.b64decode((doc.contenu or "").split("base64,", 1)[1])
    except Exception:
        abort(404)
    return send_file(io.BytesIO(raw), mimetype=doc.mime or "application/octet-stream",
                     as_attachment=True, download_name=doc.nom_fichier or "document")


@bp.route("/salaries/<int:id>/documents/<int:doc_id>/supprimer", methods=["POST"])
@login_required
def salarie_document_supprimer(id, doc_id):
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        flash("Accès refusé.", "error"); return redirect(url_for("tenant.salarie_detail", id=id))
    from models import DocumentSalarie
    doc = DocumentSalarie.query.filter_by(id=doc_id, salarie_id=id, tenant_id=t.id).first_or_404()
    db.session.delete(doc); db.session.commit()
    flash("Document supprimé du dossier.", "success")
    return redirect(url_for("tenant.salarie_detail", id=id))




@bp.route("/salaries/imprimer")
@login_required
def salaries_imprimer():
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant(); 
    if not t: return redirect(url_for("auth.login"))
    salaries_list = Salarie.query.filter_by(tenant_id=t.id).order_by(Salarie.nom).all()
    for s in salaries_list:
        s._contrat_actif = Contrat.query.filter_by(salarie_id=s.id, tenant_id=t.id, actif=True).first()
    return render_template("tenant/salaries_print.html", salaries=salaries_list, tenant=t, now=datetime.now())


@bp.route("/salaries/registre")
@login_required
def salaries_registre():
    """Registre du personnel (registre d'employeur) — document légal listant tous
    les salariés dans l'ordre chronologique d'embauche, avec les mentions requises."""
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))
    # Ordre chronologique d'embauche (usage du registre d'employeur)
    salaries_list = (Salarie.query.filter_by(tenant_id=t.id)
                     .order_by(Salarie.date_embauche.asc().nullslast(), Salarie.id.asc()).all())
    for s in salaries_list:
        s._contrat_actif = Contrat.query.filter_by(salarie_id=s.id, tenant_id=t.id, actif=True).first()
    return render_template("tenant/registre_personnel_print.html",
        salaries=salaries_list, tenant=t, now=datetime.now())




@bp.route("/salaries/<int:id>/pointages/imprimer")
@login_required
def salarie_pointages_imprimer(id):
    """Relevé mensuel imprimable des pointages d'un salarié (totaux + répartition)."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    s = Salarie.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    mois, annee = _resoudre_mois_annee()
    import calendar
    debut = date(annee, mois, 1)
    fin   = date(annee, mois, calendar.monthrange(annee, mois)[1])
    pts = (Pointage.query
           .filter_by(tenant_id=t.id, salarie_id=id)
           .filter(Pointage.date_pointage >= debut, Pointage.date_pointage <= fin)
           .options(joinedload(Pointage.site))
           .order_by(Pointage.date_pointage).all())
    ctx = _pointages_mois_contexte(t, pts, t.convention)
    return render_template("tenant/pointages_print.html",
        tenant=t, now=datetime.now(),
        personne={"nom_complet": s.nom_complet,
                  "reference": ("Matricule : " + s.matricule) if s.matricule else (s.emploi or ""),
                  "type": "Salarié"},
        mois=mois, annee=annee, mois_libelle=_MOIS_FR[mois], **ctx)




@bp.route("/salaries/pointage/modele")
@login_required
def salaries_pointage_modele():
    """Génère le modèle Excel (format LONG) pré-rempli : une ligne par salarié
    actif et par jour du mois, dimanches et fériés déjà marqués. L'utilisateur
    saisit les heures travaillées, la nuit, et un éventuel motif d'absence."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))

    import calendar as _cal
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.worksheet.datavalidation import DataValidation
    from io import BytesIO

    annee, mois = _sal_periode_demandee()
    nb_jours = _cal.monthrange(annee, mois)[1]
    mois_nom = f"{_MOIS_FR_SAL[mois]} {annee}"
    feries = {d: n for d, n in jours_feries_annee(annee).items() if d.month == mois}

    salaries = (Salarie.query.filter_by(tenant_id=t.id, statut="ACTIF")
                .order_by(Salarie.nom, Salarie.prenom).all())

    VERT, BLANC = "0F3D36", "FFFFFF"
    FERIE_FILL, DIM_FILL = "FCE4D6", "FFF2CC"
    thin = Side(style="thin", color="D1D5DB")
    bord = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center", vertical="center")

    wb = Workbook()
    ws = wb.active
    ws.title = f"Pointage {_MOIS_FR_SAL[mois][:3]} {annee}"
    ws.sheet_view.showGridLines = False

    ws["A1"] = f"POINTAGE SALARIÉS — {mois_nom}"
    ws["A1"].font = Font(name="Arial", size=13, bold=True, color=VERT)
    ws["A2"] = ("Saisissez « Heures travaillées » (et « Dont nuit » si concerné) pour chaque jour. "
                "Les dimanches et fériés sont pré-marqués. Pour une absence, indiquez le motif "
                "(CONGE, MALADIE, INJUSTIFIEE) au lieu des heures.")
    ws["A2"].font = Font(name="Arial", size=9, italic=True, color="666666")

    entetes = ["Matricule", "Nom", "Date", "Jour", "Type",
               "Heures travaillées", "Dont nuit", "Absence (motif)"]
    for i, h in enumerate(entetes, 1):
        c = ws.cell(row=4, column=i, value=h)
        c.font = Font(name="Arial", size=10, bold=True, color=BLANC)
        c.fill = PatternFill("solid", fgColor=VERT)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = bord
    ws.row_dimensions[4].height = 30

    # Validation du motif d'absence (liste déroulante)
    dv = DataValidation(type="list", formula1='"CONGE,MALADIE,INJUSTIFIEE"', allow_blank=True)
    ws.add_data_validation(dv)

    r = 5
    for s in salaries:
        nom_complet = f"{s.nom} {s.prenom or ''}".strip()
        for d in range(1, nb_jours + 1):
            dd = date(annee, mois, d)
            if dd in feries:
                tj = "FERIE"
            elif dd.weekday() == 6:
                tj = "DIMANCHE"
            else:
                tj = "NORMAL"
            ws.cell(row=r, column=1, value=s.matricule)
            ws.cell(row=r, column=2, value=nom_complet)
            ws.cell(row=r, column=3, value=dd.strftime("%d/%m/%Y"))
            ws.cell(row=r, column=4, value=_JOURS_FR_SAL[dd.weekday()])
            ws.cell(row=r, column=5, value=tj)
            for col in range(1, 9):
                cell = ws.cell(row=r, column=col)
                cell.border = bord
                cell.font = Font(name="Arial", size=9)
                if tj == "FERIE":
                    cell.fill = PatternFill("solid", fgColor=FERIE_FILL)
                elif tj == "DIMANCHE":
                    cell.fill = PatternFill("solid", fgColor=DIM_FILL)
            dv.add(ws.cell(row=r, column=8))
            r += 1
        r += 1  # ligne vide entre salariés (lisibilité)

    for col, w in {"A": 12, "B": 24, "C": 13, "D": 7, "E": 11,
                   "F": 18, "G": 11, "H": 18}.items():
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A5"

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    from flask import send_file
    nom_fichier = f"pointage_salaries_{annee}_{mois:02d}.xlsx"
    return send_file(bio, as_attachment=True, download_name=nom_fichier,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")




@bp.route("/salaries/pointage/importer", methods=["POST"])
@login_required
def salaries_pointage_importer():
    """Étape APERÇU : lit le fichier, ventile les heures par salarié via
    ventiler_heures_mois(), compte les absences, et affiche un récapitulatif.
    Rien n'est enregistré ici."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        abort(403)

    annee, mois = _sal_periode_demandee()
    fichier = request.files.get("fichier")
    if not fichier or not fichier.filename:
        flash("Choisissez un fichier de pointage à téléverser.", "error")
        return redirect(url_for("tenant.salaries_pointage"))

    data, erreurs = _lire_pointage_salaries(fichier)
    if erreurs:
        for e in erreurs:
            flash(e, "error")
        return redirect(url_for("tenant.salaries_pointage"))
    if not data:
        flash("Aucune donnée de pointage trouvée dans le fichier.", "warning")
        return redirect(url_for("tenant.salaries_pointage"))

    feries = set(jours_feries_annee(annee).keys())
    salaries = {s.matricule: s for s in
                Salarie.query.filter_by(tenant_id=t.id, statut="ACTIF").all()}

    apercu = []
    inconnus = []
    for mat, info in data.items():
        s = salaries.get(mat)
        if not s:
            inconnus.append(mat)
            continue
        vent = ventiler_heures_mois(t.convention, info["jours"], feries=feries)
        total_abs = sum(info["absences"].values())
        apercu.append({
            "matricule": mat,
            "nom": info["nom"] or s.nom_complet,
            "salarie_id": s.id,
            "heures_sup_10": vent.get("heures_sup_10", 0),
            "heures_sup_30": vent.get("heures_sup_30", 0),
            "heures_sup_30b": vent.get("heures_sup_30b", 0),
            "heures_sup_40": vent.get("heures_sup_40", 0),
            "heures_sup_70": vent.get("heures_sup_70", 0),
            "nb_jours_travailles": len(info["jours"]),
            "absences": info["absences"],
            "total_absences": total_abs,
        })
    apercu.sort(key=lambda x: x["nom"])

    # Mémoriser en session pour l'étape de confirmation (données légères)
    session["pointage_sal"] = {
        "annee": annee, "mois": mois,
        "lignes": [{k: v for k, v in a.items() if k != "absences"} for a in apercu],
    }

    return render_template("tenant/salaries_pointage_apercu.html",
        apercu=apercu, annee=annee, mois=mois, tenant=t,
        mois_nom=f"{_MOIS_FR_SAL[mois]} {annee}",
        convention=t.convention, inconnus=inconnus)


@bp.route("/salaries/pointage/confirmer", methods=["POST"])
@login_required
def salaries_pointage_confirmer():
    """Étape CONFIRMATION : crée/complète les bulletins BROUILLON de la période
    avec les heures ventilées. Ne touche jamais un bulletin existant ni une
    période clôturée. Les absences sont affichées, jamais déduites d'office."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    if not current_user.can_edit:
        abort(403)

    stock = session.get("pointage_sal")
    if not stock or not stock.get("lignes"):
        flash("Session d'import expirée. Recommencez le téléversement.", "error")
        return redirect(url_for("tenant.salaries_pointage"))

    annee, mois = stock["annee"], stock["mois"]
    periode = PeriodePaie.query.filter_by(tenant_id=t.id, annee=annee, mois=mois).first()
    if not periode:
        flash("Aucune période de paie ouverte pour ce mois. Créez-la d'abord.", "error")
        return redirect(url_for("tenant.salaries_pointage"))
    if periode.statut not in ("OUVERT", "OUVERTE"):
        flash(f"La période {periode.libelle_mois} {periode.annee} est clôturée.", "error")
        return redirect(url_for("tenant.salaries_pointage"))

    import calendar as _cal
    fin_periode = date(annee, mois, _cal.monthrange(annee, mois)[1])

    deja = {b.salarie_id for b in
            BulletinPaie.query.filter_by(tenant_id=t.id, periode_id=periode.id).all()}

    crees = maj = ignores = 0
    for ligne in stock["lignes"]:
        s = Salarie.query.filter_by(id=ligne["salarie_id"], tenant_id=t.id).first()
        if not s:
            continue
        heures = {
            "heures_sup_10": ligne.get("heures_sup_10", 0),
            "heures_sup_30": ligne.get("heures_sup_30", 0),
            "heures_sup_30b": ligne.get("heures_sup_30b", 0),
            "heures_sup_40": ligne.get("heures_sup_40", 0),
            "heures_sup_70": ligne.get("heures_sup_70", 0),
        }
        contrat = (Contrat.query
                   .filter_by(salarie_id=s.id, tenant_id=t.id, actif=True)
                   .order_by(Contrat.date_debut.desc()).first())
        if not contrat or not contrat.salaire_base:
            ignores += 1
            continue

        if s.id in deja:
            # Bulletin existant : on met à jour SEULEMENT les heures sup, sans
            # écraser les autres ajustements déjà faits.
            b = BulletinPaie.query.filter_by(
                tenant_id=t.id, periode_id=periode.id, salarie_id=s.id).first()
            if b and b.statut == "BROUILLON":
                donnees = {"salaire_base": float(contrat.salaire_base), **heures,
                           "convention": t.convention}
                if s.date_embauche:
                    donnees["anciennete_annees"] = max(0, (fin_periode - s.date_embauche).days // 365)
                res = calculer_bulletin(donnees, nb_parts=float(s.nombre_parts or 1))
                for k, v in res.items():
                    if not k.startswith("_") and hasattr(b, k):
                        setattr(b, k, v)
                maj += 1
            else:
                ignores += 1
            continue

        # Nouveau brouillon
        donnees = {"salaire_base": float(contrat.salaire_base), **heures,
                   "convention": t.convention}
        if s.date_embauche:
            donnees["anciennete_annees"] = max(0, (fin_periode - s.date_embauche).days // 365)
        res = calculer_bulletin(donnees, nb_parts=float(s.nombre_parts or 1))
        b = BulletinPaie(tenant_id=t.id, salarie_id=s.id, periode_id=periode.id)
        for k, v in res.items():
            if not k.startswith("_") and hasattr(b, k):
                setattr(b, k, v)
        b.statut = "BROUILLON"
        b.mode_paiement = s.mode_paiement or "ESPECES"
        db.session.add(b)
        crees += 1

    db.session.commit()
    session.pop("pointage_sal", None)
    log_action("IMPORT_POINTAGE_SAL", "bulletin", periode.id,
               f"Import pointage salariés {periode.libelle_mois} {annee} : "
               f"{crees} créé(s), {maj} mis à jour")
    flash(f"Pointage importé : {crees} bulletin(s) créé(s), {maj} mis à jour. "
          f"Vérifiez et complétez les absences avant validation.", "success")
    if ignores:
        flash(f"{ignores} salarié(s) ignoré(s) (sans contrat actif, ou bulletin déjà validé).", "info")
    return redirect(url_for("tenant.bulletins", periode_id=periode.id))


@bp.route("/salaries/pointage")
@login_required
def salaries_pointage():
    """Page d'accueil de l'import : choix du mois, téléchargement du modèle,
    téléversement du fichier rempli."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))
    annee, mois = _sal_periode_demandee()
    return render_template("tenant/salaries_pointage.html",
        annee=annee, mois=mois, tenant=t,
        mois_nom=f"{_MOIS_FR_SAL[mois]} {annee}",
        convention=t.convention)




@bp.route("/parametres/contrats")
@tenant_required
def modeles_contrat():
    t = get_tenant()
    modeles = ModeleContrat.query.filter_by(tenant_id=t.id).order_by(ModeleContrat.nom).all()
    from documents_rh import BALISES_CONTRAT
    return render_template("tenant/modeles_contrat.html", tenant=t, modeles=modeles, balises=BALISES_CONTRAT)


@bp.route("/parametres/contrats/nouveau", methods=["GET", "POST"])
@tenant_required
@can_edit
def modele_contrat_nouveau():
    t = get_tenant()
    from documents_rh import BALISES_CONTRAT
    if request.method == "POST":
        m = ModeleContrat(
            tenant_id=t.id,
            nom=request.form.get("nom", "").strip() or "Modèle sans nom",
            type_contrat=request.form.get("type_contrat", "CDI"),
            contenu=request.form.get("contenu", ""))
        db.session.add(m); db.session.commit()
        flash("Modèle de contrat enregistré.", "success")
        return redirect(url_for("tenant.modeles_contrat"))
    from documents_rh import TRAMES_CONTRAT
    return render_template("tenant/modele_contrat_form.html", tenant=t, modele=None, balises=BALISES_CONTRAT, trames=TRAMES_CONTRAT)


@bp.route("/parametres/contrats/<int:id>/modifier", methods=["GET", "POST"])
@tenant_required
@can_edit
def modele_contrat_modifier(id):
    t = get_tenant()
    m = ModeleContrat.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    from documents_rh import BALISES_CONTRAT
    if request.method == "POST":
        m.nom = request.form.get("nom", "").strip() or m.nom
        m.type_contrat = request.form.get("type_contrat", m.type_contrat)
        m.contenu = request.form.get("contenu", "")
        db.session.commit()
        flash("Modèle mis à jour.", "success")
        return redirect(url_for("tenant.modeles_contrat"))
    from documents_rh import TRAMES_CONTRAT
    return render_template("tenant/modele_contrat_form.html", tenant=t, modele=m, balises=BALISES_CONTRAT, trames=TRAMES_CONTRAT)


@bp.route("/parametres/contrats/<int:id>/supprimer", methods=["POST"])
@tenant_required
@can_edit
def modele_contrat_supprimer(id):
    t = get_tenant()
    m = ModeleContrat.query.filter_by(id=id, tenant_id=t.id).first_or_404()
    db.session.delete(m); db.session.commit()
    flash("Modèle supprimé.", "success")
    return redirect(url_for("tenant.modeles_contrat"))


@bp.route("/salaries/<int:sal_id>/contrat-pdf/<int:modele_id>")
@tenant_required
def salarie_contrat_pdf(sal_id, modele_id):
    """Génère le contrat d'un salarié, l'enregistre dans son dossier ET le télécharge."""
    t = get_tenant()
    s = Salarie.query.filter_by(id=sal_id, tenant_id=t.id).first_or_404()
    m = ModeleContrat.query.filter_by(id=modele_id, tenant_id=t.id).first_or_404()
    from documents_rh import generer_contrat_pdf
    try:
        pdf = generer_contrat_pdf(m, s, t)
    except Exception as e:
        current_app.logger.error(f"[CONTRAT PDF] {e}")
        flash("Erreur lors de la génération du contrat.", "error")
        return redirect(url_for("tenant.salarie_detail", id=sal_id))
    nom = f"contrat_{s.nom}_{s.prenom}_{m.type_contrat}".replace(" ", "_") + ".pdf"

    # Archiver une copie dans le dossier du salarié (sauf si ?telecharger_seul=1)
    if request.args.get("telecharger_seul") != "1":
        try:
            import base64
            from models import DocumentSalarie
            data_uri = "data:application/pdf;base64," + base64.b64encode(pdf).decode()
            doc = DocumentSalarie(
                tenant_id=t.id, salarie_id=s.id,
                type_document="Contrat",
                nom_fichier=f"{m.nom} — {date.today().strftime('%d-%m-%Y')}.pdf"[:255],
                mime="application/pdf", taille=len(pdf), contenu=data_uri)
            db.session.add(doc); db.session.commit()
            log_action("CREATE", "document_salarie", doc.id,
                       f"Contrat « {m.nom} » généré et archivé pour {s.nom_complet}",
                       user_id=current_user.id, tenant_id=t.id)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"[CONTRAT ARCHIVE] {e}")

    return _doc_response(pdf, nom)


