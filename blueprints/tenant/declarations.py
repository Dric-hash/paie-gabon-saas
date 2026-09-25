# -*- coding: utf-8 -*-
"""Déclarations sociales et fiscales (CNSS, CNAMGS, DAS) + exports Excel/CSV —
extrait de tenant.py (même blueprint « tenant »)."""
from datetime import datetime, date, timedelta
from flask import render_template, request, redirect, url_for, flash, session, Response, send_file
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from sqlalchemy import desc, func
from blueprints.tenant import bp
from core import tenant_required, get_tenant, plan_required
from audit import log_action
from models import db, BulletinPaie, PeriodePaie, Salarie


def _gen_excel_cnss(tenant, trim_label, annee, mois_labels,
                    sal_data, total_base_cnss, total_base_cnamgs,
                    tot_cnss_m):
    """
    Génère la feuille CNSS conforme au formulaire officiel gabonais.
    sal_data : liste de dicts {nom_complet, matricule, numero_cnss,
               date_embauche, m1_base_cnss, m2_base_cnss, m3_base_cnss}
    N_HRS = 8 (constante légale)
    """
    import openpyxl, io
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    BD2=Border(left=Side(style="thin",color="E5E7EB"),right=Side(style="thin",color="E5E7EB"),
               top=Side(style="thin",color="E5E7EB"),bottom=Side(style="thin",color="E5E7EB"))
    CTR2=Alignment(horizontal="center",vertical="center",wrap_text=True)
    LFT2=Alignment(horizontal="left",  vertical="center",wrap_text=True)
    RGT2=Alignment(horizontal="right", vertical="center")
    HF=PatternFill("solid",fgColor="1a2332"); HN=Font(bold=True,color="FFFFFF",size=9)
    TF=PatternFill("solid",fgColor="D6EAF8"); GF=PatternFill("solid",fgColor="E8F4FD")
    AF=PatternFill("solid",fgColor="EBF5FB")

    def Cx(ws,r,c,val,font=None,fill=None,align=None,fmt=None,span=None):
        if span and span>1:
            ws.merge_cells(start_row=r,start_column=c,end_row=r,end_column=c+span-1)
        cell=ws.cell(r,c)
        if val  is not None: cell.value=val
        if font is not None: cell.font=font
        if fill is not None: cell.fill=fill
        if align is not None: cell.alignment=align
        if fmt  is not None: cell.number_format=fmt
        cell.border=BD2; return cell

    wb=openpyxl.Workbook(); ws=wb.active; ws.title="CNSS"
    for i,w in enumerate([5,14,12,28,8,12,12,16,14,4,16,14,4,16,14,4,10,10],1):
        ws.column_dimensions[get_column_letter(i)].width=w

    def rh(r,h): ws.row_dimensions[r].height=h

    # ── En-tête employeur ─────────────────────────────────────────────────────
    rh(10,18)
    Cx(ws,10,1,"Matricule employeur",Font(bold=True,size=9),align=LFT2)
    Cx(ws,10,2,tenant.numero_cnss or "—",Font(size=9),align=LFT2)
    Cx(ws,10,5,"Période",Font(bold=True,size=9),align=CTR2)
    Cx(ws,10,6,trim_label,Font(bold=True,size=11),align=CTR2)
    Cx(ws,10,7,"Année",Font(bold=True,size=9),align=CTR2)
    Cx(ws,10,8,annee,Font(bold=True,size=11),align=CTR2)
    Cx(ws,10,13,"CACHET ET SIGNATURE",HN,HF,CTR2,span=4)

    rh(12,20)
    Cx(ws,12,1,"Nom ou Raison Sociale",Font(bold=True,size=9),align=LFT2)
    Cx(ws,12,2,tenant.denomination,Font(bold=True,size=9),align=LFT2,span=3)

    rh(15,16); Cx(ws,15,1,"B.P :",Font(size=9),align=LFT2)
    Cx(ws,15,3,f"VILLE : {getattr(tenant,'ville','Libreville')}",Font(size=9),align=LFT2,span=2)
    rh(17,16); Cx(ws,17,1,"TEL :",Font(size=9),align=LFT2)
    Cx(ws,17,2,getattr(tenant,"telephone",""),Font(size=9),align=LFT2)
    Cx(ws,17,9,"Effectif total",Font(bold=True,size=9),align=CTR2)
    Cx(ws,17,10,len(sal_data),Font(bold=True,size=11),TF,CTR2)
    rh(19,16); Cx(ws,19,1,"Email :",Font(size=9),align=LFT2)

    # ── Résumé cotisations ────────────────────────────────────────────────────
    rh(20,32)
    Cx(ws,20,2,"Rémunération totale plafonnée CNSS",Font(bold=True,size=8),GF,CTR2,span=3)
    Cx(ws,20,5,"Montant déduction Alloc. Familiales",Font(size=8),GF,CTR2,span=3)
    Cx(ws,20,9,"Rémunération totale plafonnée CNAMGS",Font(bold=True,size=8),GF,CTR2,span=3)
    Cx(ws,20,13,"DATE DE RECEPTION",HN,HF,CTR2,span=4)
    rh(21,18)
    Cx(ws,21,2,total_base_cnss,Font(bold=True,size=10),TF,RGT2,"#,##0",span=3)
    Cx(ws,21,5,0,Font(size=9),TF,RGT2,"#,##0",span=3)
    Cx(ws,21,9,total_base_cnamgs,Font(bold=True,size=10),TF,RGT2,"#,##0",span=3)
    rh(22,18)
    Cx(ws,22,2,"Cotisations brutes dues CNSS",Font(bold=True,size=8),align=CTR2,span=3)
    Cx(ws,22,5,"Cotisations nettes dues CNSS",Font(bold=True,size=8),align=CTR2,span=3)
    Cx(ws,22,9,"Cotisations nettes dues CNAMGS",Font(bold=True,size=8),align=CTR2,span=3)
    rh(23,18)
    cot_cnss   = round(total_base_cnss   * 0.23)
    cot_cnamgs = round(total_base_cnamgs * 0.061)
    Cx(ws,23,2,cot_cnss,  Font(bold=True,size=10),TF,RGT2,"#,##0",span=3)
    Cx(ws,23,5,cot_cnss,  Font(bold=True,size=10),TF,RGT2,"#,##0",span=3)
    Cx(ws,23,9,cot_cnamgs,Font(bold=True,size=10),TF,RGT2,"#,##0",span=3)

    # ── En-têtes mois ─────────────────────────────────────────────────────────
    rh(25,14)
    for col,lbl in [(8,mois_labels[0]),(11,mois_labels[1]),(14,mois_labels[2])]:
        Cx(ws,25,col,lbl,HN,HF,CTR2)
    rh(26,36)
    for col,lbl in [(1,"N°"),(2,"N°CNSS /\nN°CNAMGS"),(3,"N° Paie"),
                    (4,"NOM ET PRENOM"),(5,"Taux CNSS"),(6,"EMBAUCHE"),(7,"CESSATION"),
                    (8,"SALAIRE\nPLAFONNE"),(9,"SALAIRE\nDEPLAFONNE"),(10,"Nbre\nHrs"),
                    (11,"SALAIRE\nPLAFONNE"),(12,"SALAIRE\nDEPLAFONNE"),(13,"Nbre\nHrs"),
                    (14,"SALAIRE\nPLAFONNE"),(15,"SALAIRE\nDEPLAFONNE"),(16,"Nbre\nHrs")]:
        Cx(ws,26,col,lbl,HN,HF,CTR2)

    # ── 2 lignes par employé ──────────────────────────────────────────────────
    N_HRS_CNSS = 8  # toujours 8
    dr = 27
    for i, sal in enumerate(sal_data, 1):
        r1 = dr + (i-1)*2
        r2 = dr + (i-1)*2 + 1
        bg = AF if i%2==0 else None
        rh(r1,16); rh(r2,16)

        # Ligne impaire : numéro + taux + données BASE CNSS + heures
        Cx(ws,r1,1,i,Font(size=9),bg,CTR2)
        Cx(ws,r1,5,23,Font(size=9),bg,CTR2)  # Taux CNSS = 23%
        emb = sal.get("date_embauche","")
        Cx(ws,r1,6,emb,Font(size=8),bg,CTR2)
        Cx(ws,r1,7,"",None,bg,CTR2)
        # Mois 1, 2, 3 — BASE CNSS (plafonnée), N_HRS=8
        Cx(ws,r1,8, sal.get("m1_base_cnss",0), Font(size=9),bg,RGT2,"#,##0")
        Cx(ws,r1,9, sal.get("m1_base_cnss",0), Font(size=9),bg,RGT2,"#,##0")  # déplafonné = même valeur
        Cx(ws,r1,10,N_HRS_CNSS,Font(size=9),bg,CTR2)
        Cx(ws,r1,11,sal.get("m2_base_cnss",0), Font(size=9),bg,RGT2,"#,##0")
        Cx(ws,r1,12,sal.get("m2_base_cnss",0), Font(size=9),bg,RGT2,"#,##0")
        Cx(ws,r1,13,N_HRS_CNSS,Font(size=9),bg,CTR2)
        Cx(ws,r1,14,sal.get("m3_base_cnss",0), Font(size=9),bg,RGT2,"#,##0")
        Cx(ws,r1,15,sal.get("m3_base_cnss",0), Font(size=9),bg,RGT2,"#,##0")
        Cx(ws,r1,16,N_HRS_CNSS,Font(size=9),bg,CTR2)

        # Ligne paire : N°CNSS + Matricule + NOM
        Cx(ws,r2,2,sal.get("numero_cnss",""),Font(size=9),bg,CTR2)
        Cx(ws,r2,3,sal.get("matricule",""),  Font(size=9),bg,CTR2)
        Cx(ws,r2,4,sal.get("nom_complet",""),Font(bold=True,size=9),bg,LFT2)
        for c in [1,5,6,7,8,9,10,11,12,13,14,15,16]:
            Cx(ws,r2,c,"",None,bg,None)

    # ── Sous-total ────────────────────────────────────────────────────────────
    rst = dr + len(sal_data)*2; rh(rst,18)
    Cx(ws,rst,1,"SOUS TOTAL À REPORTER PAGE SUIVANTE",Font(bold=True,size=9),TF,LFT2,span=7)
    for col,val in [(8,tot_cnss_m[0]),(11,tot_cnss_m[1]),(14,tot_cnss_m[2])]:
        Cx(ws,rst,col,val,Font(bold=True),TF,RGT2,"#,##0")
        for cc in [col+1,col+2]: Cx(ws,rst,cc,"",None,TF,None)

    # ── RECAP ─────────────────────────────────────────────────────────────────
    rr = rst+2; rh(rr,18)
    Cx(ws,rr,1,"RECAP",HN,HF,CTR2,span=2)
    Cx(ws,rr,3,"TAUX",HN,HF,CTR2)
    Cx(ws,rr,4,23,Font(bold=True,size=10),TF,CTR2)
    Cx(ws,rr,5,"MASSE SALARIALE PLAFONNEE CNSS",Font(bold=True,size=8),TF,LFT2,span=3)
    for col,val in [(8,tot_cnss_m[0]),(11,tot_cnss_m[1]),(14,tot_cnss_m[2])]:
        Cx(ws,rr,col,val,Font(bold=True),TF,RGT2,"#,##0")
        for cc in [col+1,col+2]: Cx(ws,rr,cc,"",None,TF,None)

    rcot = rr+1; rh(rcot,24)
    Cx(ws,rcot,1,"COTISATION GLOBALE DUE (CNSS)",Font(bold=True,size=11),HF,LFT2,span=13)
    Cx(ws,rcot,14,cot_cnss,Font(bold=True,size=13),TF,RGT2,"#,##0",span=3)

    buf=io.BytesIO(); wb.save(buf); buf.seek(0)
    return buf.getvalue()


