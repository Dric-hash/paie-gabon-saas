"""
blueprints/api_v1.py — API REST v1 publique (OAuth2 + token API)
"""
import json, hmac, logging
from datetime import datetime, timedelta

from flask import (Blueprint, request, jsonify, current_app,
                   render_template, flash, redirect, url_for)
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from sqlalchemy import func

from models import (db, Tenant, Utilisateur, Salarie, CategorieEmploi,
                    PeriodePaie, BulletinPaie, OAuthClient, AuditLog)
from calculs_paie import calculer_bulletin
from audit import log_action
from core import (calculer_parts_irpp, tenant_required, get_tenant,
                  require_permission, cache_delete, _cache_delete)

from api_rest import (api_auth_required, _ok, _err, _paginate,
                      _salarie_dict, _bulletin_dict, _periode_dict,
                      _oauth_tokens, OAUTH_TOKEN_TTL)

logger = logging.getLogger("paiegalon")

bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")

def api_oauth_token():
    """
    OAuth2 client_credentials flow.
    Body JSON : { "client_id": "...", "client_secret": "...", "grant_type": "client_credentials" }
    """
    data       = request.get_json(force=True) or {}
    grant_type = data.get("grant_type", "")
    client_id  = data.get("client_id",  "").strip()
    client_sec = data.get("client_secret", "").strip()

    if grant_type != "client_credentials":
        return jsonify(_err("UNSUPPORTED_GRANT", "Seul client_credentials est supporté.")), 400
    if not client_id or not client_sec:
        return jsonify(_err("MISSING_CREDENTIALS", "client_id et client_secret requis.")), 400

    client = OAuthClient.query.filter_by(client_id=client_id, actif=True).first()
    if not client or not hmac.compare_digest(client.client_secret, client_sec):
        return jsonify(_err("INVALID_CLIENT", "Identifiants OAuth invalides.")), 401

    tenant = Tenant.query.filter_by(id=client.tenant_id, statut="ACTIF").first()
    if not tenant:
        return jsonify(_err("TENANT_INACTIVE", "Compte inactif ou suspendu.")), 403

    # Générer un access token
    import secrets as _sec
    access_token = _sec.token_hex(32)
    expires_at   = datetime.utcnow() + timedelta(seconds=OAUTH_TOKEN_TTL)
    _oauth_tokens[access_token] = {
        "tenant_id":  tenant.id,
        "expires_at": expires_at,
        "client_id":  client_id,
    }
    client.derniere_utilisation = datetime.utcnow()
    db.session.commit()

    logger.info(f"[API OAuth] Token émis — client={client_id} tenant={tenant.id}")
    return jsonify({
        "access_token": access_token,
        "token_type":   "Bearer",
        "expires_in":   OAUTH_TOKEN_TTL,
        "scope":        "read write",
    }), 200


@bp.route("/oauth/revoke", methods=["POST"])
def api_oauth_revoke():
    """Révoque un access token OAuth2."""
    data  = request.get_json(force=True) or {}
    token = data.get("token", "").strip()
    if token in _oauth_tokens:
        del _oauth_tokens[token]
    return jsonify({"success": True, "message": "Token révoqué."}), 200


# ── GET /api/v1/me ─────────────────────────────────────────────────────────────

@bp.route("/me")
@api_auth_required
def api_me(tenant):
    """Infos sur le tenant authentifié."""
    return _ok({
        "id":            tenant.id,
        "denomination":  tenant.denomination,
        "sigle":         tenant.sigle,
        "nif":           tenant.nif,
        "statut":        tenant.statut,
        "plan":          tenant.plan.nom if tenant.plan else None,
        "date_expiration": str(tenant.date_expiration) if tenant.date_expiration else None,
        "nb_salaries_actifs": tenant.nb_salaries_actifs,
    })


# ── GET /api/v1/salaries ───────────────────────────────────────────────────────

