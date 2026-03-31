"""add locg_url to issues

Revision ID: b3c9f1e72a45
Revises: 8669664a16f0
Create Date: 2026-03-29 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3c9f1e72a45'
down_revision: Union[str, None] = '8669664a16f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('issues', sa.Column('locg_url', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('issues', 'locg_url')
