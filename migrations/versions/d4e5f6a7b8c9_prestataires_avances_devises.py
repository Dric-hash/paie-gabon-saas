"""prestataires : avances (validation, site, devises), factures (site, m², devises), taux

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-22 22:15:00.000000

- avances_prestataire : site_id, devise, taux_change, montant_xaf, workflow de
  validation (valide_par_nom, valide_par_user_id, date_validation).
- factures_prestataire : site_id, surface_m2, prix_unitaire_m2,
  pourcentage_realisation, devise, taux_change, montant_xaf.
- paiements_prestataire : pourcentage_realisation.
- nouvelle table taux_devises (cache quotidien des taux de change).

En production, ces colonnes sont aussi posées par run_migrations() (ALTER ...
IF NOT EXISTS) et la table par create_all(). Migration fournie pour la traçabilité.
"""
from alembic import op
import sqlalchemy as sa

revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('avances_prestataire', schema=None) as b:
        b.add_column(sa.Column('site_id', sa.Integer(), nullable=True))
        b.add_column(sa.Column('devise', sa.String(length=5), server_default='XAF', nullable=True))
        b.add_column(sa.Column('taux_change', sa.Numeric(14, 6), server_default='1', nullable=True))
        b.add_column(sa.Column('montant_xaf', sa.Numeric(15, 2), server_default='0', nullable=True))
        b.add_column(sa.Column('valide_par_nom', sa.String(length=150), nullable=True))
        b.add_column(sa.Column('valide_par_user_id', sa.Integer(), nullable=True))
        b.add_column(sa.Column('date_validation', sa.DateTime(), nullable=True))
    op.execute("UPDATE avances_prestataire SET statut='EN_ATTENTE' WHERE statut='EN_COURS'")

    with op.batch_alter_table('factures_prestataire', schema=None) as b:
        b.add_column(sa.Column('site_id', sa.Integer(), nullable=True))
        b.add_column(sa.Column('surface_m2', sa.Numeric(12, 2), nullable=True))
        b.add_column(sa.Column('prix_unitaire_m2', sa.Numeric(15, 2), nullable=True))
        b.add_column(sa.Column('pourcentage_realisation', sa.Numeric(5, 2), server_default='0', nullable=True))
        b.add_column(sa.Column('devise', sa.String(length=5), server_default='XAF', nullable=True))
        b.add_column(sa.Column('taux_change', sa.Numeric(14, 6), server_default='1', nullable=True))
        b.add_column(sa.Column('montant_xaf', sa.Numeric(15, 2), server_default='0', nullable=True))

    with op.batch_alter_table('paiements_prestataire', schema=None) as b:
        b.add_column(sa.Column('pourcentage_realisation', sa.Numeric(5, 2), server_default='0', nullable=True))

    op.create_table(
        'taux_devises',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('date_taux', sa.Date(), nullable=False),
        sa.Column('devise', sa.String(length=5), nullable=False),
        sa.Column('taux_xaf', sa.Numeric(14, 6), nullable=False),
        sa.Column('source', sa.String(length=30), server_default='API', nullable=True),
        sa.Column('date_creation', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('date_taux', 'devise'),
    )


def downgrade():
    op.drop_table('taux_devises')
    with op.batch_alter_table('paiements_prestataire', schema=None) as b:
        b.drop_column('pourcentage_realisation')
    with op.batch_alter_table('factures_prestataire', schema=None) as b:
        for c in ('montant_xaf', 'taux_change', 'devise', 'pourcentage_realisation',
                  'prix_unitaire_m2', 'surface_m2', 'site_id'):
            b.drop_column(c)
    with op.batch_alter_table('avances_prestataire', schema=None) as b:
        for c in ('date_validation', 'valide_par_user_id', 'valide_par_nom',
                  'montant_xaf', 'taux_change', 'devise', 'site_id'):
            b.drop_column(c)
