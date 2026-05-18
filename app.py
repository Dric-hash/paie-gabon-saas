"""
app.py — SaaS Paie Gabon — Multi-tenant
"""
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, abort
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from datetime import datetime, date, timedelta
from functools import wraps
import io, os, secrets as sec

from models import (db, Plan, Tenant, Utilisateur, CategorieEmploi, Salarie,
                    Contrat, PeriodePaie, BulletinPaie, RubriquePaie, Conge)
from calculs_paie import calculer_bulletin, calculer_masse_salariale

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY","saas-paie-gabon-2026")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL","sqlite:///saas_paie.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Veuillez vous connecter."

@login_manager.user_loader
def load_user(uid): return Utilisateur.query.get(int(uid))

# ── Décorateurs ───────────────────────────────────────────────────────────────
def super_admin_required(f):
    @wraps(f)
    def d(*a,**k):
        if not current_user.is_authenticated or not current_user.is_super_admin: abort(403)
        return f(*a,**k)
    return d

def tenant_required(f):
    @wraps(f)
    def d(*a,**k):
        if not current_user.is_authenticated: return redirect(url_for("login"))
        if not current_user.is_super_admin and (not current_user.tenant_id or not current_user.tenant or current_user.tenant.statut not in ("ACTIF","ESSAI")):
            flash("Compte suspendu ou non associé à une entreprise.","error")
            return redirect(url_for("login"))
        return f(*a,**k)
    return d

def can_edit(f):
    @wraps(f)
    def d(*a,**k):
        if not current_user.can_edit: abort(403)
        return f(*a,**k)
    return d

def get_tenant():
    if current_user.is_super_admin: return None
    return current_user.tenant

# ── Auth ──────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("admin_dashboard") if current_user.is_super_admin else url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET","POST"])
def login():
    if current_user.is_authenticated: return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        pw    = request.form.get("password","")
        user  = Utilisateur.query.filter_by(email=email, actif=True).first()
        if user and user.check_password(pw):
            login_user(user)
            user.derniere_connexion = datetime.utcnow()
            db.session.commit()
            return redirect(url_for("index"))
        flash("Email ou mot de passe incorrect.","error")
    return render_template("auth/login.html")

@app.route("/inscription", methods=["GET","POST"])
def inscription():
    plans = Plan.query.filter_by(actif=True).all()
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        if Utilisateur.query.filter_by(email=email).first():
            flash("Email déjà utilisé.","error")
            return render_template("auth/inscription.html", plans=plans)
        plan = Plan.query.get(request.form.get("plan_id","")) or Plan.query.filter_by(code="STARTER").first()
        denom = request.form.get("denomination","").strip()
        slug_base = denom.lower().replace(" ","_")[:30]
        slug = slug_base; i=1
        while Tenant.query.filter_by(slug=slug).first(): slug=f"{slug_base}_{i}"; i+=1
        t = Tenant(slug=slug, denomination=denom.upper(),
                   sigle=request.form.get("sigle","").strip().upper(),
                   activite=request.form.get("activite","").strip(),
                   nif=request.form.get("nif","").strip(),
                   telephone=request.form.get("telephone","").strip(),
                   ville=request.form.get("ville","Libreville"),
                   pays="Gabon", plan_id=plan.id if plan else None,
                   statut="ESSAI", date_expiration=datetime.utcnow()+timedelta(days=30))
        t.token_api = sec.token_hex(32)
        db.session.add(t); db.session.flush()
        for code,lib in [("C1","Ouvriers"),("C2","Techniciens"),("C3","Conducteurs de Travaux"),("C4","Cadres")]:
            db.session.add(CategorieEmploi(tenant_id=t.id,code=code,libelle=lib))
        admin = Utilisateur(nom=request.form.get("nom","").strip().upper(),
                            prenom=request.form.get("prenom","").strip(),
                            email=email, role="TENANT_ADMIN", tenant_id=t.id, actif=True)
        admin.set_password(request.form.get("password",""))
        db.session.add(admin); db.session.commit()
        flash(f"Bienvenue ! Essai gratuit de 30 jours activé.","success")
        login_user(admin)
        return redirect(url_for("dashboard"))
    return render_template("auth/inscription.html", plans=plans)

