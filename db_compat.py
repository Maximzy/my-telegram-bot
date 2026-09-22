"""
Database compatibility layer.
Supports both SQLite (local dev) and PostgreSQL (Railway production).
Auto-detects via DATABASE_URL environment variable.
"""
import os
import re
import logging
import threading

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)

# Maps table name -> primary key column for INSERT OR REPLACE translation
_INSERT_OR_REPLACE_PKS = {
    'settings': 'key',
    'price_overrides': 'pack_name',
    'points_price_overrides': 'item_id',
    'banned_users': 'user_id',
    'promo_codes': 'code',
}

# Maps table name -> PK column for INSERT OR IGNORE -> ON CONFLICT DO NOTHING.
# None means composite / unknown PK — fall back to bare "ON CONFLICT DO NOTHING".
_INSERT_OR_IGNORE_PKS = {
    'settings': 'key',
    'price_overrides': 'pack_name',
    'points_price_overrides': 'item_id',
    'banned_users': 'user_id',
    'promo_codes': 'code',
    'used_promo_codes': None,
    'user_achievements': None,
    'user_points': 'user_id',
    'user_profile': 'user_id',
    'wheel_data': 'user_id',
    'admins': 'id',
    'hidden_points_items': 'item_id',
    'custom_points_items': 'id',
    'referrals': 'referred_id',
    'fake_pay_log': 'user_id',
}


def _adapt_sql(sql):
    """
    Translate SQLite SQL to PostgreSQL-compatible SQL.
    Only called when USE_POSTGRES is True.
    Returns None for PRAGMA statements (should be skipped).
    """
    s = sql.strip()

    # Skip PRAGMA entirely
    if s.upper().startswith("PRAGMA"):
        return None

    # Escape existing % first (so LIKE 'discount%' becomes 'discount%%'),
    # THEN replace ? with %s. Order matters — must be these two lines together.
    sql = sql.replace("%", "%%")
    sql = sql.replace("?", "%s")

    # `user` is a reserved word in PostgreSQL. Quote it wherever it appears as a
    # standalone column name (not part of user_id / username / current_user etc).
    sql = re.sub(r'(?<!["\w])user(?!["\w])', '"user"', sql, flags=re.IGNORECASE)

    # `amount` is TEXT in orders. COALESCE(amount, 0) in PG fails
    # ("COALESCE types text and integer cannot be matched").
    # Rewrite to a safe cast that tolerates empty strings too.
    sql = re.sub(
        r'COALESCE\s*\(\s*amount\s*,\s*0\s*\)',
        "COALESCE(NULLIF(amount, '')::int, 0)",
        sql, flags=re.IGNORECASE
    )

    # ── INSERT OR IGNORE -> INSERT ... ON CONFLICT DO NOTHING ──
    m = re.match(r'INSERT\s+OR\s+IGNORE\s+INTO\s+["\']?(\w+)["\']?', sql, re.IGNORECASE)
    if m:
        table = m.group(1)
        pk = _INSERT_OR_IGNORE_PKS.get(table, "__unknown__")
        sql = re.sub(
            r'INSERT\s+OR\s+IGNORE\s+INTO',
            'INSERT INTO',
            sql, count=1, flags=re.IGNORECASE
        )
        sql = sql.rstrip().rstrip(";")
        if pk == "__unknown__" or pk is None:
            sql = sql + " ON CONFLICT DO NOTHING"
        else:
            sql = sql + f" ON CONFLICT ({pk}) DO NOTHING"
        return sql

    # ── INSERT OR REPLACE -> INSERT ... ON CONFLICT ... DO UPDATE ──
    m = re.match(r'INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES', sql, re.IGNORECASE)
    if m:
        table = m.group(1)
        cols = [c.strip() for c in m.group(2).split(',')]
        pk = _INSERT_OR_REPLACE_PKS.get(table)
        if pk and pk in cols:
            non_pk = [c for c in cols if c != pk]
            if non_pk:
                set_clause = ', '.join(f'{c}=EXCLUDED.{c}' for c in non_pk)
                sql = re.sub(r'INSERT\s+OR\s+REPLACE\s+INTO', 'INSERT INTO', sql, count=1, flags=re.IGNORECASE)
                sql = sql.rstrip() + f' ON CONFLICT ({pk}) DO UPDATE SET {set_clause}'
            else:
                sql = re.sub(r'INSERT\s+OR\s+REPLACE\s+INTO', 'INSERT INTO', sql, count=1, flags=re.IGNORECASE)
                sql = sql.rstrip() + f' ON CONFLICT ({pk}) DO NOTHING'
        else:
            sql = re.sub(r'INSERT\s+OR\s+REPLACE\s+INTO', 'INSERT INTO', sql, count=1, flags=re.IGNORECASE)
            sql = sql.rstrip() + ' ON CONFLICT DO NOTHING'

    # AUTOINCREMENT -> SERIAL (in CREATE TABLE)
    sql = re.sub(
        r'INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT',
        'SERIAL PRIMARY KEY',
        sql, flags=re.IGNORECASE
    )

    # rowid -> ctid (PG system column, gives insertion order approximately)
    sql = re.sub(r'\browid\b', 'ctid', sql, flags=re.IGNORECASE)

    return sql

_HAS_ID_COLUMN_CACHE = {}


