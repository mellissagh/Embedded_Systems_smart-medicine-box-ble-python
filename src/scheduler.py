from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta

from database import (
    MedicineEvent,
    create_alert,
    fetch_today_dose_sessions,
    get_medication_schedules,
    get_session_for_slot_and_date,
    get_setting,
)
from dose_session_manager import DoseSessionManager


CommandSender = Callable[[str], Awaitable[bool]]
MessageSender = Callable[[str], Awaitable[None]]


ACTIVE_SESSION_STATUSES = {
    "WAITING_FOR_DOSE",
    "IN_PROGRESS",
    "NEEDS_CORRECTION",
}


class MedicationScheduler:
    """
    Python-owned medication timing.

    Responsibilities:
    - Start or retrieve the connected dose session.
    - Tell Arduino which compartment is expected.
    - Trigger the buzzer and LCD.
    - Notify the patient.
    - Escalate a late dose to the caregiver.
    - Refresh the LCD after Python restarts.
    """

    def __init__(
        self,
        *,
        manager: DoseSessionManager,
        command_sender: CommandSender,
        patient_sender: MessageSender,
        caregiver_sender: MessageSender,
    ) -> None:
        self.manager = manager
        self.command_sender = command_sender
        self.patient_sender = patient_sender
        self.caregiver_sender = caregiver_sender

        self._stop_event = asyncio.Event()
        self._sent: set[str] = set()
        self._last_cleanup_date: date | None = None
        self._last_lcd_key: str | None = None

    async def run_forever(self) -> None:
        self._stop_event.clear()

        while not self._stop_event.is_set():
            try:
                await self._tick(datetime.now())

            except Exception as exc:
                print(
                    "[SCHEDULER ERROR] "
                    f"{type(exc).__name__}: {exc}"
                )

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=15,
                )

            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stop_event.set()

    async def _tick(
        self,
        now: datetime,
    ) -> None:
        self._clean_old_tracking_keys(now.date())
        repeat_minutes = self._get_repeat_minutes()
        active_window_found = False

        for schedule in get_medication_schedules():
            if not bool(schedule["enabled"]):
                continue

            slot = str(schedule["slot"]).strip().upper()

            scheduled = datetime.combine(
                now.date(),
                datetime.strptime(
                    str(schedule["dose_time"]),
                    "%H:%M",
                ).time(),
            )

            window_end = scheduled + timedelta(
                minutes=int(
                    schedule["dose_window_minutes"]
                )
            )

            if now < scheduled or now > window_end:
                continue

            self.manager.process_event(
                MedicineEvent(
                    raw_message="SYSTEM|SCHEDULE_TICK",
                    category="SYSTEM",
                    result_type="SCHEDULE_TICK",
                ),
                now=now,
                schedule_id=int(schedule["id"]),
            )

            session = get_session_for_slot_and_date(
                slot,
                now.strftime("%Y-%m-%d"),
            )

            if session is None:
                print(
                    "[SCHEDULER WARNING] "
                    f"No session was created for {slot}."
                )
                continue

            if str(session["status"]) not in ACTIVE_SESSION_STATUSES:
                continue

            active_window_found = True

            elapsed_minutes = max(
                0,
                int(
                    (now - scheduled).total_seconds()
                    // 60
                ),
            )

            interval_number = (
                elapsed_minutes
                // repeat_minutes
            )

            reminder_key = (
                f"{now.date()}:{slot}:"
                f"interval:{interval_number}"
            )

            if reminder_key in self._sent:
                continue

            expected_sent = await self._send_hardware_command(
                f"CMD|EXPECTED|{slot}"
            )

            if not expected_sent:
                continue

            self._sent.add(reminder_key)
            self._last_lcd_key = None

            if interval_number == 0:
                await self._send_initial_reminder(
                    slot=slot,
                )

            else:
                await self._send_late_reminder(
                    slot=slot,
                    elapsed_minutes=elapsed_minutes,
                    session_id=int(session["id"]),
                    now=now,
                )

        if not active_window_found:
            await self._show_idle_summary(now)

    async def refresh_startup_display(
        self,
        now: datetime | None = None,
    ) -> bool:
        """
        Force a fresh LCD summary after Python restarts.

        Arduino keeps its previous LCD text after Python stops, so this
        method resets the display as soon as BLE becomes available.
        """
        self._last_lcd_key = None

        return await self._show_idle_summary(
            now or datetime.now(),
            force=True,
        )

    async def _show_idle_summary(
        self,
        now: datetime,
        *,
        force: bool = False,
    ) -> bool:
        """
        Show pills already completed today and the next scheduled pill.

        Examples:
            Taken: M,A
            Next: E 20:00

            Taken: None
            Next: M 08:00
        """
        schedules = sorted(
            [
                schedule
                for schedule in get_medication_schedules()
                if bool(schedule["enabled"])
            ],
            key=lambda item: str(item["dose_time"]),
        )

        sessions = fetch_today_dose_sessions()

        completed_statuses = {
            "COMPLETED",
            "COMPLETED_AFTER_CORRECTION",
        }

        slot_letters = {
            "MORNING": "M",
            "AFTERNOON": "A",
            "EVENING": "E",
        }

        taken_slots: list[str] = []

        for session in sessions:
            status = str(session["status"])
            slot = str(session["expected_slot"]).upper()

            if (
                status in completed_statuses
                and slot in slot_letters
            ):
                letter = slot_letters[slot]

                if letter not in taken_slots:
                    taken_slots.append(letter)

        if taken_slots:
            line1 = "Taken: " + ",".join(taken_slots)
        else:
            line1 = "Taken: None"

        final_statuses = {
            "COMPLETED",
            "COMPLETED_AFTER_CORRECTION",
            "MISSED",
            "WRONG_PILL_NOT_CORRECTED",
            "MULTIPLE_PILLS_NOT_CORRECTED",
        }

        sessions_by_slot = {
            str(session["expected_slot"]).upper(): session
            for session in sessions
        }

        next_schedule: dict[str, object] | None = None

        for schedule in schedules:
            slot = str(schedule["slot"]).upper()

            scheduled_time = datetime.combine(
                now.date(),
                datetime.strptime(
                    str(schedule["dose_time"]),
                    "%H:%M",
                ).time(),
            )

            session = sessions_by_slot.get(slot)

            if (
                session is not None
                and str(session["status"]) in final_statuses
            ):
                continue

            window_end = scheduled_time + timedelta(
                minutes=int(
                    schedule["dose_window_minutes"]
                )
            )

            if now <= window_end:
                next_schedule = schedule
                break

        if next_schedule is not None:
            next_slot = str(
                next_schedule["slot"]
            ).upper()

            next_letter = slot_letters.get(
                next_slot,
                next_slot[:1],
            )

            line2 = (
                f"Next: {next_letter} "
                f"{next_schedule['dose_time']}"
            )

        else:
            line2 = "All done today"

        line1 = line1[:16]
        line2 = line2[:16]

        lcd_key = f"{line1}|{line2}"

        if not force and lcd_key == self._last_lcd_key:
            return True

        sent = await self._send_hardware_command(
            f"CMD|LCD|{line1}|{line2}"
        )

        if sent:
            self._last_lcd_key = lcd_key

        return sent

    async def _send_initial_reminder(
        self,
        *,
        slot: str,
    ) -> None:
        await self._send_hardware_command(
            "CMD|BUZZER|DOSE_REMINDER"
        )

        await self._send_hardware_command(
            f"CMD|LCD|Take medicine|{slot.title()}"
        )

        await self.patient_sender(
            f"🔔 It is time for your "
            f"{slot.title()} medicine."
        )

        print(
            "[SCHEDULER] Initial reminder sent for "
            f"{slot}."
        )

    async def _send_late_reminder(
        self,
        *,
        slot: str,
        elapsed_minutes: int,
        session_id: int,
        now: datetime,
    ) -> None:
        await self._send_hardware_command(
            "CMD|BUZZER|LATE_REMINDER"
        )

        await self._send_hardware_command(
            f"CMD|LCD|Dose is late|{slot.title()}"
        )

        patient_message = (
            f"⏰ Your {slot.title()} dose is "
            f"{elapsed_minutes} minutes late and has "
            "not yet been confirmed."
        )

        await self.patient_sender(patient_message)

        caregiver_key = (
            f"{now.date()}:{slot}:caregiver-late"
        )

        if caregiver_key in self._sent:
            return

        self._sent.add(caregiver_key)

        caregiver_message = (
            f"⚠️ The patient's {slot.title()} dose is "
            f"{elapsed_minutes} minutes late and has "
            "not yet been confirmed."
        )

        await self.caregiver_sender(
            caregiver_message
        )

        create_alert(
            session_id=session_id,
            alert_type="LATE_DOSE",
            severity="WARNING",
            message=caregiver_message,
        )

        print(
            "[SCHEDULER] Late-dose escalation sent for "
            f"{slot}."
        )

    async def _send_hardware_command(
        self,
        command: str,
    ) -> bool:
        try:
            success = await self.command_sender(command)

        except Exception as exc:
            print(
                "[SCHEDULER HARDWARE ERROR] "
                f"{command}: {type(exc).__name__}: {exc}"
            )
            return False

        if not success:
            print(
                "[SCHEDULER HARDWARE WARNING] "
                f"Command was not sent: {command}"
            )

        return success

    def _get_repeat_minutes(self) -> int:
        raw_value = get_setting(
            "reminder_repeat_minutes",
            "10",
        )

        try:
            repeat_minutes = int(raw_value or "10")

        except (TypeError, ValueError):
            repeat_minutes = 10

        return max(repeat_minutes, 1)

    def _clean_old_tracking_keys(
        self,
        current_date: date,
    ) -> None:
        if self._last_cleanup_date == current_date:
            return

        current_prefix = f"{current_date}:"

        self._sent = {
            key
            for key in self._sent
            if key.startswith(current_prefix)
        }

        self._last_cleanup_date = current_date