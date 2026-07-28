from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from database import (
    acknowledge_alert,
    add_audit_log,
    create_schedule_change_request,
    fetch_active_alerts,
    fetch_audit_log,
    fetch_pending_schedule_change_requests,
    fetch_today_dose_sessions,
    get_medication_schedules,
    get_schedule_by_slot,
    get_schedule_change_request,
    get_system_status,
    review_schedule_change_request,
    update_medication_schedule,
)
from reports import build_monthly_report, build_weekly_report

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CAREGIVER_CHAT_ID_RAW = os.getenv("TELEGRAM_CAREGIVER_CHAT_ID", "").strip()
PATIENT_CHAT_ID_RAW = os.getenv("TELEGRAM_PATIENT_CHAT_ID", "").strip()

CommandSender = Callable[[str], Awaitable[bool]]


def _parse_chat_id(raw: str) -> int | None:
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def get_caregiver_chat_id() -> int | None:
    return _parse_chat_id(CAREGIVER_CHAT_ID_RAW)


def get_patient_chat_id() -> int | None:
    return _parse_chat_id(PATIENT_CHAT_ID_RAW)


def role_for_chat(chat_id: int) -> str | None:
    if chat_id == get_caregiver_chat_id():
        return "CAREGIVER"
    if chat_id == get_patient_chat_id():
        return "PATIENT"
    return None


def caregiver_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Box status", callback_data="cg:status"),
                InlineKeyboardButton("🕒 Today's doses", callback_data="cg:today"),
            ],
            [
                InlineKeyboardButton("⚠️ Active alerts", callback_data="cg:alerts"),
                InlineKeyboardButton("⏰ Schedules", callback_data="cg:schedules"),
            ],
            [
                InlineKeyboardButton("🔔 Buzzer controls", callback_data="cg:buzzer"),
                InlineKeyboardButton("📝 Change requests", callback_data="cg:requests"),
            ],
            [
                InlineKeyboardButton("📅 Weekly report", callback_data="cg:weekly"),
                InlineKeyboardButton("📆 Monthly report", callback_data="cg:monthly"),
            ],
            [InlineKeyboardButton("📜 Audit history", callback_data="cg:audit")],
        ]
    )


def patient_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💊 My next dose", callback_data="pt:next"),
                InlineKeyboardButton("📊 Today's status", callback_data="pt:today"),
            ],
            [
                InlineKeyboardButton("🔔 Remind me again", callback_data="pt:remind"),
                InlineKeyboardButton("📝 Request time change", callback_data="pt:request"),
            ],
            [InlineKeyboardButton("📦 Box status", callback_data="pt:status")],
        ]
    )


def back_button(role: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Main menu", callback_data=f"{role}:menu")]]
    )


def schedules_keyboard(prefix: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                f"{_slot_icon(str(item['slot']))} {str(item['slot']).title()} — {item['dose_time']}",
                callback_data=f"{prefix}:slot:{item['slot']}",
            )
        ]
        for item in get_medication_schedules()
    ]
    rows.append([InlineKeyboardButton("⬅️ Main menu", callback_data=f"{prefix}:menu")])
    return InlineKeyboardMarkup(rows)


def _slot_icon(slot: str) -> str:
    return {"MORNING": "🌅", "AFTERNOON": "☀️", "EVENING": "🌙"}.get(slot, "💊")


def format_status() -> str:
    status = get_system_status()
    if not status:
        return "No medicine-box status is available yet."
    ble = "🟢 Connected" if status.get("ble_connected") else "🔴 Disconnected"
    return (
        "📦 Smart medicine-box status\n\n"
        f"BLE: {ble}\n"
        f"Last seen: {status.get('last_seen') or 'Unknown'}\n"
        f"Lid: {status.get('lid_state') or 'Unknown'}\n\n"
        f"🌅 Morning: {status.get('morning_state') or 'Unknown'}\n"
        f"☀️ Afternoon: {status.get('afternoon_state') or 'Unknown'}\n"
        f"🌙 Evening: {status.get('evening_state') or 'Unknown'}"
    )