def _gen_excel_cnamgs(tenant, trim_label, annee, mois_labels,
                      sal_data, total_base_cnamgs, tot_cnamgs_m):
    """
    Génère la feuille CNAMGS conforme au formulaire officiel gabonais.
    sal_data : liste de dicts {nom_complet, matricule, numero_cnamgs,
               date_embauche, m1_base_cnamgs, m2_base_cnamgs, m3_base_cnamgs}
    N_HRS = 8 (constante légale)
    """
    import openpyxl, io
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    BD2=Border(left=Side(style="thin",color="E5E7EB"),right=Side(style="thin",color="E5E7EB"),
               top=Side(style="thin",color="E5E7EB"),bottom=Side(style="thin",color="E5E7EB"))
    CTR2=Alignment(horizontal="center",vertical="center",wrap_text=True)
    LFT2=Alignment(horizontal="left",  vertical="center",wrap_text=True)
    RGT2=Alignment(horizontal="right", vertical="center")
    HF=PatternFill("solid",fgColor="1a2332"); HN=Font(bold=True,color="FFFFFF",size=9)
    T2=PatternFill("solid",fgColor="D5F5E3"); G2=PatternFill("solid",fgColor="E8F8F5")
    YF=PatternFill("solid",fgColor="FEF9E7")

    def Cx(ws,r,c,val,font=None,fill=None,align=None,fmt=None,span=None):
        if span and span>1:
            ws.merge_cells(start_row=r,start_column=c,end_row=r,end_column=c+span-1)
        cell=ws.cell(r,c)
        if val  is not None: cell.value=val
        if font is not None: cell.font=font
        if fill is not None: cell.fill=fill
        if align is not None: cell.alignment=align
        if fmt  is not None: cell.number_format=fmt
        cell.border=BD2; return cell

    wb=openpyxl.Workbook(); ws=wb.active; ws.title="CNAMGS"
    for i,w in enumerate([5,18,28,14,14,18,10,18,10,18,10],1):
        ws.column_dimensions[get_column_letter(i)].width=w

    def rh(r,h): ws.row_dimensions[r].height=h

    # ── Titre ─────────────────────────────────────────────────────────────────
    rh(5,22)
    Cx(ws,5,4,"DECLARATION TRIMESTRIELLE DE SALAIRES",
       Font(bold=True,size=13,color="1a2332"),None,CTR2,span=6)

    # ── En-tête ───────────────────────────────────────────────────────────────
    rh(8,16)
    Cx(ws,8,4,"Période :",Font(bold=True,size=9),align=CTR2)
    Cx(ws,8,5,trim_label,Font(bold=True,size=11),align=CTR2)
    Cx(ws,8,6,annee,Font(bold=True,size=11),align=CTR2)
    rh(9,16)
    Cx(ws,9,1,"Matricule employeur CNAMGS",Font(bold=True,size=9),align=LFT2)
    Cx(ws,9,2,getattr(tenant,"numero_cnamgs","—"),Font(size=9),align=LFT2,span=2)
    rh(11,20)
    Cx(ws,11,1,"Nom ou Raison Sociale",Font(bold=True,size=9),align=LFT2)
    Cx(ws,11,2,tenant.denomination,Font(bold=True,size=9),align=LFT2,span=3)
    Cx(ws,11,5,trim_label,Font(bold=True,size=11,color="1a2332"),align=CTR2)
    Cx(ws,11,6,annee,Font(bold=True,size=11),align=CTR2)
    Cx(ws,11,8,"CACHET ET SIGNATURE",HN,HF,CTR2,span=4)

    # Taux (sur lignes séparées pour éviter conflits merge)
    rh(13,16)
    Cx(ws,13,4,"Taux de cotisation",Font(bold=True,size=9),T2,LFT2,span=2)
    rh(14,16)
    Cx(ws,14,1,"B.P :",Font(size=9),align=LFT2)
    Cx(ws,14,2,getattr(tenant,"adresse",""),Font(size=9),align=LFT2,span=2)
    Cx(ws,14,4,"Employeur",Font(bold=True,size=9),T2,CTR2)
    Cx(ws,14,5,0.041,Font(bold=True,size=9),T2,RGT2,fmt="0.0%")
    rh(15,16)
    Cx(ws,15,1,"VILLE :",Font(size=9),align=LFT2)
    Cx(ws,15,2,getattr(tenant,"ville","Libreville"),Font(size=9),align=LFT2)
    Cx(ws,15,4,"Travailleur",Font(bold=True,size=9),T2,CTR2)
    Cx(ws,15,5,0.02,Font(bold=True,size=9),T2,RGT2,fmt="0.0%")
    rh(16,16)
    Cx(ws,16,1,"TEL :",Font(size=9),align=LFT2)
    Cx(ws,16,2,getattr(tenant,"telephone",""),Font(size=9),align=LFT2)
    rh(18,16)
    Cx(ws,18,4,"Plafond mensuel CNAMGS",Font(bold=True,size=9),T2,LFT2)
    Cx(ws,18,5,2500000,Font(bold=True,size=9),T2,RGT2,fmt="#,##0")

    # ── Cotisations nettes dues ───────────────────────────────────────────────
    rh(19,16)
    Cx(ws,19,2,"Cotisations nettes dues CNAMGS",Font(bold=True,size=9),align=LFT2,span=4)
    Cx(ws,19,8,"DATE DE RECEPTION",HN,HF,CTR2,span=4)
    rh(20,20)
    Cx(ws,20,2,round(total_base_cnamgs*0.061),Font(bold=True,size=12,color="1e40af"),T2,RGT2,"#,##0",span=4)
    rh(21,16)
    Cx(ws,21,2,"Cotisations payées à la CNAMGS",Font(italic=True,size=9),align=LFT2,span=4)

    # ── RECAP ─────────────────────────────────────────────────────────────────
    rh(24,18)
    Cx(ws,24,1,"Recap.",HN,HF,CTR2)
    Cx(ws,24,2,"Effectif",HN,HF,CTR2)
    Cx(ws,24,3,len(sal_data),Font(bold=True,size=11),T2,CTR2)
    Cx(ws,24,4,"MASSE SALARIALE SOUMISE À COTISATION :",Font(bold=True,size=8),T2,LFT2,span=2)
    Cx(ws,24,6,total_base_cnamgs,Font(bold=True,size=10),T2,RGT2,"#,##0")
    Cx(ws,24,8,"COTISATIONS SOCIALES:",Font(bold=True,size=9),T2,LFT2)
    Cx(ws,24,10,round(total_base_cnamgs*0.061),Font(bold=True,size=10),T2,RGT2,"#,##0")
    rh(25,16)
    Cx(ws,25,8,"Part patronale (4.1%)",Font(size=9),G2,LFT2)
    Cx(ws,25,10,round(total_base_cnamgs*0.041),Font(bold=True,size=9),G2,RGT2,"#,##0")
    rh(26,16)
    Cx(ws,26,8,"Part salariale (2%)",Font(size=9),G2,LFT2)
    Cx(ws,26,10,round(total_base_cnamgs*0.02),Font(bold=True,size=9),G2,RGT2,"#,##0")
    rh(27,18)
    Cx(ws,27,1,"TOTAL À REPORTER PAGE SUIVANTE",HN,HF,LFT2,span=5)
    for col,val in [(6,tot_cnamgs_m[0]),(8,tot_cnamgs_m[1]),(10,tot_cnamgs_m[2])]:
        Cx(ws,27,col,val,Font(bold=True),T2,RGT2,"#,##0")

    # ── Labels mois ───────────────────────────────────────────────────────────
    rh(28,14)
    for col,lbl in [(6,mois_labels[0]),(8,mois_labels[1]),(10,mois_labels[2])]:
        Cx(ws,28,col,lbl,HN,HF,CTR2,span=2)

    # ── En-têtes colonnes employés ────────────────────────────────────────────
    rh(38,14)
    Cx(ws,38,4,"Date",HN,HF,CTR2,span=2)
    for col,lbl in [(6,mois_labels[0]),(8,mois_labels[1]),(10,mois_labels[2])]:
        Cx(ws,38,col,lbl,HN,HF,CTR2,span=2)
    rh(39,36)
    for col,lbl in [(1,"N°"),(2,"Matricule"),(3,"NOM ET PRENOM"),
                    (4,"EMBAUCHE"),(5,"CESSATION"),
                    (6,"Assiette soumise\nà cotisation"),(7,"Nbre\nHrs/Jrs"),
                    (8,"Assiette soumise\nà cotisation"),(9,"Nbre\nHrs/Jrs"),
                    (10,"Assiette soumise\nà cotisation"),(11,"Nbre\nHrs/Jrs")]:
        Cx(ws,39,col,lbl,HN,HF,CTR2)

    # ── 1 ligne par employé — BASE CNAMGS, N_HRS=8 ───────────────────────────
    N_HRS_CNAMGS = 8  # toujours 8
    for i, sal in enumerate(sal_data, 1):
        r = 39 + i; rh(r,18)
        bg = PatternFill("solid",fgColor="E8F8F5") if i%2==0 else None
        Cx(ws,r,1,i,Font(size=9),bg,CTR2)
        Cx(ws,r,2,sal.get("matricule",""),Font(size=9),bg,CTR2)
        Cx(ws,r,3,sal.get("nom_complet",""),Font(bold=True,size=9),bg,LFT2)
        Cx(ws,r,4,sal.get("date_embauche",""),Font(size=8),bg,CTR2)
        Cx(ws,r,5,"",None,bg,CTR2)
        # BASE CNAMGS + toujours 8h
        Cx(ws,r,6, sal.get("m1_base_cnamgs",0),Font(size=9),bg,RGT2,"#,##0")
        Cx(ws,r,7, N_HRS_CNAMGS,Font(size=9),bg,CTR2)
        Cx(ws,r,8, sal.get("m2_base_cnamgs",0),Font(size=9),bg,RGT2,"#,##0")
        Cx(ws,r,9, N_HRS_CNAMGS,Font(size=9),bg,CTR2)
        Cx(ws,r,10,sal.get("m3_base_cnamgs",0),Font(size=9),bg,RGT2,"#,##0")
        Cx(ws,r,11,N_HRS_CNAMGS,Font(size=9),bg,CTR2)

    # ── Note pénalités ────────────────────────────────────────────────────────
    r_note = 39 + len(sal_data) + 2; rh(r_note,60)
    Cx(ws,r_note,1,
       "Au-delà de la date limite, une pénalité est appliquée conformément à la loi :\n"
       "- 25% pour non dépôt de la DTS calculé sur le montant de la DTS du dernier trimestre déclaré ;\n"
       "- 2% pour non paiement des cotisations par mois de retard cumulable au prorata temporis.",
       Font(italic=True,size=8), YF,
       Alignment(horizontal="left",vertical="top",wrap_text=True), span=11)

    buf=io.BytesIO(); wb.save(buf); buf.seek(0)
    return buf.getvalue()




