"""db_compat.py — SQLite-style compatibility shim with PostgreSQL backend.

Якщо в env є DATABASE_URL → використовується PostgreSQL через psycopg2.
Інакше — fallback на локальний SQLite (bot.db).

Підтримує синтаксис SQLite-коду:
  • sqlite3.connect(DB_PATH, check_same_thread=False)
  • "?" placeholders
  • INSERT OR REPLACE / INSERT OR IGNORE
  • INTEGER PRIMARY KEY AUTOINCREMENT
  • PRAGMA journal_mode=WAL / synchronous=FULL / PRAGMA table_info(...)
  • cursor.lastrowid після INSERT
  • conn.execute(sql) / conn.cursor().execute(sql, params)
"""

import os
import re
import threading
import logging
from typing import Any, Optional, Tuple, List

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# Backend detection
# ──────────────────────────────────────────────────────────────────────

def _detect_backend() -> str:
    """Return 'postgres' якщо є DATABASE_URL, інакше 'sqlite'."""
    return "postgres" if os.environ.get("DATABASE_URL") else "sqlite"

# ──────────────────────────────────────────────────────────────────────
# SQL translation helpers
# ──────────────────────────────────────────────────────────────────────

_PRAGMA_TABLE_INFO_RE = re.compile(
    r"^\s*PRAGMA\s+table_info\(\s*([\w\"\'\`]+)\s*\)\s*;?\s*$",
    re.IGNORECASE,
)
_PRAGMA_RE = re.compile(r"^\s*PRAGMA\s+", re.IGNORECASE)
_INSERT_OR_REPLACE_RE = re.compile(
    r"^\s*INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_INSERT_OR_IGNORE_RE = re.compile(
    r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\s+(\w+)\s*(\([^)]*\)\s*VALUES\s*\([^)]*\))?\s*$",
    re.IGNORECASE | re.DOTALL,
)


def _replace_question_marks(sql: str) -> str:
    """Конвертуємо '?' у '%s', ігноруючи '?' всередині рядків у лапках."""
    out = []
    in_str = False
    esc = False
    for ch in sql:
        if esc:
            out.append(ch)
            esc = False
            continue
        if ch == "\\" and in_str:
            out.append(ch)
            esc = True
            continue
        if ch == "'":
            in_str = not in_str
            out.append(ch)
            continue
        if ch == "?" and not in_str:
            out.append("%s")
            continue
        out.append(ch)
    return "".join(out)


def _quote_reserved_words(sql: str) -> str:
    """Quotes standalone PostgreSQL reserved words used as column names.

    Currently handles 'user' (reserved in PG) — quotes as "user" when it appears
    as a standalone word outside string literals. Does not touch user_id, user_points, etc.
    Also replaces standalone 'rowid' (SQLite implicit column) with 'ctid' (PG equivalent).
    """
    out = []
    in_str = False
    esc = False
    i = 0
    s = sql
    n = len(s)
    while i < n:
        ch = s[i]
        if esc:
            out.append(ch)
            esc = False
            i += 1
            continue
        if ch == "\\" and in_str:
            out.append(ch)
            esc = True
            i += 1
            continue
        if ch == "'":
            in_str = not in_str
            out.append(ch)
            i += 1
            continue
        if not in_str:
            # Check for standalone 'user' (PG reserved keyword used as column name)
            # Word boundary: prev char is not alnum/underscore/quote, next char is not alnum/underscore/quote
            if s[i:i+4].lower() == "user":
                prev_ok = (i == 0 or not (s[i-1].isalnum() or s[i-1] == "_" or s[i-1] == '"'))
                next_ok = (i + 4 >= n or not (s[i+4].isalnum() or s[i+4] == "_" or s[i+4] == '"'))
                if prev_ok and next_ok:
                    out.append('"user"')
                    i += 4
                    continue
            # Check for standalone 'rowid' (SQLite implicit row ID → PG ctid)
            if s[i:i+5].lower() == "rowid":
                prev_ok = (i == 0 or not (s[i-1].isalnum() or s[i-1] == "_" or s[i-1] == '"'))
                next_ok = (i + 5 >= n or not (s[i+5].isalnum() or s[i+5] == "_" or s[i+5] == '"'))
                if prev_ok and next_ok:
                    out.append("ctid")
                    i += 5
                    continue
        out.append(ch)
        i += 1
    return "".join(out)