@app.route("/logout")
@login_required
def logout():
    logout_user(); return redirect(url_for("login"))

# ── Super-Admin ───────────────────────────────────────────────────────────────
@app.route("/admin")
@super_admin_required
def admin_dashboard():
    tenants   = Tenant.query.order_by(Tenant.date_inscription.desc()).all()
    total_sal = db.session.query(db.func.count(Salarie.id)).scalar() or 0
    total_bul = db.session.query(db.func.count(BulletinPaie.id)).scalar() or 0
    revenus   = sum((float(t.plan.prix_mensuel) if t.plan else 0) for t in tenants if t.statut=="ACTIF")
    return render_template("admin/dashboard.html",
        tenants=tenants, nb_actifs=sum(1 for t in tenants if t.statut=="ACTIF"),
        nb_essai=sum(1 for t in tenants if t.statut=="ESSAI"),
        total_sal=total_sal, total_bul=total_bul, revenus=revenus)

@app.route("/admin/tenants")
@super_admin_required
def admin_tenants():
    q=request.args.get("q",""); statut=request.args.get("statut","")
    query=Tenant.query
    if q: query=query.filter(Tenant.denomination.ilike(f"%{q}%"))
    if statut: query=query.filter_by(statut=statut)
    return render_template("admin/tenants.html", tenants=query.order_by(Tenant.date_inscription.desc()).all(),
        plans=Plan.query.all(), q=q, statut=statut)

@app.route("/admin/tenants/<int:id>")
@super_admin_required
def admin_tenant_detail(id):
    t = Tenant.query.get_or_404(id)
    return render_template("admin/tenant_detail.html", tenant=t,
        nb_salaries=Salarie.query.filter_by(tenant_id=id).count(),
        nb_bulletins=BulletinPaie.query.filter_by(tenant_id=id).count(),
        users=Utilisateur.query.filter_by(tenant_id=id).all(),
        plans=Plan.query.all())

@app.route("/admin/tenants/<int:id>/statut", methods=["POST"])
@super_admin_required
def admin_tenant_statut(id):
    t=Tenant.query.get_or_404(id)
    t.statut=request.form.get("statut",t.statut)
    if request.form.get("plan_id"): t.plan_id=int(request.form["plan_id"])
    db.session.commit(); flash(f"{t.denomination} mis à jour.","success")
    return redirect(url_for("admin_tenant_detail",id=id))

@app.route("/admin/tenants/<int:id>/impersonate")
@super_admin_required
def admin_impersonate(id):
    u=Utilisateur.query.filter_by(tenant_id=id,role="TENANT_ADMIN").first()
    if not u: flash("Aucun admin trouvé.","error"); return redirect(url_for("admin_tenant_detail",id=id))
    logout_user(); login_user(u)
    flash(f"Connecté en tant que {u.nom_complet} ({u.tenant.denomination})","warning")
    return redirect(url_for("dashboard"))

@app.route("/admin/plans", methods=["GET","POST"])
@super_admin_required
def admin_plans():
    if request.method=="POST":
        p=Plan(code=request.form["code"].strip().upper(),
               nom=request.form["nom"].strip(),
               prix_mensuel=float(request.form.get("prix_mensuel",0)),
               max_salaries=int(request.form["max_salaries"]) if request.form.get("max_salaries") else None,
               max_utilisateurs=int(request.form["max_utilisateurs"]) if request.form.get("max_utilisateurs") else None,
               description=request.form.get("description",""))
        db.session.add(p); db.session.commit(); flash("Plan créé.","success")
    return render_template("admin/plans.html", plans=Plan.query.all())

@app.route("/admin/rubriques", methods=["GET","POST"])
@super_admin_required
def admin_rubriques():
    if request.method=="POST":
        r=RubriquePaie(code=request.form["code"].strip().upper(),
            libelle=request.form["libelle"].strip(), type=request.form.get("type","COTISATION"),
            taux_salarie=float(request.form["taux_salarie"]) if request.form.get("taux_salarie") else None,
            taux_patronal=float(request.form["taux_patronal"]) if request.form.get("taux_patronal") else None,
            plafond_mensuel=float(request.form["plafond_mensuel"]) if request.form.get("plafond_mensuel") else None)
        db.session.add(r); db.session.commit(); flash("Rubrique créée.","success")
    return render_template("admin/rubriques.html", rubriques=RubriquePaie.query.all())

