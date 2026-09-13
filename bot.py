import os
import json
import logging
from datetime import date, datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== НАСТРОЙКИ ====================

# Категории только для expense (для income всегда "none")
CATEGORIES = [
    "пища", "техника", "одежда", "транспорт",
    "развлечения", "жильё", "здоровье", "другое",
]

ACCOUNTS = ["card", "cash"]
PAYERS = ["S", "D"]

# Порядок шагов сценария (category автоматически пропускается для income)
STEP_ORDER = ["date", "type", "amount", "category", "account", "payer", "description"]

# ==================== переменные окружения ====================
BOT_TOKEN = os.environ["BOT_TOKEN"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
GOOGLE_CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]

# ==================== состояния диалога ====================
DATE, DATE_INPUT, TYPE, AMOUNT, CATEGORY, ACCOUNT, PAYER, DESCRIPTION = range(8)

BACK_BUTTON = InlineKeyboardButton("◀️ Назад", callback_data="back")


def get_sheet():
    creds_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).sheet1


def kb(pairs, prefix):
    """pairs: список (label, value). Добавляет кнопку Назад последней строкой."""
    rows = [[InlineKeyboardButton(label, callback_data=f"{prefix}:{value}")] for label, value in pairs]
    rows.append([BACK_BUTTON])
    return InlineKeyboardMarkup(rows)


async def send_or_edit(update: Update, text: str, keyboard: InlineKeyboardMarkup):
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text, reply_markup=keyboard)


def next_step(current, data):
    idx = STEP_ORDER.index(current) + 1
    while idx < len(STEP_ORDER):
        step = STEP_ORDER[idx]
        if step == "category" and data.get("type") == "income":
            idx += 1
            continue
        return step
    return None


def prev_step(current, data):
    idx = STEP_ORDER.index(current) - 1
    while idx >= 0:
        step = STEP_ORDER[idx]
        if step == "category" and data.get("type") == "income":
            idx -= 1
            continue
        return step
    return None


# ==================== рендер каждого шага ====================

async def render_date(update, context):
    context.user_data["step"] = "date"
    keyboard = kb([("Сегодня", "today"), ("Другое", "custom")], "date")
    await send_or_edit(update, "Укажите date:", keyboard)
    return DATE


async def render_type(update, context):
    context.user_data["step"] = "type"
    keyboard = kb([("expense", "expense"), ("income", "income")], "type")
    await send_or_edit(update, "Укажите type:", keyboard)
    return TYPE


async def render_amount(update, context):
    context.user_data["step"] = "amount"
    keyboard = InlineKeyboardMarkup([[BACK_BUTTON]])
    await send_or_edit(update, "Укажите amount:", keyboard)
    return AMOUNT


async def render_category(update, context):
    context.user_data["step"] = "category"
    keyboard = kb([(c, c) for c in CATEGORIES], "cat")
    await send_or_edit(update, "Укажите category:", keyboard)
    return CATEGORY


async def render_account(update, context):
    context.user_data["step"] = "account"
    keyboard = kb([(a, a) for a in ACCOUNTS], "acc")
    await send_or_edit(update, "Укажите account:", keyboard)
    return ACCOUNT


async def render_payer(update, context):
    context.user_data["step"] = "payer"
    keyboard = kb([(p, p) for p in PAYERS], "payer")
    await send_or_edit(update, "Укажите payer:", keyboard)
    return PAYER


async def render_description(update, context):
    context.user_data["step"] = "description"
    keyboard = InlineKeyboardMarkup([[BACK_BUTTON]])
    await send_or_edit(update, 'Укажите description (или "-", если не нужно):', keyboard)
    return DESCRIPTION


async def render_step(step, update, context):
    return await {
        "date": render_date,
        "type": render_type,
        "amount": render_amount,
        "category": render_category,
        "account": render_account,
        "payer": render_payer,
        "description": render_description,
    }[step](update, context)


async def goto_next(update, context, current_step):
    nxt = next_step(current_step, context.user_data)
    if nxt is None:
        return await finalize(update, context)
    return await render_step(nxt, update, context)


# ==================== старт / завершение ====================

def start_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("➕ Внести транзакцию", callback_data="new_tx")]])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Готово к работе.", reply_markup=start_keyboard())


async def new_tx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    return await render_date(update, context)


