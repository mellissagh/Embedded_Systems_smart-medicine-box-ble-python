from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta

from database import (
    get_connection,
    init_database,
    initialize_default_schedules,
    set_setting,
    update_medication_schedule,
)


def clear_today_demo_data() -> None:
    """
    Delete only today's dose-session test activity.

    Medication schedules and other permanent configuration are preserved.
    """
    today = date.today().strftime("%Y-%m-%d")

    with get_connection() as connection:
        session_rows = connection.execute(
            """
            SELECT id
            FROM dose_sessions
            WHERE date(scheduled_for) = ?
            """,
            (today,),
        ).fetchall()

        session_ids = [
            int(row["id"])
            for row in session_rows
        ]

        for session_id in session_ids:
            connection.execute(
                """
                DELETE FROM dose_session_events
                WHERE session_id = ?
                """,
                (session_id,),
            )

            connection.execute(
                """
                DELETE FROM caregiver_alerts
                WHERE session_id = ?
                """,
                (session_id,),
            )

            connection.execute(
                """
                DELETE FROM dose_sessions
                WHERE id = ?
                """,
                (session_id,),
            )

        connection.execute(
            """
            DELETE FROM medicine_events
            WHERE date(timestamp) = ?
            """,
            (today,),
        )

        connection.execute(
            """
            UPDATE system_status
            SET expected_slot = NULL,
                last_message = NULL,
                lid_state = NULL,
                morning_state = NULL,
                afternoon_state = NULL,
                evening_state = NULL
            WHERE id = 1
            """
        )

        connection.commit()

    print(
        f"[DEMO] Cleared {len(session_ids)} "
        "previous sessions from today."
    )


def configure_presentation_demo() -> None:
    """
    Configure the three presentation scenarios.

    Scenario 1:
        Morning — correct pill.

    Scenario 2:
        Afternoon — wrong pill, then correction.

    Scenario 3:
        Evening — no pill taken.
    """
    init_database()
    initialize_default_schedules()
    clear_today_demo_data()

    # Disable all schedules before changing their times.
    # This prevents overlap validation during configuration.
    for slot in (
        "MORNING",
        "AFTERNOON",
        "EVENING",
    ):
        update_medication_schedule(
            slot,
            enabled=False,
        )

    # Give the team two minutes to start main.py
    # and prepare the physical box.
    morning_time = (
        datetime.now()
        .replace(second=0, microsecond=0)
        + timedelta(minutes=2)
    )

    afternoon_time = (
        morning_time
        + timedelta(minutes=2)
    )

    evening_time = (
        afternoon_time
        + timedelta(minutes=2)
    )

    update_medication_schedule(
        "MORNING",
        dose_time=morning_time.strftime("%H:%M"),
        dose_window_minutes=1,
        correction_window_minutes=1,
        enabled=True,
    )

    update_medication_schedule(
        "AFTERNOON",
        dose_time=afternoon_time.strftime("%H:%M"),
        dose_window_minutes=1,
        correction_window_minutes=1,
        enabled=True,
    )

    update_medication_schedule(
        "EVENING",
        dose_time=evening_time.strftime("%H:%M"),
        dose_window_minutes=1,
        correction_window_minutes=1,
        enabled=True,
    )

    # Repeat late reminders every minute during the demo.
    set_setting(
        "reminder_repeat_minutes",
        "1",
    )

    print()
    print("=" * 70)
    print("SMART MEDICINE BOX — PRESENTATION DEMO")
    print("=" * 70)

    print(
        f"1. MORNING   {morning_time:%H:%M}"
        " — correct pill"
    )

    print(
        f"2. AFTERNOON {afternoon_time:%H:%M}"
        " — wrong pill and correction"
    )

    print(
        f"3. EVENING   {evening_time:%H:%M}"
        " — no pill taken"
    )

    print()
    print(
        "Each stage starts 2 minutes "
        "after the previous stage."
    )

    print(
        "Dose window: 1 minute"
    )

    print(
        "Correction window: 1 minute"
    )

    print(
        "Reminder repetition: every 1 minute"
    )

    print()
    print("Before starting main.py:")

    print(
        "1. Put all three pills inside."
    )

    print(
        "2. Close the lid."
    )

    print(
        "3. Close Arduino Serial Monitor."
    )

    print(
        "4. Connect HM-11 to Arduino D0/D1."
    )

    print()
    print("Then run:")
    print("python3 src/main.py")
    print("=" * 70)


def restore_normal_schedules() -> None:
    """
    Restore the normal schedules after the presentation.
    """
    init_database()
    initialize_default_schedules()

    for slot in (
        "MORNING",
        "AFTERNOON",
        "EVENING",
    ):
        update_medication_schedule(
            slot,
            enabled=False,
        )

    update_medication_schedule(
        "MORNING",
        dose_time="08:00",
        dose_window_minutes=60,
        correction_window_minutes=5,
        enabled=True,
    )

    update_medication_schedule(
        "AFTERNOON",
        dose_time="13:00",
        dose_window_minutes=60,
        correction_window_minutes=5,
        enabled=True,
    )

    update_medication_schedule(
        "EVENING",
        dose_time="20:00",
        dose_window_minutes=60,
        correction_window_minutes=5,
        enabled=True,
    )

    set_setting(
        "reminder_repeat_minutes",
        "10",
    )

    print()
    print("Normal schedules restored:")
    print(
        "Morning   08:00 | "
        "60-minute window | "
        "5-minute correction"
    )
    print(
        "Afternoon 13:00 | "
        "60-minute window | "
        "5-minute correction"
    )
    print(
        "Evening   20:00 | "
        "60-minute window | "
        "5-minute correction"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Configure or restore the "
            "Smart Medicine Box presentation."
        )
    )

    parser.add_argument(
        "action",
        choices=(
            "setup",
            "restore",
        ),
    )

    args = parser.parse_args()

    if args.action == "setup":
        configure_presentation_demo()

    else:
        restore_normal_schedules()


if __name__ == "__main__":
    main()