@bp.route("/salaries")
@api_auth_required
def api_salaries_list(tenant):
    """
    Liste des salariés.
    Filtres : ?statut=ACTIF&categorie_id=1&q=dupont
    Pagination : ?page=1&per_page=25
    """
    q = Salarie.query.filter_by(tenant_id=tenant.id)

    statut = request.args.get("statut")
    if statut:
        q = q.filter(Salarie.statut == statut.upper())

    cat_id = request.args.get("categorie_id", type=int)
    if cat_id:
        q = q.filter(Salarie.categorie_id == cat_id)

    search = request.args.get("q", "").strip()
    if search:
        like = f"%{search}%"
        q = q.filter(
            db.or_(Salarie.nom.ilike(like), Salarie.prenom.ilike(like),
                   Salarie.matricule.ilike(like))
        )

    q = q.order_by(Salarie.nom, Salarie.prenom)
    result, meta = _paginate(q, request)
    return _ok([_salarie_dict(s) for s in result.items], meta)


# ── GET /api/v1/salaries/<id> ──────────────────────────────────────────────────

@bp.route("/salaries/<int:sal_id>")
@api_auth_required
def api_salarie_detail(tenant, sal_id):
    """Détail complet d'un salarié."""
    s = Salarie.query.filter_by(id=sal_id, tenant_id=tenant.id).first()
    if not s:
        return jsonify(_err("NOT_FOUND", "Salarié introuvable.")), 404
    return _ok(_salarie_dict(s, detail=True))


# ── POST /api/v1/salaries ──────────────────────────────────────────────────────

@bp.route("/salaries", methods=["POST"])
@api_auth_required
def api_salarie_create(tenant):
    """
    Crée un nouveau salarié.
    Body JSON : { "matricule", "nom", "prenom", "emploi", "date_embauche", ... }
    """
    data = request.get_json(force=True) or {}

    # Champs obligatoires
    for champ in ["matricule", "nom", "prenom", "date_embauche"]:
        if not data.get(champ, "").strip():
            return jsonify(_err("MISSING_FIELD", f"Champ obligatoire manquant : {champ}")), 400

    # Vérifier doublon matricule
    if Salarie.query.filter_by(tenant_id=tenant.id, matricule=data["matricule"].strip()).first():
        return jsonify(_err("DUPLICATE_MATRICULE", f"Le matricule {data['matricule']} existe déjà.")), 409

    try:
        from datetime import date as _date
        date_emb = datetime.strptime(data["date_embauche"], "%Y-%m-%d").date()
    except ValueError:
        return jsonify(_err("INVALID_DATE", "Format date_embauche invalide. Utilisez YYYY-MM-DD.")), 400

    s = Salarie(
        tenant_id      = tenant.id,
        matricule      = data["matricule"].strip().upper(),
        nom            = data["nom"].strip().upper(),
        prenom         = data["prenom"].strip(),
        emploi         = data.get("emploi", "").strip(),
        email          = data.get("email", "").strip(),
        telephone      = data.get("telephone", "").strip(),
        sexe           = data.get("sexe", "").strip().upper(),
        nationalite    = data.get("nationalite", "GABONAISE").strip().upper(),
        date_embauche  = date_emb,
        numero_cnss    = data.get("numero_cnss", "").strip(),
        numero_cnamgs  = data.get("numero_cnamgs", "").strip(),
        nombre_parts   = float(data.get("nombre_parts", 1.0)),
        nb_enfants     = int(data.get("nb_enfants", 0)),
        statut         = "ACTIF",
    )
    cat_id = data.get("categorie_id")
    if cat_id:
        cat = CategorieEmploi.query.filter_by(id=cat_id, tenant_id=tenant.id).first()
        if cat:
            s.categorie_id = cat.id

    db.session.add(s)
    db.session.commit()
    _cache_delete(f"{tenant.id}:")
    logger.info(f"[API] Salarié créé — matricule={s.matricule} tenant={tenant.id}")
    return _ok(_salarie_dict(s, detail=True), status=201)


# ── PUT /api/v1/salaries/<id> ──────────────────────────────────────────────────

