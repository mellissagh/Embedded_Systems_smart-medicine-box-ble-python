from __future__ import annotations

import re
from dataclasses import dataclass

from database import MedicineEvent


SLOTS = {
    "MORNING",
    "AFTERNOON",
    "EVENING",
}

VALID_STATES = {
    "PRESENT",
    "ABSENT",
}

VALID_LID_STATES = {
    "OPEN",
    "CLOSED",
}


@dataclass
class ParsedMessage:
    event: MedicineEvent | None = None

    expected_slot: str | None = None
    removed_slots: list[str] | None = None

    lid_state: str | None = None

    morning_state: str | None = None
    afternoon_state: str | None = None
    evening_state: str | None = None

    should_log: bool = False
    should_alert_caregiver: bool = False


def parse_key_value_tokens(
    tokens: list[str],
) -> dict[str, str]:
    """
    Parse structured tokens such as:

        LID=CLOSED
        MORNING=PRESENT
        AFTERNOON=ABSENT
    """
    values: dict[str, str] = {}

    for token in tokens:
        if "=" not in token:
            continue

        key, value = token.split("=", 1)

        values[key.strip().upper()] = value.strip().upper()

    return values


def parse_slots(tokens: list[str]) -> list[str]:
    """
    Return valid compartment names from a token list.
    """
    result: list[str] = []

    for token in tokens:
        clean = token.strip().upper()

        if clean in SLOTS:
            result.append(clean)

    return result


def normalise_compartment_state(
    value: str | None,
) -> str | None:
    """
    Return PRESENT or ABSENT only.
    """
    if value is None:
        return None

    clean = value.strip().upper()

    if clean in VALID_STATES:
        return clean

    return None


def normalise_lid_state(
    value: str | None,
) -> str | None:
    """
    Return OPEN or CLOSED only.
    """
    if value is None:
        return None

    clean = value.strip().upper()

    if clean in VALID_LID_STATES:
        return clean

    return None


def parse_readable_state_message(
    clean_message: str,
) -> ParsedMessage | None:
    """
    Parse readable Arduino sensor lines such as:

        Lid: CLOSED |
        Morning: 407 PRESENT |
        Afternoon: 32 ABSENT |
        Evening: 50 PRESENT

    Numeric sensor readings are preserved in raw_message but the
    resulting event stores only PRESENT/ABSENT states.

    Partial or damaged lines are ignored.
    """
    if not clean_message.upper().startswith("LID:"):
        return None

    lid_match = re.search(
        r"\bLid:\s*(OPEN|CLOSED)\b",
        clean_message,
        flags=re.IGNORECASE,
    )

    morning_match = re.search(
        r"\bMorning:\s*-?\d+\s+(PRESENT|ABSENT)\b",
        clean_message,
        flags=re.IGNORECASE,
    )

    afternoon_match = re.search(
        r"\bAfternoon:\s*-?\d+\s+(PRESENT|ABSENT)\b",
        clean_message,
        flags=re.IGNORECASE,
    )

    evening_match = re.search(
        r"\bEvening:\s*-?\d+\s+(PRESENT|ABSENT)\b",
        clean_message,
        flags=re.IGNORECASE,
    )

    # A readable state line is useful only when complete.
    if not all(
        (
            lid_match,
            morning_match,
            afternoon_match,
            evening_match,
        )
    ):
        return ParsedMessage()

    lid_state = normalise_lid_state(
        lid_match.group(1)
    )

    morning_state = normalise_compartment_state(
        morning_match.group(1)
    )

    afternoon_state = normalise_compartment_state(
        afternoon_match.group(1)
    )

    evening_state = normalise_compartment_state(
        evening_match.group(1)
    )

    event = MedicineEvent(
        raw_message=clean_message,
        category="STATE",
        result_type="STATE",
        lid_state=lid_state,
        morning_state=morning_state,
        afternoon_state=afternoon_state,
        evening_state=evening_state,
        severity="INFO",
    )

    return ParsedMessage(
        event=event,
        lid_state=lid_state,
        morning_state=morning_state,
        afternoon_state=afternoon_state,
        evening_state=evening_state,
        should_log=True,
    )


