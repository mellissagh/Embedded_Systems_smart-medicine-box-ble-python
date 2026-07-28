from __future__ import annotations

import asyncio

from ble_client import MedicineBoxBLEClient
from database import (
    create_alert,
    init_database,
    initialize_default_schedules,
    log_event,
    update_system_status,
)
from dose_session_manager import DoseSessionManager, SessionOutcome
from event_parser import parse_arduino_message
from scheduler import MedicationScheduler
from telegram_bot import TelegramBotService


class MedicineBoxApplication:
    def __init__(self) -> None:
        self.expected_slot: str | None = None
        self.pending_removed_slots: list[str] = []
        self.ble_client = MedicineBoxBLEClient(on_message=self.handle_ble_message)
        self.telegram = TelegramBotService(command_sender=self.ble_client.send_command)
        self.session_manager = DoseSessionManager(notification_callback=self.handle_session_outcome)
        self.scheduler = MedicationScheduler(
            manager=self.session_manager,
            command_sender=self.ble_client.send_command,
            patient_sender=self.telegram.send_to_patient,
            caregiver_sender=self.telegram.send_to_caregiver,
        )
        self.tasks: list[asyncio.Task[None]] = []

    def handle_session_outcome(self, outcome: SessionOutcome) -> None:
        print("\n" + "=" * 60)
        print(f"[DOSE SESSION] #{outcome.session_id} {outcome.status}")
        print(outcome.message)
        print("=" * 60 + "\n")
        asyncio.create_task(self.telegram.notify_session_outcome(outcome))
        if outcome.notify_caregiver:
            create_alert(
                session_id=outcome.session_id,
                alert_type=outcome.status,
                severity=outcome.severity,
                message=outcome.message,
            )

    async def handle_ble_message(self, raw_message: str) -> None:
        parsed = parse_arduino_message(raw_message)
        update_system_status(ble_connected=True, last_message=raw_message)

        if parsed.expected_slot is not None:
            self.expected_slot = parsed.expected_slot
            update_system_status(expected_slot=self.expected_slot, last_message=raw_message)

        if parsed.removed_slots is not None:
            self.pending_removed_slots = list(parsed.removed_slots)

        if parsed.event is None:
            return
        event = parsed.event
        event.expected_slot = self.expected_slot
        if event.removed_slots is None and self.pending_removed_slots and event.category in {"RESULT", "DETAIL"}:
            event.removed_slots = ",".join(self.pending_removed_slots)
        if parsed.should_log:
            log_event(event)
        update_system_status(
            last_message=raw_message,
            expected_slot=self.expected_slot,
            lid_state=parsed.lid_state,
            morning_state=parsed.morning_state,
            afternoon_state=parsed.afternoon_state,
            evening_state=parsed.evening_state,
        )
        self.session_manager.process_event(event)
        if event.category == "RESULT":
            self.pending_removed_slots = []

    async def expiration_monitor(self) -> None:
        while True:
            self.session_manager.check_expired_sessions()
            await asyncio.sleep(5)

    async def run(self) -> None:
        init_database()
        initialize_default_schedules()
        update_system_status(ble_connected=False)
        await self.telegram.start()
        self.tasks = [
            asyncio.create_task(self.ble_client.run_forever()),
            asyncio.create_task(self.expiration_monitor()),
            asyncio.create_task(self.scheduler.run_forever()),
        ]
        print("Smart Medicine Box service is running.")
        try:
            await asyncio.gather(*self.tasks)
        finally:
            self.scheduler.stop()
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            update_system_status(ble_connected=False)
            await self.ble_client.stop()
            await self.telegram.stop()


async def main() -> None:
    await MedicineBoxApplication().run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSmart Medicine Box service stopped.")