def _strip_quotes(name: str) -> str:
    return name.strip().strip('"').strip("`").strip("'")


def _split_cols(cols_str: str) -> List[str]:
    return [_strip_quotes(c) for c in cols_str.split(",")]


def _table_info_sql(table: str) -> str:
    """Емуляція PRAGMA table_info для PostgreSQL через information_schema."""
    t = _strip_quotes(table)
    return (
        "SELECT "
        "(ordinal_position - 1) AS cid, "
        "column_name AS name, "
        "data_type AS type, "
        "(CASE WHEN is_nullable = 'NO' THEN 1 ELSE 0 END) AS notnull, "
        "column_default AS dflt_value, "
        "0 AS pk "
        "FROM information_schema.columns "
        f"WHERE LOWER(table_name) = LOWER('{t}') "
        "ORDER BY ordinal_position"
    )


class _SQLTranslator:
    """Тримає кеш PK таблиць для ON CONFLICT і транслює SQLite-SQL → PG-SQL."""

    def __init__(self):
        self._pk_cache: dict = {}
        self._loaded: bool = False
        self._lock = threading.Lock()

    def load_pk_cache(self, pg_cursor):
        """Завантажує PK усіх таблиць з information_schema."""
        with self._lock:
            if self._loaded:
                return
            try:
                pg_cursor.execute(
                    "SELECT tc.table_name, kcu.column_name "
                    "FROM information_schema.table_constraints tc "
                    "JOIN information_schema.key_column_usage kcu "
                    "  ON tc.constraint_name = kcu.constraint_name "
                    "WHERE tc.constraint_type = 'PRIMARY KEY' "
                    "  AND tc.table_schema = 'public'"
                )
                rows = pg_cursor.fetchall()
            except Exception as e:
                logger.warning(f"db_compat: failed to load PK cache: {e}")
                self._loaded = True
                return
            for r in rows:
                tbl = (r[0] or "").lower()
                col = r[1]
                if not tbl:
                    continue
                if tbl in self._pk_cache:
                    existing = self._pk_cache[tbl]
                    if isinstance(existing, str):
                        self._pk_cache[tbl] = [existing, col]
                    elif col not in existing:
                        existing.append(col)
                else:
                    self._pk_cache[tbl] = col
            self._loaded = True

    def get_pk(self, table: str):
        """Повертає список колонок PK (або None, якщо таблиця без PK)."""
        return self._pk_cache.get(table.lower())

    def ensure_pk_for(self, table: str, pg_cursor) -> None:
        """Lazy-load PK для конкретної таблиці (наприклад, створеної після connect).

        Запитує information_schema для однієї таблиці і кешує результат.
        Нічого не робить, якщо таблиця вже є в кеші.
        """
        tbl = table.lower()
        if tbl in self._pk_cache:
            return
        try:
            pg_cursor.execute(
                "SELECT kcu.column_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON tc.constraint_name = kcu.constraint_name "
                "WHERE tc.constraint_type = 'PRIMARY KEY' "
                "  AND tc.table_schema = 'public' "
                "  AND LOWER(tc.table_name) = %s",
                (tbl,)
            )
            rows = pg_cursor.fetchall()
        except Exception as e:
            logger.warning(f"db_compat: ensure_pk_for({table}) failed: {e}")
            self._pk_cache[tbl] = None  # позначаємо що перевіряли — нема PK
            return
        if not rows:
            self._pk_cache[tbl] = None
            return
        cols = [r[0] for r in rows if r[0]]
        self._pk_cache[tbl] = cols if len(cols) > 1 else cols[0]

    def translate(self, sql: str, pg_cursor=None) -> Optional[str]:
        """Повертає PG-SQL або None якщо запит треба skip'нути (наприклад, PRAGMA).

        pg_cursor: опціональний psycopg2-cursor для lazy-load PK таблиць,
                   які були створені після початкового завантаження кешу.
        """
        s = sql

        # PRAGMA table_info(<table>) → емуляція
        m = _PRAGMA_TABLE_INFO_RE.match(s)
        if m:
            return _table_info_sql(m.group(1))

        # Інші PRAGMA → skip
        if _PRAGMA_RE.match(s):
            return None

        # INSERT OR REPLACE → INSERT ... ON CONFLICT (...) DO UPDATE SET ...
        m = _INSERT_OR_REPLACE_RE.match(s)
        if m:
            table = m.group(1)
            cols_str = m.group(2)
            vals = m.group(3)
            cols = _split_cols(cols_str)
            if pg_cursor is not None:
                self.ensure_pk_for(table, pg_cursor)
            pk = self.get_pk(table)
            if pk:
                pk_list = pk if isinstance(pk, list) else [pk]
                non_pk = [c for c in cols if c not in pk_list]
                if non_pk:
                    set_clause = ", ".join([f"{c} = EXCLUDED.{c}" for c in non_pk])
                    s = (
                        f"INSERT INTO {table} ({cols_str}) VALUES ({vals}) "
                        f"ON CONFLICT ({', '.join(pk_list)}) DO UPDATE SET {set_clause}"
                    )
                else:
                    s = (
                        f"INSERT INTO {table} ({cols_str}) VALUES ({vals}) "
                        f"ON CONFLICT ({', '.join(pk_list)}) DO NOTHING"
                    )
            else:
                # Без PK — fallback DO NOTHING
                s = (
                    f"INSERT INTO {table} ({cols_str}) VALUES ({vals}) "
                    f"ON CONFLICT DO NOTHING"
                )

        # INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING
        m2 = _INSERT_OR_IGNORE_RE.match(s)
        if m2 and "INSERT OR IGNORE" in s.upper():
            table = m2.group(1)
            tail = m2.group(2) or ""
            if pg_cursor is not None:
                self.ensure_pk_for(table, pg_cursor)
            pk = self.get_pk(table)
            if pk:
                pk_list = pk if isinstance(pk, list) else [pk]
                s = f"INSERT INTO {table} {tail} ON CONFLICT ({', '.join(pk_list)}) DO NOTHING"
            else:
                s = f"INSERT INTO {table} {tail} ON CONFLICT DO NOTHING"

        # INTEGER PRIMARY KEY AUTOINCREMENT → SERIAL PRIMARY KEY (тільки CREATE TABLE)
        s = re.sub(
            r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
            "SERIAL PRIMARY KEY",
            s,
            flags=re.IGNORECASE,
        )

        # datetime('now') → CURRENT_TIMESTAMP
        s = re.sub(
            r"datetime\s*\(\s*'now'\s*\)",
            "CURRENT_TIMESTAMP",
            s,
            flags=re.IGNORECASE,
        )
        s = re.sub(
            r"datetime\s*\(\s*'now'\s*,\s*'localtime'\s*\)",
            "CURRENT_TIMESTAMP",
            s,
            flags=re.IGNORECASE,
        )

        # strftime('%Y-%m-%d %H:%M:%S', 'now') → TO_CHAR(CURRENT_TIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')
        s = re.sub(
            r"strftime\s*\(\s*'%Y-%m-%d %H:%M:%S'\s*,\s*'now'\s*\)",
            "TO_CHAR(CURRENT_TIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')",
            s,
            flags=re.IGNORECASE,
        )

        # Quote reserved words (user → "user") + rowid → ctid
        s = _quote_reserved_words(s)

        # ? → %s
        s = _replace_question_marks(s)
        return s


