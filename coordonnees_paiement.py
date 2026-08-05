# coordonnees_paiement.py
# ═══════════════════════════════════════════════════════════════════════════
#  COORDONNÉES DE PAIEMENT — À COMPLÉTER PAR CÉDRIC
# ═══════════════════════════════════════════════════════════════════════════
#  Remplacez les valeurs « À COMPLÉTER » par vos vraies coordonnées.
#  C'est le SEUL fichier à modifier : ces informations s'affichent
#  automatiquement sur la page de paiement que voient vos clients.
#  Après modification : git commit + push, puis git pull sur le serveur.
# ═══════════════════════════════════════════════════════════════════════════

# ── Airtel Money ────────────────────────────────────────────────────────────
AIRTEL_MONEY = {
    "numero":    "À COMPLÉTER (ex: 074 58 47 72)",   # Votre numéro Airtel Money
    "titulaire": "À COMPLÉTER (ex: NOM Prénom)",      # Nom du titulaire du compte
}

# ── Virement bancaire (RIB) ─────────────────────────────────────────────────
BANQUE = {
    "nom":       "À COMPLÉTER (ex: BGFI Bank Gabon)",     # Nom de la banque
    "titulaire": "À COMPLÉTER (ex: AMERIACK I.T. SOLUTIONS)",  # Titulaire du compte
    "iban":      "À COMPLÉTER (ex: GA21 XXXX XXXX XXXX XXXX XXXX XXX)",  # IBAN / RIB
    "bic":       "À COMPLÉTER (ex: BGFIGALIXXX)",         # Code BIC / SWIFT
}

# ── Contact pour les questions de paiement ──────────────────────────────────
CONTACT_PAIEMENT = {
    "whatsapp": "24174584772",                # Numéro WhatsApp (format international sans +)
    "email":    "infospaiegabon@paiegabon.com",
}
