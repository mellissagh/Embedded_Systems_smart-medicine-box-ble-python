from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from database import fetch_dose_sessions_between

DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
SUCCESS_STATUSES = {"COMPLETED", "COMPLETED_AFTER_CORRECTION"}
PROBLEM_STATUSES = {
    "MISSED",
    "WRONG_PILL_NOT_CORRECTED",
    "MULTIPLE_PILLS_NOT_CORRECTED",
}


@dataclass
class AdherenceReport:
    title: str
    start: datetime
    end: datetime
    sessions: list[dict[str, object]]

    def as_text(self) -> str:
        total = len(self.sessions)
        if total == 0:
            return (
                f"📊 {self.title}\n\n"
                "No dose sessions were recorded in this period."
            )

        status_counts = Counter(str(item.get("status") or "UNKNOWN") for item in self.sessions)
        completed = sum(status_counts[s] for s in SUCCESS_STATUSES)
        adherence = completed / total * 100
        corrected = status_counts["COMPLETED_AFTER_CORRECTION"]
        missed = status_counts["MISSED"]
        wrong = status_counts["WRONG_PILL_NOT_CORRECTED"]
        multiple = status_counts["MULTIPLE_PILLS_NOT_CORRECTED"]

        delays: list[float] = []
        delays_by_slot: dict[str, list[float]] = defaultdict(list)
        late_by_slot: Counter[str] = Counter()

        for session in self.sessions:
            scheduled = _parse(session.get("scheduled_for"))
            completed_at = _parse(session.get("completed_at"))
            if scheduled and completed_at and str(session.get("status")) in SUCCESS_STATUSES:
                delay = max(0.0, (completed_at - scheduled).total_seconds() / 60)
                delays.append(delay)
                slot = str(session.get("expected_slot") or "UNKNOWN")
                delays_by_slot[slot].append(delay)
                if delay >= 10:
                    late_by_slot[slot] += 1

        average_delay = sum(delays) / len(delays) if delays else 0.0
        most_problematic = _most_problematic_slot(self.sessions)

        lines = [
            f"📊 {self.title}",
            f"{self.start:%d %b %Y} – {self.end:%d %b %Y}",
            "",
            f"Adherence: {adherence:.1f}% ({completed}/{total})",
            f"Completed normally: {status_counts['COMPLETED']}",
            f"Completed after correction: {corrected}",
            f"Missed: {missed}",
            f"Wrong-pill unresolved: {wrong}",
            f"Multiple-pill unresolved: {multiple}",
            f"Average confirmed delay: {average_delay:.0f} minutes",
        ]

        if most_problematic:
            lines.append(f"Most problematic slot: {most_problematic.title()}")

        pattern = _late_pattern_text(late_by_slot, delays_by_slot)
        if pattern:
            lines.extend(["", "📈 Pattern detected", pattern])

        lines.extend(
            [
                "",
                "This report describes behaviour patterns only. "
                "Medication or dosage changes should be discussed with a qualified clinician.",
            ]
        )
        return "\n".join(lines)


def _parse(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), DATETIME_FORMAT)
    except ValueError:
        return None


def _most_problematic_slot(sessions: list[dict[str, object]]) -> str | None:
    scores: Counter[str] = Counter()
    for item in sessions:
        slot = str(item.get("expected_slot") or "UNKNOWN")
        status = str(item.get("status") or "UNKNOWN")
        if status in PROBLEM_STATUSES:
            scores[slot] += 3
        elif status == "COMPLETED_AFTER_CORRECTION":
            scores[slot] += 1
    return scores.most_common(1)[0][0] if scores else None


def _late_pattern_text(
    late_by_slot: Counter[str],
    delays_by_slot: dict[str, list[float]],
) -> str | None:
    if not late_by_slot:
        return None
    slot, count = late_by_slot.most_common(1)[0]
    if count < 2:
        return None
    average = sum(delays_by_slot[slot]) / len(delays_by_slot[slot])
    return (
        f"The {slot.title()} dose was confirmed at least 10 minutes late "
        f"on {count} occasions. Its average delay was {average:.0f} minutes. "
        "The caregiver may review whether the current reminder time fits the patient's routine."
    )


def build_weekly_report(now: datetime | None = None) -> AdherenceReport:
    current = now or datetime.now()
    start = (current - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = current.replace(hour=23, minute=59, second=59, microsecond=0)
    sessions = fetch_dose_sessions_between(
        start.strftime(DATETIME_FORMAT), end.strftime(DATETIME_FORMAT)
    )
    return AdherenceReport("Weekly adherence report", start, end, sessions)


def build_monthly_report(now: datetime | None = None) -> AdherenceReport:
    current = now or datetime.now()
    start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = current.replace(hour=23, minute=59, second=59, microsecond=0)
    sessions = fetch_dose_sessions_between(
        start.strftime(DATETIME_FORMAT), end.strftime(DATETIME_FORMAT)
    )
    return AdherenceReport("Monthly adherence report", start, end, sessions)