@bp.route("/salaries/<int:sal_id>", methods=["PUT", "PATCH"])
@api_auth_required
def api_salarie_update(tenant, sal_id):
    """Met à jour les informations d'un salarié."""
    s = Salarie.query.filter_by(id=sal_id, tenant_id=tenant.id).first()
    if not s:
        return jsonify(_err("NOT_FOUND", "Salarié introuvable.")), 404

    data = request.get_json(force=True) or {}
    champs_modifiables = [
        "nom", "prenom", "emploi", "email", "telephone", "sexe",
        "nationalite", "numero_cnss", "numero_cnamgs",
        "nombre_parts", "nb_enfants", "statut",
        "situation_matrimoniale",
    ]
    for champ in champs_modifiables:
        if champ in data:
            val = data[champ]
            if isinstance(val, str):
                val = val.strip()
                if champ in ("nom", "nationalite"):
                    val = val.upper()
            setattr(s, champ, val)

    db.session.commit()
    logger.info(f"[API] Salarié modifié — id={sal_id} tenant={tenant.id}")
    return _ok(_salarie_dict(s, detail=True))


# ── GET /api/v1/periodes ───────────────────────────────────────────────────────

@bp.route("/periodes")
@api_auth_required
def api_periodes_list(tenant):
    """Liste des périodes de paie, tri décroissant."""
    q = PeriodePaie.query.filter_by(tenant_id=tenant.id)\
        .order_by(PeriodePaie.annee.desc(), PeriodePaie.mois.desc())

    statut = request.args.get("statut")
    if statut:
        q = q.filter(PeriodePaie.statut == statut.upper())

    result, meta = _paginate(q, request, default_per_page=12)
    return _ok([_periode_dict(p) for p in result.items], meta)


# ── POST /api/v1/periodes ──────────────────────────────────────────────────────

@bp.route("/periodes", methods=["POST"])
@api_auth_required
def api_periode_create(tenant):
    """Crée une période de paie. Body : { "annee": 2026, "mois": 7 }"""
    data  = request.get_json(force=True) or {}
    annee = data.get("annee")
    mois  = data.get("mois")

    if not annee or not mois:
        return jsonify(_err("MISSING_FIELD", "annee et mois sont obligatoires.")), 400

    annee, mois = int(annee), int(mois)
    if not (1 <= mois <= 12) or not (2000 <= annee <= 2100):
        return jsonify(_err("INVALID_DATE", "Mois (1-12) ou année invalide.")), 400

    if PeriodePaie.query.filter_by(tenant_id=tenant.id, annee=annee, mois=mois).first():
        return jsonify(_err("DUPLICATE", f"La période {mois:02d}/{annee} existe déjà.")), 409

    MOIS_FR = ["","Janvier","Février","Mars","Avril","Mai","Juin",
               "Juillet","Août","Septembre","Octobre","Novembre","Décembre"]
    p = PeriodePaie(
        tenant_id    = tenant.id,
        annee        = annee,
        mois         = mois,
        libelle_mois = MOIS_FR[mois],
        statut       = "OUVERT",
    )
    db.session.add(p)
    db.session.commit()
    return _ok(_periode_dict(p), status=201)


# ── GET /api/v1/bulletins ──────────────────────────────────────────────────────

@bp.route("/bulletins")
@api_auth_required
def api_bulletins_list(tenant):
    """
    Liste des bulletins de paie.
    Filtres : ?periode_id=1&salarie_id=5&statut=VALIDÉ&annee=2026&mois=6
    """
    q = BulletinPaie.query.filter_by(tenant_id=tenant.id)\
        .join(Salarie).join(PeriodePaie)

    if request.args.get("periode_id"):
        q = q.filter(BulletinPaie.periode_id == request.args.get("periode_id", type=int))
    if request.args.get("salarie_id"):
        q = q.filter(BulletinPaie.salarie_id == request.args.get("salarie_id", type=int))
    if request.args.get("statut"):
        q = q.filter(BulletinPaie.statut == request.args["statut"])
    if request.args.get("annee"):
        q = q.filter(PeriodePaie.annee == request.args.get("annee", type=int))
    if request.args.get("mois"):
        q = q.filter(PeriodePaie.mois == request.args.get("mois", type=int))

    q = q.order_by(PeriodePaie.annee.desc(), PeriodePaie.mois.desc(), Salarie.nom)
    result, meta = _paginate(q, request)
    return _ok([_bulletin_dict(b) for b in result.items], meta)