_TRANSLATOR = _SQLTranslator()


# ──────────────────────────────────────────────────────────────────────
# PostgreSQL connection wrapper
# ──────────────────────────────────────────────────────────────────────

class _PGCursor:
    """psycopg2 cursor wrapper: додає RETURNING id для INSERT і lastrowid."""

    def __init__(self, pg_cursor, translator: _SQLTranslator):
        self._cur = pg_cursor
        self._translator = translator
        self._lastrowid: Optional[int] = None

    @property
    def lastrowid(self) -> Optional[int]:
        return self._lastrowid

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount or 0

    @property
    def description(self):
        return self._cur.description

    def execute(self, sql: str, params: Tuple = ()):
        translated = self._translator.translate(sql, pg_cursor=self._cur)
        if translated is None:
            # PRAGMA — skip. Повертаємо self для ланцюжків, без реального execute.
            return self

        upper = translated.lstrip().upper()
        if upper.startswith("INSERT") and "RETURNING" not in upper:
            # Додаємо RETURNING id через SAVEPOINT — якщо таблиця без колонки `id`,
            # rollback до savepoint і retry без RETURNING (PG abortить транзакцію при помилці).
            try:
                self._cur.execute("SAVEPOINT sp_returning")
                translated_with_returning = translated.rstrip().rstrip(";") + " RETURNING id"
                self._cur.execute(translated_with_returning, params)
                row = self._cur.fetchone()
                if row and row[0] is not None:
                    self._lastrowid = int(row[0])
                self._cur.execute("RELEASE SAVEPOINT sp_returning")
            except Exception:
                # ROLLBACK TO SAVEPOINT відновлює транзакцію з попереднього стану
                try:
                    self._cur.execute("ROLLBACK TO SAVEPOINT sp_returning")
                    self._cur.execute("RELEASE SAVEPOINT sp_returning")
                except Exception:
                    pass
                self._cur.execute(translated, params)
                self._lastrowid = None
        else:
            self._cur.execute(translated, params)
        return self

    def executemany(self, sql: str, seq):
        translated = self._translator.translate(sql, pg_cursor=self._cur)
        if translated is None:
            return self
        self._cur.executemany(translated, seq)
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def fetchmany(self, size=None):
        if size is None:
            return self._cur.fetchmany()
        return self._cur.fetchmany(size)

    def close(self):
        self._cur.close()


