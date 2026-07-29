from __future__ import annotations

import sqlite3
from datetime import datetime

from database import (
    MedicineEvent,
    fetch_dose_session_events,
    get_dose_session,
    init_database,
    initialize_default_schedules,
    update_medication_schedule,
)
from dose_session_manager import DoseSessionManager


def print_outcome(label: str, outcome: object) -> None:
    print(f"\n--- {label} ---")
    print(outcome if outcome is not None else "No caregiver-facing outcome.")


def clear_test_sessions() -> None:
    with sqlite3.connect("data/medicine_box.db") as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("DELETE FROM caregiver_alerts")
        connection.execute("DELETE FROM dose_sessions")
        connection.commit()


def main() -> None:
    init_database()
    initialize_default_schedules()
    clear_test_sessions()

    update_medication_schedule(
        "AFTERNOON",
        dose_time="13:00",
        dose_window_minutes=60,
        correction_window_minutes=10,
        enabled=True,
    )

    manager = DoseSessionManager()

    initial_state = MedicineEvent(
        raw_message=(
            "STATE|LID=CLOSED|MORNING=PRESENT|"
            "AFTERNOON=PRESENT|EVENING=PRESENT"
        ),
        category="STATE",
        result_type="STATE",
        lid_state="CLOSED",
        morning_state="PRESENT",
        afternoon_state="PRESENT",
        evening_state="PRESENT",
        timestamp="2026-07-27 13:10:00",
    )

    print_outcome(
        "Initial state",
        manager.process_event(
            initial_state,
            now=datetime(2026, 7, 27, 13, 10, 0),
        ),
    )

    lid_open_1 = MedicineEvent(
        raw_message="EVENT|LID_OPEN",
        category="EVENT",
        result_type="LID_OPEN",
        lid_state="OPEN",
        timestamp="2026-07-27 13:11:00",
    )

    print_outcome(
        "First lid opening",
        manager.process_event(
            lid_open_1,
            now=datetime(2026, 7, 27, 13, 11, 0),
        ),
    )

    lid_closed_1 = MedicineEvent(
        raw_message="EVENT|LID_CLOSED",
        category="EVENT",
        result_type="LID_CLOSED",
        lid_state="CLOSED",
        timestamp="2026-07-27 13:11:59",
    )

    print_outcome(
        "First lid closure",
        manager.process_event(
            lid_closed_1,
            now=datetime(2026, 7, 27, 13, 11, 59),
        ),
    )

    wrong_state = MedicineEvent(
        raw_message=(
            "STATE|LID=CLOSED|MORNING=ABSENT|"
            "AFTERNOON=PRESENT|EVENING=PRESENT"
        ),
        category="STATE",
        result_type="STATE",
        lid_state="CLOSED",
        morning_state="ABSENT",
        afternoon_state="PRESENT",
        evening_state="PRESENT",
        timestamp="2026-07-27 13:12:00",
    )

    print_outcome(
        "Wrong pill temporarily removed",
        manager.process_event(
            wrong_state,
            now=datetime(2026, 7, 27, 13, 12, 0),
        ),
    )

    repeated_closed_state = MedicineEvent(
        raw_message=(
            "STATE|LID=CLOSED|MORNING=ABSENT|"
            "AFTERNOON=PRESENT|EVENING=PRESENT"
        ),
        category="STATE",
        result_type="STATE",
        lid_state="CLOSED",
        morning_state="ABSENT",
        afternoon_state="PRESENT",
        evening_state="PRESENT",
        timestamp="2026-07-27 13:12:01",
    )

    print_outcome(
        "Repeated closed reading (must be ignored)",
        manager.process_event(
            repeated_closed_state,
            now=datetime(2026, 7, 27, 13, 12, 1),
        ),
    )

    lid_open_2 = MedicineEvent(
        raw_message="EVENT|LID_OPEN",
        category="EVENT",
        result_type="LID_OPEN",
        lid_state="OPEN",
        timestamp="2026-07-27 13:13:00",
    )

    print_outcome(
        "Correction lid opening",
        manager.process_event(
            lid_open_2,
            now=datetime(2026, 7, 27, 13, 13, 0),
        ),
    )

    lid_closed_2 = MedicineEvent(
        raw_message="EVENT|LID_CLOSED",
        category="EVENT",
        result_type="LID_CLOSED",
        lid_state="CLOSED",
        timestamp="2026-07-27 13:13:59",
    )

    print_outcome(
        "Correction lid closure",
        manager.process_event(
            lid_closed_2,
            now=datetime(2026, 7, 27, 13, 13, 59),
        ),
    )

    corrected_state = MedicineEvent(
        raw_message=(
            "STATE|LID=CLOSED|MORNING=PRESENT|"
            "AFTERNOON=ABSENT|EVENING=PRESENT"
        ),
        category="STATE",
        result_type="STATE",
        lid_state="CLOSED",
        morning_state="PRESENT",
        afternoon_state="ABSENT",
        evening_state="PRESENT",
        timestamp="2026-07-27 13:14:00",
    )

    final_outcome = manager.process_event(
        corrected_state,
        now=datetime(2026, 7, 27, 13, 14, 0),
    )

    print_outcome("Corrected final state", final_outcome)

    if manager.active_session_id is None:
        raise RuntimeError("No active test session was created.")

    session = get_dose_session(manager.active_session_id)

    print("\n=== Final session ===")
    print(session)

    print("\n=== Connected timeline ===")
    events = fetch_dose_session_events(manager.active_session_id)
    for event in events:
        print(event)

    if session is None:
        raise RuntimeError("Session was not found.")

    assert session["status"] == "COMPLETED_AFTER_CORRECTION"
    assert session["final_result"] == "COMPLETED_AFTER_CORRECTION"
    assert session["corrected"] == 1

    issue_events = [
        event
        for event in events
        if event["event_type"] == "NEEDS_CORRECTION_WRONG_PILL"
    ]
    assert len(issue_events) == 1

    print(
        "\n✅ Test passed: one wrong-pill warning was stored, "
        "the repeated CLOSED reading was ignored, and the "
        "correction completed the same dose session."
    )


if __name__ == "__main__":
    main()