# ── GET /api/v1/bulletins/<id> ─────────────────────────────────────────────────

@bp.route("/bulletins/<int:bul_id>")
@api_auth_required
def api_bulletin_detail(tenant, bul_id):
    """Détail complet d'un bulletin."""
    b = BulletinPaie.query.filter_by(id=bul_id, tenant_id=tenant.id)\
        .options(joinedload(BulletinPaie.salarie),
                 joinedload(BulletinPaie.periode)).first()
    if not b:
        return jsonify(_err("NOT_FOUND", "Bulletin introuvable.")), 404
    return _ok(_bulletin_dict(b, detail=True))


# ── POST /api/v1/bulletins/calculer ───────────────────────────────────────────

@bp.route("/bulletins/calculer", methods=["POST"])
@api_auth_required
def api_bulletin_calculer(tenant):
    """
    Calcule et crée un bulletin de paie.
    Body : { "salarie_id": 1, "periode_id": 2, "salaire_base": 400000, ... }
    """
    data = request.get_json(force=True) or {}

    sal_id = data.get("salarie_id")
    per_id = data.get("periode_id")
    if not sal_id or not per_id:
        return jsonify(_err("MISSING_FIELD", "salarie_id et periode_id sont obligatoires.")), 400

    s = Salarie.query.filter_by(id=sal_id, tenant_id=tenant.id).first()
    if not s:
        return jsonify(_err("NOT_FOUND", "Salarié introuvable.")), 404

    p = PeriodePaie.query.filter_by(id=per_id, tenant_id=tenant.id).first()
    if not p:
        return jsonify(_err("NOT_FOUND", "Période introuvable.")), 404

    # Vérifier doublon
    if BulletinPaie.query.filter_by(
        tenant_id=tenant.id, salarie_id=sal_id, periode_id=per_id
    ).first():
        return jsonify(_err("DUPLICATE", "Un bulletin existe déjà pour ce salarié et cette période.")), 409

    # Lancer le calcul
    from calculs_paie import calculer_bulletin
    donnees = {
        "salaire_base": float(data.get("salaire_base", 0)),
        "prime_caisse": float(data.get("prime_caisse", 0)),
        "prime_transport": float(data.get("prime_transport", 0)),
        "indem_logement": float(data.get("indem_logement", 0)),
        "heures_sup_10": float(data.get("heures_sup_10", 0)),
        "heures_sup_30": float(data.get("heures_sup_30", 0)),
        "acompte": float(data.get("acompte", 0)),
        "absences": float(data.get("absences", 0)),
        "prime_anciennete": float(data.get("prime_anciennete", 0)),
        "sursalaire": float(data.get("sursalaire", 0)),
    }
    try:
        res = calculer_bulletin(donnees, nb_parts=float(s.nombre_parts or 1))
    except Exception as e:
        return jsonify(_err("CALCUL_ERROR", f"Erreur de calcul : {e}")), 500

    # Créer le bulletin
    b = BulletinPaie(tenant_id=tenant.id, salarie_id=s.id, periode_id=p.id, statut="BROUILLON")
    for k, v in {**donnees, **res}.items():
        if hasattr(b, k):
            setattr(b, k, v)
    db.session.add(b)
    db.session.commit()
    _cache_delete(f"{tenant.id}:")
    logger.info(f"[API] Bulletin créé — sal={sal_id} periode={per_id} tenant={tenant.id}")
    return _ok(_bulletin_dict(b, detail=True), status=201)


# ── POST /api/v1/bulletins/<id>/valider ───────────────────────────────────────