# ── Tenant ────────────────────────────────────────────────────────────────────
@app.route("/app")
@tenant_required
def dashboard():
    t=get_tenant(); now=datetime.now()
    nb_actifs=Salarie.query.filter_by(tenant_id=t.id,statut="ACTIF").count()
    periode=PeriodePaie.query.filter_by(tenant_id=t.id,annee=now.year,mois=now.month).first()
    masse={}; nb_v=nb_b=0
    if periode:
        buls=BulletinPaie.query.filter_by(tenant_id=t.id,periode_id=periode.id).all()
        masse=calculer_masse_salariale(buls)
        nb_v=sum(1 for b in buls if b.statut in("VALIDÉ","PAYÉ"))
        nb_b=sum(1 for b in buls if b.statut=="BROUILLON")
    derniers=BulletinPaie.query.filter_by(tenant_id=t.id).order_by(BulletinPaie.date_creation.desc()).limit(5).all()
    return render_template("tenant/dashboard.html", tenant=t,
        nb_actifs=nb_actifs, nb_total=Salarie.query.filter_by(tenant_id=t.id).count(),
        periode=periode, masse=masse, nb_valides=nb_v, nb_brouillon=nb_b, derniers=derniers)

@app.route("/app/salaries")
@tenant_required
def salaries():
    t=get_tenant(); q=request.args.get("q",""); statut=request.args.get("statut","")
    query=Salarie.query.filter_by(tenant_id=t.id)
    if q: query=query.filter(db.or_(Salarie.nom.ilike(f"%{q}%"),Salarie.prenom.ilike(f"%{q}%"),Salarie.matricule.ilike(f"%{q}%")))
    if statut: query=query.filter_by(statut=statut)
    return render_template("tenant/salaries.html", salaries=query.order_by(Salarie.nom).all(),
        categories=CategorieEmploi.query.filter_by(tenant_id=t.id).all(), q=q, statut=statut, tenant=t)

@app.route("/app/salaries/nouveau", methods=["GET","POST"])
@tenant_required
@can_edit
def salarie_nouveau():
    t=get_tenant()
    cats=CategorieEmploi.query.filter_by(tenant_id=t.id).all()
    if request.method=="POST":
        if not t.est_dans_limite:
            flash(f"Limite atteinte ({t.plan.max_salaries} salariés). Passez au plan supérieur.","error")
            return redirect(url_for("salaries"))
        s=Salarie(tenant_id=t.id,
            matricule=request.form["matricule"].strip().upper(),
            categorie_id=request.form.get("categorie_id") or None,
            nom=request.form["nom"].strip().upper(), prenom=request.form["prenom"].strip(),
            telephone=request.form.get("telephone"), nationalite=request.form.get("nationalite","GABONAISE"),
            sexe=request.form.get("sexe"),
            date_naissance=_pd(request.form.get("date_naissance")),
            date_embauche=_pd(request.form["date_embauche"]),
            situation_matrimoniale=request.form.get("situation_matrimoniale"),
            nb_enfants=int(request.form.get("nb_enfants") or 0),
            nb_enfants_moins_16ans=int(request.form.get("nb_enfants_moins_16ans") or 0),
            nombre_parts=float(request.form.get("nombre_parts") or 1),
            numero_cnss=request.form.get("numero_cnss"), numero_cnamgs=request.form.get("numero_cnamgs"),
            emploi=request.form.get("emploi"), assujetti_cnamgs=request.form.get("assujetti_cnamgs")=="OUI", statut="ACTIF")
        db.session.add(s)
        sb=float(request.form.get("salaire_base") or 0)
        if sb: db.session.add(Contrat(tenant_id=t.id,salarie=s,type_contrat=request.form.get("type_contrat","CDI"),date_debut=s.date_embauche,salaire_base=sb,poste=s.emploi,actif=True))
        db.session.commit(); flash(f"Salarié {s.nom_complet} créé.","success")
        return redirect(url_for("salarie_detail",id=s.id))
    return render_template("tenant/salarie_form.html", salarie=None, categories=cats, action="nouveau", tenant=t)