def parse_arduino_message(
    raw_message: str,
) -> ParsedMessage:
    """
    Convert one complete Arduino BLE message into structured data.

    Supports both:
    - structured pipe messages
    - readable sensor-state messages
    """
    clean_message = raw_message.strip()

    if not clean_message:
        return ParsedMessage()

    # --------------------------------------------------------
    # Readable state format:
    #
    # Lid: CLOSED | Morning: 407 PRESENT |
    # Afternoon: 32 ABSENT | Evening: 50 PRESENT
    # --------------------------------------------------------

    readable_state = parse_readable_state_message(
        clean_message
    )

    if readable_state is not None:
        return readable_state

    tokens = [
        token.strip()
        for token in clean_message.split("|")
    ]

    category = tokens[0].upper()

    # --------------------------------------------------------
    # SYSTEM messages
    # --------------------------------------------------------

    if category == "SYSTEM":
        result_type = (
            tokens[1].upper()
            if len(tokens) > 1
            else "UNKNOWN"
        )

        event = MedicineEvent(
            raw_message=clean_message,
            category="SYSTEM",
            result_type=result_type,
            severity="INFO",
        )

        return ParsedMessage(
            event=event,
            should_log=True,
        )

    # --------------------------------------------------------
    # EXPECTED|AFTERNOON
    # --------------------------------------------------------

    if category == "EXPECTED":
        expected_slot = (
            tokens[1].upper()
            if len(tokens) > 1
            else None
        )

        if expected_slot not in SLOTS:
            expected_slot = None

        return ParsedMessage(
            expected_slot=expected_slot,
        )

    # --------------------------------------------------------
    # EVENT|LID_OPEN
    # EVENT|LID_CLOSED
    # --------------------------------------------------------

    if category == "EVENT":
        result_type = (
            tokens[1].upper()
            if len(tokens) > 1
            else "UNKNOWN"
        )

        lid_state: str | None = None

        if result_type == "LID_OPEN":
            lid_state = "OPEN"

        elif result_type == "LID_CLOSED":
            lid_state = "CLOSED"

        event = MedicineEvent(
            raw_message=clean_message,
            category="EVENT",
            result_type=result_type,
            lid_state=lid_state,
            severity="INFO",
        )

        return ParsedMessage(
            event=event,
            lid_state=lid_state,
            should_log=True,
        )

    # --------------------------------------------------------
    # STATE|LID=CLOSED|MORNING=PRESENT|...
    # SNAPSHOT|MORNING=PRESENT|...
    # --------------------------------------------------------

    if category in {"STATE", "SNAPSHOT"}:
        values = parse_key_value_tokens(tokens[1:])

        lid_state = normalise_lid_state(
            values.get("LID")
        )

        morning_state = normalise_compartment_state(
            values.get("MORNING")
        )

        afternoon_state = normalise_compartment_state(
            values.get("AFTERNOON")
        )

        evening_state = normalise_compartment_state(
            values.get("EVENING")
        )

        # STATE must include all compartment states.
        if category == "STATE":
            if (
                lid_state is None
                or morning_state is None
                or afternoon_state is None
                or evening_state is None
            ):
                return ParsedMessage()

        # SNAPSHOT does not require a lid state.
        if category == "SNAPSHOT":
            if (
                morning_state is None
                or afternoon_state is None
                or evening_state is None
            ):
                return ParsedMessage()

        event = MedicineEvent(
            raw_message=clean_message,
            category=category,
            result_type=category,
            lid_state=lid_state,
            morning_state=morning_state,
            afternoon_state=afternoon_state,
            evening_state=evening_state,
            severity="INFO",
        )

        return ParsedMessage(
            event=event,
            lid_state=lid_state,
            morning_state=morning_state,
            afternoon_state=afternoon_state,
            evening_state=evening_state,
            should_log=True,
        )

    # --------------------------------------------------------
    # REMOVED|MORNING|AFTERNOON|
    # --------------------------------------------------------

    if category == "REMOVED":
        removed_slots = parse_slots(tokens[1:])

        return ParsedMessage(
            removed_slots=removed_slots,
        )

    # --------------------------------------------------------
    # RESULT messages
    # --------------------------------------------------------

    if category == "RESULT":
        severity = (
            tokens[1].upper()
            if len(tokens) > 1
            else "INFO"
        )

        result_type = (
            tokens[2].upper()
            if len(tokens) > 2
            else "UNKNOWN"
        )

        removed_slots: list[str] = []

        if result_type in {
            "CORRECT_PILL_TAKEN",
            "WRONG_PILL_TAKEN",
        }:
            removed_slots = parse_slots(tokens[3:])

        event = MedicineEvent(
            raw_message=clean_message,
            category="RESULT",
            result_type=result_type,
            removed_slots=(
                ",".join(removed_slots)
                if removed_slots
                else None
            ),
            severity=severity,
        )

        should_alert = severity in {
            "WARNING",
            "CRITICAL",
        }

        return ParsedMessage(
            event=event,
            removed_slots=removed_slots,
            should_log=True,
            should_alert_caregiver=should_alert,
        )

    # --------------------------------------------------------
    # Other multi-line result details
    # --------------------------------------------------------

    if category in {
        "EXPECTED_PILL_INCLUDED",
        "EXPECTED_PILL_NOT_TAKEN",
        "EXTRA_PILLS",
    }:
        detail_slots = parse_slots(tokens[1:])

        event = MedicineEvent(
            raw_message=clean_message,
            category="DETAIL",
            result_type=category,
            removed_slots=(
                ",".join(detail_slots)
                if detail_slots
                else None
            ),
            severity=(
                "INFO"
                if category == "EXPECTED_PILL_INCLUDED"
                else "WARNING"
            ),
        )

        return ParsedMessage(
            event=event,
            removed_slots=detail_slots,
            should_log=True,
        )

    # --------------------------------------------------------
    # Ignore damaged, debug, or partial messages
    # --------------------------------------------------------

    return ParsedMessage()


if __name__ == "__main__":
    sample_messages = [
        "SYSTEM|MEDICINE_BOX_READY",
        "EXPECTED|AFTERNOON",
        "EVENT|LID_OPEN",
        (
            "STATE|LID=CLOSED|MORNING=PRESENT|"
            "AFTERNOON=ABSENT|EVENING=PRESENT"
        ),
        (
            "Lid: CLOSED | Morning: 407 PRESENT | "
            "Afternoon: 32 ABSENT | Evening: 50 PRESENT"
        ),
        (
            "RESULT|SUCCESS|CORRECT_PILL_TAKEN|"
            "AFTERNOON"
        ),
        (
            "RESULT|WARNING|WRONG_PILL_TAKEN|"
            "MORNING"
        ),
        "RESULT|WARNING|NO_PILL_TAKEN",
        "RESULT|WARNING|2_PILLS_TAKEN",
        "REMOVED|MORNING|AFTERNOON|",
        "noon: 75 PRESENT | Evening: 50 PRESENT",
    ]

    for message in sample_messages:
        parsed = parse_arduino_message(message)

        print("\nRaw:")
        print(message)

        print("Parsed:")
        print(parsed)