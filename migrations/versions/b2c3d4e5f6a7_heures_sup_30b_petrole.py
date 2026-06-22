"""ajout case heures_sup_30b (Convention Pétrole — repos/férié de jour, +30%)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-22 09:00:00.000000

Ajoute la 5ᵉ case d'heures supplémentaires `heures_sup_30b` introduite par la
Convention Collective des professionnels du pétrole (SGEPP/GPP, Art. 38.2) pour
les heures de JOUR effectuées un jour de repos, un dimanche ou un jour férié
(+30 %). Cette case reste nulle pour les conventions BTP/Commerce/AUCUNE, qui
conservent strictement leur comportement antérieur.

Colonnes ajoutées :
  • pointages.heures_sup_30b        (heures saisies au pointage)
  • bulletins_paie.heures_sup_30b   (montant porté au bulletin)
  • bulletins_paie.base_heures_sup_30b / taux_heures_sup_30b (détail d'affichage)

Toutes par défaut à 0 → aucun impact sur les paies existantes.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('pointages', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('heures_sup_30b', sa.Numeric(precision=5, scale=2),
                      nullable=True, server_default='0')
        )
    with op.batch_alter_table('bulletins_paie', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('heures_sup_30b', sa.Numeric(precision=15, scale=2),
                      nullable=True, server_default='0')
        )
        batch_op.add_column(
            sa.Column('base_heures_sup_30b', sa.Numeric(precision=15, scale=2),
                      nullable=True, server_default='0')
        )
        batch_op.add_column(
            sa.Column('taux_heures_sup_30b', sa.String(length=20),
                      nullable=True, server_default='')
        )
    # Renseigne explicitement les lignes existantes (au cas où le défaut serveur
    # ne s'appliquerait pas rétroactivement selon le SGBD).
    op.execute("UPDATE pointages SET heures_sup_30b = 0 WHERE heures_sup_30b IS NULL")
    op.execute("UPDATE bulletins_paie SET heures_sup_30b = 0 WHERE heures_sup_30b IS NULL")
    op.execute("UPDATE bulletins_paie SET base_heures_sup_30b = 0 WHERE base_heures_sup_30b IS NULL")
    op.execute("UPDATE bulletins_paie SET taux_heures_sup_30b = '' WHERE taux_heures_sup_30b IS NULL")


def downgrade():
    with op.batch_alter_table('bulletins_paie', schema=None) as batch_op:
        batch_op.drop_column('taux_heures_sup_30b')
        batch_op.drop_column('base_heures_sup_30b')
        batch_op.drop_column('heures_sup_30b')
    with op.batch_alter_table('pointages', schema=None) as batch_op:
        batch_op.drop_column('heures_sup_30b')
