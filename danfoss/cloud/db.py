import os
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pool = None

def pool():
    """prepare_threshold=None: Supabase's pooler (transaction mode) does not support prepared statements."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(os.environ["DATABASE_URL"], min_size=1, max_size=4, open=True,
                               kwargs={"row_factory": dict_row, "prepare_threshold": None})
    return _pool

def init_schema():
    with pool().connection() as c:
        c.execute(open(os.path.join(os.path.dirname(__file__), "schema.sql")).read())
