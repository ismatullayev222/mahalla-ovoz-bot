
import os
import json
import sqlite3
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN topilmadi! Railway Variables sozlamasini tekshiring.")
CHANNEL = "@Karmana_yoshlaruz"
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Railway Volume ulangan bo'lsa DB_PATH=/data/votes.db deb belgilang
DB_PATH = os.getenv("DB_PATH", "votes.db")

db = sqlite3.connect(DB_PATH, check_same_thread=False)

db.execute("""
CREATE TABLE IF NOT EXISTS votes (
    user_id INTEGER PRIMARY KEY,
    choice TEXT NOT NULL
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
""")

db.commit()

DEFAULT_QUESTION = "Sizningcha, Karmana tumanidagi eng faol mahalla yettiligi?"
DEFAULT_OPTIONS = ["A variant", "B variant", "C variant"]


def get_setting(key, default):
    row = db.execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    return row[0] if row else default


def save_setting(key, value):
    db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, value)
    )
    db.commit()


def get_question():
    return get_setting("question", DEFAULT_QUESTION)


def get_options():
    return json.loads(
        get_setting("options", json.dumps(DEFAULT_OPTIONS, ensure_ascii=False))
    )


def reset_votes():
    db.execute("DELETE FROM votes")
    db.commit()


def is_admin(user_id):
    return ADMIN_ID != 0 and user_id == ADMIN_ID


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Savolni o'zgartirish", callback_data="admin_question")],
        [InlineKeyboardButton("📝 Javoblarni o'zgartirish", callback_data="admin_options")],
        [InlineKeyboardButton("📊 Natijalarni ko'rish", callback_data="admin_results")],
        [InlineKeyboardButton("❌ Bekor qilish", callback_data="admin_cancel")]
    ])


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
    options = get_options()

    keyboard = [
        [InlineKeyboardButton(option, callback_data=f"vote_{i}")]
        for i, option in enumerate(options)
    ]

    await query.edit_message_text(
        get_question(),
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Bu bo'lim faqat administrator uchun.")
        return

    context.user_data.pop("admin_action", None)

    await update.message.reply_text(
        "⚙️ ADMIN BOSHQARUV PANELI\n\n"
        "Kerakli amalni tanlang:",
        reply_markup=admin_keyboard()
    )


async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    action = context.user_data.get("admin_action")

    if not action:
        return

    text = update.message.text.strip()

    if action == "question":
        if not text:
            await update.message.reply_text("Savol bo'sh bo'lmasligi kerak.")
            return

        save_setting("question", text)
        reset_votes()

        context.user_data.pop("admin_action", None)

        await update.message.reply_text(
            "✅ Yangi savol saqlandi!\n"
            "Eski ovozlar yangi so'rovnoma uchun tozalandi.",
            reply_markup=admin_keyboard()
        )

    elif action == "options":
        options = [item.strip() for item in text.splitlines() if item.strip()]

        if len(options) < 2:
            await update.message.reply_text(
                "Kamida 2 ta javob varianti kiriting.\n"
                "Har bir variantni yangi qatordan yozing."
            )
            return

        if len(options) > 10:
            await update.message.reply_text("Eng ko'pi bilan 10 ta variant kiritish mumkin.")
            return

        if len(set(options)) != len(options):
            await update.message.reply_text("Bir xil javob variantlarini takrorlamang.")
            return

        save_setting("options", json.dumps(options, ensure_ascii=False))
        reset_votes()

        context.user_data.pop("admin_action", None)

        await update.message.reply_text(
            "✅ Javob variantlari saqlandi!\n"
            "Eski ovozlar yangi so'rovnoma uchun tozalandi.",
            reply_markup=admin_keyboard()
        )


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    # ADMIN PANEL
    if data.startswith("admin_"):
        if not is_admin(user_id):
            await query.answer("Ruxsat yo'q!", show_alert=True)
            return

        if data == "admin_question":
            context.user_data["admin_action"] = "question"
            await query.edit_message_text(
                "✏️ Yangi savol matnini yuboring:"
            )
            return

        if data == "admin_options":
            context.user_data["admin_action"] = "options"
            await query.edit_message_text(
                "📝 Yangi javob variantlarini yuboring.\n\n"
                "Har bir variantni alohida qatordan yozing.\n\n"
                "Masalan:\n"
                "1-mahalla\n"
                "2-mahalla\n"
                "3-mahalla"
            )
            return

        if data == "admin_results":
            rows = db.execute(
                "SELECT choice, COUNT(*) FROM votes GROUP BY choice"
            ).fetchall()

            total = db.execute(
                "SELECT COUNT(*) FROM votes"
            ).fetchone()[0]

            message = (
                f"📊 OVOZ BERISH NATIJALARI\n"
                f"Jami ovozlar: {total}\n\n"
            )

            for option in get_options():
                count = next(
                    (r[1] for r in rows if r[0] == option), 0
                )
                message += f"{option}: {count} ta\n"

            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Admin panel", callback_data="admin_back")]
                ])
            )
            return

        if data == "admin_back":
            context.user_data.pop("admin_action", None)
            await query.edit_message_text(
                "⚙️ ADMIN BOSHQARUV PANELI",
                reply_markup=admin_keyboard()
            )
            return

        if data == "admin_cancel":
            context.user_data.pop("admin_action", None)
            await query.edit_message_text("❌ Amal bekor qilindi.")
            return

    # OBUNANI TEKSHIRISH
    if data == "check":
        if await is_subscriber(context.bot, user_id):
            await show_poll(query)
        else:
            await query.answer(
                "Siz hali kanalga a'zo emassiz!",
                show_alert=True
            )
        return

    # OVOZ BERISH
    if data.startswith("vote_"):
        if not await is_subscriber(context.bot, user_id):
            await query.answer(
                "Ovoz berish uchun kanalga a'zo bo'ling!",
                show_alert=True
            )
            return

        try:
            index = int(data.split("_")[1])
            options = get_options()
            choice = options[index]
        except (ValueError, IndexError):
            await query.answer("Bu javob varianti mavjud emas.", show_alert=True)
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
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "Bu buyruq faqat administrator uchun."
        )
        return

    rows = db.execute(
        "SELECT choice, COUNT(*) FROM votes GROUP BY choice"
    ).fetchall()

    total = db.execute(
        "SELECT COUNT(*) FROM votes"
    ).fetchone()[0]

    message = f"📊 OVOZ BERISH NATIJALARI\nJami ovozlar: {total}\n\n"

    for option in get_options():
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
    app.add_handler(CommandHandler("admin", admin_menu))
    app.add_handler(CommandHandler("natija", results))
    app.add_handler(CommandHandler("myid", myid))

    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_text))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
