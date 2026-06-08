"""
backup.py — Sauvegarde automatique de la base PostgreSQL vers Backblaze B2

Fonctionnement :
    1. pg_dump exporte la base PostgreSQL dans un fichier .sql
    2. Le fichier est compressé en .sql.gz
    3. Il est envoyé sur Backblaze B2 (API compatible S3, via boto3)
    4. Les sauvegardes plus anciennes que BACKUP_RETENTION_DAYS sont supprimées

Déclenchement :
    - Manuellement   : python backup.py
    - Programmé      : via un Railway Cron Job (voir DEPLOIEMENT_BACKUP.md)
    - Depuis l'app   : route POST /admin/backup (super-admin)

Variables d'environnement requises :
    DATABASE_URL              URL PostgreSQL (fournie par Railway)
    B2_KEY_ID                 Identifiant de clé applicative Backblaze
    B2_APP_KEY                Clé applicative Backblaze
    B2_BUCKET                 Nom du bucket B2
    B2_ENDPOINT               Endpoint S3 du bucket (ex: s3.us-west-004.backblazeb2.com)
Variables optionnelles :
    BACKUP_RETENTION_DAYS     Jours de conservation (défaut: 30)
    BACKUP_PREFIX             Préfixe des fichiers dans le bucket (défaut: paiegalon)
"""
import os
import sys
import gzip
import shutil
import logging
import subprocess
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("paiegalon.backup")


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════
def _config():
    """Lit la configuration depuis l'environnement. Lève une erreur si incomplet."""
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    cfg = {
        "db_url":      db_url,
        "b2_key_id":   os.environ.get("B2_KEY_ID", ""),
        "b2_app_key":  os.environ.get("B2_APP_KEY", ""),
        "b2_bucket":   os.environ.get("B2_BUCKET", ""),
        "b2_endpoint": os.environ.get("B2_ENDPOINT", ""),
        "retention":   int(os.environ.get("BACKUP_RETENTION_DAYS", "30")),
        "prefix":      os.environ.get("BACKUP_PREFIX", "paiegalon"),
    }
    return cfg


def _check_config(cfg):
    """Vérifie que toute la config nécessaire est présente."""
    manquants = []
    if not cfg["db_url"] or not cfg["db_url"].startswith("postgresql://"):
        manquants.append("DATABASE_URL (PostgreSQL)")
    for key, env in [("b2_key_id", "B2_KEY_ID"), ("b2_app_key", "B2_APP_KEY"),
                     ("b2_bucket", "B2_BUCKET"), ("b2_endpoint", "B2_ENDPOINT")]:
        if not cfg[key]:
            manquants.append(env)
    return manquants


# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 1 — DUMP POSTGRESQL
# ══════════════════════════════════════════════════════════════════════════════
def dump_database(db_url, output_path):
    """Exporte la base PostgreSQL avec pg_dump."""
    # pg_dump lit l'URL directement via --dbname
    cmd = [
        "pg_dump",
        "--dbname", db_url,
        "--no-owner",          # portable entre serveurs
        "--no-acl",            # pas de permissions spécifiques
        "--clean",             # inclut les DROP avant CREATE (restauration propre)
        "--if-exists",
        "--file", output_path,
    ]
    logger.info("Démarrage de pg_dump…")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"pg_dump a échoué : {result.stderr[:500]}")
    size = os.path.getsize(output_path)
    logger.info(f"Dump créé : {output_path} ({size // 1024} Ko)")
    return output_path


def compress_file(input_path, output_path):
    """Compresse un fichier en gzip."""
    with open(input_path, "rb") as f_in, gzip.open(output_path, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    size = os.path.getsize(output_path)
    logger.info(f"Compressé : {output_path} ({size // 1024} Ko)")
    return output_path


# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 2 — UPLOAD VERS BACKBLAZE B2
# ══════════════════════════════════════════════════════════════════════════════
def _b2_client(cfg):
    """Crée un client S3 (boto3) configuré pour Backblaze B2."""
    import boto3
    endpoint = cfg["b2_endpoint"]
    if not endpoint.startswith("http"):
        endpoint = f"https://{endpoint}"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=cfg["b2_key_id"],
        aws_secret_access_key=cfg["b2_app_key"],
    )


def upload_to_b2(cfg, local_path, remote_name):
    """Envoie un fichier sur B2."""
    client = _b2_client(cfg)
    key = f"{cfg['prefix']}/{remote_name}"
    logger.info(f"Envoi vers B2 : {cfg['b2_bucket']}/{key}…")
    client.upload_file(local_path, cfg["b2_bucket"], key)
    logger.info("Envoi réussi.")
    return key


# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 3 — ROTATION (suppression des vieilles sauvegardes)
# ══════════════════════════════════════════════════════════════════════════════
def cleanup_old_backups(cfg):
    """Supprime les sauvegardes B2 plus anciennes que la rétention."""
    client = _b2_client(cfg)
    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg["retention"])
    supprimes = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=cfg["b2_bucket"], Prefix=f"{cfg['prefix']}/"):
        for obj in page.get("Contents", []):
            if obj["LastModified"] < cutoff:
                client.delete_object(Bucket=cfg["b2_bucket"], Key=obj["Key"])
                supprimes += 1
                logger.info(f"Supprimé (ancien) : {obj['Key']}")
    if supprimes:
        logger.info(f"Rotation : {supprimes} ancienne(s) sauvegarde(s) supprimée(s).")
    return supprimes


# ══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATION
# ══════════════════════════════════════════════════════════════════════════════
def run_backup():
    """Exécute une sauvegarde complète. Retourne (succès: bool, message: str)."""
    cfg = _config()
    manquants = _check_config(cfg)
    if manquants:
        msg = f"Configuration incomplète. Variables manquantes : {', '.join(manquants)}"
        logger.error(msg)
        return False, msg

    horodatage = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    tmp_dir   = "/tmp"
    sql_path  = os.path.join(tmp_dir, f"backup_{horodatage}.sql")
    gz_path   = sql_path + ".gz"
    remote    = f"backup_{horodatage}.sql.gz"

    try:
        dump_database(cfg["db_url"], sql_path)
        compress_file(sql_path, gz_path)
        key = upload_to_b2(cfg, gz_path, remote)
        cleanup_old_backups(cfg)
        return True, f"Sauvegarde réussie : {key}"
    except FileNotFoundError:
        msg = "pg_dump introuvable. Installez postgresql-client sur l'environnement."
        logger.error(msg)
        return False, msg
    except Exception as e:
        logger.error(f"Échec de la sauvegarde : {e}")
        return False, str(e)
    finally:
        for p in (sql_path, gz_path):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


def list_backups():
    """Liste les sauvegardes disponibles sur B2. Retourne une liste de dicts."""
    cfg = _config()
    if _check_config(cfg):
        return []
    client = _b2_client(cfg)
    backups = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=cfg["b2_bucket"], Prefix=f"{cfg['prefix']}/"):
        for obj in page.get("Contents", []):
            backups.append({
                "nom":    obj["Key"].split("/")[-1],
                "taille": obj["Size"],
                "date":   obj["LastModified"],
            })
    backups.sort(key=lambda b: b["date"], reverse=True)
    return backups


# ══════════════════════════════════════════════════════════════════════════════
# EXÉCUTION EN LIGNE DE COMMANDE
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    ok, message = run_backup()
    print(message)
    sys.exit(0 if ok else 1)
