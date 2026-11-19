"""SQLite storage and paginated queries."""

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from .importer import COLUMNS, Issue, parse_csv

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    student_id TEXT PRIMARY KEY,
    full_name TEXT NOT NULL,
    search_name TEXT NOT NULL,
    email TEXT NOT NULL,
    date_of_birth TEXT NOT NULL,
    grade_level INTEGER NOT NULL,
    section TEXT NOT NULL,
    maths TEXT NOT NULL,
    science TEXT NOT NULL,
    english TEXT NOT NULL,
    social_studies TEXT NOT NULL,
    second_language TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS students_by_name ON students(search_name, student_id);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY,
    digest TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    rows_read INTEGER NOT NULL,
    imported INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS import_rejections (
    import_id INTEGER NOT NULL REFERENCES imports(id),
    line INTEGER NOT NULL,
    student_id TEXT,
    issues TEXT NOT NULL,
    PRIMARY KEY (import_id, line)
);
"""

INSERT_STUDENT = f"""
    INSERT INTO students ({', '.join(COLUMNS)}, search_name)
    VALUES ({', '.join('?' for _ in range(len(COLUMNS) + 1))})
    ON CONFLICT(student_id) DO NOTHING
"""


def connect(path):
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path)) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA)


def import_results(path, payload, name):
    digest = hashlib.sha256(payload).hexdigest()
    with closing(connect(path)) as connection:
        existing = connection.execute("SELECT id FROM imports WHERE digest = ?", (digest,)).fetchone()
        if existing:
            return existing["id"], True

        rows = parse_csv(payload)
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT id FROM imports WHERE digest = ?", (digest,)).fetchone()
            if existing:
                return existing["id"], True

            cursor = connection.execute(
                "INSERT INTO imports (digest, name, rows_read) VALUES (?, ?, ?)",
                (digest, name, len(rows)),
            )
            import_id = cursor.lastrowid
            rejections = []
            for row in rows:
                if not row.issues:
                    values = row.values
                    cursor = connection.execute(
                        INSERT_STUDENT,
                        (*[values[field] for field in COLUMNS], values["full_name"].casefold()),
                    )
                    if cursor.rowcount:
                        continue
                    row.issues.append(Issue(
                        "student_id", "DUPLICATE_ID",
                        "A student with this ID is already in the portal.",
                    ))
                issues = [asdict(issue) | {"label": issue.label} for issue in row.issues]
                rejections.append((import_id, row.line, row.student_id, json.dumps(issues)))

            connection.executemany(
                "INSERT INTO import_rejections VALUES (?, ?, ?, ?)", rejections,
            )
            connection.execute(
                "UPDATE imports SET imported = ?, rejected = ? WHERE id = ?",
                (len(rows) - len(rejections), len(rejections), import_id),
            )
        return import_id, False


def get_import(path, import_id, page, size):
    with closing(connect(path)) as connection:
        batch = connection.execute(
            "SELECT id, name, rows_read, imported, rejected FROM imports WHERE id = ?",
            (import_id,),
        ).fetchone()
        if batch is None:
            return None
        page = min(page, max(1, (batch["rejected"] + size - 1) // size))
        rows = connection.execute(
            """SELECT line, student_id, issues FROM import_rejections
               WHERE import_id = ? ORDER BY line LIMIT ? OFFSET ?""",
            (import_id, size, (page - 1) * size),
        ).fetchall()
        return dict(batch) | {
            "page": page,
            "page_size": size,
            "errors": [dict(row) | {"issues": json.loads(row["issues"])} for row in rows],
        }


def list_imports(path, page, size):
    with closing(connect(path)) as connection, connection:
        connection.execute("BEGIN")
        total = connection.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
        page = min(page, max(1, (total + size - 1) // size))
        rows = connection.execute(
            """SELECT id, name, rows_read, imported, rejected FROM imports
               ORDER BY id DESC LIMIT ? OFFSET ?""",
            (size, (page - 1) * size),
        ).fetchall()
    return {"batches": [dict(row) for row in rows], "total": total, "page": page, "page_size": size}


def stream_rejections(path, import_id):
    with closing(connect(path)) as connection:
        rows = connection.execute(
            """SELECT line, student_id, issues FROM import_rejections
               WHERE import_id = ? ORDER BY line""",
            (import_id,),
        )
        for row in rows:
            yield row["line"], row["student_id"], json.loads(row["issues"])


def list_students(path, query, page, size):
    where = "WHERE instr(search_name, ?) OR instr(student_id, ?)" if query else ""
    params = (query.casefold(), query.upper()) if query else ()
    with closing(connect(path)) as connection, connection:
        connection.execute("BEGIN")
        total = connection.execute(f"SELECT COUNT(*) FROM students {where}", params).fetchone()[0]
        page = min(page, max(1, (total + size - 1) // size))
        rows = connection.execute(
            f"""SELECT student_id, full_name, email, grade_level, section FROM students
                {where} ORDER BY search_name, student_id LIMIT ? OFFSET ?""",
            (*params, size, (page - 1) * size),
        ).fetchall()
    return {"students": [dict(row) for row in rows], "total": total, "page": page, "page_size": size}


def get_student(path, student_id):
    with closing(connect(path)) as connection:
        row = connection.execute(
            "SELECT * FROM students WHERE student_id = ?", (student_id.upper(),),
        ).fetchone()
    if row is None:
        return None
    return {field: row[field] for field in COLUMNS}
