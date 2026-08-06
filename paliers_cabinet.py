# paliers_cabinet.py
# ═══════════════════════════════════════════════════════════════════════════
#  PALIERS TARIFAIRES DU PLAN CABINET (multi-entreprises)
#  Modifiez librement cette grille : seuils et prix. C'est le SEUL fichier
#  à changer ; la page des offres et le contrôle de limite s'y adaptent.
#  Après modification : git commit + push, puis git pull sur le serveur.
# ═══════════════════════════════════════════════════════════════════════════
#
#  Chaque palier : nombre MAXIMUM d'entreprises incluses + prix mensuel (F).
#  Les paliers doivent être classés par 'max_entreprises' croissant.
#  Le dernier niveau "sur devis" est géré à part (SUR_DEVIS ci-dessous).
# ═══════════════════════════════════════════════════════════════════════════

PALIERS_CABINET = [
    {"max_entreprises": 10, "prix_mensuel": 100000},
    {"max_entreprises": 15, "prix_mensuel": 150000},
    {"max_entreprises": 20, "prix_mensuel": 200000},
]

# Au-delà du plus grand palier chiffré : sur devis (le client vous contacte).
SUR_DEVIS = {
    "au_dela_de": 20,   # nombre d'entreprises au-delà duquel c'est sur devis
    "message": "Au-delà de 20 entreprises, contactez-nous pour un devis personnalisé.",
}


def palier_pour_limite(limite):
    """Renvoie le palier correspondant à une limite d'entreprises donnée,
    ou None si la limite dépasse tous les paliers chiffrés (→ sur devis)."""
    for p in PALIERS_CABINET:
        if limite <= p["max_entreprises"]:
            return p
    return None


def palier_par_defaut():
    """Le premier palier (celui attribué par défaut à un nouveau cabinet)."""
    return PALIERS_CABINET[0]


def limite_par_defaut():
    """Limite d'entreprises du palier par défaut."""
    return PALIERS_CABINET[0]["max_entreprises"]