# ══════════════════════════════════════════════════════════════════════════════
# DÉCLARATIONS SOCIALES & FISCALES
# ══════════════════════════════════════════════════════════════════════════════

@bp.route("/declaration-cnss")
@login_required
def declaration_cnss():
    """Déclarations sociales : CNSS/CNAMGS (trimestrielles) + CFP/FNH/TCS/IRPP (mensuelles)."""
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))

    periodes = PeriodePaie.query.filter_by(tenant_id=t.id)        .order_by(PeriodePaie.annee.desc(), PeriodePaie.mois.desc()).all()

    # Période sélectionnée
    pid     = request.args.get("periode_id", type=int)
    periode = PeriodePaie.query.filter_by(id=pid, tenant_id=t.id).first() if pid else               (periodes[0] if periodes else None)

    # Mode : mensuel (CFP/FNH/TCS/IRPP) ou trimestriel (CNSS/CNAMGS)
    mode    = request.args.get("mode", "mensuel")  # mensuel | trimestriel

    bulletins_mois = []
    bulletins_trim = []
    stats_mensuel  = {}
    stats_trim     = {}

    if periode:
        def s(buls, field): return round(sum(float(getattr(b, field) or 0) for b in buls), 2)

        # ── Bulletins du mois sélectionné (CFP, FNH, TCS, IRPP) ─────────────
        bulletins_mois = BulletinPaie.query.filter_by(
            tenant_id=t.id, periode_id=periode.id
        ).options(joinedload(BulletinPaie.salarie)).all()

        stats_mensuel = {
            "nb":           len(bulletins_mois),
            "total_brut":   s(bulletins_mois, "salaire_brut"),
            "total_cfp":    s(bulletins_mois, "cfp"),
            "total_fnh":    s(bulletins_mois, "fnh"),
            "total_tcs":    s(bulletins_mois, "tcs"),
            "total_irpp":   s(bulletins_mois, "irpp"),
        }
        stats_mensuel["total_mensuel"] = (stats_mensuel["total_cfp"]  +
                                          stats_mensuel["total_fnh"]  +
                                          stats_mensuel["total_tcs"]  +
                                          stats_mensuel["total_irpp"])

        # ── Bulletins du trimestre (CNSS/CNAMGS) ─────────────────────────────
        # Trimestre : T1=Jan-Mar, T2=Avr-Jun, T3=Jul-Sep, T4=Oct-Dec
        mois = periode.mois
        trim_debut = ((mois - 1) // 3) * 3 + 1   # 1, 4, 7, 10
        trim_fin   = trim_debut + 2                # 3, 6, 9, 12
        trim_num   = (mois - 1) // 3 + 1          # 1, 2, 3, 4
        trim_label = f"T{trim_num} {periode.annee} ({['Jan-Mar','Avr-Jun','Jul-Sep','Oct-Déc'][trim_num-1]})"

        periodes_trim = PeriodePaie.query.filter_by(
            tenant_id=t.id, annee=periode.annee
        ).filter(
            PeriodePaie.mois >= trim_debut,
            PeriodePaie.mois <= trim_fin
        ).all()
        ids_trim = [p.id for p in periodes_trim]

        bulletins_trim = BulletinPaie.query.filter(
            BulletinPaie.tenant_id == t.id,
            BulletinPaie.periode_id.in_(ids_trim)
        ).options(joinedload(BulletinPaie.salarie),
                  joinedload(BulletinPaie.periode)).all()

        # Regrouper par salarié pour le trimestre
        from collections import defaultdict
        sal_trim = defaultdict(lambda: {
            "salarie": None, "brut": 0, "base_cnss": 0,
            "cnss_sal": 0, "cnss_pat": 0,
            "base_cnamgs": 0, "cnamgs_sal": 0, "cnamgs_pat": 0,
            "mois_list": []
        })
        for b in bulletins_trim:
            k = b.salarie_id
            sal_trim[k]["salarie"]    = b.salarie
            sal_trim[k]["brut"]      += float(b.salaire_brut   or 0)
            sal_trim[k]["base_cnss"] += float(b.base_cnss      or 0)
            sal_trim[k]["cnss_sal"]  += float(b.cnss_salarie   or 0)
            sal_trim[k]["cnss_pat"]  += float(b.cnss_patronale or 0)
            sal_trim[k]["base_cnamgs"]+= float(b.base_cnamgs   or 0)
            sal_trim[k]["cnamgs_sal"]+= float(b.cnamgs_salarie  or 0)
            sal_trim[k]["cnamgs_pat"]+= float(b.cnamgs_patronale or 0)
            sal_trim[k]["mois_list"].append(b.periode.mois if b.periode else 0)

        lignes_trim = sorted(sal_trim.values(), key=lambda x: x["salarie"].nom if x["salarie"] else "")

        stats_trim = {
            "trim_label":    trim_label,
            "trim_num":      trim_num,
            "nb":            len(lignes_trim),
            "mois_couverts": sorted(set(
                m for lg in lignes_trim for m in lg["mois_list"]
            )),
            "total_brut":    sum(lg["brut"]       for lg in lignes_trim),
            "total_cnss_sal":sum(lg["cnss_sal"]   for lg in lignes_trim),
            "total_cnss_pat":sum(lg["cnss_pat"]   for lg in lignes_trim),
            "total_cnamgs_sal":sum(lg["cnamgs_sal"]  for lg in lignes_trim),
            "total_cnamgs_pat":sum(lg["cnamgs_pat"]  for lg in lignes_trim),
        }
        stats_trim["total_cnss"]     = stats_trim["total_cnss_sal"]   + stats_trim["total_cnss_pat"]
        stats_trim["total_cnamgs"]   = stats_trim["total_cnamgs_sal"] + stats_trim["total_cnamgs_pat"]
        stats_trim["total_a_verser"] = stats_trim["total_cnss"] + stats_trim["total_cnamgs"]

    else:
        lignes_trim = []

    MOIS_FR = ["","Jan","Fév","Mar","Avr","Mai","Jun","Jul","Aoû","Sep","Oct","Nov","Déc"]

    return render_template("tenant/declaration_cnss.html",
        tenant=t, periodes=periodes, periode=periode, mode=mode,
        bulletins_mois=bulletins_mois, stats_mensuel=stats_mensuel,
        lignes_trim=lignes_trim, stats_trim=stats_trim,
        MOIS_FR=MOIS_FR)


# ══════════════════════════════════════════════════════════════════════════════
# DÉCLARATION ANNUELLE DES SALAIRES (DAS) — réservée à l'abonnement Cabinet
# ══════════════════════════════════════════════════════════════════════════════

@bp.route("/declaration-das/annexes")
@login_required
def declaration_das_annexes():
    """Formulaires annexes DGI : ID20 (masse salariale), ID22 (récap salaires),
    ID26 (sommes versées aux prestataires). Page autonome imprimable."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))

    from datetime import datetime as _dt
    annee = request.args.get("annee", _dt.now().year - 1, type=int)
    try:
        from declaration_das import agreger_das, agreger_honoraires
        import models as _models
        lignes, totaux = agreger_das(t, annee, models=_models)
    except Exception:
        lignes, totaux = [], {}
    try:
        from declaration_das import agreger_honoraires
        import models as _models
        lignes_hono, tot_hono = agreger_honoraires(t, annee, models=_models)
    except Exception:
        lignes_hono, tot_hono = [], {}

    # ID20 : ventilation de la masse salariale selon le seuil de 80 000 F/mois
    SEUIL = 80000
    sup = [l for l in lignes if (l.get("total_1a5", 0) / 12.0) > SEUIL]
    inf = [l for l in lignes if (l.get("total_1a5", 0) / 12.0) <= SEUIL]
    id20 = {
        "nb_sup": len(sup), "tot_sup": round(sum(l["total_1a5"] for l in sup), 0),
        "nb_inf": len(inf), "tot_inf": round(sum(l["total_1a5"] for l in inf), 0),
        "nb_total": len(lignes), "tot_total": round(sum(l["total_1a5"] for l in lignes), 0),
        "seuil": SEUIL,
    }

    log_action("EXPORT", "declaration", None, f"Impression formulaires annexes DAS — exercice {annee}")
    db.session.commit()

    return render_template("tenant/declaration_das_annexes_print.html",
        tenant=t, lignes=lignes, totaux=totaux, id20=id20,
        lignes_hono=lignes_hono, tot_hono=tot_hono,
        annee=annee, nb=len(lignes), genere_le=_dt.now())


@bp.route("/declaration-das/id21")
@login_required
def declaration_das_id21():
    """Listing détaillé ID21 (bordereau détaillé DGI), au format officiel.
    Page autonome imprimable en paysage, avec la numérotation officielle des
    colonnes (1 à 10) et les contrôles de cohérence."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))

    from datetime import datetime as _dt
    annee = request.args.get("annee", _dt.now().year - 1, type=int)
    try:
        from declaration_das import agreger_das, controles_coherence_das
        import models as _models
        lignes, totaux = agreger_das(t, annee, models=_models)
        controles = controles_coherence_das(lignes, totaux)
    except Exception:
        lignes, totaux, controles = [], {}, []

    log_action("EXPORT", "declaration", None, f"Impression listing ID21 — exercice {annee}")
    db.session.commit()

    return render_template("tenant/declaration_das_id21_print.html",
        tenant=t, lignes=lignes, totaux=totaux, controles=controles,
        annee=annee, nb=len(lignes), genere_le=_dt.now())


@bp.route("/declaration-das/id19")
@login_required
def declaration_das_id19():
    """Bulletins individuels ID19 imprimables (un par salarié percevant plus de
    80 000 F/mois), au format officiel DGI. Page autonome, prête à imprimer."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))

    from datetime import datetime as _dt
    annee = request.args.get("annee", _dt.now().year - 1, type=int)

    try:
        from declaration_das import agreger_das, DASVide
        import models as _models
        lignes, totaux = agreger_das(t, annee, models=_models)
    except Exception:
        lignes, totaux = [], {}

    # Seuil DGI : salariés percevant plus de 80 000 F/mois en moyenne sur
    # leur période de présence. On approxime par le total imposable / 12.
    SEUIL_MENSUEL = 80000
    eligibles = [l for l in lignes if (l.get("total_1a5", 0) / 12.0) > SEUIL_MENSUEL]

    log_action("EXPORT", "declaration", None,
               f"Impression bulletins ID19 — exercice {annee} ({len(eligibles)} salariés)")
    db.session.commit()

    return render_template("tenant/declaration_das_id19_print.html",
        tenant=t, lignes=eligibles, annee=annee, nb=len(eligibles),
        genere_le=_dt.now(), seuil=SEUIL_MENSUEL)


@bp.route("/declaration-cnss/imprimer-mensuel")
@login_required
def declaration_mensuelle_imprimer():
    """Bordereau mensuel imprimable des impôts et taxes sur salaires
    (CFP, FNH, TCS, IRPP), détaillé par salarié. Page autonome, prête à imprimer."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))

    pid = request.args.get("periode_id", type=int)
    periode = (PeriodePaie.query.filter_by(id=pid, tenant_id=t.id).first() if pid
               else PeriodePaie.query.filter_by(tenant_id=t.id)
                    .order_by(PeriodePaie.annee.desc(), PeriodePaie.mois.desc()).first())
    if not periode:
        flash("Aucune période disponible pour la déclaration.", "warning")
        return redirect(url_for("tenant.declaration_cnss"))

    bulletins = (BulletinPaie.query.filter_by(tenant_id=t.id, periode_id=periode.id)
                 .options(joinedload(BulletinPaie.salarie)).all())

    lignes = []
    for b in bulletins:
        cfp  = float(b.cfp or 0);  fnh = float(b.fnh or 0)
        tcs  = float(b.tcs or 0);  irpp = float(b.irpp or 0)
        lignes.append({
            "salarie": b.salarie,
            "brut": float(b.salaire_brut or 0),
            "cfp": cfp, "fnh": fnh, "tcs": tcs, "irpp": irpp,
            "total": round(cfp + fnh + tcs + irpp, 2),
        })
    lignes.sort(key=lambda x: x["salarie"].nom if x["salarie"] else "")

    def _t(champ): return round(sum(l[champ] for l in lignes), 2)
    totaux = {c: _t(c) for c in ("brut", "cfp", "fnh", "tcs", "irpp", "total")}

    log_action("EXPORT", "declaration", periode.id,
               f"Impression bordereau mensuel impôts/taxes — {periode.libelle_complet}")
    db.session.commit()

    return render_template("tenant/declaration_mensuelle_print.html",
        tenant=t, lignes=lignes, totaux=totaux, periode=periode,
        nb=len(lignes), genere_le=datetime.now())


@bp.route("/declaration-cnss/imprimer")
@login_required
def declaration_cnss_imprimer():
    """Bordereaux officiels imprimables CNSS et CNAMGS pour un trimestre.
    Page autonome (sans habillage de l'app) — deux bordereaux distincts,
    un par caisse, prêts à imprimer ou enregistrer en PDF."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))

    pid = request.args.get("periode_id", type=int)
    periode = (PeriodePaie.query.filter_by(id=pid, tenant_id=t.id).first() if pid
               else PeriodePaie.query.filter_by(tenant_id=t.id)
                    .order_by(PeriodePaie.annee.desc(), PeriodePaie.mois.desc()).first())
    if not periode:
        flash("Aucune période disponible pour la déclaration.", "warning")
        return redirect(url_for("tenant.declaration_cnss"))

    # Trimestre couvrant le mois sélectionné
    mois = periode.mois
    trim_debut = ((mois - 1) // 3) * 3 + 1
    trim_fin   = trim_debut + 2
    trim_num   = (mois - 1) // 3 + 1
    libelles   = ["Jan-Mar", "Avr-Jun", "Jul-Sep", "Oct-Déc"]
    trim_label = f"T{trim_num} {periode.annee} ({libelles[trim_num-1]})"

    periodes_trim = (PeriodePaie.query.filter_by(tenant_id=t.id, annee=periode.annee)
                     .filter(PeriodePaie.mois >= trim_debut, PeriodePaie.mois <= trim_fin).all())
    ids_trim = [p.id for p in periodes_trim]

    bulletins_trim = (BulletinPaie.query
                      .filter(BulletinPaie.tenant_id == t.id, BulletinPaie.periode_id.in_(ids_trim))
                      .options(joinedload(BulletinPaie.salarie)).all())

    from collections import defaultdict
    agg = defaultdict(lambda: {"salarie": None, "base_cnss": 0.0, "cnss_sal": 0.0,
                               "cnss_pat": 0.0, "base_cnamgs": 0.0, "cnamgs_sal": 0.0,
                               "cnamgs_pat": 0.0})
    for b in bulletins_trim:
        a = agg[b.salarie_id]
        a["salarie"]     = b.salarie
        a["base_cnss"]  += float(b.base_cnss or 0)
        a["cnss_sal"]   += float(b.cnss_salarie or 0)
        a["cnss_pat"]   += float(b.cnss_patronale or 0)
        a["base_cnamgs"]+= float(b.base_cnamgs or 0)
        a["cnamgs_sal"] += float(b.cnamgs_salarie or 0)
        a["cnamgs_pat"] += float(b.cnamgs_patronale or 0)
    lignes = sorted(agg.values(), key=lambda x: x["salarie"].nom if x["salarie"] else "")

    def _tot(champ):
        return round(sum(l[champ] for l in lignes), 2)
    totaux = {c: _tot(c) for c in ("base_cnss", "cnss_sal", "cnss_pat",
                                   "base_cnamgs", "cnamgs_sal", "cnamgs_pat")}
    totaux["cnss_total"]   = round(totaux["cnss_sal"] + totaux["cnss_pat"], 2)
    totaux["cnamgs_total"] = round(totaux["cnamgs_sal"] + totaux["cnamgs_pat"], 2)

    log_action("EXPORT", "declaration", periode.id,
               f"Impression bordereaux CNSS/CNAMGS — {trim_label}")
    db.session.commit()

    return render_template("tenant/declaration_cnss_print.html",
        tenant=t, lignes=lignes, totaux=totaux, trim_label=trim_label,
        trim_num=trim_num, annee=periode.annee, nb=len(lignes),
        genere_le=datetime.now())


@bp.route("/declaration-das")
@login_required
@plan_required("CABINET", "CABINET_COMPTABLE")
def declaration_das():
    """Déclaration Annuelle des Salaires (DGI) — synthèse annuelle par exercice."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))

    # Exercices disponibles (années ayant au moins une période)
    annees = sorted({p.annee for p in PeriodePaie.query.filter_by(tenant_id=t.id).all()},
                    reverse=True)
    annee = request.args.get("annee", type=int) or (annees[0] if annees else date.today().year)

    lignes, totaux, erreur = [], {}, None
    lignes_hono, tot_hono = [], {}
    from declaration_das import agreger_das, agreger_honoraires, DASVide
    import models as _models
    try:
        lignes, totaux = agreger_das(t, annee, models=_models)
    except DASVide as e:
        erreur = str(e)
    # Volet honoraires (optionnel — ne bloque jamais la DAS salaires)
    try:
        lignes_hono, tot_hono = agreger_honoraires(t, annee, models=_models)
    except Exception:
        lignes_hono, tot_hono = [], {}

    # Contrôles de cohérence (aide au contrôle avant dépôt)
    controles = []
    if lignes:
        from declaration_das import controles_coherence_das
        controles = controles_coherence_das(lignes, totaux)

    return render_template("tenant/declaration_das.html",
        tenant=t, annees=annees, annee=annee,
        lignes=lignes, totaux=totaux, erreur=erreur, controles=controles,
        lignes_hono=lignes_hono, tot_hono=tot_hono)


@bp.route("/declaration-das/excel")
@login_required
@plan_required("CABINET", "CABINET_COMPTABLE")
def declaration_das_excel():
    """Télécharge la DAS de l'exercice au format Excel."""
    if current_user.is_super_admin:
        return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t:
        return redirect(url_for("auth.login"))

    annee = request.args.get("annee", type=int) or date.today().year
    from declaration_das import generer_das_excel, DASVide
    import models as _models
    try:
        contenu = generer_das_excel(t, annee, models=_models)
    except DASVide as e:
        flash(str(e), "error")
        return redirect(url_for("tenant.declaration_das", annee=annee))

    from flask import Response
    slug = (t.sigle or t.denomination or "entreprise").replace(" ", "_")[:30]
    nom = f"DAS_{slug}_{annee}.xlsx"
    return Response(
        contenu,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nom}"'},
    )