def _status_label(status: str) -> str:
    labels = {
        "WAITING_FOR_DOSE": "⏳ Waiting",
        "IN_PROGRESS": "🔄 In progress",
        "NEEDS_CORRECTION": "⚠️ Needs correction",
        "COMPLETED": "✅ Completed",
        "COMPLETED_AFTER_CORRECTION": "✅ Corrected and completed",
        "MISSED": "🔴 Missed",
        "WRONG_PILL_NOT_CORRECTED": "🚨 Wrong pill unresolved",
        "MULTIPLE_PILLS_NOT_CORRECTED": "🚨 Multiple pills unresolved",
    }
    return labels.get(status, status.replace("_", " ").title())


def format_today_sessions() -> str:
    sessions = fetch_today_dose_sessions()
    if not sessions:
        schedules = get_medication_schedules()
        lines = ["🕒 Today's medication plan", ""]
        for item in schedules:
            enabled = "enabled" if item.get("enabled") else "disabled"
            lines.append(
                f"{_slot_icon(str(item['slot']))} {str(item['slot']).title()} "
                f"at {item['dose_time']} — {enabled}"
            )
        lines.append("\nNo dose activity has been recorded yet.")
        return "\n".join(lines)

    lines = ["🕒 Today's dose sessions", ""]
    for session in sessions:
        lines.append(
            f"{_slot_icon(str(session['expected_slot']))} "
            f"{str(session['expected_slot']).title()} "
            f"({str(session['scheduled_for'])[11:16]})\n"
            f"{_status_label(str(session['status']))}"
        )
        if session.get("summary_text"):
            lines.append(str(session["summary_text"]))
        lines.append("")
    return "\n".join(lines).strip()


def format_schedules() -> str:
    lines = ["⏰ Medication schedules", ""]
    for item in get_medication_schedules():
        state = "Enabled" if item.get("enabled") else "Disabled"
        lines.extend(
            [
                f"{_slot_icon(str(item['slot']))} {str(item['slot']).title()}",
                f"Time: {item['dose_time']}",
                f"Dose window: {item['dose_window_minutes']} minutes",
                f"Correction window: {item['correction_window_minutes']} minutes",
                f"State: {state}",
                "",
            ]
        )
    return "\n".join(lines).strip()


def format_alerts() -> tuple[str, InlineKeyboardMarkup | None]:
    alerts = fetch_active_alerts()
    if not alerts:
        return "✅ There are no active caregiver alerts.", None
    lines = ["⚠️ Active caregiver alerts", ""]
    buttons: list[list[InlineKeyboardButton]] = []
    for alert in alerts:
        lines.append(
            f"#{alert['id']} • {alert['severity']} • {alert['created_at']}\n"
            f"{alert['message']}"
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    f"Acknowledge #{alert['id']}",
                    callback_data=f"cg:ackalert:{alert['id']}",
                )
            ]
        )
    buttons.append([InlineKeyboardButton("⬅️ Main menu", callback_data="cg:menu")])
    return "\n\n".join(lines), InlineKeyboardMarkup(buttons)


def format_audit() -> str:
    rows = fetch_audit_log(limit=15)
    if not rows:
        return "📜 No configuration changes have been recorded."
    lines = ["📜 Recent configuration activity", ""]
    for row in rows:
        lines.append(
            f"{row['timestamp']} • {row['actor_role']}\n"
            f"{row['action']} — {row.get('target') or ''}\n"
            f"{row.get('old_value') or '—'} → {row.get('new_value') or '—'}"
        )
    return "\n\n".join(lines)


def next_dose_text() -> str:
    now = datetime.now()
    candidates: list[tuple[datetime, dict[str, object]]] = []
    for schedule in get_medication_schedules():
        if not schedule.get("enabled"):
            continue
        dose_time = datetime.strptime(str(schedule["dose_time"]), "%H:%M").time()
        moment = datetime.combine(now.date(), dose_time)
        if moment < now:
            from datetime import timedelta
            moment += timedelta(days=1)
        candidates.append((moment, schedule))
    if not candidates:
        return "No medication schedules are currently enabled."
    moment, schedule = min(candidates, key=lambda item: item[0])
    day_text = "today" if moment.date() == now.date() else "tomorrow"
    return (
        f"💊 Next dose\n\n"
        f"{_slot_icon(str(schedule['slot']))} {str(schedule['slot']).title()}\n"
        f"{day_text.title()} at {moment:%H:%M}\n"
        f"Allowed window: {schedule['dose_window_minutes']} minutes"
    )


