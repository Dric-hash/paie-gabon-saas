"""factures prestataire : validation + table lignes_facture_prestataire

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-23 09:00:00.000000

- factures_prestataire : valide_par_nom, valide_par_user_id, date_validation ;
  les factures existantes en EN_ATTENTE passent en VALIDEE (restent payables).
- nouvelle table lignes_facture_prestataire (lignes de détail des factures).

En production : colonnes posées par run_migrations() (ALTER ... IF NOT EXISTS),
table créée par create_all(). Migration fournie pour la traçabilité.
"""
from alembic import op
import sqlalchemy as sa

revision = 'e5f6a7b8c9d0'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('factures_prestataire', schema=None) as b:
        b.add_column(sa.Column('valide_par_nom', sa.String(length=150), nullable=True))
        b.add_column(sa.Column('valide_par_user_id', sa.Integer(), nullable=True))
        b.add_column(sa.Column('date_validation', sa.DateTime(), nullable=True))
    op.execute("UPDATE factures_prestataire SET statut='VALIDEE' WHERE statut='EN_ATTENTE'")

    op.create_table(
        'lignes_facture_prestataire',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('facture_id', sa.Integer(), nullable=False),
        sa.Column('designation', sa.String(length=300), nullable=False),
        sa.Column('quantite', sa.Numeric(12, 2), server_default='1', nullable=True),
        sa.Column('unite', sa.String(length=20), server_default='u', nullable=True),
        sa.Column('prix_unitaire', sa.Numeric(15, 2), server_default='0', nullable=True),
        sa.Column('montant', sa.Numeric(15, 2), server_default='0', nullable=True),
        sa.Column('ordre', sa.Integer(), server_default='0', nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.ForeignKeyConstraint(['facture_id'], ['factures_prestataire.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_lignes_facture', 'lignes_facture_prestataire',
                    ['tenant_id', 'facture_id'], unique=False)


def downgrade():
    op.drop_index('idx_lignes_facture', table_name='lignes_facture_prestataire')
    op.drop_table('lignes_facture_prestataire')
    with op.batch_alter_table('factures_prestataire', schema=None) as b:
        b.drop_column('date_validation')
        b.drop_column('valide_par_user_id')
        b.drop_column('valide_par_nom')
