
import os
import sqlite3
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes
)

TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = "@Karmana_yoshlaruz"
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

QUESTION = "Sizningcha, qaysi variant eng yaxshi?"
OPTIONS = ["A variant", "B variant", "C variant"]

db = sqlite3.connect("votes.db", check_same_thread=False)
db.execute("""
CREATE TABLE IF NOT EXISTS votes (
    user_id INTEGER PRIMARY KEY,
    choice TEXT NOT NULL
)
""")
db.commit()


async def is_subscriber(bot, user_id):
    try:
        member = await bot.get_chat_member(CHANNEL, user_id)
        return (
            member.status in ("creator", "administrator", "member")
            or (
                member.status == "restricted"
                and member.is_member
            )
        )
    except Exception:
        return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(
            "📢 Kanalga a'zo bo'lish",
            url="https://t.me/Karmana_yoshlaruz"
        )],
        [InlineKeyboardButton(
            "✅ A'zolikni tekshirish",
            callback_data="check"
        )]
    ]

    await update.message.reply_text(
        "Assalomu alaykum!\n\n"
        "Ovoz berishda qatnashish uchun avval "
        "kanalimizga a'zo bo'ling.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_poll(query):
    keyboard = [
        [InlineKeyboardButton(
            option, callback_data=f"vote_{i}"
        )]
        for i, option in enumerate(OPTIONS)
    ]

    await query.edit_message_text(
        QUESTION,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    if data == "check":
        if await is_subscriber(context.bot, user_id):
            await show_poll(query)
        else:
            await query.answer(
                "Siz hali kanalga a'zo emassiz!",
                show_alert=True
            )
        return

    if data.startswith("vote_"):
        if not await is_subscriber(context.bot, user_id):
            await query.answer(
                "Ovoz berish uchun kanalga a'zo bo'ling!",
                show_alert=True
            )
            return

        try:
            index = int(data.split("_")[1])
            choice = OPTIONS[index]
        except (ValueError, IndexError):
            return

        try:
            db.execute(
                "INSERT INTO votes (user_id, choice) VALUES (?, ?)",
                (user_id, choice)
            )
            db.commit()
            await query.edit_message_text(
                f"✅ Ovozingiz qabul qilindi!\n\nTanlovingiz: {choice}"
            )
        except sqlite3.IntegrityError:
            await query.answer(
                "Siz avval ovoz bergansiz!",
                show_alert=True
            )


async def results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ADMIN_ID == 0 or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Bu buyruq faqat administrator uchun.")
        return

    rows = db.execute(
        "SELECT choice, COUNT(*) FROM votes GROUP BY choice"
    ).fetchall()

    total = db.execute("SELECT COUNT(*) FROM votes").fetchone()[0]

    message = f"📊 OVOZ BERISH NATIJALARI\nJami ovozlar: {total}\n\n"

    for option in OPTIONS:
        count = next((r[1] for r in rows if r[0] == option), 0)
        message += f"{option}: {count} ta\n"

    await update.message.reply_text(message)


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Sizning Telegram ID raqamingiz: {update.effective_user.id}"
    )

async def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("natija", results))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(CommandHandler("myid", myid))
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await asyncio.Event().wait()


import asyncio

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