class PgCursorWrapper:
    """Wraps psycopg2 cursor to provide SQLite-compatible interface."""

    def __init__(self, real_cursor):
        self._cur = real_cursor
        self.lastrowid = None

    def _has_id_column(self, table):
        if table in _HAS_ID_COLUMN_CACHE:
            return _HAS_ID_COLUMN_CACHE[table]
        try:
            self._cur.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name=%s AND column_name='id' LIMIT 1",
                (table,)
            )
            has = self._cur.fetchone() is not None
        except Exception:
            has = False
        _HAS_ID_COLUMN_CACHE[table] = has
        return has

    def execute(self, sql, params=()):
        s_raw = sql.strip()

        # ── PRAGMA emulation for PG ──
        if s_raw.upper().startswith("PRAGMA"):
            m = re.match(
                r'PRAGMA\s+table_info\s*\(\s*["\']?(\w+)["\']?\s*\)',
                s_raw, re.IGNORECASE
            )
            if m:
                tbl = m.group(1)
                try:
                    self._cur.execute(
                        """
                        SELECT
                            (c.ordinal_position - 1)::int AS cid,
                            c.column_name AS name,
                            c.data_type AS type,
                            CASE WHEN c.is_nullable = 'NO' THEN 1 ELSE 0 END AS notnull,
                            c.column_default AS dflt_value,
                            CASE WHEN pk.column_name IS NOT NULL THEN 1 ELSE 0 END AS pk
                        FROM information_schema.columns c
                        LEFT JOIN (
                            SELECT kcu.column_name
                            FROM information_schema.table_constraints tc
                            JOIN information_schema.key_column_usage kcu
                              ON tc.constraint_name = kcu.constraint_name
                             AND tc.table_schema = kcu.table_schema
                            WHERE tc.table_name = %s
                              AND tc.constraint_type = 'PRIMARY KEY'
                        ) pk ON pk.column_name = c.column_name
                        WHERE c.table_name = %s
                        ORDER BY c.ordinal_position
                        """,
                        (tbl, tbl)
                    )
                except Exception:
                    self._cur.execute("SELECT 1 WHERE FALSE")
                return self._cur
            self._cur.execute("SELECT 1 WHERE FALSE")
            return self._cur

        adapted = _adapt_sql(sql)
        if adapted is None:
            self._cur.execute("SELECT 1 WHERE FALSE")
            return self._cur

        # For INSERTs without RETURNING, add RETURNING id only if table has id column
        a_upper = adapted.strip().upper()
        if a_upper.startswith("INSERT") and "RETURNING" not in a_upper:
            m = re.match(r'INSERT\s+INTO\s+["\']?(\w+)', adapted, re.IGNORECASE)
            if m and self._has_id_column(m.group(1)):
                adapted = adapted.rstrip(";").rstrip() + " RETURNING id"

        self._cur.execute(adapted, params)

        if "RETURNING ID" in adapted.upper():
            try:
                row = self._cur.fetchone()
                if row:
                    self.lastrowid = row[0]
            except Exception:
                pass

        return self._cur

    def fetchall(self):
        return self._cur.fetchall()

    def fetchone(self):
        return self._cur.fetchone()

    @property
    def rowcount(self):
        return self._cur.rowcount

    def __getattr__(self, name):
        return getattr(self._cur, name)

    def close(self):
        return self._cur.close()


class PgConnectionWrapper:
    """Wraps psycopg2 connection to provide SQLite-compatible interface."""

    def __init__(self, pg_conn):
        self._conn = pg_conn

    def cursor(self):
        return PgCursorWrapper(self._conn.cursor())

    def execute(self, sql, params=()):
        adapted = _adapt_sql(sql)
        if adapted is None:
            return self._conn.cursor()
        cur = self._conn.cursor()
        cur.execute(adapted, params)
        return cur

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def get_connection():
    """
    Create database connection based on environment.
    Returns (connection, db_type) where db_type is 'sqlite' or 'postgres'.
    """
    if USE_POSTGRES:
        import psycopg2
        logging.info("db_compat: using PostgreSQL backend")
        pg_conn = psycopg2.connect(DATABASE_URL)
        pg_conn.autocommit = False
        wrapper = PgConnectionWrapper(pg_conn)
        return wrapper, 'postgres'
    else:
        import sqlite3
        _data_dir = os.environ.get("DATA_DIR", "")
        if _data_dir:
            os.makedirs(_data_dir, exist_ok=True)
            DB_PATH = os.path.join(_data_dir, "bot.db")
        else:
            DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.db")
        logging.info(f"db_compat: using SQLite backend ({DB_PATH})")
        sqlite_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        sqlite_conn.execute("PRAGMA journal_mode=WAL")
        sqlite_conn.execute("PRAGMA synchronous=FULL")
        sqlite_conn.commit()
        return sqlite_conn, 'sqlite'


def get_table_columns(connection, table_name):
    """
    Get list of column names for a table.
    Works with both SQLite (PRAGMA) and PostgreSQL (information_schema).
    """
    if USE_POSTGRES:
        cur = connection.cursor()
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s ORDER BY ordinal_position",
            (table_name,)
        )
        return [r[0] for r in cur.fetchall()]
    else:
        cur = connection.cursor()
        cur.execute(f"PRAGMA table_info({table_name})")
        return [r[1] for r in cur.fetchall()]


def table_exists(connection, table_name):
    """Check if a table exists."""
    if USE_POSTGRES:
        cur = connection.cursor()
        cur.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)",
            (table_name,)
        )
        return cur.fetchone()[0]
    else:
        cur = connection.cursor()
        cur.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
        return cur.fetchone() is not None
