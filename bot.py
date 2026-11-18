
import os
import json
import sqlite3
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from database import (
    init_db,
    create_poll,
    get_all_polls,
    get_poll,
    cast_vote,
    get_results,
    close_poll,
)

# ==========================================
# 1. BOT SOZLAMALARI
# ==========================================

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

CHANNEL = "@Karmana_yoshlaruz"
CHANNEL_LINK = "https://t.me/Karmana_yoshlaruz"

DB_PATH = os.getenv("DB_PATH", "votes.db")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN topilmadi!")

if ADMIN_ID == 0:
    raise RuntimeError("ADMIN_ID belgilanmagan!")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

# ==========================================
# 2. ESKI OPROS SOZLAMALARI
# ==========================================

DEFAULT_QUESTION = (
    "Sizningcha, Karmana tumanidagi eng faol mahalla yettiligi?"
)

DEFAULT_OPTIONS = [
    "A variant",
    "B variant",
    "C variant",
]

# ==========================================
# 3. ESKI SQLITE BAZASI
# ==========================================

def legacy_db():
    folder = os.path.dirname(DB_PATH)

    if folder:
        os.makedirs(folder, exist_ok=True)

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30
    )

    conn.execute("PRAGMA busy_timeout = 30000")

    return conn


def init_legacy_db():
    conn = legacy_db()

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS votes (
                user_id INTEGER PRIMARY KEY,
                choice TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        conn.commit()

    finally:
        conn.close()


def get_setting(key, default):
    conn = legacy_db()

    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,)
        ).fetchone()

        return row[0] if row else default

    finally:
        conn.close()


