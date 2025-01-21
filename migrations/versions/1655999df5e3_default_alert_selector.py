"""set default alert selector

Revision ID: 1655999df5e3
Revises: 9e8c841d1a30
Create Date: 2024-10-18 11:24:49

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = '1655999df5e3'
down_revision = '9e8c841d1a30'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    UPDATE alerts
    SET options = jsonb_set(options, '{selector}', '"first"')
    WHERE options->>'selector' IS NULL;
    """)

def downgrade():
    pass
