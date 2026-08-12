-- Migration : suivi de l'alerte « expiration dans 72h »
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS alerte_expiration_envoyee TIMESTAMP;
