"""lignes facture : quantité prévue (quantite_totale) pour le % réalisation/ligne

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-23 11:00:00.000000

Ajoute `quantite_totale` (quantité prévue au marché) sur les lignes de facture,
permettant de calculer automatiquement le pourcentage de réalisation par ligne
(= quantité réalisée / quantité prévue).
"""
from alembic import op
import sqlalchemy as sa

revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('lignes_facture_prestataire', schema=None) as b:
        b.add_column(sa.Column('quantite_totale', sa.Numeric(12, 2), nullable=True))


def downgrade():
    with op.batch_alter_table('lignes_facture_prestataire', schema=None) as b:
        b.drop_column('quantite_totale')