async def back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.callback_query.edit_message_text("Процесс завершён.", reply_markup=start_keyboard())
    return ConversationHandler.END


async def go_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    current = context.user_data.get("step")
    prev = prev_step(current, context.user_data)
    if prev is None:
        return await back_to_start(update, context)
    return await render_step(prev, update, context)


async def back_from_date_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    return await render_date(update, context)


# ==================== обработчики шагов ====================

async def date_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data.split(":")[1]
    if choice == "today":
        context.user_data["date"] = date.today().isoformat()
        return await goto_next(update, context, "date")
    await query.edit_message_text(
        "Укажите date (в формате гггг-мм-дд, например 2026-09-13):",
        reply_markup=InlineKeyboardMarkup([[BACK_BUTTON]]),
    )
    return DATE_INPUT


async def date_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    parsed = None
    for fmt in ("%Y-%m-%d", "%d.%m.%y", "%d.%m.%Y"):
        try:
            parsed = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    if not parsed:
        await update.message.reply_text(
            "Не получилось распознать дату. Укажите date (в формате гггг-мм-дд):",
            reply_markup=InlineKeyboardMarkup([[BACK_BUTTON]]),
        )
        return DATE_INPUT
    context.user_data["date"] = parsed.date().isoformat()
    return await goto_next(update, context, "date")


async def type_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    value = query.data.split(":")[1]
    context.user_data["type"] = value
    if value == "income":
        context.user_data["category"] = "none"
    return await goto_next(update, context, "type")


async def amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(",", ".")
    try:
        amount = float(text)
    except ValueError:
        await update.message.reply_text(
            "Это не похоже на число. Укажите amount:",
            reply_markup=InlineKeyboardMarkup([[BACK_BUTTON]]),
        )
        return AMOUNT
    context.user_data["amount"] = amount
    return await goto_next(update, context, "amount")


async def category_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["category"] = query.data.split(":", 1)[1]
    return await goto_next(update, context, "category")


async def account_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["account"] = query.data.split(":", 1)[1]
    return await goto_next(update, context, "account")


async def payer_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["payer"] = query.data.split(":", 1)[1]
    return await goto_next(update, context, "payer")


async def description_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["description"] = "" if text == "-" else text
    return await goto_next(update, context, "description")


async def finalize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data
    row = [
        data.get("date"),
        data.get("type"),
        data.get("amount"),
        data.get("category", "none"),
        data.get("account"),
        data.get("payer"),
        data.get("description", ""),
    ]
    try:
        sheet = get_sheet()
        sheet.append_row(row, value_input_option="USER_ENTERED")
        msg = "✅ Записано."
    except Exception as e:
        logger.exception("Ошибка записи в таблицу")
        msg = f"⚠️ Не удалось записать в таблицу: {e}"

    context.user_data.clear()

    if update.callback_query:
        await update.callback_query.edit_message_text(msg)
        target = update.callback_query.message
    else:
        await update.message.reply_text(msg)
        target = update.message

    await target.reply_text("Готово к работе.", reply_markup=start_keyboard())
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Отменено.", reply_markup=start_keyboard())
    return ConversationHandler.END


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(new_tx, pattern="^new_tx$")],
        states={
            DATE: [
                CallbackQueryHandler(date_choice, pattern="^date:"),
                CallbackQueryHandler(go_back, pattern="^back$"),
            ],
            DATE_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, date_input_handler),
                CallbackQueryHandler(back_from_date_input, pattern="^back$"),
            ],
            TYPE: [
                CallbackQueryHandler(type_choice, pattern="^type:"),
                CallbackQueryHandler(go_back, pattern="^back$"),
            ],
            AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, amount_input),
                CallbackQueryHandler(go_back, pattern="^back$"),
            ],
            CATEGORY: [
                CallbackQueryHandler(category_choice, pattern="^cat:"),
                CallbackQueryHandler(go_back, pattern="^back$"),
            ],
            ACCOUNT: [
                CallbackQueryHandler(account_choice, pattern="^acc:"),
                CallbackQueryHandler(go_back, pattern="^back$"),
            ],
            PAYER: [
                CallbackQueryHandler(payer_choice, pattern="^payer:"),
                CallbackQueryHandler(go_back, pattern="^back$"),
            ],
            DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, description_input),
                CallbackQueryHandler(go_back, pattern="^back$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)

    app.run_polling()


if __name__ == "__main__":
    main()