@app.route("/app/salaries/<int:id>")
@tenant_required
def salarie_detail(id):
    t=get_tenant()
    s=Salarie.query.filter_by(id=id,tenant_id=t.id).first_or_404()
    return render_template("tenant/salarie_detail.html", salarie=s, tenant=t,
        bulletins=BulletinPaie.query.filter_by(salarie_id=id,tenant_id=t.id).order_by(BulletinPaie.date_creation.desc()).limit(12).all(),
        contrat=Contrat.query.filter_by(salarie_id=id,tenant_id=t.id,actif=True).first())

@app.route("/app/salaries/<int:id>/modifier", methods=["GET","POST"])
@tenant_required
@can_edit
def salarie_modifier(id):
    t=get_tenant(); s=Salarie.query.filter_by(id=id,tenant_id=t.id).first_or_404()
    cats=CategorieEmploi.query.filter_by(tenant_id=t.id).all()
    if request.method=="POST":
        for f,v in [("nom",request.form["nom"].strip().upper()),("prenom",request.form["prenom"].strip()),
            ("telephone",request.form.get("telephone")),("nationalite",request.form.get("nationalite")),
            ("sexe",request.form.get("sexe")),("date_naissance",_pd(request.form.get("date_naissance"))),
            ("situation_matrimoniale",request.form.get("situation_matrimoniale")),
            ("nb_enfants",int(request.form.get("nb_enfants") or 0)),
            ("nombre_parts",float(request.form.get("nombre_parts") or 1)),
            ("numero_cnss",request.form.get("numero_cnss")),("numero_cnamgs",request.form.get("numero_cnamgs")),
            ("emploi",request.form.get("emploi")),("categorie_id",request.form.get("categorie_id") or None),
            ("statut",request.form.get("statut","ACTIF")),("date_modification",datetime.utcnow())]:
            setattr(s,f,v)
        db.session.commit(); flash("Fiche mise à jour.","success")
        return redirect(url_for("salarie_detail",id=s.id))
    return render_template("tenant/salarie_form.html", salarie=s, categories=cats, action="modifier", tenant=t)

@app.route("/app/bulletins")
@tenant_required
def bulletins():
    t=get_tenant(); pid=request.args.get("periode_id",type=int); sf=request.args.get("statut","")
    periodes=PeriodePaie.query.filter_by(tenant_id=t.id).order_by(PeriodePaie.annee.desc(),PeriodePaie.mois.desc()).all()
    ps=None; buls=[]; masse={}
    if pid:
        ps=PeriodePaie.query.filter_by(id=pid,tenant_id=t.id).first_or_404()
        q=BulletinPaie.query.filter_by(periode_id=pid,tenant_id=t.id)
        if sf: q=q.filter_by(statut=sf)
        buls=q.join(Salarie).order_by(Salarie.nom).all()
        masse=calculer_masse_salariale(buls)
    return render_template("tenant/bulletins.html", periodes=periodes, periode_sel=ps,
        bulletins=buls, masse=masse, statut_filtre=sf, tenant=t)

@app.route("/app/bulletins/saisie", methods=["GET","POST"])
@tenant_required
@can_edit
def bulletin_saisie():
    t=get_tenant()
    sals=Salarie.query.filter_by(tenant_id=t.id,statut="ACTIF").order_by(Salarie.nom).all()
    pers=PeriodePaie.query.filter_by(tenant_id=t.id,statut="OUVERT").order_by(PeriodePaie.annee.desc(),PeriodePaie.mois.desc()).all()
    if request.method=="POST":
        sid=int(request.form["salarie_id"]); pid=int(request.form["periode_id"])
        s=Salarie.query.filter_by(id=sid,tenant_id=t.id).first_or_404()
        donnees={k:float(v) if v else 0 for k,v in request.form.items() if k not in("salarie_id","periode_id","csrf_token","action","nb_jours_travailles")}
        res=calculer_bulletin(donnees,nb_parts=float(s.nombre_parts or 1))
        ex=BulletinPaie.query.filter_by(tenant_id=t.id,salarie_id=sid,periode_id=pid).first()
        b=ex or BulletinPaie(tenant_id=t.id,salarie_id=sid,periode_id=pid)
        if not ex: db.session.add(b)
        for k,v in res.items():
            if not k.startswith("_") and hasattr(b,k): setattr(b,k,v)
        b.nb_jours_travailles=int(request.form.get("nb_jours_travailles") or 0)
        action=request.form.get("action","brouillon")
        if action=="valider": b.statut="VALIDÉ"; b.date_validation=datetime.utcnow()
        else: b.statut="BROUILLON"
        db.session.commit(); flash(f"Bulletin {'validé' if b.statut=='VALIDÉ' else 'sauvegardé'}.","success")
        return redirect(url_for("bulletin_detail",id=b.id))
    sid=request.args.get("salarie_id",type=int)
    ss=Salarie.query.filter_by(id=sid,tenant_id=t.id).first() if sid else None
    c=Contrat.query.filter_by(salarie_id=sid,tenant_id=t.id,actif=True).first() if sid else None
    return render_template("tenant/bulletin_saisie.html", salaries=sals, periodes=pers, salarie_sel=ss, contrat=c, tenant=t)

