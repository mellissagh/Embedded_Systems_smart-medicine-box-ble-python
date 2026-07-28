from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta

from database import (
    MedicineEvent,
    create_alert,
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

            # Create or retrieve today's connected session before
            # the patient interacts with the physical box.
            self.manager.process_event(
                MedicineEvent(
                    raw_message="SYSTEM|SCHEDULE_TICK",
                    category="SYSTEM",
                    result_type="SCHEDULE_TICK",
                ),
                now=now,
                schedule_id=int(schedule["id"])
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

            self._sent.add(reminder_key)

            # Keep Arduino synchronised with Python's schedule.
            await self._send_hardware_command(
                f"CMD|EXPECTED|{slot}"
            )

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

        # Notify the caregiver only once per dose.
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