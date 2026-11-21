from contextlib import contextmanager
from datetime import UTC, datetime
import json
from shutil import rmtree
import sqlite3


def now():
    return datetime.now(UTC).isoformat()


JOB_SELECT = """SELECT j.*, CASE WHEN j.status='queued' THEN (
    SELECT COUNT(*) FROM jobs q WHERE q.status='queued' AND q.deleted_at IS NULL AND q.seq<=j.seq
) END AS queue_position FROM jobs j"""


class ParentUnavailable(ValueError):
    pass


class Store:
    def __init__(self, root):
        self.root = root

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.root / "queue.sqlite3")
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def worker_lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.root / "worker-lock.sqlite3", timeout=0)
        try:
            try:
                connection.execute("BEGIN EXCLUSIVE")
            except sqlite3.OperationalError as error:
                if error.sqlite_errorcode != sqlite3.SQLITE_BUSY:
                    raise
                raise RuntimeError("A LayerLens worker already owns this queue") from error
            yield
        finally:
            connection.close()

    def setup(self):
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    seq INTEGER PRIMARY KEY,
                    id TEXT UNIQUE NOT NULL,
                    filename TEXT NOT NULL,
                    input_name TEXT NOT NULL,
                    options TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error TEXT
                )
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            if "batch_id" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN batch_id TEXT")
            if "derivation" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN derivation TEXT")
            if "deleted_at" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN deleted_at TEXT")
            if "purging" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN purging INTEGER NOT NULL DEFAULT 0")
            if "engine_id" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN engine_id TEXT NOT NULL DEFAULT 'seethrough'")
            if "engine_version" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN engine_version TEXT NOT NULL DEFAULT 'layerdiff3d-0.0.2'")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_batch ON jobs(batch_id)")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, seq)")
            for job in db.execute("SELECT * FROM jobs WHERE status='running'").fetchall():
                if (self.root / job["id"] / "result").is_dir():
                    self._finish(db, job["id"], "succeeded")
                    # The crash happened after publishing; the scratch directory is orphaned.
                    rmtree(self.root / job["id"] / "work", ignore_errors=True)
                elif job["cancel_requested"]:
                    self._finish(db, job["id"], "cancelled")
                else:
                    self._finish(db, job["id"], "failed", "ENGINE_INTERRUPTED")

    def enqueue(self, job_id, filename, input_name, options, derivation=None, engine_id="seethrough", engine_version="layerdiff3d-0.0.2"):
        self.enqueue_many([(job_id, filename, input_name)], options, derivation=derivation, engine_id=engine_id, engine_version=engine_version)

    def enqueue_many(self, jobs, options, batch_id=None, derivation=None, engine_id="seethrough", engine_version="layerdiff3d-0.0.2"):
        with self.connect() as db:
            if derivation:
                db.execute("BEGIN IMMEDIATE")
                parent = db.execute("SELECT purging FROM jobs WHERE id=?", (derivation["parent_id"],)).fetchone()
                if parent is None or parent["purging"]:
                    raise ParentUnavailable(derivation["parent_id"])
            db.executemany(
                """INSERT INTO jobs(id, filename, input_name, options, status, created_at, batch_id, derivation, engine_id, engine_version)
                   VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)""",
                [(*entry, json.dumps(options), now(), batch_id, json.dumps(derivation) if derivation else None, engine_id, engine_version)
                 for entry in jobs],
            )

    def get(self, job_id, include_deleted=False):
        with self.connect() as db:
            filter_deleted = "" if include_deleted else " AND j.deleted_at IS NULL"
            return db.execute(JOB_SELECT + " WHERE j.id=?" + filter_deleted, (job_id,)).fetchone()

    def recent(self):
        with self.connect() as db:
            return db.execute(JOB_SELECT + """
                WHERE j.deleted_at IS NULL
                ORDER BY CASE j.status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
                    CASE WHEN j.status='queued' THEN j.seq ELSE -j.seq END
                LIMIT 80
            """).fetchall()

    def batch(self, batch_id):
        with self.connect() as db:
            return db.execute(JOB_SELECT + " WHERE j.batch_id=? AND j.deleted_at IS NULL ORDER BY j.seq", (batch_id,)).fetchall()

    def task_page(self, kind, query, page, size, engine_id=None):
        clauses = {
            "all": "deleted_at IS NULL",
            "active": "deleted_at IS NULL AND status IN ('queued','running')",
            "succeeded": "deleted_at IS NULL AND status='succeeded'",
            "issues": "deleted_at IS NULL AND status IN ('cancelled','failed')",
            "trash": "deleted_at IS NOT NULL",
        }
        with self.connect() as db:
            db.execute("BEGIN")
            counts = {}
            engine_filter = " AND engine_id=?" if engine_id else ""
            args = (engine_id,) if engine_id else ()
            for name, clause in clauses.items():
                counts[name] = db.execute("SELECT count(*) FROM jobs WHERE " + clause + engine_filter, args).fetchone()[0]
            where = f" WHERE {clauses[kind]}{engine_filter} AND instr(lower(j.filename), lower(?)) > 0"
            # SQLite reports instr(text, '') as 1, so the same query serves an empty search.
            total = db.execute("SELECT count(*) FROM jobs j" + where, (*args, query)).fetchone()[0]
            rows = db.execute(JOB_SELECT + where + """
                ORDER BY CASE j.status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
                    CASE WHEN j.status='queued' THEN j.seq ELSE -j.seq END
                LIMIT ? OFFSET ?
            """, (*args, query, size, (page - 1) * size)).fetchall()
            return rows, counts, total

    def trash(self, job_id):
        return self.set_trash([job_id], True)

    def restore(self, job_id):
        return self.set_trash([job_id], False)

    def set_trash(self, ids, trash):
        slots = ",".join("?" for _ in ids)
        condition = "deleted_at IS NULL AND status IN ('succeeded','failed','cancelled')" if trash else "deleted_at IS NOT NULL AND purging=0"
        with self.connect() as db:
            result = db.execute(f"UPDATE jobs SET deleted_at=? WHERE id IN ({slots}) AND {condition}",
                                (now() if trash else None, *ids))
            if result.rowcount != len(ids):
                db.rollback()
                return False
            return True

    def begin_purge(self, ids):
        slots = ",".join("?" for _ in ids)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(f"SELECT id, batch_id FROM jobs WHERE id IN ({slots}) AND deleted_at IS NOT NULL", ids).fetchall()
            if len(rows) != len(ids):
                return [], "TRASH_REQUIRED"
            dependent = db.execute(f"""
                SELECT 1 FROM jobs WHERE status IN ('running','queued')
                    AND json_extract(derivation, '$.parent_id') IN ({slots}) LIMIT 1
            """, ids).fetchone()
            if dependent:
                return [], "ACTIVE_DERIVATION"
            db.execute(f"UPDATE jobs SET purging=1 WHERE id IN ({slots})", ids)
            return rows, None

    def finish_purge(self, job_id):
        with self.connect() as db:
            db.execute("DELETE FROM jobs WHERE id=? AND purging=1", (job_id,))

    def claim(self):
        with self.connect() as db:
            return db.execute("""
                UPDATE jobs SET status='running', started_at=?
                WHERE seq=(SELECT seq FROM jobs WHERE status='queued' AND deleted_at IS NULL ORDER BY seq LIMIT 1)
                RETURNING *
            """, (now(),)).fetchone()

    def progress(self, job_id, event):
        with self.connect() as db:
            db.execute("UPDATE jobs SET progress=? WHERE id=?", (event, job_id))

    def cancel(self, job_id):
        with self.connect() as db:
            updated = db.execute("""
                UPDATE jobs SET cancel_requested=1,
                    status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END,
                    finished_at=CASE WHEN status='queued' THEN ? ELSE finished_at END
                WHERE id=? AND deleted_at IS NULL AND status IN ('queued', 'running')
            """, (now(), job_id))
            return updated.rowcount == 1

    def settle(self, job_id, error=None, requeue=False):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job["cancel_requested"]:
                self._finish(db, job_id, "cancelled")
            elif requeue:
                db.execute("""
                    UPDATE jobs SET status='queued', progress=NULL, started_at=NULL WHERE id=?
                """, (job_id,))
            elif error:
                self._finish(db, job_id, "failed", error)
            else:
                folder = self.root / job_id
                (folder / "work").rename(folder / "result")
                self._finish(db, job_id, "succeeded")

    def _finish(self, db, job_id, status, error=None):
        db.execute(
            "UPDATE jobs SET status=?, finished_at=?, error=? WHERE id=?",
            (status, now(), error, job_id),
        )

