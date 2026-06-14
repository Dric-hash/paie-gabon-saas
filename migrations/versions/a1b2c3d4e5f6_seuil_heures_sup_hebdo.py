"""ajout seuil_heures_sup_hebdo (dérogation heures supplémentaires)

Revision ID: a1b2c3d4e5f6
Revises: 8470b4956f15
Create Date: 2026-06-14 12:30:00.000000

Ajoute la colonne tenants.seuil_heures_sup_hebdo permettant à chaque entreprise
de définir le seuil hebdomadaire de déclenchement des heures supplémentaires.
La loi fixe ce seuil à 40h/semaine ; une dérogation peut le porter jusqu'à 48h.
Défaut = 40.0 (comportement légal, identique à l'existant).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '8470b4956f15'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('tenants', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('seuil_heures_sup_hebdo', sa.Numeric(precision=4, scale=1),
                      nullable=True, server_default='40.0')
        )
    # Renseigne explicitement les lignes existantes (au cas où le défaut serveur
    # ne s'appliquerait pas rétroactivement selon le SGBD).
    op.execute("UPDATE tenants SET seuil_heures_sup_hebdo = 40.0 "
               "WHERE seuil_heures_sup_hebdo IS NULL")


def downgrade():
    with op.batch_alter_table('tenants', schema=None) as batch_op:
        batch_op.drop_column('seuil_heures_sup_hebdo')
