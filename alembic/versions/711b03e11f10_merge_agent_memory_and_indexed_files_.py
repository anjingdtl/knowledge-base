"""merge agent_memory and indexed_files branches

Revision ID: 711b03e11f10
Revises: f001_agent_memory, g001
Create Date: 2026-06-22 19:37:18.375988

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '711b03e11f10'
down_revision: Union[str, Sequence[str], None] = ('f001_agent_memory', 'g001')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