def save_setting(key, value):
    conn = legacy_db()

    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO settings (key, value)
            VALUES (?, ?)
            """,
            (key, value)
        )

        conn.commit()

    finally:
        conn.close()


def get_question():
    return get_setting(
        "question",
        DEFAULT_QUESTION
    )


def get_options():
    try:
        options = json.loads(
            get_setting(
                "options",
                json.dumps(DEFAULT_OPTIONS, ensure_ascii=False)
            )
        )

        if isinstance(options, list) and options:
            return options

        return DEFAULT_OPTIONS

    except (ValueError, TypeError):
        return DEFAULT_OPTIONS


def is_admin(user_id):
    return user_id == ADMIN_ID


# ==========================================
# 4. ADMIN PANEL TUGMALARI
# ==========================================

def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "➕ Yangi opros yaratish",
                callback_data="admin_new_poll"
            )
        ],
        [
            InlineKeyboardButton(
                "📋 Barcha oproslar",
                callback_data="admin_all_polls"
            )
        ],
        [
            InlineKeyboardButton(
                "✏️ Eski savolni o'zgartirish",
                callback_data="admin_question"
            )
        ],
        [
            InlineKeyboardButton(
                "📝 Eski javoblarni o'zgartirish",
                callback_data="admin_options"
            )
        ],
        [
            InlineKeyboardButton(
                "📊 Eski natijalarni ko'rish",
                callback_data="admin_results"
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Bekor qilish",
                callback_data="admin_cancel"
            )
        ]
    ])

# ==========================================
# 5. YANGI OPROS YARATISH
# ==========================================

async def new_poll_start(query, context):
    context.user_data["new_poll"] = {}
    context.user_data["admin_action"] = "poll_title"

    await query.edit_message_text(
        "➕ YANGI OPROS YARATISH\n\n"
        "Avval opros nomini yuboring.\n\n"
        "Bekor qilish uchun /cancel yuboring."
    )


async def show_all_polls(query):
    polls = get_all_polls()

    if not polls:
        await query.edit_message_text(
            "Hozircha oproslar mavjud emas.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ Admin panel",
                        callback_data="admin_back"
                    )
                ]
            ])
        )
        return

    keyboard = []

    for poll in polls:
        status = (
            "🟢"
            if poll["status"] == "active"
            else "🔴"
        )

        keyboard.append([
            InlineKeyboardButton(
                f"{status} {str(poll['title'])[:55]}",
                callback_data=f"admin_view_poll_{poll['id']}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ Admin panel",
            callback_data="admin_back"
        )
    ])

    await query.edit_message_text(
        "📋 BARCHA OPROSLAR:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ==========================================
# 6. ALOHIDA OPROS MA'LUMOTLARI
# ==========================================

async def show_poll_details(query, poll_id, context):
    poll = get_poll(poll_id)

    if not poll:
        await query.edit_message_text(
            "Opros topilmadi."
        )
        return

    bot_info = await context.bot.get_me()

    link = (
        f"https://t.me/{bot_info.username}"
        f"?start=poll_{poll_id}"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "🔗 Opros havolasi",
                url=link
            )
        ],
        [
            InlineKeyboardButton(
                "📊 Natijalarni ko'rish",
                callback_data=f"admin_poll_results_{poll_id}"
            )
        ]
    ]

    if poll["status"] == "active":
        keyboard.append([
            InlineKeyboardButton(
                "🔒 Oprosni yopish",
                callback_data=f"admin_close_poll_{poll_id}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ Barcha oproslar",
            callback_data="admin_all_polls"
        )
    ])

    await query.edit_message_text(
        f"📊 {poll['title']}\n\n"
        f"❓ {poll['question']}\n\n"
        f"Holati: {poll['status']}\n\n"
        f"🔗 Havola:\n{link}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ==========================================
# 7. YANGI OPROS NATIJALARI
# ==========================================

async def show_new_results(query, poll_id):
    poll = get_poll(poll_id)

    if not poll:
        await query.edit_message_text(
            "Opros topilmadi."
        )
        return

    rows = get_results(poll_id)

    total = sum(
        row["votes"]
        for row in rows
    )

    message = (
        f"📊 {poll['title']}\n\n"
        f"Jami ovozlar: {total}\n\n"
    )

    for row in rows:
        count = row["votes"]

        percent = (
            count / total * 100
            if total
            else 0
        )

        message += (
            f"▪️ {row['option_text']}\n"
            f"{count} ta ({percent:.1f}%)\n\n"
        )

    await query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Oprosga qaytish",
                    callback_data=f"admin_view_poll_{poll_id}"
                )
            ]
        ])
    )


# ==========================================
# 8. ADMIN YUBORGAN MATNLARNI QABUL QILISH
# ==========================================

async def handle_admin_text(update, context):

    if not is_admin(update.effective_user.id):
        return

    action = context.user_data.get("admin_action")

    if not action:
        return

    text = update.message.text.strip()

    if not text:
        await update.message.reply_text(
            "Matn bo'sh bo'lmasligi kerak."
        )
        return

    # OPROS NOMI

    if action == "poll_title":

        if len(text) > 200:
            await update.message.reply_text(
                "Opros nomi 200 belgidan oshmasin."
            )
            return

        context.user_data["new_poll"]["title"] = text

        context.user_data["admin_action"] = "poll_question"

        await update.message.reply_text(
            "❓ Endi opros savolini yuboring:"
        )

        return

    # OPROS SAVOLI

    if action == "poll_question":

        if len(text) > 1000:
            await update.message.reply_text(
                "Savol 1000 belgidan oshmasin."
            )
            return

        context.user_data["new_poll"]["question"] = text

        context.user_data["admin_action"] = "poll_options"

        await update.message.reply_text(
            "📝 Javob variantlarini yuboring.\n\n"
            "Har bir variantni alohida qatorda yozing.\n\n"
            "Masalan:\n"
            "1-mahalla\n"
            "2-mahalla\n"
            "3-mahalla"
        )

        return

    # JAVOB VARIANTLARI

    if action == "poll_options":

        options = [
            item.strip()
            for item in text.splitlines()
            if item.strip()
        ]

        if len(options) < 2:
            await update.message.reply_text(
                "Kamida 2 ta variant kiriting."
            )
            return

        if len(options) > 50:
            await update.message.reply_text(
                "Eng ko'pi bilan 50 ta variant mumkin."
            )
            return

        if any(len(item) > 64 for item in options):
            await update.message.reply_text(
                "Har bir variant 64 belgidan oshmasin."
            )
            return

        if len(set(options)) != len(options):
            await update.message.reply_text(
                "Bir xil variantlarni takrorlamang."
            )
            return

        poll_data = context.user_data["new_poll"]

        try:
            poll_id = create_poll(
                poll_data["title"],
                poll_data["question"],
                options
            )

        except Exception:
            logger.exception(
                "Opros yaratishda xatolik"
            )

            await update.message.reply_text(
                "Opros yaratishda xatolik yuz berdi."
            )
            return

        bot_info = await context.bot.get_me()

        link = (
            f"https://t.me/{bot_info.username}"
            f"?start=poll_{poll_id}"
        )

        context.user_data.pop("new_poll", None)
        context.user_data.pop("admin_action", None)

        await update.message.reply_text(
            "✅ YANGI OPROS YARATILDI!\n\n"
            f"📌 Nomi: {poll_data['title']}\n"
            f"❓ Savol: {poll_data['question']}\n"
            f"📝 Variantlar: {len(options)} ta\n\n"
            f"🔗 Havola:\n{link}",
            reply_markup=admin_keyboard()
        )

        return

    # ESKI SAVOLNI O'ZGARTIRISH

    if action == "question":

        save_setting("question", text)

        context.user_data.pop("admin_action", None)

        await update.message.reply_text(
            "✅ Eski savol yangilandi.\n"
            "Avvalgi ovozlar saqlab qolindi.",
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

        if len(options) < 2 or len(options) > 50:
            await update.message.reply_text(
                "Kamida 2 ta va ko'pi bilan 50 ta variant kiriting."
            )
            return

        if any(len(item) > 64 for item in options):
            await update.message.reply_text(
                "Har bir variant 64 belgidan oshmasin."
            )
            return

        if len(set(options)) != len(options):
            await update.message.reply_text(
                "Takroriy variantlar mavjud."
            )
            return

        save_setting(
            "options",
            json.dumps(options, ensure_ascii=False)
        )

        context.user_data.pop("admin_action", None)

        await update.message.reply_text(
            "✅ Eski variantlar yangilandi.\n"
            "Avvalgi ovozlar o'chirilmadi.",
            reply_markup=admin_keyboard()
        )

        return

# ==========================================
# 9. KANAL A'ZOLIGINI TEKSHIRISH
# ==========================================

async def is_subscriber(bot, user_id):
    try:
        member = await bot.get_chat_member(
            CHANNEL,
            user_id
        )

        return (
            member.status in (
                "creator",
                "administrator",
                "member"
            )
            or (
                member.status == "restricted"
                and member.is_member
            )
        )

    except Exception:
        logger.exception("Kanal tekshiruvida xatolik")
        return False


def subscription_keyboard(callback_data):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📢 Kanalga a'zo bo'lish",
                url=CHANNEL_LINK
            )
        ],
        [
            InlineKeyboardButton(
                "✅ A'zolikni tekshirish",
                callback_data=callback_data
            )
        ]
    ])


# ==========================================
# 10. START VA DEEP-LINK
# ==========================================

async def start(update, context):

    if not update.message:
        return

    if context.args and context.args[0].startswith("poll_"):

        try:
            poll_id = int(context.args[0][5:])

        except ValueError:
            await update.message.reply_text(
                "Opros havolasi noto'g'ri."
            )
            return

        poll = get_poll(poll_id)

        if not poll or poll["status"] != "active":
            await update.message.reply_text(
                "Bu opros mavjud emas yoki yopilgan."
            )
            return

        await update.message.reply_text(
            f"📊 {poll['title']}\n\n"
            f"❓ {poll['question']}\n\n"
            "Ovoz berish uchun kanalimizga a'zo bo'ling.",
            reply_markup=subscription_keyboard(
                f"check_poll_{poll_id}"
            )
        )

        return

    await update.message.reply_text(
        "Assalomu alaykum!\n\n"
        "Ovoz berishda qatnashish uchun "
        "avval kanalimizga a'zo bo'ling.",
        reply_markup=subscription_keyboard("check")
    )


# ==========================================
# 11. ESKI NATIJALARNI KO'RISH
# ==========================================

async def show_old_results(target):

    conn = legacy_db()

    try:
        rows = conn.execute(
            """
            SELECT choice, COUNT(*)
            FROM votes
            GROUP BY choice
            """
        ).fetchall()

        total = conn.execute(
            "SELECT COUNT(*) FROM votes"
        ).fetchone()[0]

    finally:
        conn.close()

    message = (
        "📊 ESKI OVOZ BERISH NATIJALARI\n\n"
        f"Jami ovozlar: {total}\n\n"
    )

    options = list(get_options())

    for choice, count in rows:
        if choice not in options:
            options.append(choice)

    for option in options:

        count = next(
            (
                row[1]
                for row in rows
                if row[0] == option
            ),
            0
        )

        percent = count / total * 100 if total else 0

        message += (
            f"▪️ {option}\n"
            f"{count} ta ({percent:.1f}%)\n\n"
        )

    await target.reply_text(message)


# ==========================================
# 12. NATIJA BUYRUG'I
# ==========================================

async def results(update, context):

    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "Bu buyruq faqat administrator uchun."
        )
        return

    await show_old_results(update.message)


# ==========================================
# 13. TELEGRAM ID
# ==========================================

async def myid(update, context):

    await update.message.reply_text(
        f"Sizning Telegram ID raqamingiz: "
        f"{update.effective_user.id}"
    )


# ==========================================
# 14. BEKOR QILISH
# ==========================================

async def cancel(update, context):

    if not is_admin(update.effective_user.id):
        return

    context.user_data.pop("admin_action", None)
    context.user_data.pop("new_poll", None)

    await update.message.reply_text(
        "❌ Amal bekor qilindi.",
        reply_markup=admin_keyboard()
    )


# ==========================================
# 15. CALLBACK TUGMALARI
# ==========================================

async def buttons(update, context):

    query = update.callback_query

    user_id = query.from_user.id
    data = query.data or ""

    # ADMIN PANEL

    if data.startswith("admin_"):

        if not is_admin(user_id):
            await query.answer(
                "Ruxsat yo'q!",
                show_alert=True
            )
            return

        await query.answer()

        if data == "admin_new_poll":
            await new_poll_start(query, context)
            return

        if data == "admin_all_polls":
            await show_all_polls(query)
            return

        if data.startswith("admin_view_poll_"):

            try:
                poll_id = int(
                    data[len("admin_view_poll_"):]
                )

                await show_poll_details(
                    query,
                    poll_id,
                    context
                )

            except ValueError:
                await query.edit_message_text(
                    "Opros ID noto'g'ri."
                )

            return

        if data.startswith("admin_poll_results_"):

            try:
                poll_id = int(
                    data[len("admin_poll_results_"):]
                )

                await show_new_results(
                    query,
                    poll_id
                )

            except ValueError:
                await query.edit_message_text(
                    "Opros ID noto'g'ri."
                )

            return

        if data.startswith("admin_close_poll_"):

            try:
                poll_id = int(
                    data[len("admin_close_poll_"):]
                )

                if close_poll(poll_id):
                    await show_poll_details(
                        query,
                        poll_id,
                        context
                    )
                else:
                    await query.edit_message_text(
                        "Opros topilmadi."
                    )

            except ValueError:
                await query.edit_message_text(
                    "Opros ID noto'g'ri."
                )

            return

        if data == "admin_question":

            context.user_data["admin_action"] = "question"

            await query.edit_message_text(
                "✏️ Yangi savolni yuboring.\n\n"
                "Bekor qilish: /cancel"
            )

            return

        if data == "admin_options":

            context.user_data["admin_action"] = "options"

            await query.edit_message_text(
                "📝 Yangi javob variantlarini "
                "alohida qatorlarda yuboring.\n\n"
                "Bekor qilish: /cancel"
            )

            return

        if data == "admin_results":

            await query.delete_message()
            await show_old_results(query.message)

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
            context.user_data.pop("new_poll", None)

            await query.edit_message_text(
                "❌ Amal bekor qilindi.",
                reply_markup=admin_keyboard()
            )

            return

        return

    # ESKI OPROS SAHIFALARI

    if data.startswith("poll_page_"):

        await query.answer()

        try:
            page = int(data[len("poll_page_"):])

            await show_old_poll(query, page)

        except ValueError:
            await query.edit_message_text(
                "Sahifa noto'g'ri."
            )

        return

    # YANGI OPROS A'ZOLIGINI TEKSHIRISH

    if data.startswith("check_poll_"):

        try:
            poll_id = int(
                data[len("check_poll_"):]
            )

        except ValueError:
            await query.answer(
                "Opros ID noto'g'ri.",
                show_alert=True
            )
            return

        poll = get_poll(poll_id)

        if not poll or poll["status"] != "active":
            await query.answer(
                "Opros yopilgan yoki mavjud emas.",
                show_alert=True
            )
            return

        if not await is_subscriber(context.bot, user_id):
            await query.answer(
                "Avval kanalga a'zo bo'ling!",
                show_alert=True
            )
            return

        await query.answer("A'zolik tasdiqlandi!")

        keyboard = []

        for option in poll["options"]:

            keyboard.append([
                InlineKeyboardButton(
                    option["option_text"],
                    callback_data=(
                        f"new_vote_{poll_id}_{option['id']}"
                    )
                )
            ])

        await query.edit_message_text(
            f"📊 {poll['title']}\n\n"
            f"❓ {poll['question']}\n\n"
            "Javobingizni tanlang:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return

    # ESKI OPROS A'ZOLIGINI TEKSHIRISH

    if data == "check":

        if not await is_subscriber(context.bot, user_id):
            await query.answer(
                "Avval kanalga a'zo bo'ling!",
                show_alert=True
            )
            return

        await query.answer("A'zolik tasdiqlandi!")

        await show_old_poll(query)

        return

    # YANGI OPROS OVOZ BERISH

    if data.startswith("new_vote_"):

        try:
            parts = data.split("_")

            if len(parts) != 4:
                raise ValueError

            poll_id = int(parts[2])
            option_id = int(parts[3])

        except ValueError:
            await query.answer(
                "Ovoz ma'lumoti noto'g'ri.",
                show_alert=True
            )
            return

        if not await is_subscriber(context.bot, user_id):
            await query.answer(
                "Avval kanalga a'zo bo'ling!",
                show_alert=True
            )
            return

        result = cast_vote(
            poll_id,
            user_id,
            option_id
        )

        if result == "success":

            await query.answer(
                "Ovozingiz qabul qilindi!"
            )

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

    # ESKI OPROS OVOZ BERISH

    if data.startswith("vote_"):

        if not await is_subscriber(context.bot, user_id):
            await query.answer(
                "Ovoz berish uchun kanalga a'zo bo'ling!",
                show_alert=True
            )
            return

        try:
            index = int(data[len("vote_"):])

            options = get_options()

            if index < 0 or index >= len(options):
                raise ValueError

            choice = options[index]

        except (ValueError, IndexError):
            await query.answer(
                "Javob varianti mavjud emas.",
                show_alert=True
            )
            return

        conn = legacy_db()

        try:
            conn.execute(
                """
                INSERT INTO votes (user_id, choice)
                VALUES (?, ?)
                """,
                (user_id, choice)
            )

            conn.commit()

            await query.answer(
                "Ovozingiz qabul qilindi!"
            )

            await query.edit_message_text(
                "✅ Ovozingiz qabul qilindi!\n\n"
                f"Tanlovingiz: {choice}"
            )

        except sqlite3.IntegrityError:

            await query.answer(
                "Siz avval ovoz bergansiz!",
                show_alert=True
            )

        finally:
            conn.close()

        return

    await query.answer(
        "Noma'lum tugma.",
        show_alert=True
    )


# ==========================================
# 16. XATOLARNI YOZIB BORISH
# ==========================================

async def error_handler(update, context):

    logger.error(
        "Botda xatolik yuz berdi:",
        exc_info=context.error
    )


# ==========================================
# 17. BOTNI ISHGA TUSHIRISH
# ==========================================

def main():

    logger.info("Ma'lumotlar bazasi tayyorlanmoqda...")

    init_legacy_db()
    init_db()

    logger.info(
        "Ma'lumotlar bazasi manzili: %s",
        os.path.abspath(DB_PATH)
    )

    app = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("admin", admin_menu)
    )

    app.add_handler(
        CommandHandler("natija", results)
    )

    app.add_handler(
        CommandHandler("myid", myid)
    )

    app.add_handler(
        CommandHandler("cancel", cancel)
    )

    app.add_handler(
        CallbackQueryHandler(buttons)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_admin_text
        )
    )

    app.add_error_handler(error_handler)

    logger.info("Telegram bot ishga tushmoqda...")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
