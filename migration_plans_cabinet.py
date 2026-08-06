#!/usr/bin/env python3
# migration_plans_cabinet.py
# ═══════════════════════════════════════════════════════════════════════════
#  ÉTAPE 5 — Restructuration des offres pour le mode cabinet.
#
#  1. Renomme le plan existant "Cabinet" (code CABINET) en "Grande Entreprise".
#     → Son code reste CABINET pour ne pas casser l'accès à la DAS des tenants
#       qui l'utilisent déjà.
#  2. Crée un NOUVEAU plan "Cabinet" (code CABINET_COMPTABLE) : le vrai forfait
#     cabinet multi-entreprises, à 100 000 F, entreprises illimitées.
#
#  À lancer UNE fois sur le serveur (idempotent : peut être relancé sans risque).
#     cd /var/www/paiegabon && set -a; . ./.env; set +a; venv/bin/python3 migration_plans_cabinet.py
# ═══════════════════════════════════════════════════════════════════════════
from app import app, db
from models import Plan


def migrer():
    # 1. Renommer l'ancien "Cabinet" (code CABINET) en "Grande Entreprise"
    ancien = Plan.query.filter_by(code="CABINET").first()
    if ancien:
        if ancien.nom != "Grande Entreprise":
            ancien.nom = "Grande Entreprise"
            ancien.description = "Salariés illimités, 10 utilisateurs"
            print(f"✅ Plan 'CABINET' renommé en 'Grande Entreprise' (prix {int(ancien.prix_mensuel)}F inchangé).")
        else:
            print("• Plan 'Grande Entreprise' déjà à jour.")
    else:
        print("⚠️  Aucun plan de code CABINET trouvé (rien à renommer).")

    # 2. Créer le nouveau plan Cabinet (code CABINET_COMPTABLE) s'il n'existe pas
    nouveau = Plan.query.filter_by(code="CABINET_COMPTABLE").first()
    if not nouveau:
        nouveau = Plan(
            code="CABINET_COMPTABLE",
            nom="Cabinet",
            prix_mensuel=100000,
            max_salaries=None,        # illimité
            max_utilisateurs=None,    # illimité (le cabinet gère plusieurs entreprises)
            description="Gérez la paie de plusieurs entreprises. Entreprises illimitées.",
            actif=True,
        )
        db.session.add(nouveau)
        print("✅ Nouveau plan 'Cabinet' (CABINET_COMPTABLE) créé à 100 000 F.")
    else:
        print("• Plan 'Cabinet' (CABINET_COMPTABLE) déjà existant.")

    db.session.commit()
    print("\n=== Plans après migration ===")
    for p in Plan.query.order_by(Plan.prix_mensuel).all():
        print(f"  {p.code}: {p.nom} — {int(p.prix_mensuel)}F")


if __name__ == "__main__":
    with app.app_context():
        migrer()
