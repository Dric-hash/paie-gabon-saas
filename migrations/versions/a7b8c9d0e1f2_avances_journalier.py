"""journaliers : avances (table avances_journalier) + colonne avance_deduite

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-06-23 12:00:00.000000

Ajoute la table des avances des journaliers et la colonne `avance_deduite` sur
les feuilles de paie journalier, pour déduire les avances de la paie de période.
"""
from alembic import op
import sqlalchemy as sa

revision = 'a7b8c9d0e1f2'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'avances_journalier',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('journalier_id', sa.Integer(), nullable=False),
        sa.Column('site_id', sa.Integer(), nullable=True),
        sa.Column('montant', sa.Numeric(15, 2), nullable=False),
        sa.Column('montant_regularise', sa.Numeric(15, 2), nullable=True),
        sa.Column('date_avance', sa.Date(), nullable=False),
        sa.Column('mode_paiement', sa.String(length=30), nullable=True),
        sa.Column('reference', sa.String(length=80), nullable=True),
        sa.Column('motif', sa.String(length=200), nullable=True),
        sa.Column('date_creation', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.ForeignKeyConstraint(['journalier_id'], ['journaliers.id']),
        sa.ForeignKeyConstraint(['site_id'], ['sites.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_avances_journalier', 'avances_journalier',
                    ['tenant_id', 'journalier_id'], unique=False)
    with op.batch_alter_table('feuilles_paie_journalier', schema=None) as b:
        b.add_column(sa.Column('avance_deduite', sa.Numeric(15, 2), nullable=True))


def downgrade():
    with op.batch_alter_table('feuilles_paie_journalier', schema=None) as b:
        b.drop_column('avance_deduite')
    op.drop_index('idx_avances_journalier', table_name='avances_journalier')
    op.drop_table('avances_journalier')
