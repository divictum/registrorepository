import os
import json
import logging
from datetime import date, datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, Update
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

# ==================== НАСТРОЙКИ — МЕНЯЙТЕ ПОД СЕБЯ ====================

# Ровно 8 категорий (можно переименовать, порядок и количество не важны)
CATEGORIES = [
    "Продукты", "Транспорт", "Жильё", "Развлечения",
    "Здоровье", "Одежда", "Зарплата", "Прочее",
]

# Счета — добавьте/уберите свои
ACCOUNTS = ["Карта", "Наличные"]

# Плательщики — добавьте/уберите свои
PAYERS = ["Я", "Партнёр"]

# ==================== переменные окружения (задаются в Railway) ====================
BOT_TOKEN = os.environ["BOT_TOKEN"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
GOOGLE_CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]

# ==================== состояния диалога ====================
(
    DATE, DATE_CUSTOM, TYPE, AMOUNT, CATEGORY, ACCOUNT, PAYER, DESCRIPTION,
) = range(8)


def get_sheet():
    creds_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).sheet1


def build_keyboard(options, prefix):
    buttons = [[InlineKeyboardButton(o, callback_data=f"{prefix}:{o}")] for o in options]
    return InlineKeyboardMarkup(buttons)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("➕ Внести транзакцию", callback_data="new_tx")]]
    )
    await update.message.reply_text("Привет! Что делаем?", reply_markup=keyboard)


async def new_tx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Сегодня", callback_data="date:today")],
            [InlineKeyboardButton("Другое", callback_data="date:other")],
        ]
    )
    await query.edit_message_text("Выберите дату:", reply_markup=keyboard)
    return DATE


async def date_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data.split(":")[1]
    if choice == "today":
        context.user_data["date"] = date.today().strftime("%d.%m.%y")
        return await ask_type(update, context, edit=True)
    await query.edit_message_text("Введите дату в формате дд.мм.гг (например, 05.03.25):")
    return DATE_CUSTOM


async def date_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    parsed = None
    for fmt in ("%d.%m.%y", "%d.%m.%Y"):
        try:
            parsed = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    if not parsed:
        await update.message.reply_text(
            "Не получилось распознать дату. Введите в формате дд.мм.гг, например 05.03.25:"
        )
        return DATE_CUSTOM
    context.user_data["date"] = parsed.strftime("%d.%m.%y")
    return await ask_type(update, context, edit=False)


async def ask_type(update, context, edit: bool):
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Доход", callback_data="type:Доход")],
            [InlineKeyboardButton("Расход", callback_data="type:Расход")],
        ]
    )
    text = "Тип операции:"
    if edit:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text, reply_markup=keyboard)
    return TYPE


async def type_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["type"] = query.data.split(":")[1]
    await query.edit_message_text("Введите сумму (например, 1500.50):")
    return AMOUNT


async def amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(",", ".")
    try:
        amount = float(text)
    except ValueError:
        await update.message.reply_text("Это не похоже на число. Введите сумму ещё раз:")
        return AMOUNT
    context.user_data["amount"] = amount
    keyboard = build_keyboard(CATEGORIES, "cat")
    await update.message.reply_text("Выберите категорию:", reply_markup=keyboard)
    return CATEGORY


async def category_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["category"] = query.data.split(":", 1)[1]
    keyboard = build_keyboard(ACCOUNTS, "acc")
    await query.edit_message_text("Выберите счёт:", reply_markup=keyboard)
    return ACCOUNT


async def account_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["account"] = query.data.split(":", 1)[1]
    keyboard = build_keyboard(PAYERS, "payer")
    await query.edit_message_text("Кто платил?", reply_markup=keyboard)
    return PAYER


async def payer_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["payer"] = query.data.split(":", 1)[1]
    await query.edit_message_text('Добавьте описание (или отправьте "-", если не нужно):')
    return DESCRIPTION


async def description_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["description"] = "" if text == "-" else text

    data = context.user_data
    row = [
        data["date"],
        data["type"],
        data["amount"],
        data["category"],
        data["account"],
        data["payer"],
        data["description"],
    ]
    try:
        sheet = get_sheet()
        sheet.append_row(row, value_input_option="USER_ENTERED")
        await update.message.reply_text("✅ Транзакция записана в таблицу!")
    except Exception as e:
        logger.exception("Ошибка записи в таблицу")
        await update.message.reply_text(f"⚠️ Не удалось записать в таблицу: {e}")

    context.user_data.clear()
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("➕ Внести ещё одну", callback_data="new_tx")]]
    )
    await update.message.reply_text("Готово. Что дальше?", reply_markup=keyboard)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Отменено.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(new_tx, pattern="^new_tx$")],
        states={
            DATE: [CallbackQueryHandler(date_choice, pattern="^date:")],
            DATE_CUSTOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, date_custom)],
            TYPE: [CallbackQueryHandler(type_choice, pattern="^type:")],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, amount_input)],
            CATEGORY: [CallbackQueryHandler(category_choice, pattern="^cat:")],
            ACCOUNT: [CallbackQueryHandler(account_choice, pattern="^acc:")],
            PAYER: [CallbackQueryHandler(payer_choice, pattern="^payer:")],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, description_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)

    app.run_polling()


if __name__ == "__main__":
    main()
