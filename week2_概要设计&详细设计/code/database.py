"""Store scene records and perform spatiotemporal queries."""

import os
import sqlite3
from contextlib import contextmanager
from getpass import getpass
from pathlib import Path

from nuscenes_data import ROOT


class DatabaseError(RuntimeError):
    pass


def add_database_arguments(parser):
    parser.add_argument("--database-url", help="PostgreSQL 连接串")
    parser.add_argument("--ask-db-password", action="store_true", help="在终端提示输入 PostgreSQL 密码")


def database_options(parser, args):
    url = args.database_url or os.environ.get("DATABASE_URL")
    if args.ask_db_password and not url:
        parser.error("--ask-db-password 需要指定 --database-url")
    password = getpass("PostgreSQL 密码：") if args.ask_db_password else None
    return {"url": url, "password": password}


class Database:
    def __init__(self, connection, postgres):
        self.connection = connection
        self.postgres = postgres

    def execute(self, sql, params=()):
        return self.connection.cursor().execute(self.sql(sql), params)

    def insert(self, table, columns, rows):
        placeholders = ", ".join("?" for _ in columns.split(","))
        statement = "INSERT INTO {} ({}) VALUES ({})".format(table, columns, placeholders)
        self.connection.cursor().executemany(self.sql(statement), rows)

    def sql(self, statement):
        return statement.replace("?", "%s") if self.postgres else statement

    def covers(self):
        if self.postgres:
            return "ST_Covers(ST_MakeEnvelope(r.min_x, r.min_y, r.max_x, r.max_y, 0), ST_MakePoint(p.x, p.y))"
        return "p.x BETWEEN r.min_x AND r.max_x AND p.y BETWEEN r.min_y AND r.max_y"


@contextmanager
def connect(initialize=False, url=None, password=None):
    url = url or os.environ.get("DATABASE_URL")
    connection = None
    if url:
        try:
            import psycopg
            from psycopg import IsolationLevel
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise DatabaseError('PostGIS 模式需要安装 psycopg[binary]') from exc
        driver_error = psycopg.Error
    else:
        path = Path(os.environ.get("SCENE_DB_PATH", str(ROOT / ".runtime" / "scenes.sqlite3")))
        if initialize:
            path.parent.mkdir(parents=True, exist_ok=True)
        elif not path.is_file():
            raise DatabaseError("请先运行 python import_data.py 导入场景")
        driver_error = sqlite3.Error
    try:
        if url:
            credentials = {"password": password} if password is not None else {}
            connection = psycopg.connect(url, connect_timeout=5, row_factory=dict_row, **credentials)
            connection.isolation_level = IsolationLevel.REPEATABLE_READ
        else:
            connection = sqlite3.connect(str(path))
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE" if initialize else "BEGIN")
        yield Database(connection, bool(url))
        connection.commit()
    except driver_error as exc:
        raise DatabaseError(str(exc)) from exc
    finally:
        if connection is not None:
            connection.close()


def replace_dataset(database, dataset):
    if database.postgres:
        database.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    for statement in (ROOT / "schema.sql").read_text(encoding="utf-8").split(";"):
        if statement.strip():
            database.execute(statement)

    # This database serves a single fixed dataset. Foreign keys cascade to its children.
    database.execute("DELETE FROM scenes")
    database.execute("DELETE FROM regions")
    columns = {
        "scenes": "id,name,location,start_us,end_us",
        "regions": "id,name,location,min_x,min_y,max_x,max_y,reference_scene",
        "track_points": "id,scene_id,timestamp_us,x,y",
        "events": "id,scene_id,timestamp_us,x,y,acceleration,speed_before",
        "keyframes": "id,scene_id,timestamp_us,filename",
    }
    for table, names in columns.items():
        database.insert(table, names, dataset[table])


def list_regions(database):
    return [dict(row) for row in database.execute("SELECT * FROM regions ORDER BY id")]


def list_keyframes(database, start=None, end=None):
    query = "SELECT id, scene_id, timestamp_us, filename FROM keyframes"
    params = ()
    if start is not None:
        query += " WHERE timestamp_us >= ? AND timestamp_us < ?"
        params = (start, end)
    return [dict(row) for row in database.execute(query + " ORDER BY timestamp_us, id", params)]


def find_scenes(database, start=None, end=None, region=None, brake=False):
    """Return scenes with a first frame and, when filtered, the matching evidence."""
    point_table = "events" if brake else "track_points"
    evidence_required = brake or region is not None
    params = []
    evidence = "NULL"
    if evidence_required:
        filters = ["p.scene_id = s.id"]
        region_join = ""
        if region is not None:
            region_join = "JOIN regions r ON r.id = ?"
            params.append(region)
            filters.extend(("s.location = r.location", database.covers()))
        if start is not None:
            filters.extend(("p.timestamp_us >= ?", "p.timestamp_us < ?"))
            params.extend((start, end))
        evidence = """(SELECT p.id FROM {table} p {join}
                          WHERE {filters} ORDER BY p.timestamp_us, p.id LIMIT 1)""".format(
            table=point_table, join=region_join, filters=" AND ".join(filters))

    time_filter = ""
    if start is not None:
        time_filter = "WHERE s.start_us < ? AND s.end_us > ?"
        params.extend((end, start))

    acceleration = "p.acceleration" if brake else "NULL"
    query = """
        WITH candidates AS (
            SELECT s.*, {evidence} AS evidence_id FROM scenes s {time_filter}
        )
        SELECT c.id, c.name, c.location, c.start_us, c.end_us,
               f.id AS frame_id, f.timestamp_us AS frame_time_us,
               p.timestamp_us AS evidence_time_us, p.x AS evidence_x, p.y AS evidence_y,
               {acceleration} AS acceleration
        FROM candidates c
        LEFT JOIN {point_table} p ON {evidence_join}
        LEFT JOIN keyframes f ON f.id = (
            SELECT k.id FROM keyframes k WHERE k.scene_id = c.id
            ORDER BY k.timestamp_us, k.id LIMIT 1
        )
        {require_match}
        ORDER BY c.start_us DESC, c.id
    """.format(evidence=evidence, time_filter=time_filter, point_table=point_table,
               evidence_join="p.id = c.evidence_id" if evidence_required else "1 = 0",
               require_match="WHERE c.evidence_id IS NOT NULL" if evidence_required else "",
               acceleration=acceleration)
    return [dict(row) for row in database.execute(query, params)]


def scene_detail(database, scene_id):
    scene = database.execute("SELECT * FROM scenes WHERE id = ?", (scene_id,)).fetchone()
    if scene is None:
        return None
    result = dict(scene)
    for table, key in (("track_points", "track"), ("events", "events"), ("keyframes", "frames")):
        rows = database.execute(
            "SELECT * FROM {} WHERE scene_id = ? ORDER BY timestamp_us, id".format(table),
            (scene_id,),
        )
        result[key] = [dict(row) for row in rows]
    result["regions"] = [dict(row) for row in database.execute(
        "SELECT * FROM regions WHERE location = ? ORDER BY id", (scene["location"],)
    )]
    return result
