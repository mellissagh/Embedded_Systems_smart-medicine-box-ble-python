from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from database import (
    MedicineEvent,
    add_dose_session_event,
    create_dose_session,
    get_dose_session,
    get_medication_schedules,
    get_schedule_by_id,
    get_schedule_by_slot,
    get_session_for_slot_and_date,
    get_system_status,
    update_dose_session,
)

DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
SLOTS = ("MORNING", "AFTERNOON", "EVENING")
ACTIVE_STATUSES = {"WAITING_FOR_DOSE", "IN_PROGRESS", "NEEDS_CORRECTION"}
FINAL_STATUSES = {
    "COMPLETED",
    "COMPLETED_AFTER_CORRECTION",
    "MISSED",
    "WRONG_PILL_NOT_CORRECTED",
    "MULTIPLE_PILLS_NOT_CORRECTED",
}


@dataclass
class SessionOutcome:
    session_id: int
    status: str
    message: str
    notify_caregiver: bool = False
    severity: str = "INFO"
    final: bool = False


class DoseSessionManager:
    def __init__(
        self,
        notification_callback: Callable[[SessionOutcome], None] | None = None,
    ) -> None:
        self.notification_callback = notification_callback
        self.active_session_id: int | None = None
        self.lid_was_opened = False
        self.awaiting_closed_state = False
        self.current_closure_evaluated = False
        self.latest_states: dict[str, str | None] = {
            "MORNING": None,
            "AFTERNOON": None,
            "EVENING": None,
        }

    def process_event(
    self,
    event: MedicineEvent,
    *,
    now: datetime | None = None,
    schedule_id: int | None = None,
) -> SessionOutcome | None:
        """
        Process one Arduino or scheduler event.

        schedule_id is supplied by MedicationScheduler so that the
        session is created for the exact scheduled dose rather than
        guessed from the current clock time.
        """
        current_time = now or datetime.now()

        self._update_latest_states(event)
        self.check_expired_sessions(now=current_time)

        if schedule_id is not None:
            session = self._get_or_create_session_for_schedule(
                schedule_id=schedule_id,
                now=current_time,
            )
        else:
            session = self._get_active_or_current_session(
                now=current_time,
            )

        if session is None:
            return None

        self.active_session_id = int(session["id"])

        if event.category == "EVENT":
            return self._handle_lid_event(
                session=session,
                event=event,
                now=current_time,
            )

        if event.category in {"STATE", "SNAPSHOT"}:
            return self._handle_state_event(
                session=session,
                event=event,
                now=current_time,
            )

        if event.category == "RESULT":
            return self._handle_result_event(
                session=session,
                event=event,
            )

        if event.category == "DETAIL":
            self._record_event(
                session_id=int(session["id"]),
                event_type=event.result_type or "DETAIL",
                event=event,
                details="Additional Arduino event information.",
            )

        return None

    def check_expired_sessions(
        self,
        *,
        now: datetime | None = None,
    ) -> list[SessionOutcome]:
        current_time = now or datetime.now()
        outcomes: list[SessionOutcome] = []

        for schedule in get_medication_schedules():
            if not bool(schedule["enabled"]):
                continue

            session = get_session_for_slot_and_date(
                str(schedule["slot"]),
                current_time.strftime("%Y-%m-%d"),
            )
            if session is None or str(session["status"]) not in ACTIVE_STATUSES:
                continue

            window_end = self._parse_datetime(str(session["window_end"]))
            correction_deadline = None
            if session["correction_deadline"]:
                correction_deadline = self._parse_datetime(str(session["correction_deadline"]))

            deadline = window_end if correction_deadline is None else min(window_end, correction_deadline)
            if current_time < deadline:
                continue

            outcome = self._finalise_unresolved_session(session=session, now=current_time)
            outcomes.append(outcome)
            self._emit(outcome)

        return outcomes

    def _get_active_or_current_session(
        self,
        *,
        now: datetime,
    ) -> dict[str, object] | None:
        """
        Return the already active session for physical Arduino events.

        If no active session exists, fall back to the single schedule
        whose window contains the current time.
        """
        if self.active_session_id is not None:
            active = get_dose_session(
                self.active_session_id
            )

            if (
                active is not None
                and str(active["status"]) in ACTIVE_STATUSES
            ):
                return active

            self.active_session_id = None

        schedule = self._find_schedule_for_time(now)

        if schedule is None:
            return None

        return self._get_or_create_session_for_schedule(
            schedule_id=int(schedule["id"]),
            now=now,
        )


    def _get_or_create_session_for_schedule(
        self,
        *,
        schedule_id: int,
        now: datetime,
    ) -> dict[str, object] | None:
        """
        Create or retrieve a session for one exact database schedule.
        """
        schedule = get_schedule_by_id(schedule_id)

        if schedule is None:
            print(
                "[SESSION MANAGER WARNING] "
                f"Schedule #{schedule_id} does not exist."
            )
            return None

        if not bool(schedule["enabled"]):
            return None

        slot = str(schedule["slot"]).strip().upper()

        scheduled_for, window_start, window_end = (
            self._build_window(schedule, now)
        )

        if not window_start <= now <= window_end:
            return None

        existing = get_session_for_slot_and_date(
            slot,
            now.strftime("%Y-%m-%d"),
        )

        if existing is not None:
            self.active_session_id = int(existing["id"])
            return existing

        baseline = self._get_baseline_states()

        session_id = create_dose_session(
            schedule_id=int(schedule["id"]),
            expected_slot=slot,
            scheduled_for=self._format_datetime(
                scheduled_for
            ),
            window_start=self._format_datetime(
                window_start
            ),
            window_end=self._format_datetime(
                window_end
            ),
            baseline_morning=baseline["MORNING"],
            baseline_afternoon=baseline["AFTERNOON"],
            baseline_evening=baseline["EVENING"],
            started_at=self._format_datetime(now),
        )

        add_dose_session_event(
            session_id,
            event_type="SESSION_STARTED",
            details=(
                f"{slot.title()} dose window started. "
                f"Window ends at "
                f"{window_end.strftime('%H:%M')}."
            ),
            morning_state=baseline["MORNING"],
            afternoon_state=baseline["AFTERNOON"],
            evening_state=baseline["EVENING"],
            timestamp=self._format_datetime(now),
        )

        self.active_session_id = session_id
        self.lid_was_opened = False
        self.awaiting_closed_state = False
        self.current_closure_evaluated = False

        return get_dose_session(session_id)

    @staticmethod
    def _build_window(
        schedule: dict[str, object],
        now: datetime,
    ) -> tuple[datetime, datetime, datetime]:
        """
        Build today's medication window for one schedule.

        The scheduled time is also the beginning of the allowed
        medication window.
        """
        dose_time = datetime.strptime(
            str(schedule["dose_time"]),
            "%H:%M",
        ).time()

        scheduled_for = datetime.combine(
            now.date(),
            dose_time,
        )

        window_start = scheduled_for
        window_end = scheduled_for + timedelta(
            minutes=int(schedule["dose_window_minutes"])
        )

        return scheduled_for, window_start, window_end

    def _find_schedule_for_time(
        self,
        now: datetime,
    ) -> dict[str, object] | None:
        """
        Find the only enabled schedule active at this time.

        Overlapping active windows are rejected rather than silently
        selecting the wrong medication session.
        """
        matches: list[dict[str, object]] = []

        for schedule in get_medication_schedules():
            if not bool(schedule["enabled"]):
                continue

            _, window_start, window_end = self._build_window(
                schedule,
                now,
            )

            if window_start <= now <= window_end:
                matches.append(schedule)

        if not matches:
            return None

        if len(matches) > 1:
            slots = ", ".join(
                str(schedule["slot"])
                for schedule in matches
            )

            print(
                "[SESSION MANAGER ERROR] "
                "Overlapping active medication windows: "
                f"{slots}. Arduino event was not assigned."
            )

            return None

        return matches[0]

    def _handle_lid_event(
        self,
        *,
        session: dict[str, object],
        event: MedicineEvent,
        now: datetime,
    ) -> SessionOutcome | None:
        session_id = int(session["id"])
        event_type = event.result_type or "LID_EVENT"
        self._record_event(session_id=session_id, event_type=event_type, event=event)

        if event_type == "LID_OPEN":
            self.lid_was_opened = True
            self.awaiting_closed_state = False
            self.current_closure_evaluated = False

            if str(session["status"]) == "WAITING_FOR_DOSE":
                update_dose_session(
                    session_id,
                    status="IN_PROGRESS",
                    summary_text=f"{str(session['expected_slot']).title()} dose is in progress.",
                )
                outcome = SessionOutcome(
                    session_id=session_id,
                    status="IN_PROGRESS",
                    message=f"{str(session['expected_slot']).title()} dose is now in progress.",
                )
                self._emit(outcome)
                return outcome

        elif event_type == "LID_CLOSED" and self.lid_was_opened:
            self.awaiting_closed_state = True
            self.current_closure_evaluated = False

        return None

    def _handle_state_event(
        self,
        *,
        session: dict[str, object],
        event: MedicineEvent,
        now: datetime,
    ) -> SessionOutcome | None:
        session_id = int(session["id"])
        self._save_current_states(session_id)

        if event.category == "SNAPSHOT" or event.lid_state != "CLOSED":
            return None
        if not self.lid_was_opened or not self.awaiting_closed_state or self.current_closure_evaluated:
            return None

        self._record_event(
            session_id=session_id,
            event_type="CLOSED_STATE_EVALUATED",
            event=event,
        )
        self.current_closure_evaluated = True
        self.awaiting_closed_state = False
        return self._evaluate_closed_box(session_id=session_id, now=now)

    def _handle_result_event(
        self,
        *,
        session: dict[str, object],
        event: MedicineEvent,
    ) -> None:
        self._record_event(
            session_id=int(session["id"]),
            event_type=f"ARDUINO_{event.result_type or 'UNKNOWN_RESULT'}",
            event=event,
            details=(
                "Arduino result stored as supporting evidence. "
                "Python CLOSED-state logic determines the final outcome."
            ),
        )
        return None

    def _evaluate_closed_box(self, *, session_id: int, now: datetime) -> SessionOutcome | None:
        session = get_dose_session(session_id)
        if session is None or str(session["status"]) in FINAL_STATUSES:
            return None

        expected_slot = str(session["expected_slot"])
        baseline = {
            "MORNING": session["baseline_morning"],
            "AFTERNOON": session["baseline_afternoon"],
            "EVENING": session["baseline_evening"],
        }
        current = dict(self.latest_states)
        if any(current[slot] is None for slot in SLOTS):
            return None

        removed = {
            slot
            for slot in SLOTS
            if baseline[slot] == "PRESENT" and current[slot] == "ABSENT"
        }
        expected_removed = expected_slot in removed
        extra_removed = removed - {expected_slot}
        previous_issue = session["provisional_issue"]

        if expected_removed and not extra_removed:
            return self._complete_successfully(
                session=session,
                now=now,
                corrected=bool(previous_issue or int(session["corrected"])),
            )

        if not removed:
            return self._mark_needs_correction(
                session=session,
                issue="NO_PILL",
                now=now,
                message=f"The {expected_slot.title()} dose is not complete yet. No medicine was confirmed.",
            )

        if not expected_removed and extra_removed:
            wrong_names = ", ".join(slot.title() for slot in sorted(extra_removed))
            return self._mark_needs_correction(
                session=session,
                issue="WRONG_PILL",
                now=now,
                message=(
                    f"The {wrong_names} compartment may have been used instead of "
                    f"{expected_slot.title()}. The mistake can still be corrected."
                ),
            )

        extra_names = ", ".join(slot.title() for slot in sorted(extra_removed))
        return self._mark_needs_correction(
            session=session,
            issue="MULTIPLE_PILLS",
            now=now,
            message=(
                f"The {expected_slot.title()} medicine and extra medicine from {extra_names} "
                "appear to be removed. Return the extra medicine during the correction window."
            ),
        )

    def _mark_needs_correction(
        self,
        *,
        session: dict[str, object],
        issue: str,
        now: datetime,
        message: str,
    ) -> SessionOutcome | None:
        session_id = int(session["id"])

        if str(session["status"]) == "NEEDS_CORRECTION" and str(session["provisional_issue"]) == issue:
            return None

        schedule = get_schedule_by_slot(str(session["expected_slot"]))
        correction_minutes = 5 if schedule is None else int(schedule["correction_window_minutes"])
        window_end = self._parse_datetime(str(session["window_end"]))

        if issue == "NO_PILL":
            # No-pill cases remain open until the medication window ends.
            correction_deadline = window_end
        else:
            # Wrong or multiple pills receive a shorter correction period.
            correction_deadline = min(
                now + timedelta(minutes=correction_minutes),
                window_end,
            )

        previous_issue = session["provisional_issue"]

        update_dose_session(
            session_id,
            status="NEEDS_CORRECTION",
            provisional_issue=issue,
            correction_deadline=self._format_datetime(correction_deadline),
            corrected=bool(int(session["corrected"])) or bool(previous_issue and str(previous_issue) != issue),
            current_morning=self.latest_states["MORNING"],
            current_afternoon=self.latest_states["AFTERNOON"],
            current_evening=self.latest_states["EVENING"],
            summary_text=message,
        )

        add_dose_session_event(
            session_id,
            event_type=f"NEEDS_CORRECTION_{issue}",
            details=message,
            morning_state=self.latest_states["MORNING"],
            afternoon_state=self.latest_states["AFTERNOON"],
            evening_state=self.latest_states["EVENING"],
            timestamp=self._format_datetime(now),
        )

        outcome = SessionOutcome(
            session_id=session_id,
            status="NEEDS_CORRECTION",
            message=message,
            severity="WARNING",
        )
        self._emit(outcome)
        return outcome

    def _complete_successfully(
        self,
        *,
        session: dict[str, object],
        now: datetime,
        corrected: bool,
    ) -> SessionOutcome:
        session_id = int(session["id"])
        slot = str(session["expected_slot"]).title()

        if corrected:
            status = "COMPLETED_AFTER_CORRECTION"
            message = f"✅ {slot} dose completed.\n\nAn earlier issue was corrected within the allowed time."
        else:
            status = "COMPLETED"
            message = f"✅ {slot} dose completed successfully at {now.strftime('%H:%M')}."

        update_dose_session(
            session_id,
            status=status,
            final_result=status,
            corrected=corrected,
            completed_at=self._format_datetime(now),
            current_morning=self.latest_states["MORNING"],
            current_afternoon=self.latest_states["AFTERNOON"],
            current_evening=self.latest_states["EVENING"],
            summary_text=message,
            clear_provisional_issue=True,
            clear_correction_deadline=True,
        )

        add_dose_session_event(
            session_id,
            event_type=status,
            details=message,
            morning_state=self.latest_states["MORNING"],
            afternoon_state=self.latest_states["AFTERNOON"],
            evening_state=self.latest_states["EVENING"],
            timestamp=self._format_datetime(now),
        )

        self.lid_was_opened = False
        self.awaiting_closed_state = False
        self.current_closure_evaluated = True

        outcome = SessionOutcome(
            session_id=session_id,
            status=status,
            message=message,
            notify_caregiver=True,
            final=True,
        )
        self._emit(outcome)
        return outcome

    def _finalise_unresolved_session(
        self,
        *,
        session: dict[str, object],
        now: datetime,
    ) -> SessionOutcome:
        session_id = int(session["id"])
        slot = str(session["expected_slot"]).title()
        issue = str(session["provisional_issue"] or "NO_PILL")

        if issue == "WRONG_PILL":
            status = "WRONG_PILL_NOT_CORRECTED"
            severity = "CRITICAL"
            message = f"🚨 {slot} dose needs attention.\n\nA different medicine appears to remain removed."
        elif issue == "MULTIPLE_PILLS":
            status = "MULTIPLE_PILLS_NOT_CORRECTED"
            severity = "CRITICAL"
            message = f"🚨 {slot} dose needs urgent attention.\n\nMore than one medicine appears to remain removed."
        else:
            status = "MISSED"
            severity = "WARNING"
            message = f"🔴 {slot} dose was not confirmed before the allowed medication window ended."

        update_dose_session(
            session_id,
            status=status,
            final_result=status,
            completed_at=self._format_datetime(now),
            summary_text=message,
            current_morning=self.latest_states["MORNING"],
            current_afternoon=self.latest_states["AFTERNOON"],
            current_evening=self.latest_states["EVENING"],
        )

        add_dose_session_event(
            session_id,
            event_type=status,
            details=message,
            morning_state=self.latest_states["MORNING"],
            afternoon_state=self.latest_states["AFTERNOON"],
            evening_state=self.latest_states["EVENING"],
            timestamp=self._format_datetime(now),
        )

        return SessionOutcome(
            session_id=session_id,
            status=status,
            message=message,
            notify_caregiver=True,
            severity=severity,
            final=True,
        )

    def _update_latest_states(self, event: MedicineEvent) -> None:
        if event.morning_state is not None:
            self.latest_states["MORNING"] = event.morning_state
        if event.afternoon_state is not None:
            self.latest_states["AFTERNOON"] = event.afternoon_state
        if event.evening_state is not None:
            self.latest_states["EVENING"] = event.evening_state

    def _get_baseline_states(self) -> dict[str, str | None]:
        baseline = dict(self.latest_states)
        status = get_system_status()
        if status is None:
            return baseline
        if baseline["MORNING"] is None:
            baseline["MORNING"] = status.get("morning_state")
        if baseline["AFTERNOON"] is None:
            baseline["AFTERNOON"] = status.get("afternoon_state")
        if baseline["EVENING"] is None:
            baseline["EVENING"] = status.get("evening_state")
        return baseline

    def _save_current_states(self, session_id: int) -> None:
        update_dose_session(
            session_id,
            current_morning=self.latest_states["MORNING"],
            current_afternoon=self.latest_states["AFTERNOON"],
            current_evening=self.latest_states["EVENING"],
        )

    def _record_event(
        self,
        *,
        session_id: int,
        event_type: str,
        event: MedicineEvent,
        details: str | None = None,
    ) -> None:
        add_dose_session_event(
            session_id,
            event_type=event_type,
            raw_message=event.raw_message,
            details=details,
            morning_state=event.morning_state,
            afternoon_state=event.afternoon_state,
            evening_state=event.evening_state,
            timestamp=event.timestamp,
        )

    def _emit(self, outcome: SessionOutcome) -> None:
        if self.notification_callback is not None:
            self.notification_callback(outcome)

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        return datetime.strptime(value, DATETIME_FORMAT)

    @staticmethod
    def _format_datetime(value: datetime) -> str:
        return value.strftime(DATETIME_FORMAT)