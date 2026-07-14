"""add message metadata_json

Revision ID: 20260713_1218
Revises: ba7e52c55a34
Create Date: 2026-07-13 12:18:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260713_1218"
down_revision: Union[str, None] = "ba7e52c55a34"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "session_messages",
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("session_messages", "metadata_json")
