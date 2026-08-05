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
    "numero":    "074 58 47 72",   # Votre numéro Airtel Money
    "titulaire": "AFFOGNON CEDRIC KEVIN A.",      # Nom du titulaire du compte
}

# ── Virement bancaire (RIB) ─────────────────────────────────────────────────
BANQUE = {
    "nom":       "ORABANK GABON",     # Nom de la banque
    "titulaire": "AFFOGNON CEDRIC KEVIN A.",  # Titulaire du compte
    "iban":      "GA2140021030012592290050185 / 85",  # IBAN / RIB
    "bic":       "ORBKGALI",         # Code BIC / SWIFT
}

# ── Contact pour les questions de paiement ──────────────────────────────────
CONTACT_PAIEMENT = {
    "whatsapp": "24174584772",                # Numéro WhatsApp (format international sans +)
    "email":    "infospaiegabon@paiegabon.com",
}