@app.route("/app/bulletins/<int:id>")
@tenant_required
def bulletin_detail(id):
    t=get_tenant()
    return render_template("tenant/bulletin_detail.html",
        bulletin=BulletinPaie.query.filter_by(id=id,tenant_id=t.id).first_or_404(), tenant=t)

@app.route("/app/bulletins/<int:id>/valider", methods=["POST"])
@tenant_required
@can_edit
def bulletin_valider(id):
    t=get_tenant(); b=BulletinPaie.query.filter_by(id=id,tenant_id=t.id).first_or_404()
    b.statut="VALIDÉ"; b.date_validation=datetime.utcnow(); db.session.commit()
    flash("Bulletin validé.","success"); return redirect(url_for("bulletin_detail",id=id))

@app.route("/app/bulletins/<int:id>/payer", methods=["POST"])
@tenant_required
@can_edit
def bulletin_paye(id):
    t=get_tenant(); b=BulletinPaie.query.filter_by(id=id,tenant_id=t.id).first_or_404()
    b.statut="PAYÉ"; db.session.commit(); flash("Payé.","success")
    return redirect(url_for("bulletin_detail",id=id))

@app.route("/app/periodes")
@tenant_required
def periodes():
    t=get_tenant()
    return render_template("tenant/periodes.html", tenant=t,
        periodes=PeriodePaie.query.filter_by(tenant_id=t.id).order_by(PeriodePaie.annee.desc(),PeriodePaie.mois.desc()).all())

@app.route("/app/periodes/nouvelle", methods=["POST"])
@tenant_required
@can_edit
def periode_nouvelle():
    t=get_tenant(); annee=int(request.form["annee"]); mois=int(request.form["mois"])
    noms=PeriodePaie.MOIS_NOMS
    if PeriodePaie.query.filter_by(tenant_id=t.id,annee=annee,mois=mois).first(): flash("Période existante.","warning")
    else:
        db.session.add(PeriodePaie(tenant_id=t.id,annee=annee,mois=mois,libelle_mois=noms[mois],
            trimestre=f"T{(mois-1)//3+1}",statut="OUVERT",date_ouverture=datetime.utcnow()))
        db.session.commit(); flash(f"Période {noms[mois]} {annee} créée.","success")
    return redirect(url_for("periodes"))

@app.route("/app/periodes/<int:id>/cloturer", methods=["POST"])
@tenant_required
@can_edit
def periode_cloturer(id):
    t=get_tenant(); p=PeriodePaie.query.filter_by(id=id,tenant_id=t.id).first_or_404()
    p.statut="CLÔTURÉ"; p.date_cloture=datetime.utcnow(); db.session.commit()
    flash("Période clôturée.","success"); return redirect(url_for("periodes"))

@app.route("/app/parametres")
@tenant_required
def parametres():
    t=get_tenant()
    return render_template("tenant/parametres.html", tenant=t,
        rubriques=RubriquePaie.query.filter_by(actif=True).all(),
        categories=CategorieEmploi.query.filter_by(tenant_id=t.id).all(),
        users=Utilisateur.query.filter_by(tenant_id=t.id).all())

