"""Make case insensitive hash of query text

Revision ID: 1038c2174f5d
Revises: fd4fc850d7ea
Create Date: 2023-07-16 23:10:12.885949

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import table

from redash.utils import gen_query_hash

# revision identifiers, used by Alembic.
revision = '1038c2174f5d'
down_revision = 'fd4fc850d7ea'
branch_labels = None
depends_on = None



def change_query_hash(conn, table, query_text_to):
    for record in conn.execute(table.select()):
        query_text = query_text_to(record.query)
        conn.execute(
            table
            .update()
            .where(table.c.id == record.id)
            .values(query_hash=gen_query_hash(query_text, {}, False)))


def upgrade():
    queries = table(
        'queries',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('query', sa.Text),
        sa.Column('query_hash', sa.String(length=10)))

    conn = op.get_bind()
    change_query_hash(conn, queries, query_text_to=str)


def downgrade():
    queries = table(
        'queries',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('query', sa.Text),
        sa.Column('query_hash', sa.String(length=10)))

    conn = op.get_bind()
    change_query_hash(conn, queries, query_text_to=str.lower)