def _gen_excel_mensuel(tenant, mois_label, annee, lignes):
    """Feuille Excel des contributions mensuelles (CFP / FNH / TCS / IRPP) par salarié."""
    import openpyxl, io
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    thin = Side(style="thin", color="D0D5DD")
    BD = Border(left=thin, right=thin, top=thin, bottom=thin)
    CTR = Alignment(horizontal="center", vertical="center", wrap_text=True)
    LFT = Alignment(horizontal="left", vertical="center")
    RGT = Alignment(horizontal="right", vertical="center")
    HF = PatternFill("solid", fgColor="0f3d36"); HN = Font(bold=True, color="FFFFFF", size=10)
    TF = PatternFill("solid", fgColor="DCEAE5"); TN = Font(bold=True, size=10)

    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Déclaration mensuelle"
    ws.merge_cells("A1:H1")
    ws["A1"] = f"{tenant.denomination} — Déclaration mensuelle {mois_label} {annee}"
    ws["A1"].font = Font(bold=True, size=13, color="0f3d36"); ws["A1"].alignment = LFT
    ws.merge_cells("A2:H2")
    sub = []
    if getattr(tenant, "nif", None): sub.append(f"NIF : {tenant.nif}")
    if getattr(tenant, "numero_cnss", None): sub.append(f"CNSS : {tenant.numero_cnss}")
    ws["A2"] = " · ".join(sub); ws["A2"].font = Font(size=9, color="667085")

    headers = ["N°", "Matricule", "Nom & Prénom", "Salaire brut",
               "CFP", "FNH", "TCS", "IRPP"]
    r0 = 4
    for c, h in enumerate(headers, 1):
        cell = ws.cell(r0, c, h); cell.fill = HF; cell.font = HN; cell.alignment = CTR; cell.border = BD

    tot = {"brut": 0, "cfp": 0, "fnh": 0, "tcs": 0, "irpp": 0}
    r = r0 + 1
    for i, l in enumerate(lignes, 1):
        vals = [i, l["matricule"], l["nom"], l["brut"], l["cfp"], l["fnh"], l["tcs"], l["irpp"]]
        for c, v in enumerate(vals, 1):
            cell = ws.cell(r, c, v); cell.border = BD
            if c == 1: cell.alignment = CTR
            elif c in (2, 3): cell.alignment = LFT
            else: cell.alignment = RGT; cell.number_format = "# ##0"
        for k in tot: tot[k] += l[k]
        r += 1
    # Ligne total
    ws.cell(r, 3, "TOTAL").font = TN; ws.cell(r, 3, "TOTAL").fill = TF; ws.cell(r, 3).alignment = RGT
    for c, k in zip((4, 5, 6, 7, 8), ("brut", "cfp", "fnh", "tcs", "irpp")):
        cell = ws.cell(r, c, tot[k]); cell.font = TN; cell.fill = TF; cell.alignment = RGT
        cell.number_format = "# ##0"; cell.border = BD
    for c in (1, 2): ws.cell(r, c).fill = TF; ws.cell(r, c).border = BD
    ws.cell(r, 3).border = BD

    widths = [5, 14, 30, 15, 12, 12, 12, 14]
    for c, w in enumerate(widths, 1): ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = "A5"
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return buf.read()