@app.route("/app/parametres/societe", methods=["POST"])
@tenant_required
@can_edit
def parametres_societe():
    t=get_tenant()
    for f in ["denomination","sigle","activite","nif","numero_cnss","numero_cnamgs","adresse","boite_postale","telephone","ville","region"]:
        setattr(t,f,request.form.get(f,"").strip() or None)
    db.session.commit(); flash("Informations mises à jour.","success")
    return redirect(url_for("parametres"))

@app.route("/app/utilisateurs/nouveau", methods=["POST"])
@tenant_required
def utilisateur_nouveau():
    t=get_tenant()
    if not current_user.is_tenant_admin: abort(403)
    max_u=t.plan.max_utilisateurs if t.plan else 3
    if max_u and Utilisateur.query.filter_by(tenant_id=t.id).count()>=max_u:
        flash(f"Limite atteinte ({max_u} utilisateurs).","error"); return redirect(url_for("parametres"))
    email=request.form.get("email","").strip().lower()
    if Utilisateur.query.filter_by(email=email).first():
        flash("Email déjà utilisé.","error"); return redirect(url_for("parametres"))
    u=Utilisateur(nom=request.form["nom"].strip().upper(),prenom=request.form["prenom"].strip(),
        email=email,role=request.form.get("role","GESTIONNAIRE"),tenant_id=t.id,actif=True)
    u.set_password(request.form.get("password","changeme2026"))
    db.session.add(u); db.session.commit(); flash(f"Utilisateur {u.nom_complet} créé.","success")
    return redirect(url_for("parametres"))

@app.route("/app/bulletins/export/<int:periode_id>")
@tenant_required
def export_journal(periode_id):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    t=get_tenant()
    p=PeriodePaie.query.filter_by(id=periode_id,tenant_id=t.id).first_or_404()
    buls=BulletinPaie.query.filter_by(periode_id=periode_id,tenant_id=t.id).join(Salarie).order_by(Salarie.nom).all()
    wb=Workbook(); ws=wb.active; ws.title=f"Journal {p.libelle_complet}"
    ws.merge_cells("A1:R1"); ws["A1"]=f"JOURNAL DE PAIE — {p.libelle_complet} — {t.denomination}"
    ws["A1"].font=Font(bold=True,size=13); ws["A1"].alignment=Alignment(horizontal="center")
    hdrs=["Matricule","Nom","Prénom","Emploi","Cat.","Base","Brut","CNSS Sal.","CNAMGS Sal.","TCS","IRPP","Net","Net à Payer","CNSS Pat.","CNAMGS Pat.","FNH","CFP","Statut"]
    for col,h in enumerate(hdrs,1):
        c=ws.cell(row=3,column=col,value=h); c.font=Font(bold=True,color="FFFFFF")
        c.fill=PatternFill("solid",fgColor="1a2332"); c.alignment=Alignment(horizontal="center")
    for row,b in enumerate(buls,4):
        s=b.salarie
        vals=[s.matricule,s.nom,s.prenom,s.emploi,s.categorie.code if s.categorie else "",
              float(b.salaire_base or 0),float(b.salaire_brut or 0),float(b.cnss_salarie or 0),
              float(b.cnamgs_salarie or 0),float(b.tcs or 0),float(b.irpp or 0),
              float(b.salaire_net or 0),float(b.net_a_payer or 0),float(b.cnss_patronale or 0),
              float(b.cnamgs_patronale or 0),float(b.fnh or 0),float(b.cfp or 0),b.statut]
        for col,v in enumerate(vals,1):
            cell=ws.cell(row=row,column=col,value=v)
            if isinstance(v,float): cell.number_format='#,##0'
            if row%2==0: cell.fill=PatternFill("solid",fgColor="F5F5F5")
    out=io.BytesIO(); wb.save(out); out.seek(0)
    return send_file(out,mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,download_name=f"Journal_{p.libelle_mois}_{p.annee}_{t.slug}.xlsx")

@app.route("/api/calculer-bulletin", methods=["POST"])
@tenant_required
def api_calculer():
    t=get_tenant(); data=request.get_json(); sid=data.pop("salarie_id",None); nb_parts=1.0
    if sid:
        s=Salarie.query.filter_by(id=sid,tenant_id=t.id).first()
        if s: nb_parts=float(s.nombre_parts or 1)
    return jsonify(calculer_bulletin(data,nb_parts=nb_parts))