@bp.route("/bulletins/<int:bul_id>/valider", methods=["POST"])
@api_auth_required
def api_bulletin_valider(tenant, bul_id):
    """Valide un bulletin en brouillon."""
    b = BulletinPaie.query.filter_by(id=bul_id, tenant_id=tenant.id).first()
    if not b:
        return jsonify(_err("NOT_FOUND", "Bulletin introuvable.")), 404
    if b.statut != "BROUILLON":
        return jsonify(_err("INVALID_STATE", f"Le bulletin est déjà en statut '{b.statut}'.")), 400

    b.statut          = "VALIDÉ"
    b.date_validation = datetime.utcnow()
    db.session.commit()
    return _ok(_bulletin_dict(b, detail=True))


# ── GET /api/v1/stats ──────────────────────────────────────────────────────────

@bp.route("/stats")
@api_auth_required
def api_stats(tenant):
    """
    Statistiques de paie du tenant.
    Optionnel : ?periode_id=X pour les stats d'une période précise.
    """
    stats = {
        "nb_salaries_actifs":   tenant.nb_salaries_actifs,
        "nb_salaries_total":    Salarie.query.filter_by(tenant_id=tenant.id).count(),
        "nb_periodes":          PeriodePaie.query.filter_by(tenant_id=tenant.id).count(),
        "nb_bulletins_total":   BulletinPaie.query.filter_by(tenant_id=tenant.id).count(),
        "nb_bulletins_valides": BulletinPaie.query.filter_by(
            tenant_id=tenant.id, statut="VALIDÉ").count(),
    }

    # Stats d'une période précise
    per_id = request.args.get("periode_id", type=int)
    if per_id:
        buls = BulletinPaie.query.filter_by(
            tenant_id=tenant.id, periode_id=per_id).all()
        stats["periode"] = {
            "nb_bulletins":       len(buls),
            "masse_salariale_brute": sum(float(b.salaire_brut or 0) for b in buls),
            "total_net_a_payer":     sum(float(b.net_a_payer or 0) for b in buls),
            "total_cnss_sal":        sum(float(b.cnss_salarie or 0) for b in buls),
            "total_cnss_pat":        sum(float(b.cnss_patronale or 0) for b in buls),
            "total_irpp":            sum(float(b.irpp or 0) for b in buls),
        }

    return _ok(stats)


# ── Gestion des clients OAuth depuis l'interface ───────────────────────────────

@bp.route("/api/clients")
@tenant_required
def api_clients_list():
    """Page de gestion des clients OAuth du tenant."""
    t = get_tenant()
    clients = OAuthClient.query.filter_by(tenant_id=t.id).order_by(
        OAuthClient.date_creation.desc()).all()
    return render_template("tenant/api_clients.html", tenant=t, clients=clients)


@bp.route("/api/clients/creer", methods=["POST"])
@tenant_required
def api_client_creer():
    """Crée un nouveau client OAuth2 pour ce tenant."""
    t   = get_tenant()
    nom = request.form.get("nom", "").strip()
    if not nom:
        flash("Le nom du client est obligatoire.", "error")
        return redirect(url_for("tenant.api_clients_list"))

    import secrets as _sec
    client = OAuthClient(
        tenant_id     = t.id,
        nom           = nom,
        client_id     = f"pg_{_sec.token_hex(16)}",
        client_secret = _sec.token_hex(32),
        description   = request.form.get("description", "").strip(),
        actif         = True,
    )
    db.session.add(client)
    db.session.commit()
    flash(f"Client OAuth '{nom}' créé. Conservez le secret — il ne sera plus affiché.", "success")
    return redirect(url_for("tenant.api_clients_list"))


@bp.route("/api/clients/<int:client_id>/supprimer", methods=["POST"])
@tenant_required
def api_client_supprimer(client_id):
    """Désactive un client OAuth."""
    t      = get_tenant()
    client = OAuthClient.query.filter_by(id=client_id, tenant_id=t.id).first_or_404()
    client.actif = False
    db.session.commit()
    flash(f"Client '{client.nom}' désactivé.", "success")
    return redirect(url_for("tenant.api_clients_list"))


@bp.route("/recherche")
@login_required
def recherche_globale():
    """Recherche globale : salariés, journaliers, bulletins, acomptes."""
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()