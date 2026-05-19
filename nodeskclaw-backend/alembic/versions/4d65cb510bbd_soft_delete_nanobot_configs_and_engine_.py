"""soft_delete_nanobot_configs_and_engine_versions

Revision ID: 4d65cb510bbd
Revises: b9f5520c1ffb
Create Date: 2026-05-20 03:38:42.271600

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4d65cb510bbd'
down_revision: Union[str, Sequence[str], None] = 'b9f5520c1ffb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE system_configs SET deleted_at = now() "
        "WHERE key = 'image_registry_nanobot' AND deleted_at IS NULL"
    )
    op.execute(
        "UPDATE engine_versions SET deleted_at = now() "
        "WHERE runtime = 'nanobot' AND deleted_at IS NULL"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE system_configs SET deleted_at = NULL "
        "WHERE key = 'image_registry_nanobot' AND deleted_at IS NOT NULL"
    )
    op.execute(
        "UPDATE engine_versions SET deleted_at = NULL "
        "WHERE runtime = 'nanobot' AND deleted_at IS NOT NULL"
    )
