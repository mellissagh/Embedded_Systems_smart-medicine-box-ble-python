from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from database import (
    acknowledge_event,
    fetch_recent_events,
    fetch_today_events,
    fetch_unacknowledged_warnings,
    get_system_status,
)


load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CAREGIVER_CHAT_ID_RAW = os.getenv(
    "TELEGRAM_CAREGIVER_CHAT_ID",
    "",
).strip()


def get_caregiver_chat_id() -> int | None:
    if not CAREGIVER_CHAT_ID_RAW:
        return None

    try:
        return int(CAREGIVER_CHAT_ID_RAW)
    except ValueError:
        return None


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📊 Current status",
                    callback_data="status",
                ),
                InlineKeyboardButton(
                    "🕒 Today's doses",
                    callback_data="today",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⚠️ Active warnings",
                    callback_data="warnings",
                ),
                InlineKeyboardButton(
                    "📜 Recent events",
                    callback_data="recent",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔗 My chat ID",
                    callback_data="chat_id",
                ),
            ],
        ]
    )


def is_authorised(chat_id: int) -> bool:
    caregiver_chat_id = get_caregiver_chat_id()

    # During initial setup, allow access until the chat ID is configured.
    if caregiver_chat_id is None:
        return True

    return chat_id == caregiver_chat_id


async def reject_unauthorised(update: Update) -> bool:
    chat = update.effective_chat

    if chat is None:
        return True

    if is_authorised(chat.id):
        return False

    if update.effective_message is not None:
        await update.effective_message.reply_text(
            "This caregiver account is not authorised."
        )

    return True


async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context

    if await reject_unauthorised(update):
        return

    chat = update.effective_chat

    await update.effective_message.reply_text(
        "💊 Smart Medicine Box Caregiver\n\n"
        "Use the buttons below to check the patient's medicine-box status.\n\n"
        f"Your Telegram chat ID is: {chat.id}",
        reply_markup=main_menu(),
    )


async def chat_id_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context

    chat = update.effective_chat

    if chat is None:
        return

    await update.effective_message.reply_text(
        f"Your Telegram chat ID is:\n\n{chat.id}"
    )


def format_status() -> str:
    status = get_system_status()

    if not status:
        return "No medicine-box status is available yet."

    ble_text = (
        "Connected"
        if status.get("ble_connected")
        else "Disconnected"
    )

    return (
        "📊 Current medicine-box status\n\n"
        f"BLE: {ble_text}\n"
        f"Last seen: {status.get('last_seen') or 'Unknown'}\n"
        f"Expected dose: {status.get('expected_slot') or 'Unknown'}\n"
        f"Lid: {status.get('lid_state') or 'Unknown'}\n\n"
        f"Morning: {status.get('morning_state') or 'Unknown'}\n"
        f"Afternoon: {status.get('afternoon_state') or 'Unknown'}\n"
        f"Evening: {status.get('evening_state') or 'Unknown'}"
    )


def format_today() -> str:
    events = fetch_today_events()

    dose_results = [
        event
        for event in events
        if event.get("category") == "RESULT"
    ]

    if not dose_results:
        return "🕒 No dose results have been recorded today."

    lines = ["🕒 Today's dose events", ""]

    for event in dose_results[-10:]:
        lines.append(
            f"{event['timestamp']} — "
            f"{event.get('result_type') or 'UNKNOWN'}"
        )

    return "\n".join(lines)


def format_recent() -> str:
    events = fetch_recent_events(limit=10)

    if not events:
        return "📜 No events have been recorded."

    lines = ["📜 Recent medicine-box events", ""]

    for event in events:
        lines.append(
            f"#{event['id']} | {event['timestamp']}\n"
            f"{event['raw_message']}"
        )

    return "\n\n".join(lines)


def format_warnings() -> tuple[str, InlineKeyboardMarkup | None]:
    warnings = fetch_unacknowledged_warnings()

    if not warnings:
        return (
            "✅ There are no unacknowledged warnings.",
            None,
        )

    lines = ["⚠️ Unacknowledged warnings", ""]
    keyboard: list[list[InlineKeyboardButton]] = []

    for event in warnings[:10]:
        lines.append(
            f"#{event['id']} — {event['timestamp']}\n"
            f"{event.get('result_type') or event['raw_message']}"
        )

        keyboard.append(
            [
                InlineKeyboardButton(
                    f"Acknowledge #{event['id']}",
                    callback_data=f"ack:{event['id']}",
                )
            ]
        )

    return (
        "\n\n".join(lines),
        InlineKeyboardMarkup(keyboard),
    )


async def menu_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context

    query = update.callback_query

    if query is None:
        return

    await query.answer()

    chat = query.message.chat if query.message else None

    if chat is None or not is_authorised(chat.id):
        await query.edit_message_text(
            "This caregiver account is not authorised."
        )
        return

    action = query.data or ""

    if action == "status":
        await query.edit_message_text(
            format_status(),
            reply_markup=main_menu(),
        )

    elif action == "today":
        await query.edit_message_text(
            format_today(),
            reply_markup=main_menu(),
        )

    elif action == "recent":
        await query.edit_message_text(
            format_recent(),
            reply_markup=main_menu(),
        )

    elif action == "warnings":
        text, keyboard = format_warnings()

        await query.edit_message_text(
            text,
            reply_markup=keyboard or main_menu(),
        )

    elif action == "chat_id":
        await query.edit_message_text(
            f"Your Telegram chat ID is:\n\n{chat.id}",
            reply_markup=main_menu(),
        )

    elif action.startswith("ack:"):
        event_id_text = action.split(":", 1)[1]

        try:
            event_id = int(event_id_text)
        except ValueError:
            await query.edit_message_text(
                "Invalid event ID.",
                reply_markup=main_menu(),
            )
            return

        success = acknowledge_event(event_id)

        await query.edit_message_text(
            (
                f"✅ Warning #{event_id} acknowledged."
                if success
                else f"Warning #{event_id} was not found."
            ),
            reply_markup=main_menu(),
        )


def build_telegram_application() -> Application:
    if not BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing from the .env file."
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start_command)
    )

    application.add_handler(
        CommandHandler("chatid", chat_id_command)
    )

    application.add_handler(
        CallbackQueryHandler(menu_callback)
    )

    return application


async def send_caregiver_message(
    application: Application,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    chat_id = get_caregiver_chat_id()

    if chat_id is None:
        print(
            "[TELEGRAM] Caregiver chat ID not configured. "
            f"Message not sent: {text}"
        )
        return

    await application.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
    )


async def run_bot_standalone() -> None:
    application = build_telegram_application()

    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    print("Telegram caregiver bot is running.")
    print("Press Control+C to stop.")

    try:
        while True:
            await __import__("asyncio").sleep(1)
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    import asyncio

    try:
        asyncio.run(run_bot_standalone())
    except KeyboardInterrupt:
        print("\nTelegram bot stopped.")