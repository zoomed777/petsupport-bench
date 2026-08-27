from __future__ import annotations

import logging
import sqlite3
from collections import Counter
from datetime import datetime, timedelta

from app.core.config import get_settings
from pydantic import BaseModel
from app.models.schemas import AnalysisRecord, FeedbackMetrics, FeedbackRecord, FeedbackRequest

logger = logging.getLogger(__name__)


class SatisfactionMetrics(BaseModel):
    total: int = 0
    satisfied_count: int = 0
    unsatisfied_count: int = 0
    satisfaction_rate: float = 0.0


class FeedbackRepository:
    def __init__(self) -> None:
        self.db_path = get_settings().feedback_db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._cleanup_old_analyses()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        return connection

    def _cleanup_old_analyses(self, days: int = 30) -> None:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM analyses WHERE created_at < ?", (cutoff,))
            if cursor.rowcount > 0:
                logger.info("Cleaned up %d old analysis records (>%d days)", cursor.rowcount, days)

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL;")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id TEXT NOT NULL,
                    original_reply TEXT NOT NULL,
                    revised_reply TEXT NOT NULL,
                    category TEXT NOT NULL,
                    accepted INTEGER NOT NULL,
                    editor TEXT,
                    notes TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id TEXT NOT NULL,
                    customer_id TEXT,
                    order_id TEXT,
                    message TEXT NOT NULL,
                    category TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    reply_draft TEXT NOT NULL,
                    should_escalate INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    prompt_version TEXT DEFAULT 'v1',
                    user_satisfied INTEGER DEFAULT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS satisfaction (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id TEXT NOT NULL,
                    satisfied INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def save(self, request: FeedbackRequest) -> FeedbackRecord:
        created_at = datetime.now().isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO feedback (
                    ticket_id, original_reply, revised_reply, category,
                    accepted, editor, notes, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.ticket_id,
                    request.original_reply,
                    request.revised_reply,
                    request.category,
                    int(request.accepted),
                    request.editor,
                    request.notes,
                    created_at,
                ),
            )
            record_id = cursor.lastrowid

        return FeedbackRecord(id=record_id, created_at=created_at, **request.model_dump())

    def list_recent(self, limit: int = 20) -> list[FeedbackRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM feedback ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def metrics(self) -> FeedbackMetrics:
        rows = self.list_recent(limit=1000)
        total = len(rows)
        if total == 0:
            return FeedbackMetrics(
                total_feedback=0,
                acceptance_rate=0,
                avg_revision_ratio=0,
                by_category={},
            )

        accepted = sum(record.accepted for record in rows)
        ratios = [
            abs(len(record.revised_reply) - len(record.original_reply)) / max(len(record.original_reply), 1)
            for record in rows
        ]
        by_category = Counter(record.category for record in rows)
        return FeedbackMetrics(
            total_feedback=total,
            acceptance_rate=round(accepted / total, 3),
            avg_revision_ratio=round(sum(ratios) / total, 3),
            by_category=dict(by_category),
        )

    def save_analysis(self, record: AnalysisRecord, full_response: str | None = None) -> None:
        self._migrate_analyses()
        self._ensure_full_response_column()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO analyses (ticket_id, customer_id, order_id, message, category, priority, confidence, reply_draft, should_escalate, source, prompt_version, created_at, full_response)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.ticket_id,
                    record.customer_id,
                    record.order_id,
                    record.message,
                    record.category,
                    record.priority,
                    record.confidence,
                    record.reply_draft,
                    int(record.should_escalate),
                    record.source,
                    record.prompt_version,
                    record.created_at,
                    full_response,
                ),
            )

    def _migrate_analyses(self) -> None:
        with self._connect() as connection:
            cols = [row[1] for row in connection.execute("PRAGMA table_info(analyses)").fetchall()]
            if "prompt_version" not in cols:
                connection.execute("ALTER TABLE analyses ADD COLUMN prompt_version TEXT DEFAULT 'v1'")
            if "user_satisfied" not in cols:
                connection.execute("ALTER TABLE analyses ADD COLUMN user_satisfied INTEGER DEFAULT NULL")

    def _ensure_full_response_column(self) -> None:
        with self._connect() as connection:
            try:
                connection.execute("ALTER TABLE analyses ADD COLUMN full_response TEXT")
            except sqlite3.OperationalError:
                pass

    def get_recent_by_category(self, category: str, limit: int = 5) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT a.*, f.original_reply, f.revised_reply
                FROM analyses a
                LEFT JOIN feedback f ON f.ticket_id = a.ticket_id
                WHERE a.category = ?
                ORDER BY a.id DESC LIMIT ?
                """,
                (category, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_analyses(self, limit: int = 20, offset: int = 0) -> tuple[list[dict], int]:
        with self._connect() as connection:
            total_row = connection.execute("SELECT COUNT(*) as cnt FROM analyses").fetchone()
            total = total_row["cnt"] if total_row else 0
            rows = connection.execute(
                "SELECT * FROM analyses ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(row) for row in rows], total

    def list_analyses_by_ticket(self, ticket_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM analyses WHERE ticket_id = ? ORDER BY id ASC",
                (ticket_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_analysis_by_id(self, analysis_id: int) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM analyses WHERE id = ?", (analysis_id,)
            ).fetchone()
        return dict(row) if row else None

    def save_satisfaction(self, ticket_id: str, satisfied: bool) -> None:
        created_at = datetime.now().isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO satisfaction (ticket_id, satisfied, created_at) VALUES (?, ?, ?)",
                (ticket_id, int(satisfied), created_at),
            )
            connection.execute(
                "UPDATE analyses SET user_satisfied = ? WHERE id = (SELECT id FROM analyses WHERE ticket_id = ? AND user_satisfied IS NULL ORDER BY id DESC LIMIT 1)",
                (int(satisfied), ticket_id),
            )

    def satisfaction_metrics(self) -> SatisfactionMetrics:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT satisfied FROM satisfaction"
            ).fetchall()
        total = len(rows)
        if total == 0:
            return SatisfactionMetrics()
        satisfied_count = sum(1 for r in rows if r["satisfied"])
        return SatisfactionMetrics(
            total=total,
            satisfied_count=satisfied_count,
            unsatisfied_count=total - satisfied_count,
            satisfaction_rate=round(satisfied_count / total, 3) if total else 0.0,
        )

    def _row_to_record(self, row: sqlite3.Row) -> FeedbackRecord:
        return FeedbackRecord(
            id=row["id"],
            ticket_id=row["ticket_id"],
            original_reply=row["original_reply"],
            revised_reply=row["revised_reply"],
            category=row["category"],
            accepted=bool(row["accepted"]),
            editor=row["editor"],
            notes=row["notes"],
            created_at=row["created_at"],
        )


feedback_repository = FeedbackRepository()