class TelegramBotService:
    def __init__(self, command_sender: CommandSender | None = None) -> None:
        if not BOT_TOKEN:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is missing from .env.")
        self.command_sender = command_sender
        self.application = Application.builder().token(BOT_TOKEN).build()
        self._register_handlers()

    def _register_handlers(self) -> None:
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("chatid", self.chat_id_command))
        self.application.add_handler(CommandHandler("cancel", self.cancel_command))
        self.application.add_handler(CallbackQueryHandler(self.callback_handler))
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.text_handler)
        )

    async def start(self) -> None:
        await self.application.initialize()
        await self.application.start()
        if self.application.updater is None:
            raise RuntimeError("Telegram updater is unavailable.")
        await self.application.updater.start_polling(drop_pending_updates=True)
        print("Telegram bot is running.")

    async def stop(self) -> None:
        if self.application.updater is not None:
            await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        chat = update.effective_chat
        message = update.effective_message
        if chat is None or message is None:
            return
        role = role_for_chat(chat.id)
        if role == "CAREGIVER":
            await message.reply_text(
                "💊 Smart Medicine Box — Caregiver\n\n"
                "Manage schedules, alerts, reminders and adherence reports.",
                reply_markup=caregiver_menu(),
            )
        elif role == "PATIENT":
            await message.reply_text(
                "💊 Smart Medicine Box — Patient\n\n"
                "Check your next dose, today's progress or request help.",
                reply_markup=patient_menu(),
            )
        else:
            await message.reply_text(
                "This Telegram account is not authorised yet.\n\n"
                f"Your chat ID is: {chat.id}\n\n"
                "Add it to TELEGRAM_CAREGIVER_CHAT_ID or "
                "TELEGRAM_PATIENT_CHAT_ID in .env, then restart the service."
            )

    async def chat_id_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        if update.effective_chat and update.effective_message:
            await update.effective_message.reply_text(
                f"Your Telegram chat ID is:\n{update.effective_chat.id}"
            )

    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data.clear()
        if update.effective_chat and update.effective_message:
            role = role_for_chat(update.effective_chat.id)
            await update.effective_message.reply_text(
                "Cancelled.",
                reply_markup=caregiver_menu() if role == "CAREGIVER" else patient_menu(),
            )

    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None or query.message is None:
            return
        await query.answer()
        role = role_for_chat(query.message.chat.id)
        if role is None:
            await query.edit_message_text("This Telegram account is not authorised.")
            return
        data = query.data or ""
        if data == "cg:menu":
            await query.edit_message_text("Caregiver menu", reply_markup=caregiver_menu())
            return
        if data == "pt:menu":
            await query.edit_message_text("Patient menu", reply_markup=patient_menu())
            return
        if role == "CAREGIVER":
            await self._caregiver_callback(query, context, data)
        else:
            await self._patient_callback(query, context, data)

    async def _caregiver_callback(self, query: Any, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
        chat_id = query.message.chat.id
        if data == "cg:status":
            await query.edit_message_text(format_status(), reply_markup=back_button("cg"))
        elif data == "cg:today":
            await query.edit_message_text(format_today_sessions(), reply_markup=back_button("cg"))
        elif data == "cg:alerts":
            text, keyboard = format_alerts()
            await query.edit_message_text(text, reply_markup=keyboard or back_button("cg"))
        elif data == "cg:schedules":
            await query.edit_message_text(format_schedules(), reply_markup=schedules_keyboard("cg"))
        elif data.startswith("cg:slot:"):
            slot = data.split(":", 2)[2]
            schedule = get_schedule_by_slot(slot)
            if schedule is None:
                await query.edit_message_text("Schedule not found.", reply_markup=back_button("cg"))
                return
            keyboard = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🕒 Change time", callback_data=f"cg:edit:time:{slot}")],
                    [InlineKeyboardButton("⏱ Change dose window", callback_data=f"cg:edit:window:{slot}")],
                    [InlineKeyboardButton("🛠 Change correction window", callback_data=f"cg:edit:correction:{slot}")],
                    [InlineKeyboardButton("⏯ Enable / disable", callback_data=f"cg:toggle:{slot}")],
                    [InlineKeyboardButton("⬅️ Schedules", callback_data="cg:schedules")],
                ]
            )
            await query.edit_message_text(
                f"{_slot_icon(slot)} {slot.title()} schedule\n\n"
                f"Time: {schedule['dose_time']}\n"
                f"Dose window: {schedule['dose_window_minutes']} minutes\n"
                f"Correction window: {schedule['correction_window_minutes']} minutes\n"
                f"Enabled: {'Yes' if schedule['enabled'] else 'No'}",
                reply_markup=keyboard,
            )
        elif data.startswith("cg:edit:"):
            _, _, field, slot = data.split(":", 3)
            context.user_data["pending"] = {"kind": "schedule_edit", "field": field, "slot": slot}
            prompt = {
                "time": "Send the new time in HH:MM format, for example 13:30.",
                "window": "Send the new dose window in minutes (1–720).",
                "correction": "Send the new correction window in minutes (1–120).",
            }[field]
            await query.edit_message_text(prompt + "\n\nSend /cancel to stop.")
        elif data.startswith("cg:toggle:"):
            slot = data.split(":", 2)[2]
            schedule = get_schedule_by_slot(slot)
            if schedule is None:
                return
            old = bool(schedule["enabled"])
            update_medication_schedule(slot, enabled=not old)
            add_audit_log(
                actor_chat_id=chat_id,
                actor_role="CAREGIVER",
                action="TOGGLE_SCHEDULE",
                target=slot,
                old_value=str(old),
                new_value=str(not old),
            )
            await query.edit_message_text(
                f"✅ {slot.title()} schedule is now {'enabled' if not old else 'disabled'}.",
                reply_markup=schedules_keyboard("cg"),
            )
        elif data == "cg:buzzer":
            keyboard = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🔔 Dose reminder", callback_data="cg:buzz:DOSE_REMINDER")],
                    [InlineKeyboardButton("⏰ Late reminder", callback_data="cg:buzz:LATE_REMINDER")],
                    [InlineKeyboardButton("🔇 Stop buzzer", callback_data="cg:buzz:STOP")],
                    [InlineKeyboardButton("⬅️ Main menu", callback_data="cg:menu")],
                ]
            )
            await query.edit_message_text(
                "🔔 Buzzer controls\n\nCommands are sent to the medicine box over BLE.",
                reply_markup=keyboard,
            )
        elif data.startswith("cg:buzz:"):
            mode = data.split(":", 2)[2]
            success = await self._send_hardware_command(f"CMD|BUZZER|{mode}")
            add_audit_log(
                actor_chat_id=chat_id,
                actor_role="CAREGIVER",
                action="BUZZER_COMMAND",
                target=mode,
                new_value="SENT" if success else "BOX_OFFLINE",
            )
            await query.edit_message_text(
                "✅ Command sent to the box." if success else "🔴 The box is offline, so the command could not be sent.",
                reply_markup=back_button("cg"),
            )
        elif data == "cg:weekly":
            await query.edit_message_text(build_weekly_report().as_text(), reply_markup=back_button("cg"))
        elif data == "cg:monthly":
            await query.edit_message_text(build_monthly_report().as_text(), reply_markup=back_button("cg"))
        elif data == "cg:audit":
            await query.edit_message_text(format_audit(), reply_markup=back_button("cg"))
        elif data == "cg:requests":
            requests = fetch_pending_schedule_change_requests()
            if not requests:
                await query.edit_message_text("✅ There are no pending schedule-change requests.", reply_markup=back_button("cg"))
                return
            lines = ["📝 Pending patient requests", ""]
            buttons: list[list[InlineKeyboardButton]] = []
            for item in requests:
                lines.append(
                    f"#{item['id']} • {str(item['slot']).title()}\n"
                    f"{item['old_time']} → {item['requested_time']}"
                )
                buttons.append(
                    [
                        InlineKeyboardButton("✅ Approve", callback_data=f"cg:req:approve:{item['id']}"),
                        InlineKeyboardButton("❌ Reject", callback_data=f"cg:req:reject:{item['id']}"),
                    ]
                )
            buttons.append([InlineKeyboardButton("⬅️ Main menu", callback_data="cg:menu")])
            await query.edit_message_text("\n\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))
        elif data.startswith("cg:req:"):
            _, _, decision, request_id_text = data.split(":", 3)
            request = get_schedule_change_request(int(request_id_text))
            if request is None or request["status"] != "PENDING":
                await query.edit_message_text("That request is no longer pending.", reply_markup=back_button("cg"))
                return
            approved = decision == "approve"
            if approved:
                update_medication_schedule(str(request["slot"]), dose_time=str(request["requested_time"]))
            review_schedule_change_request(
                int(request["id"]),
                status="APPROVED" if approved else "REJECTED",
                reviewed_by_chat_id=chat_id,
            )
            add_audit_log(
                actor_chat_id=chat_id,
                actor_role="CAREGIVER",
                action="REVIEW_TIME_REQUEST",
                target=str(request["slot"]),
                old_value=str(request["old_time"]),
                new_value=str(request["requested_time"]) if approved else "REJECTED",
            )
            patient_id = get_patient_chat_id()
            if patient_id is not None:
                await self.application.bot.send_message(
                    chat_id=patient_id,
                    text=(
                        f"✅ Your request to change {str(request['slot']).title()} to "
                        f"{request['requested_time']} was approved."
                        if approved
                        else f"❌ Your request to change {str(request['slot']).title()} was not approved."
                    ),
                )
            await query.edit_message_text(
                "Request approved and schedule updated." if approved else "Request rejected.",
                reply_markup=back_button("cg"),
            )
        elif data.startswith("cg:ackalert:"):
            alert_id = int(data.rsplit(":", 1)[1])
            acknowledge_alert(alert_id)
            add_audit_log(
                actor_chat_id=chat_id,
                actor_role="CAREGIVER",
                action="ACKNOWLEDGE_ALERT",
                target=str(alert_id),
            )
            await query.edit_message_text(f"✅ Alert #{alert_id} acknowledged.", reply_markup=back_button("cg"))

    async def _patient_callback(self, query: Any, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
        if data == "pt:next":
            await query.edit_message_text(next_dose_text(), reply_markup=back_button("pt"))
        elif data == "pt:today":
            await query.edit_message_text(format_today_sessions(), reply_markup=back_button("pt"))
        elif data == "pt:status":
            await query.edit_message_text(format_status(), reply_markup=back_button("pt"))
        elif data == "pt:remind":
            success = await self._send_hardware_command("CMD|BUZZER|PATIENT_REMINDER")
            await query.edit_message_text(
                "🔔 Reminder sent to the box." if success else "🔴 The box is offline, so a reminder could not be sent.",
                reply_markup=back_button("pt"),
            )
        elif data == "pt:request":
            await query.edit_message_text(
                "Choose the dose whose time you want to change.",
                reply_markup=schedules_keyboard("pt"),
            )
        elif data.startswith("pt:slot:"):
            slot = data.split(":", 2)[2]
            schedule = get_schedule_by_slot(slot)
            if schedule is None:
                return
            context.user_data["pending"] = {
                "kind": "patient_time_request",
                "slot": slot,
                "old_time": str(schedule["dose_time"]),
            }
            await query.edit_message_text(
                f"Current {slot.title()} time: {schedule['dose_time']}\n\n"
                "Send the requested new time in HH:MM format. "
                "The caregiver will be notified and must approve it.\n\n"
                "Send /cancel to stop."
            )

    async def text_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        message = update.effective_message
        if chat is None or message is None:
            return
        role = role_for_chat(chat.id)
        pending = context.user_data.get("pending")
        if role is None or not pending:
            return
        text = (message.text or "").strip()
        try:
            if pending["kind"] == "schedule_edit" and role == "CAREGIVER":
                await self._apply_schedule_edit(chat.id, pending, text)
                context.user_data.clear()
                await message.reply_text("✅ Schedule updated.", reply_markup=caregiver_menu())
            elif pending["kind"] == "patient_time_request" and role == "PATIENT":
                datetime.strptime(text, "%H:%M")
                request_id = create_schedule_change_request(
                    requested_by_chat_id=chat.id,
                    requested_by_role="PATIENT",
                    slot=pending["slot"],
                    old_time=pending["old_time"],
                    requested_time=text,
                )
                add_audit_log(
                    actor_chat_id=chat.id,
                    actor_role="PATIENT",
                    action="REQUEST_TIME_CHANGE",
                    target=pending["slot"],
                    old_value=pending["old_time"],
                    new_value=text,
                )
                context.user_data.clear()
                await message.reply_text(
                    f"✅ Request #{request_id} sent to the caregiver.",
                    reply_markup=patient_menu(),
                )
                caregiver_id = get_caregiver_chat_id()
                if caregiver_id is not None:
                    keyboard = InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton("✅ Approve", callback_data=f"cg:req:approve:{request_id}"),
                                InlineKeyboardButton("❌ Reject", callback_data=f"cg:req:reject:{request_id}"),
                            ]
                        ]
                    )
                    await self.application.bot.send_message(
                        chat_id=caregiver_id,
                        text=(
                            "📝 Patient schedule-change request\n\n"
                            f"{str(pending['slot']).title()}: "
                            f"{pending['old_time']} → {text}"
                        ),
                        reply_markup=keyboard,
                    )
        except ValueError as exc:
            await message.reply_text(f"Invalid value: {exc}\nPlease try again or send /cancel.")

    async def _apply_schedule_edit(self, chat_id: int, pending: dict[str, Any], text: str) -> None:
        slot = str(pending["slot"])
        field = str(pending["field"])
        schedule = get_schedule_by_slot(slot)
        if schedule is None:
            raise ValueError("Schedule not found.")
        if field == "time":
            datetime.strptime(text, "%H:%M")
            old_value = str(schedule["dose_time"])
            update_medication_schedule(slot, dose_time=text)
        elif field == "window":
            value = int(text)
            old_value = str(schedule["dose_window_minutes"])
            update_medication_schedule(slot, dose_window_minutes=value)
        elif field == "correction":
            value = int(text)
            old_value = str(schedule["correction_window_minutes"])
            update_medication_schedule(slot, correction_window_minutes=value)
        else:
            raise ValueError("Unsupported schedule field.")
        add_audit_log(
            actor_chat_id=chat_id,
            actor_role="CAREGIVER",
            action=f"EDIT_{field.upper()}",
            target=slot,
            old_value=old_value,
            new_value=text,
        )
        patient_id = get_patient_chat_id()
        if patient_id is not None:
            await self.application.bot.send_message(
                chat_id=patient_id,
                text=f"ℹ️ The caregiver updated the {slot.title()} schedule: {old_value} → {text}.",
            )

    async def _send_hardware_command(self, command: str) -> bool:
        if self.command_sender is None:
            print(f"[TELEGRAM] Hardware command unavailable: {command}")
            return False
        try:
            return await self.command_sender(command)
        except Exception as exc:
            print(f"[TELEGRAM] Hardware command failed: {exc}")
            return False

    async def send_to_caregiver(
        self,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        chat_id = get_caregiver_chat_id()
        if chat_id is not None:
            await self.application.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)

    async def send_to_patient(self, text: str) -> None:
        chat_id = get_patient_chat_id()
        if chat_id is not None:
            await self.application.bot.send_message(chat_id=chat_id, text=text)

    async def notify_session_outcome(self, outcome: Any) -> None:
        status = str(outcome.status)
        message = str(outcome.message)
        await self.send_to_patient(message)
        caregiver_should_receive = bool(outcome.notify_caregiver) or (
            status == "NEEDS_CORRECTION"
            and any(word in message.lower() for word in ("instead", "extra medicine", "more than one"))
        )
        if caregiver_should_receive:
            await self.send_to_caregiver(
                f"Dose session #{outcome.session_id}\n\n{message}"
            )


async def run_bot_standalone() -> None:
    service = TelegramBotService()
    await service.start()
    try:
        import asyncio
        while True:
            await asyncio.sleep(1)
    finally:
        await service.stop()


if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(run_bot_standalone())
    except KeyboardInterrupt:
        print("\nTelegram bot stopped.")
