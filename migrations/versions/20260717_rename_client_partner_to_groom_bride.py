"""rename_client_partner_to_groom_bride

Rename sessions.client_name → groom_name and sessions.partner_name → bride_name.

Revision ID: f3a91c7d2e05
Revises: add_message_metadata
Create Date: 2026-07-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f3a91c7d2e05'
# Point at the last applied migration
down_revision: Union[str, None] = '20260713_1218'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('sessions', 'client_name',
                    new_column_name='groom_name',
                    existing_type=sa.Text(),
                    existing_nullable=True)
    op.alter_column('sessions', 'partner_name',
                    new_column_name='bride_name',
                    existing_type=sa.Text(),
                    existing_nullable=True)


def downgrade() -> None:
    op.alter_column('sessions', 'groom_name',
                    new_column_name='client_name',
                    existing_type=sa.Text(),
                    existing_nullable=True)
    op.alter_column('sessions', 'bride_name',
                    new_column_name='partner_name',
                    existing_type=sa.Text(),
                    existing_nullable=True)
