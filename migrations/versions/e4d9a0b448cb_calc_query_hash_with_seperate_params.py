"""calc query_hash with seperate params

Revision ID: e4d9a0b448cb
Revises: 7205816877ec
Create Date: 2024-05-20 00:48:25.674748

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import table

from redash.query_runner import get_query_runner
from redash.utils import gen_query_hash

# revision identifiers, used by Alembic.
revision = "e4d9a0b448cb"
down_revision = "7205816877ec"
branch_labels = None
depends_on = None


queries = table(
    "queries",
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("query", sa.Text),
    sa.Column("query_hash", sa.String(length=32)),
    sa.Column("data_source_id", sa.Integer),
    sa.Column("options", sa.JSON))

data_sources = table(
    "data_sources",
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("type", sa.String(length=255)))


def load_data_sources(conn):
    data_source_type_map = {}
    for data_source in conn.execute(data_sources.select()):
        data_source_type_map[data_source.id] = data_source.type
    return data_source_type_map


def upgrade():
    conn = op.get_bind()

    data_source_type_map = load_data_sources(conn)

    for query in conn.execute(queries.select()):
        data_source_type = data_source_type_map.get(query.data_source_id)
        if not data_source_type:
            continue

        query_runner = get_query_runner(data_source_type, {})
        if not query_runner:
            print(f"query #{query.id}: can't get query runner '{data_source_type}'")
            continue

        parameters_dict = {p["name"]: p.get("value") for p in query.options.get("parameters", [])}

        if query_runner.supports_auto_limit:
            should_apply_auto_limit = query.options.get("apply_auto_limit", False)
        else:
            should_apply_auto_limit = False

        new_query_hash = gen_query_hash(query.query, parameters_dict, should_apply_auto_limit)

        conn.execute(
            queries
            .update()
            .where(queries.c.id == query.id)
            .values(query_hash=new_query_hash))


def downgrade():
    # We can't calculate the old query_hash.
    # Because the dynamic date(-range) parameters were lost.
    # This is the root cause of the problem I am trying to fix.
    pass