@app.route("/api/salarie/<int:id>/contrat")
@tenant_required
def api_contrat(id):
    t=get_tenant()
    c=Contrat.query.filter_by(salarie_id=id,tenant_id=t.id,actif=True).first()
    if c: return jsonify({"salaire_base":float(c.salaire_base),"poste":c.poste})
    return jsonify({})

def _pd(v):
    if not v: return None
    try: return datetime.strptime(v,"%Y-%m-%d").date()
    except: return None

@app.template_filter("fcfa")
def fcfa_filter(v):
    try: return f"{int(float(v)):,}".replace(","," ")+" FCFA"
    except: return "— FCFA"

@app.template_filter("date_fr")
def date_fr_filter(v):
    if not v: return "—"
    if isinstance(v,str):
        try: v=datetime.strptime(v[:10],"%Y-%m-%d").date()
        except: return v
    return v.strftime("%d/%m/%Y")

@app.context_processor
def inject_globals(): return {"now":datetime.now(),"enumerate":enumerate}

@app.errorhandler(403)
def forbidden(e): return render_template("auth/403.html"),403

def init_db():
    db.create_all()
    if not Plan.query.first():
        for code,nom,prix,ms,mu,desc in [
            ("STARTER","Starter",15000,10,1,"10 salariés max, 1 utilisateur"),
            ("PRO","Pro",35000,50,3,"50 salariés max, 3 utilisateurs"),
            ("CABINET","Cabinet",100000,None,10,"Illimité, 10 utilisateurs"),
        ]: db.session.add(Plan(code=code,nom=nom,prix_mensuel=prix,max_salaries=ms,max_utilisateurs=mu,description=desc))
    if not RubriquePaie.query.first():
        for code,lib,typ,ts,tp,plaf in [
            ("CNSS","Caisse Nationale Sécurité Sociale","COTISATION",0.025,0.16,1500000),
            ("CNAMGS","Assurance Maladie Garantie Sociale","COTISATION",0.02,0.041,2500000),
            ("TCS","Taxe Complémentaire Salaires","RETENUE",0.05,None,None),
            ("FNH","Fonds National Habitat","COTISATION",None,0.02,1500000),
            ("CFP","Contribution Formation Professionnelle","COTISATION",None,0.005,None),
        ]: db.session.add(RubriquePaie(code=code,libelle=lib,type=typ,taux_salarie=ts,taux_patronal=tp,plafond_mensuel=plaf))
    if not Utilisateur.query.filter_by(role="SUPER_ADMIN").first():
        sa=Utilisateur(nom="ADMIN",prenom="Super",email="admin@paiegalon.ga",role="SUPER_ADMIN",actif=True)
        sa.set_password("Admin2026!"); db.session.add(sa)
    if not Tenant.query.first():
        db.session.flush()
        plan=Plan.query.filter_by(code="PRO").first()
        t=Tenant(slug="demo",denomination="ENTREPRISE DEMO",sigle="DEMO",activite="À RENSEIGNER",
                 nif="",ville="Libreville",pays="Gabon",
                 plan_id=plan.id if plan else None,statut="ACTIF")
        t.token_api=sec.token_hex(32)
        db.session.add(t); db.session.flush()
        for code,lib in [("C1","Ouvriers"),("C2","Techniciens"),("C3","Conducteurs"),("C4","Cadres")]:
            db.session.add(CategorieEmploi(tenant_id=t.id,code=code,libelle=lib))
        u=Utilisateur(nom="DEMO",prenom="Responsable",email="demo@paiegalon.ga",role="TENANT_ADMIN",tenant_id=t.id,actif=True)
        u.set_password("Demo2026!"); db.session.add(u)
    db.session.commit()
    print("Base initialisée.\n  Super-admin: superadmin@paiegalon.com / Admin2026!\n  Compte démo: demo@paiegalon.ga / Demo2026!")
  
with app.app_context():
    init_db()

if __name__=="__main__":
    with app.app_context(): init_db()
    app.run(debug=True,port=5000)
