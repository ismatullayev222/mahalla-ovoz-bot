
import os
import json
import sqlite3
import asyncio

from database import (
    init_db,
    create_poll,
    get_all_polls,
    get_poll,
    cast_vote,
    get_results,
    close_poll,
    delete_poll
)

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
        [InlineKeyboardButton("➕ Yangi opros yaratish", callback_data="admin_new_poll")],
        [InlineKeyboardButton("📋 Barcha oproslar", callback_data="admin_all_polls")],
        [InlineKeyboardButton("✏️ Eski savolni o'zgartirish", callback_data="admin_question")],
        [InlineKeyboardButton("📝 Eski javoblarni o'zgartirish", callback_data="admin_options")],
        [InlineKeyboardButton("📊 Eski natijalarni ko'rish", callback_data="admin_results")],
        [InlineKeyboardButton("❌ Bekor qilish", callback_data="admin_cancel")]
    ])

async def new_poll_start(query, context):
    context.user_data["new_poll"] = {}
    context.user_data["admin_action"] = "poll_title"

    await query.edit_message_text(
        "➕ YANGI OPROS YARATISH\n\n"
        "Avval opros nomini yuboring:"
    )


async def show_all_polls(query):
    polls = get_all_polls()

    if not polls:
        await query.edit_message_text(
            "Hozircha oproslar mavjud emas.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Admin panel", callback_data="admin_back")]
            ])
        )
        return

    keyboard = []

    for poll in polls:
        status = "🟢" if poll["status"] == "active" else "🔴"
        keyboard.append([
            InlineKeyboardButton(
                f"{status} {poll['title']}",
                callback_data=f"admin_view_poll_{poll['id']}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("⬅️ Admin panel", callback_data="admin_back")
    ])

    await query.edit_message_text(
        "📋 BARCHA OPROSLAR:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_poll_details(query, poll_id, context):
    poll = get_poll(poll_id)

    if not poll:
        await query.edit_message_text("Opros topilmadi.")
        return

    bot_username = context.bot.username

    link = f"https://t.me/{bot_username}?start=poll_{poll_id}"

    
    
    keyboard = [
        [InlineKeyboardButton("🔗 Opros havolasini ochish", url=link)],
        [InlineKeyboardButton("📊 Natijalarni ko'rish", callback_data=f"admin_poll_results_{poll_id}")],
        [InlineKeyboardButton(
            "🔒 Oprosni yopish",
            callback_data=f"admin_close_poll_{poll_id}"
        )] if poll["status"] == "active" else [],
        [InlineKeyboardButton("⬅️ Barcha oproslar", callback_data="admin_all_polls")]
    ]
    keyboard = [row for row in keyboard if row]


    await query.edit_message_text(
        f"📊 {poll['title']}\n\n"
        f"❓ {poll['question']}\n\n"
        f"Holati: {poll['status']}\n\n"
        f"🔗 Havola:\n{link}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

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
    args = context.args

    if args and args[0].startswith("poll_"):
        try:
            poll_id = int(args[0].replace("poll_", ""))
        except ValueError:
            await update.message.reply_text("Opros havolasi noto'g'ri.")
            return

        poll = get_poll(poll_id)

        if not poll or poll["status"] != "active":
            await update.message.reply_text(
                "Bu opros mavjud emas yoki yopilgan."
            )
            return

        context.user_data["current_poll_id"] = poll_id

        keyboard = [
            [InlineKeyboardButton(
                "📢 Kanalga a'zo bo'lish",
                url="https://t.me/Karmana_yoshlaruz"
            )],
            [InlineKeyboardButton(
                "✅ A'zolikni tekshirish",
                callback_data=f"check_poll_{poll_id}"
            )]
        ]

        await update.message.reply_text(
            f"📊 {poll['title']}\n\n"
            f"❓ {poll['question']}\n\n"
            "Ovoz berishda qatnashish uchun kanalga a'zo bo'ling.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

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

async def show_poll(query, page=0):
    options = get_options()
    per_page = 10
    start = page * per_page
    end = start + per_page

    keyboard = [
        [InlineKeyboardButton(option, callback_data=f"vote_{i}")]
        for i, option in enumerate(options[start:end], start=start)
    ]

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton("⬅️ Oldingi", callback_data=f"poll_page_{page-1}")
        )

    if end < len(options):
        navigation.append(
            InlineKeyboardButton("Keyingi ➡️", callback_data=f"poll_page_{page+1}")
        )

    if navigation:
        keyboard.append(navigation)

    await query.edit_message_text(
        get_question() + f"\n\n📄 Sahifa: {page+1}/{(len(options)+9)//10}",
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

    # 1. YANGI OPROS NOMI
    if action == "poll_title":
        if not text:
            await update.message.reply_text("Opros nomi bo'sh bo'lmasin.")
            return

        context.user_data["new_poll"]["title"] = text
        context.user_data["admin_action"] = "poll_question"

        await update.message.reply_text(
            "❓ Endi opros savolini yuboring:"
        )
        return

    # 2. YANGI OPROS SAVOLI
    if action == "poll_question":
        if not text:
            await update.message.reply_text("Savol bo'sh bo'lmasin.")
            return

        context.user_data["new_poll"]["question"] = text
        context.user_data["admin_action"] = "poll_options"

        await update.message.reply_text(
            "📝 Endi javob variantlarini yuboring.\n\n"
            "Har bir variantni alohida qatorda yozing.\n\n"
            "Masalan:\n"
            "1-mahalla\n"
            "2-mahalla\n"
            "3-mahalla"
        )
        return

    # 3. YANGI OPROS JAVOB VARIANTLARI
    if action == "poll_options":
        options = [
            item.strip()
            for item in text.splitlines()
            if item.strip()
        ]

        if len(options) < 2:
            await update.message.reply_text(
                "Kamida 2 ta javob varianti kiriting."
            )
            return

        if len(options) > 50:
            await update.message.reply_text(
                "Eng ko'pi bilan 50 ta variant kiritish mumkin."
            )
            return

        if len(set(options)) != len(options):
            await update.message.reply_text(
                "Bir xil javob variantlarini takrorlamang."
            )
            return

        poll_data = context.user_data["new_poll"]

        poll_id = create_poll(
            poll_data["title"],
            poll_data["question"],
            options
        )

        context.user_data.pop("new_poll", None)
        context.user_data.pop("admin_action", None)

        bot_username = context.bot.username
        link = f"https://t.me/{bot_username}?start=poll_{poll_id}"

        await update.message.reply_text(
            f"✅ YANGI OPROS YARATILDI!\n\n"
            f"📌 Nomi: {poll_data['title']}\n"
            f"❓ Savol: {poll_data['question']}\n"
            f"📝 Variantlar soni: {len(options)} ta\n\n"
            f"🔗 Opros havolasi:\n{link}",
            reply_markup=admin_keyboard()
        )
        return

    # ESKI SAVOLNI O'ZGARTIRISH
    if action == "question":
        if not text:
            await update.message.reply_text(
                "Savol bo'sh bo'lmasligi kerak."
            )
            return

        save_setting("question", text)
        reset_votes()
        context.user_data.pop("admin_action", None)

        await update.message.reply_text(
            "✅ Eski savol yangilandi!",
            reply_markup=admin_keyboard()
        )
        return

    # ESKI JAVOBLARNI O'ZGARTIRISH
    if action == "options":
        options = [
            item.strip()
            for item in text.splitlines()
            if item.strip()
        ]

        if len(options) < 2:
            await update.message.reply_text(
                "Kamida 2 ta javob varianti kiriting."
            )
            return

        if len(options) > 50:
            await update.message.reply_text(
                "Eng ko'pi bilan 50 ta variant kiriting."
            )
            return

        if len(set(options)) != len(options):
            await update.message.reply_text(
                "Bir xil javob variantlarini takrorlamang."
            )
            return

        save_setting(
            "options",
            json.dumps(options, ensure_ascii=False)
        )
        reset_votes()
        context.user_data.pop("admin_action", None)

        await update.message.reply_text(
            "✅ Eski javob variantlari yangilandi!",
            reply_markup=admin_keyboard()
        )
        return

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data
    if data.startswith("poll_page_"):
        page = int(data.split("_")[2])
        await show_poll(query, page)
        return
    # ADMIN PANEL
    if data.startswith("admin_"):
        if not is_admin(user_id):
            await query.answer("Ruxsat yo'q!", show_alert=True)
            return

        # YANGI OPROS YARATISH
        if data == "admin_new_poll":
            await new_poll_start(query, context)
            return

        # BARCHA OPROSLAR
        if data == "admin_all_polls":
            await show_all_polls(query)
            return

        # ALOHIDA OPROSNI KO'RISH
        if data.startswith("admin_view_poll_"):
            try:
                poll_id = int(data.replace("admin_view_poll_", ""))
                await show_poll_details(query, poll_id, context)
            except ValueError:
                await query.edit_message_text("Opros ID noto'g'ri.")
            return

        # YANGI OPROS NATIJALARI
        if data.startswith("admin_poll_results_"):
            try:
                poll_id = int(data.replace("admin_poll_results_", ""))
            except ValueError:
                await query.answer("Opros ID noto'g'ri.", show_alert=True)
                return

            poll = get_poll(poll_id)

            if not poll:
                await query.answer("Opros topilmadi.", show_alert=True)
                return

            results_data = get_results(poll_id)
            total = sum(row["votes"] for row in results_data)

            message = (
                f"📊 {poll['title']}\n\n"
                f"Jami ovozlar: {total}\n\n"
            )

            for row in results_data:
                count = row["votes"]
                percent = (count / total * 100) if total else 0

                message += (
                    f"▪️ {row['option_text']}\n"
                    f"   {count} ta ({percent:.1f}%)\n\n"
                )

            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "⬅️ Oprosga qaytish",
                        callback_data=f"admin_view_poll_{poll_id}"
                    )]
                ])
            )
            return

        # OPROSNI YOPISH
        if data.startswith("admin_close_poll_"):
            try:
                poll_id = int(data.replace("admin_close_poll_", ""))
            except ValueError:
                await query.answer("Opros ID noto'g'ri.", show_alert=True)
                return

            success = close_poll(poll_id)

            if success:
                await query.answer("Opros muvaffaqiyatli yopildi!")
                await show_poll_details(query, poll_id, context)
            else:
                await query.answer("Opros topilmadi.", show_alert=True)

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

    # YANGI OPROS UCHUN OBUNANI TEKSHIRISH
    if data.startswith("check_poll_"):
        try:
            poll_id = int(data.replace("check_poll_", ""))
        except ValueError:
            await query.answer("Opros ID noto'g'ri.", show_alert=True)
            return

        poll = get_poll(poll_id)

        if not poll or poll["status"] != "active":
            await query.answer(
                "Bu opros yopilgan yoki mavjud emas.",
                show_alert=True
            )
            return

               if await is_subscriber(context.bot, user_id):
            await query.answer("A'zolik tasdiqlandi!")

            keyboard = []

            for option in poll["options"]:
                keyboard.append([
                    InlineKeyboardButton(
                        option["option_text"],
                        callback_data=f"new_vote_{poll_id}_{option['id']}"
                    )
                ])

            await query.edit_message_text(
                f"📊 {poll['title']}\n\n"
                f"❓ {poll['question']}\n\n"
                "O'zingizga ma'qul javobni tanlang:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await query.answer(
                "Avval kanalga a'zo bo'ling!",
                show_alert=True
            )
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

    # YANGI OPROS UCHUN OVOZ BERISH
    if data.startswith("new_vote_"):
        if not await is_subscriber(context.bot, user_id):
            await query.answer(
                "Ovoz berish uchun kanalga a'zo bo'ling!",
                show_alert=True
            )
            return

        try:
            parts = data.split("_")
            poll_id = int(parts[2])
            option_id = int(parts[3])
        except (ValueError, IndexError):
            await query.answer(
                "Ovoz berish ma'lumoti noto'g'ri.",
                show_alert=True
            )
            return

        result = cast_vote(poll_id, user_id, option_id)

        if result == "success":
            await query.edit_message_text(
                "✅ Ovozingiz muvaffaqiyatli qabul qilindi!"
            )
        elif result == "already_voted":
            await query.answer(
                "Siz bu oprosda avval ovoz bergansiz!",
                show_alert=True
            )
        elif result == "closed":
            await query.answer(
                "Bu opros yopilgan!",
                show_alert=True
            )
        else:
            await query.answer(
                "Javob varianti topilmadi!",
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
    # Yangi ma'lumotlar bazasi jadvallarini yaratish
    init_db()

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
