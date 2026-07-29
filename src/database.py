from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "medicine_box.db"
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
SLOTS = ("MORNING", "AFTERNOON", "EVENING")


@dataclass
class MedicineEvent:
    raw_message: str
    category: str = "UNKNOWN"
    result_type: str | None = None
    expected_slot: str | None = None
    removed_slots: str | None = None
    lid_state: str | None = None
    morning_state: str | None = None
    afternoon_state: str | None = None
    evening_state: str | None = None
    severity: str = "INFO"
    acknowledged: bool = False
    timestamp: str | None = None


def now_text() -> str:
    return datetime.now().strftime(DATETIME_FORMAT)


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def _add_column_if_missing(
    connection: sqlite3.Connection,
    table: str,
    column_name: str,
    definition: str,
) -> None:
    if column_name not in _column_names(connection, table):
        connection.execute(
            f"ALTER TABLE {table} ADD COLUMN {column_name} {definition}"
        )


def init_database() -> None:
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS medicine_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                raw_message TEXT NOT NULL,
                category TEXT NOT NULL,
                result_type TEXT,
                expected_slot TEXT,
                removed_slots TEXT,
                lid_state TEXT,
                morning_state TEXT,
                afternoon_state TEXT,
                evening_state TEXT,
                severity TEXT NOT NULL DEFAULT 'INFO',
                acknowledged INTEGER NOT NULL DEFAULT 0,
                acknowledged_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_events_timestamp
            ON medicine_events(timestamp);

            CREATE TABLE IF NOT EXISTS system_status (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                ble_connected INTEGER NOT NULL DEFAULT 0,
                last_seen TEXT,
                last_message TEXT,
                expected_slot TEXT,
                lid_state TEXT,
                morning_state TEXT,
                afternoon_state TEXT,
                evening_state TEXT
            );

            INSERT OR IGNORE INTO system_status (id, ble_connected)
            VALUES (1, 0);

            CREATE TABLE IF NOT EXISTS medication_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slot TEXT NOT NULL UNIQUE,
                dose_time TEXT NOT NULL,
                dose_window_minutes INTEGER NOT NULL DEFAULT 60,
                correction_window_minutes INTEGER NOT NULL DEFAULT 5,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dose_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schedule_id INTEGER,
                expected_slot TEXT NOT NULL,
                scheduled_for TEXT NOT NULL,
                window_start TEXT NOT NULL,
                window_end TEXT NOT NULL,
                correction_deadline TEXT,
                status TEXT NOT NULL,
                provisional_issue TEXT,
                final_result TEXT,
                corrected INTEGER NOT NULL DEFAULT 0,
                baseline_morning TEXT,
                baseline_afternoon TEXT,
                baseline_evening TEXT,
                current_morning TEXT,
                current_afternoon TEXT,
                current_evening TEXT,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                summary_text TEXT,
                alert_sent INTEGER NOT NULL DEFAULT 0,
                acknowledged INTEGER NOT NULL DEFAULT 0,
                acknowledged_at TEXT,
                FOREIGN KEY (schedule_id) REFERENCES medication_schedules(id)
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_scheduled_for
            ON dose_sessions(scheduled_for);

            CREATE INDEX IF NOT EXISTS idx_sessions_status
            ON dose_sessions(status);

            CREATE TABLE IF NOT EXISTS dose_session_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                raw_message TEXT,
                details TEXT,
                morning_state TEXT,
                afternoon_state TEXT,
                evening_state TEXT,
                FOREIGN KEY (session_id) REFERENCES dose_sessions(id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_session_events_session
            ON dose_session_events(session_id);

            CREATE TABLE IF NOT EXISTS caregiver_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                created_at TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                telegram_message_id INTEGER,
                delivered INTEGER NOT NULL DEFAULT 0,
                delivered_at TEXT,
                acknowledged INTEGER NOT NULL DEFAULT 0,
                acknowledged_at TEXT,
                FOREIGN KEY (session_id) REFERENCES dose_sessions(id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS schedule_change_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requested_by_chat_id INTEGER NOT NULL,
                requested_by_role TEXT NOT NULL,
                slot TEXT NOT NULL,
                old_time TEXT NOT NULL,
                requested_time TEXT NOT NULL,
                reason TEXT,
                status TEXT NOT NULL DEFAULT 'PENDING',
                created_at TEXT NOT NULL,
                reviewed_by_chat_id INTEGER,
                reviewed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                actor_chat_id INTEGER,
                actor_role TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT,
                old_value TEXT,
                new_value TEXT,
                details TEXT
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        # Backward-compatible additions for databases created by older code.
        _add_column_if_missing(connection, "dose_sessions", "alert_sent", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(connection, "dose_sessions", "acknowledged", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(connection, "dose_sessions", "acknowledged_at", "TEXT")
        connection.commit()

    print(f"[DB] Database ready: {DB_PATH}")


def initialize_default_schedules() -> None:
    stamp = now_text()
    defaults = [
        ("MORNING", "08:00", 60, 5),
        ("AFTERNOON", "13:00", 60, 5),
        ("EVENING", "20:00", 60, 5),
    ]
    with get_connection() as connection:
        for slot, dose_time, window, correction in defaults:
            connection.execute(
                """
                INSERT OR IGNORE INTO medication_schedules (
                    slot, dose_time, dose_window_minutes,
                    correction_window_minutes, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (slot, dose_time, window, correction, stamp, stamp),
            )
        defaults_settings = {
            "reminder_repeat_minutes": "10",
            "ble_offline_alert_minutes": "5",
            "patient_can_directly_edit_schedule": "0",
            "quiet_hours_start": "23:00",
            "quiet_hours_end": "07:00",
        }
        for key, value in defaults_settings.items():
            connection.execute(
                """
                INSERT OR IGNORE INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                """,
                (key, value, stamp),
            )
        connection.commit()


def get_medication_schedules() -> list[dict[str, object]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM medication_schedules
            ORDER BY CASE slot
                WHEN 'MORNING' THEN 1
                WHEN 'AFTERNOON' THEN 2
                WHEN 'EVENING' THEN 3
                ELSE 4 END
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_schedule_by_slot(slot: str) -> dict[str, object] | None:
    normalized = slot.strip().upper()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM medication_schedules WHERE slot = ?",
            (normalized,),
        ).fetchone()
    return dict(row) if row else None

def get_schedule_by_id(
    schedule_id: int,
) -> dict[str, object] | None:
    """
    Return one medication schedule by its database ID.
    """
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM medication_schedules
            WHERE id = ?
            """,
            (schedule_id,),
        ).fetchone()

    return dict(row) if row else None

def update_medication_schedule(
    slot: str,
    *,
    dose_time: str | None = None,
    dose_window_minutes: int | None = None,
    correction_window_minutes: int | None = None,
    enabled: bool | None = None,
) -> bool:
    """
    Update one medication schedule.

    Enabled medication windows are not allowed to overlap.
    This guarantees that one physical box interaction belongs
    to exactly one connected dose session.
    """
    normalized = slot.strip().upper()

    if normalized not in SLOTS:
        raise ValueError("Invalid medication slot.")

    current = get_schedule_by_slot(normalized)

    if current is None:
        raise ValueError(
            f"No medication schedule exists for {normalized}."
        )

    new_dose_time = (
        dose_time
        if dose_time is not None
        else str(current["dose_time"])
    )

    new_window_minutes = (
        dose_window_minutes
        if dose_window_minutes is not None
        else int(current["dose_window_minutes"])
    )

    new_correction_minutes = (
        correction_window_minutes
        if correction_window_minutes is not None
        else int(current["correction_window_minutes"])
    )

    new_enabled = (
        bool(enabled)
        if enabled is not None
        else bool(current["enabled"])
    )

    datetime.strptime(new_dose_time, "%H:%M")

    if not 1 <= new_window_minutes <= 720:
        raise ValueError(
            "Dose window must be between 1 and 720 minutes."
        )

    if not 1 <= new_correction_minutes <= 120:
        raise ValueError(
            "Correction window must be between 1 and 120 minutes."
        )

    if new_enabled:
        proposed_start = datetime.strptime(
            new_dose_time,
            "%H:%M",
        )

        proposed_end = proposed_start + timedelta(
            minutes=new_window_minutes
        )

        for other in get_medication_schedules():
            other_slot = str(other["slot"]).strip().upper()

            if other_slot == normalized:
                continue

            if not bool(other["enabled"]):
                continue

            other_start = datetime.strptime(
                str(other["dose_time"]),
                "%H:%M",
            )

            other_end = other_start + timedelta(
                minutes=int(
                    other["dose_window_minutes"]
                )
            )

            windows_overlap = (
                proposed_start < other_end
                and other_start < proposed_end
            )

            if windows_overlap:
                raise ValueError(
                    f"{normalized.title()} medication window "
                    f"overlaps with {other_slot.title()}.\n"
                    f"{normalized.title()}: "
                    f"{new_dose_time} for "
                    f"{new_window_minutes} minutes.\n"
                    f"{other_slot.title()}: "
                    f"{other['dose_time']} for "
                    f"{other['dose_window_minutes']} minutes."
                )

    updates: list[str] = []
    values: list[object] = []

    if dose_time is not None:
        updates.append("dose_time = ?")
        values.append(new_dose_time)

    if dose_window_minutes is not None:
        updates.append("dose_window_minutes = ?")
        values.append(new_window_minutes)

    if correction_window_minutes is not None:
        updates.append("correction_window_minutes = ?")
        values.append(new_correction_minutes)

    if enabled is not None:
        updates.append("enabled = ?")
        values.append(int(new_enabled))

    if not updates:
        return False

    updates.append("updated_at = ?")
    values.append(now_text())
    values.append(normalized)

    with get_connection() as connection:
        cursor = connection.execute(
            f"""
            UPDATE medication_schedules
            SET {", ".join(updates)}
            WHERE slot = ?
            """,
            values,
        )

        connection.commit()

    return cursor.rowcount > 0


def log_event(event: MedicineEvent) -> int:
    timestamp = event.timestamp or now_text()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO medicine_events (
                timestamp, raw_message, category, result_type, expected_slot,
                removed_slots, lid_state, morning_state, afternoon_state,
                evening_state, severity, acknowledged
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                event.raw_message,
                event.category,
                event.result_type,
                event.expected_slot,
                event.removed_slots,
                event.lid_state,
                event.morning_state,
                event.afternoon_state,
                event.evening_state,
                event.severity,
                int(event.acknowledged),
            ),
        )
        connection.commit()
        event_id = int(cursor.lastrowid)
    print(f"[DB] Event #{event_id}: {event.category} | {event.raw_message}")
    return event_id


def update_system_status(
    *,
    ble_connected: bool | None = None,
    last_message: str | None = None,
    expected_slot: str | None = None,
    lid_state: str | None = None,
    morning_state: str | None = None,
    afternoon_state: str | None = None,
    evening_state: str | None = None,
) -> bool:
    updates: list[str] = []
    values: list[object] = []
    if ble_connected is not None:
        updates.append("ble_connected = ?")
        values.append(int(ble_connected))
    if last_message is not None:
        updates.extend(["last_message = ?", "last_seen = ?"])
        values.extend([last_message, now_text()])
    if expected_slot is not None:
        updates.append("expected_slot = ?")
        values.append(expected_slot)
    if lid_state is not None:
        updates.append("lid_state = ?")
        values.append(lid_state)
    if morning_state is not None:
        updates.append("morning_state = ?")
        values.append(morning_state)
    if afternoon_state is not None:
        updates.append("afternoon_state = ?")
        values.append(afternoon_state)
    if evening_state is not None:
        updates.append("evening_state = ?")
        values.append(evening_state)
    if not updates:
        return False
    with get_connection() as connection:
        cursor = connection.execute(
            f"UPDATE system_status SET {', '.join(updates)} WHERE id = 1",
            values,
        )
        connection.commit()
    return cursor.rowcount > 0


def get_system_status() -> dict[str, object] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM system_status WHERE id = 1").fetchone()
    return dict(row) if row else None


def fetch_recent_events(limit: int = 20) -> list[dict[str, object]]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM medicine_events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_today_events() -> list[dict[str, object]]:
    today = datetime.now().strftime("%Y-%m-%d")
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM medicine_events WHERE timestamp LIKE ? ORDER BY id ASC",
            (f"{today}%",),
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_unacknowledged_warnings() -> list[dict[str, object]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM medicine_events
            WHERE severity IN ('WARNING', 'CRITICAL') AND acknowledged = 0
            ORDER BY id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def acknowledge_event(event_id: int) -> bool:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE medicine_events
            SET acknowledged = 1, acknowledged_at = ?
            WHERE id = ?
            """,
            (now_text(), event_id),
        )
        connection.commit()
    return cursor.rowcount > 0


ACTIVE_SESSION_STATUSES = {"WAITING_FOR_DOSE", "IN_PROGRESS", "NEEDS_CORRECTION"}


def create_dose_session(
    *,
    schedule_id: int,
    expected_slot: str,
    scheduled_for: str,
    window_start: str,
    window_end: str,
    baseline_morning: str | None,
    baseline_afternoon: str | None,
    baseline_evening: str | None,
    started_at: str | None = None,
) -> int:
    normalized = expected_slot.strip().upper()
    started_at = started_at or now_text()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO dose_sessions (
                schedule_id, expected_slot, scheduled_for, window_start,
                window_end, status, corrected, baseline_morning,
                baseline_afternoon, baseline_evening, current_morning,
                current_afternoon, current_evening, started_at
            ) VALUES (?, ?, ?, ?, ?, 'WAITING_FOR_DOSE', 0, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                schedule_id,
                normalized,
                scheduled_for,
                window_start,
                window_end,
                baseline_morning,
                baseline_afternoon,
                baseline_evening,
                baseline_morning,
                baseline_afternoon,
                baseline_evening,
                started_at,
            ),
        )
        connection.commit()
        session_id = int(cursor.lastrowid)
    print(f"[DB] Created dose session #{session_id}: {normalized} at {scheduled_for}")
    return session_id


def get_dose_session(session_id: int) -> dict[str, object] | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM dose_sessions WHERE id = ?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


def get_active_dose_session() -> dict[str, object] | None:
    placeholders = ",".join("?" for _ in ACTIVE_SESSION_STATUSES)
    with get_connection() as connection:
        row = connection.execute(
            f"SELECT * FROM dose_sessions WHERE status IN ({placeholders}) ORDER BY id DESC LIMIT 1",
            tuple(ACTIVE_SESSION_STATUSES),
        ).fetchone()
    return dict(row) if row else None


def get_session_for_slot_and_date(slot: str, date_text: str) -> dict[str, object] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT * FROM dose_sessions
            WHERE expected_slot = ? AND scheduled_for LIKE ?
            ORDER BY id DESC LIMIT 1
            """,
            (slot.strip().upper(), f"{date_text}%"),
        ).fetchone()
    return dict(row) if row else None


def update_dose_session(
    session_id: int,
    *,
    status: str | None = None,
    provisional_issue: str | None = None,
    final_result: str | None = None,
    correction_deadline: str | None = None,
    corrected: bool | None = None,
    current_morning: str | None = None,
    current_afternoon: str | None = None,
    current_evening: str | None = None,
    summary_text: str | None = None,
    completed_at: str | None = None,
    alert_sent: bool | None = None,
    acknowledged: bool | None = None,
    acknowledged_at: str | None = None,
    clear_provisional_issue: bool = False,
    clear_correction_deadline: bool = False,
) -> bool:
    updates: list[str] = []
    values: list[object] = []
    field_values = {
        "status": status,
        "final_result": final_result,
        "correction_deadline": correction_deadline,
        "current_morning": current_morning,
        "current_afternoon": current_afternoon,
        "current_evening": current_evening,
        "summary_text": summary_text,
        "completed_at": completed_at,
        "acknowledged_at": acknowledged_at,
    }
    if clear_provisional_issue:
        updates.append("provisional_issue = NULL")
    elif provisional_issue is not None:
        updates.append("provisional_issue = ?")
        values.append(provisional_issue)
    if clear_correction_deadline:
        updates.append("correction_deadline = NULL")
        field_values.pop("correction_deadline", None)
    for field, value in field_values.items():
        if value is not None:
            updates.append(f"{field} = ?")
            values.append(value)
    for field, value in {
        "corrected": corrected,
        "alert_sent": alert_sent,
        "acknowledged": acknowledged,
    }.items():
        if value is not None:
            updates.append(f"{field} = ?")
            values.append(int(value))
    if not updates:
        return False
    values.append(session_id)
    with get_connection() as connection:
        cursor = connection.execute(
            f"UPDATE dose_sessions SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        connection.commit()
    return cursor.rowcount > 0


def add_dose_session_event(
    session_id: int,
    *,
    event_type: str,
    raw_message: str | None = None,
    details: str | None = None,
    morning_state: str | None = None,
    afternoon_state: str | None = None,
    evening_state: str | None = None,
    timestamp: str | None = None,
) -> int:
    stamp = timestamp or now_text()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO dose_session_events (
                session_id, timestamp, event_type, raw_message, details,
                morning_state, afternoon_state, evening_state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                stamp,
                event_type,
                raw_message,
                details,
                morning_state,
                afternoon_state,
                evening_state,
            ),
        )
        connection.commit()
        event_id = int(cursor.lastrowid)
    print(f"[DB] Session #{session_id} event #{event_id}: {event_type}")
    return event_id


def fetch_dose_session_events(session_id: int) -> list[dict[str, object]]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM dose_session_events WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_dose_sessions_between(start_datetime: str, end_datetime: str) -> list[dict[str, object]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT ds.*, ms.dose_time, ms.dose_window_minutes,
                   ms.correction_window_minutes
            FROM dose_sessions ds
            LEFT JOIN medication_schedules ms ON ms.id = ds.schedule_id
            WHERE ds.scheduled_for >= ? AND ds.scheduled_for <= ?
            ORDER BY ds.scheduled_for ASC
            """,
            (start_datetime, end_datetime),
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_today_dose_sessions() -> list[dict[str, object]]:
    today = datetime.now().strftime("%Y-%m-%d")
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT ds.*, ms.dose_time, ms.dose_window_minutes,
                   ms.correction_window_minutes
            FROM dose_sessions ds
            LEFT JOIN medication_schedules ms ON ms.id = ds.schedule_id
            WHERE ds.scheduled_for LIKE ?
            ORDER BY ds.scheduled_for ASC
            """,
            (f"{today}%",),
        ).fetchall()
    return [dict(row) for row in rows]


def acknowledge_dose_session(session_id: int) -> bool:
    return update_dose_session(
        session_id, acknowledged=True, acknowledged_at=now_text()
    )


def create_alert(
    *,
    session_id: int | None,
    alert_type: str,
    severity: str,
    message: str,
) -> int:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO caregiver_alerts (
                session_id, created_at, alert_type, severity, message
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, now_text(), alert_type, severity, message),
        )
        connection.commit()
        return int(cursor.lastrowid)


def mark_alert_delivered(alert_id: int, telegram_message_id: int | None = None) -> bool:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE caregiver_alerts
            SET delivered = 1, delivered_at = ?, telegram_message_id = ?
            WHERE id = ?
            """,
            (now_text(), telegram_message_id, alert_id),
        )
        connection.commit()
    return cursor.rowcount > 0


def fetch_active_alerts(limit: int = 20) -> list[dict[str, object]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM caregiver_alerts
            WHERE acknowledged = 0
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def acknowledge_alert(alert_id: int) -> bool:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE caregiver_alerts
            SET acknowledged = 1, acknowledged_at = ?
            WHERE id = ?
            """,
            (now_text(), alert_id),
        )
        connection.commit()
    return cursor.rowcount > 0


def create_schedule_change_request(
    *,
    requested_by_chat_id: int,
    requested_by_role: str,
    slot: str,
    old_time: str,
    requested_time: str,
    reason: str | None = None,
) -> int:
    datetime.strptime(requested_time, "%H:%M")
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO schedule_change_requests (
                requested_by_chat_id, requested_by_role, slot, old_time,
                requested_time, reason, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?)
            """,
            (
                requested_by_chat_id,
                requested_by_role,
                slot.strip().upper(),
                old_time,
                requested_time,
                reason,
                now_text(),
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def get_schedule_change_request(request_id: int) -> dict[str, object] | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM schedule_change_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
    return dict(row) if row else None


def review_schedule_change_request(
    request_id: int,
    *,
    status: str,
    reviewed_by_chat_id: int,
) -> bool:
    normalized = status.strip().upper()
    if normalized not in {"APPROVED", "REJECTED"}:
        raise ValueError("Invalid request status.")
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE schedule_change_requests
            SET status = ?, reviewed_by_chat_id = ?, reviewed_at = ?
            WHERE id = ? AND status = 'PENDING'
            """,
            (normalized, reviewed_by_chat_id, now_text(), request_id),
        )
        connection.commit()
    return cursor.rowcount > 0


def fetch_pending_schedule_change_requests() -> list[dict[str, object]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM schedule_change_requests
            WHERE status = 'PENDING' ORDER BY id ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def add_audit_log(
    *,
    actor_role: str,
    action: str,
    actor_chat_id: int | None = None,
    target: str | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
    details: str | None = None,
) -> int:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO audit_log (
                timestamp, actor_chat_id, actor_role, action, target,
                old_value, new_value, details
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_text(), actor_chat_id, actor_role, action, target,
                old_value, new_value, details,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def fetch_audit_log(limit: int = 20) -> list[dict[str, object]]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_setting(key: str, default: str | None = None) -> str | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
    return str(row["value"]) if row else default


def set_setting(key: str, value: str) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, now_text()),
        )
        connection.commit()


def delete_all_events() -> None:
    with get_connection() as connection:
        connection.execute("DELETE FROM medicine_events")
        connection.commit()
