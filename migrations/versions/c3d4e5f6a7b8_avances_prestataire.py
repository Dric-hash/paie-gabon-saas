"""ajout table avances_prestataire (avances versées aux prestataires/sous-traitants)

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-22 21:30:00.000000

Crée la table `avances_prestataire` : sommes versées à un prestataire ou
sous-traitant hors facture (avances de démarrage, acomptes de chantier…),
régularisables ensuite lors de la facturation.

Note : en production, le schéma est aussi matérialisé par `db.create_all()` au
démarrage (qui crée les tables manquantes). Cette migration assure la traçabilité
versionnée du schéma.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'avances_prestataire',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('prestataire_id', sa.Integer(), nullable=False),
        sa.Column('contrat_id', sa.Integer(), nullable=True),
        sa.Column('montant', sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column('montant_regularise', sa.Numeric(precision=15, scale=2), server_default='0', nullable=True),
        sa.Column('date_avance', sa.Date(), nullable=False),
        sa.Column('mode_paiement', sa.String(length=30), server_default='VIREMENT', nullable=True),
        sa.Column('reference', sa.String(length=100), nullable=True),
        sa.Column('motif', sa.String(length=300), nullable=True),
        sa.Column('statut', sa.String(length=20), server_default='EN_COURS', nullable=True),
        sa.Column('date_creation', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.ForeignKeyConstraint(['prestataire_id'], ['prestataires.id']),
        sa.ForeignKeyConstraint(['contrat_id'], ['contrats_prestation.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_avances_prest_tenant', 'avances_prestataire',
                    ['tenant_id', 'prestataire_id'], unique=False)
    op.create_index('idx_avances_prest_statut', 'avances_prestataire',
                    ['tenant_id', 'statut'], unique=False)


def downgrade():
    op.drop_index('idx_avances_prest_statut', table_name='avances_prestataire')
    op.drop_index('idx_avances_prest_tenant', table_name='avances_prestataire')
    op.drop_table('avances_prestataire')