@bp.route("/declaration-cnss/export-excel")
@login_required
def declaration_cnss_excel():
    """Export Excel déclarations : mensuel (CFP/FNH/TCS/IRPP) ou trimestriel (CNSS/CNAMGS)."""
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))

    pid     = request.args.get("periode_id", type=int)
    mode    = request.args.get("mode", "mensuel")
    periode = PeriodePaie.query.filter_by(id=pid, tenant_id=t.id).first_or_404()

    MOIS_FR2 = ["","Janvier","Février","Mars","Avril","Mai","Juin",
                "Juillet","Août","Septembre","Octobre","Novembre","Décembre"]

    if mode != "trimestriel":
        # ── Mensuel : CFP / FNH / TCS / IRPP par salarié ─────────────────────
        buls = (BulletinPaie.query.filter_by(tenant_id=t.id, periode_id=periode.id)
                .options(joinedload(BulletinPaie.salarie)).all())
        lignes = []
        for b in sorted(buls, key=lambda x: (x.salarie.nom_complet if x.salarie else "")):
            sal = b.salarie
            lignes.append({
                "nom": (sal.nom_complet if sal else ""),
                "matricule": (sal.matricule if sal else "") or "",
                "brut": float(b.salaire_brut or 0), "base_irpp": float(b.base_irpp or 0),
                "cfp": float(b.cfp or 0), "fnh": float(b.fnh or 0),
                "tcs": float(b.tcs or 0), "irpp": float(b.irpp or 0),
            })
        data = _gen_excel_mensuel(t, MOIS_FR2[periode.mois], periode.annee, lignes)
        from flask import Response
        nom_base = t.denomination.replace(" ", "_")[:20]
        nom = f"declaration_mensuelle_{nom_base}_{MOIS_FR2[periode.mois]}_{periode.annee}.xlsx"
        return Response(data,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{nom}"'})

    if mode == "trimestriel":
        # ── Trimestre : récupérer les 3 mois ─────────────────────────────────
        mois=periode.mois; trim_num=(mois-1)//3+1
        trim_debut=((mois-1)//3)*3+1; trim_fin=trim_debut+2
        trim_label=f"T {trim_num}"
        mois_labels=[MOIS_FR2[m] for m in range(trim_debut, trim_fin+1)]

        periodes_trim=PeriodePaie.query.filter_by(tenant_id=t.id, annee=periode.annee)            .filter(PeriodePaie.mois>=trim_debut, PeriodePaie.mois<=trim_fin).all()
        mois_map={p.mois: p.id for p in periodes_trim}

        buls_trim=BulletinPaie.query.filter(
            BulletinPaie.tenant_id==t.id,
            BulletinPaie.periode_id.in_([p.id for p in periodes_trim])
        ).options(joinedload(BulletinPaie.salarie), joinedload(BulletinPaie.periode)).all()

        # Regrouper par salarié
        from collections import defaultdict
        sal_map = defaultdict(lambda: {
            "nom_complet":"","matricule":"","numero_cnss":"","numero_cnamgs":"",
            "date_embauche":"",
            "m1_base_cnss":0,"m2_base_cnss":0,"m3_base_cnss":0,
            "m1_base_cnamgs":0,"m2_base_cnamgs":0,"m3_base_cnamgs":0,
        })
        for b in buls_trim:
            k=b.salarie_id
            sal=b.salarie
            sal_map[k]["nom_complet"]   = sal.nom_complet
            sal_map[k]["matricule"]     = sal.matricule or ""
            sal_map[k]["numero_cnss"]   = sal.numero_cnss or ""
            sal_map[k]["numero_cnamgs"] = sal.numero_cnamgs or ""
            if sal.date_embauche:
                sal_map[k]["date_embauche"] = sal.date_embauche.strftime("%d/%m/%Y")
            m = b.periode.mois if b.periode else 0
            if m == trim_debut:
                sal_map[k]["m1_base_cnss"]   = float(b.base_cnss   or 0)
                sal_map[k]["m1_base_cnamgs"] = float(b.base_cnamgs or 0)
            elif m == trim_debut+1:
                sal_map[k]["m2_base_cnss"]   = float(b.base_cnss   or 0)
                sal_map[k]["m2_base_cnamgs"] = float(b.base_cnamgs or 0)
            elif m == trim_fin:
                sal_map[k]["m3_base_cnss"]   = float(b.base_cnss   or 0)
                sal_map[k]["m3_base_cnamgs"] = float(b.base_cnamgs or 0)

        sal_data = sorted(sal_map.values(), key=lambda x: x["nom_complet"])

        tot_cnss_m  = [sum(s["m1_base_cnss"]   for s in sal_data),
                       sum(s["m2_base_cnss"]   for s in sal_data),
                       sum(s["m3_base_cnss"]   for s in sal_data)]
        tot_cnamgs_m= [sum(s["m1_base_cnamgs"] for s in sal_data),
                       sum(s["m2_base_cnamgs"] for s in sal_data),
                       sum(s["m3_base_cnamgs"] for s in sal_data)]
        total_base_cnss   = sum(tot_cnss_m)
        total_base_cnamgs = sum(tot_cnamgs_m)

        # Générer les deux fichiers et les zip
        import zipfile, io
        cnss_bytes   = _gen_excel_cnss(t, trim_label, periode.annee, mois_labels,
                                       sal_data, total_base_cnss, total_base_cnamgs, tot_cnss_m)
        cnamgs_bytes = _gen_excel_cnamgs(t, trim_label, periode.annee, mois_labels,
                                         sal_data, total_base_cnamgs, tot_cnamgs_m)

        zip_buf = io.BytesIO()
        nom_base = t.denomination.replace(" ","_")[:20]
        with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"CNSS_{nom_base}_{trim_label.replace(' ','')}_{periode.annee}.xlsx",   cnss_bytes)
            zf.writestr(f"CNAMGS_{nom_base}_{trim_label.replace(' ','')}_{periode.annee}.xlsx", cnamgs_bytes)
        zip_buf.seek(0)

        from flask import Response
        nom_zip = f"declarations_trimestrielles_{nom_base}_{trim_label.replace(' ','')}_{periode.annee}.zip"
        return Response(zip_buf.read(), mimetype="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{nom_zip}"'})




# ══════════════════════════════════════════════════════════════════════════════
# DÉCLARATION CNSS/CNAMGS — Export CSV portail électronique
# ══════════════════════════════════════════════════════════════════════════════

@bp.route("/declaration-cnss/export-csv")
@login_required
def declaration_cnss_csv():
    """
    Export CSV uploadable directement sur le portail CNSS Gabon (cnss.ga)
    et CNAMGS. Génère une archive ZIP avec les deux fichiers CSV.
    """
    if current_user.is_super_admin: return redirect(url_for("admin.admin_dashboard"))
    t = get_tenant()
    if not t: return redirect(url_for("auth.login"))

    pid     = request.args.get("periode_id", type=int)
    periode = PeriodePaie.query.filter_by(id=pid, tenant_id=t.id).first_or_404()

    # ── Calculer le trimestre ──────────────────────────────────────────────
    from declaration_cnss import calculer_trimestre, generer_csv_cnss, generer_csv_cnamgs
    trim_num, trim_debut, trim_fin, trim_label = calculer_trimestre(periode.mois)

    periodes_trim = PeriodePaie.query.filter_by(
        tenant_id=t.id, annee=periode.annee
    ).filter(
        PeriodePaie.mois >= trim_debut,
        PeriodePaie.mois <= trim_fin
    ).all()

    if not periodes_trim:
        flash("Aucune période trouvée pour ce trimestre.", "warning")
        return redirect(url_for("tenant.declaration_cnss", periode_id=pid, mode="trimestriel"))

    buls_trim = BulletinPaie.query.filter(
        BulletinPaie.tenant_id == t.id,
        BulletinPaie.periode_id.in_([p.id for p in periodes_trim])
    ).options(
        joinedload(BulletinPaie.salarie),
        joinedload(BulletinPaie.periode)
    ).all()

    if not buls_trim:
        flash("Aucun bulletin pour ce trimestre. Saisissez et validez les bulletins d'abord.", "warning")
        return redirect(url_for("tenant.declaration_cnss", periode_id=pid, mode="trimestriel"))

    # ── Regrouper par salarié ──────────────────────────────────────────────
    from collections import defaultdict
    sal_map = defaultdict(lambda: {
        "nom_complet": "", "matricule": "", "numero_cnss": "",
        "numero_cnamgs": "", "date_embauche": "",
        "m1_base_cnss": 0, "m2_base_cnss": 0, "m3_base_cnss": 0,
        "m1_base_cnamgs": 0, "m2_base_cnamgs": 0, "m3_base_cnamgs": 0,
    })

    for b in buls_trim:
        k   = b.salarie_id
        sal = b.salarie
        sal_map[k]["nom_complet"]   = sal.nom_complet
        sal_map[k]["matricule"]     = sal.matricule or ""
        sal_map[k]["numero_cnss"]   = sal.numero_cnss or ""
        sal_map[k]["numero_cnamgs"] = sal.numero_cnamgs or ""
        if sal.date_embauche:
            sal_map[k]["date_embauche"] = sal.date_embauche.strftime("%d/%m/%Y")
        m = b.periode.mois if b.periode else 0
        if m == trim_debut:
            sal_map[k]["m1_base_cnss"]   = float(b.base_cnss   or 0)
            sal_map[k]["m1_base_cnamgs"] = float(b.base_cnamgs or 0)
        elif m == trim_debut + 1:
            sal_map[k]["m2_base_cnss"]   = float(b.base_cnss   or 0)
            sal_map[k]["m2_base_cnamgs"] = float(b.base_cnamgs or 0)
        elif m == trim_fin:
            sal_map[k]["m3_base_cnss"]   = float(b.base_cnss   or 0)
            sal_map[k]["m3_base_cnamgs"] = float(b.base_cnamgs or 0)

    sal_data = list(sal_map.values())

    # ── Générer les deux CSV ───────────────────────────────────────────────
    try:
        csv_cnss   = generer_csv_cnss(sal_data, periode, t, trim_debut, trim_fin)
        csv_cnamgs = generer_csv_cnamgs(sal_data, periode, t, trim_debut, trim_fin)

        import zipfile
        zip_buf  = io.BytesIO()
        nom_base = (t.sigle or t.denomination[:15]).replace(" ", "_")
        trim_str = f"T{trim_num}_{periode.annee}"

        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"CNSS_{nom_base}_{trim_str}.csv",   csv_cnss)
            zf.writestr(f"CNAMGS_{nom_base}_{trim_str}.csv", csv_cnamgs)
            # Ajouter un fichier README avec les instructions d'upload
            zf.writestr(
                "INSTRUCTIONS_UPLOAD.txt",
                _instructions_upload(t, trim_label, periode.annee, len(sal_data))
            )
        zip_buf.seek(0)

        nom_zip = f"declarations_CNSS_CNAMGS_{nom_base}_{trim_str}.zip"
        logger.info(f"[CNSS CSV] Export {trim_str} — {len(sal_data)} salariés — tenant={t.id}")

        return send_file(
            zip_buf,
            mimetype="application/zip",
            as_attachment=True,
            download_name=nom_zip,
        )

    except Exception as e:
        logger.error(f"[CNSS CSV] Erreur export : {e}")
        flash(f"Erreur lors de la génération : {e}", "error")
        return redirect(url_for("tenant.declaration_cnss", periode_id=pid, mode="trimestriel"))


def _instructions_upload(tenant, trim_label, annee, nb_salaries) -> str:
    """Génère un fichier texte d'instructions pour l'upload sur les portails."""
    return f"""
INSTRUCTIONS D'UPLOAD — DÉCLARATIONS TRIMESTRIELLES
====================================================
Entreprise : {tenant.denomination}
NIF        : {tenant.nif or "—"}
Trimestre  : {trim_label} {annee}
Salariés   : {nb_salaries}
Généré le  : {datetime.now().strftime("%d/%m/%Y à %H:%M")}


FICHIER CNSS : CNSS_*.csv
─────────────────────────
1. Connectez-vous sur https://cnss.ga
2. Allez dans : Mon Espace → Déclarations → Nouvelle déclaration
3. Choisissez : Déclaration trimestrielle de salaires
4. Cliquez sur "Importer un fichier"
5. Sélectionnez le fichier CNSS_*.csv
6. Vérifiez les montants affichés
7. Validez et téléchargez le reçu


FICHIER CNAMGS : CNAMGS_*.csv
──────────────────────────────
1. Connectez-vous sur le portail CNAMGS
2. Allez dans : Déclarations → Déclaration trimestrielle
3. Importez le fichier CNAMGS_*.csv
4. Vérifiez et validez


MONTANTS À VERSER (rappel) :
────────────────────────────
CNSS  : cotisations salariales (5%) + patronales (18%) = 23% de la base
CNAMGS: cotisations salariales (1,5%) + patronales (6%) = 7,5% de la base

Date limite de dépôt : dernier jour du mois suivant la fin du trimestre
  T1 (Jan-Mar) → 30 Avril
  T2 (Avr-Jun) → 31 Juillet
  T3 (Jul-Sep) → 31 Octobre
  T4 (Oct-Déc) → 31 Janvier


IMPORTANT :
──────────
- Utilisez le fichier CSV, pas le fichier Excel, pour l'upload portail
- Le fichier Excel (généré séparément) est pour vos archives papier
- Gardez le reçu de dépôt comme justificatif

En cas de problème : support@paiegalon.com
""".strip()