class _PGConnection:
    """psycopg2 connection wrapper з SQLite-сумісним API."""

    def __init__(self, dsn: str):
        import psycopg2  # local import: дозволяє працювати без psycopg2 коли SQLite-only
        self._pg = psycopg2.connect(dsn)
        self._pg.autocommit = False
        # Завантажуємо PK для поточних таблиць (якщо такі вже є).
        try:
            with self._pg.cursor() as c:
                _TRANSLATOR.load_pk_cache(c)
        except Exception as e:
            logger.warning(f"db_compat: initial PK load failed: {e}")

    def cursor(self):
        return _PGCursor(self._pg.cursor(), _TRANSLATOR)

    def execute(self, sql: str, params: Tuple = ()):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def commit(self):
        self._pg.commit()

    def rollback(self):
        self._pg.rollback()

    def close(self):
        self._pg.close()

    @property
    def backend(self) -> str:
        return "postgres"


# ──────────────────────────────────────────────────────────────────────
# SQLite fallback wrapper (щоб API повністю збігався)
# ──────────────────────────────────────────────────────────────────────

class _SQLiteCursor:
    def __init__(self, cursor):
        self._cur = cursor

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description

    def execute(self, sql, params=()):
        self._cur.execute(sql, params)
        return self

    def executemany(self, sql, seq):
        self._cur.executemany(sql, seq)
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def fetchmany(self, size=None):
        if size is None:
            return self._cur.fetchmany()
        return self._cur.fetchmany(size)

    def close(self):
        self._cur.close()


class _SQLiteConnection:
    def __init__(self, db_path: str):
        import sqlite3 as _sqlite3
        self._conn = _sqlite3.connect(db_path, check_same_thread=False)
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.commit()
        except Exception as e:
            logger.warning(f"db_compat: PRAGMA setup failed: {e}")
        self._sqlite3 = _sqlite3  # for backup() / sqlite3-specific calls

    def cursor(self):
        return _SQLiteCursor(self._conn.cursor())

    def execute(self, sql, params=()):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    @property
    def backend(self) -> str:
        return "sqlite"

    # Проксування sqlite3-специфічних методів для беккапу
    def __getattr__(self, name):
        return getattr(self._conn, name)


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────

def connect(db_path: Optional[str] = None):
    """Головна точка входу. Повертає SQLite- або PostgreSQL-сумісний connection.

    Якщо заданий DATABASE_URL → PostgreSQL (параметр db_path ігнорується).
    Інакше → SQLite, db_path за замовчуванням = bot.db поруч зі скриптом.
    """
    backend = _detect_backend()
    if backend == "postgres":
        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            raise RuntimeError("DATABASE_URL is set to empty")
        logger.info("db_compat: using PostgreSQL backend")
        return _PGConnection(dsn)
    else:
        if not db_path:
            try:
                here = os.path.dirname(os.path.abspath(__file__))
            except NameError:
                here = os.getcwd()
            db_path = os.path.join(here, "bot.db")
        logger.info(f"db_compat: using SQLite backend at {db_path}")
        return _SQLiteConnection(db_path)


def get_backend() -> str:
    """Повертає поточний бекенд ('postgres' або 'sqlite') — корисно для беккапу."""
    return _detect_backend()


def is_sqlite(conn) -> bool:
    return isinstance(conn, _SQLiteConnection)


def is_postgres(conn) -> bool:
    return isinstance(conn, _PGConnection)