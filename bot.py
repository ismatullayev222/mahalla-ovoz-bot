import json
import logging
import os
import sqlite3
from math import ceil

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
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


# =========================================================
# SOZLAMALAR
# =========================================================

TOKEN = (os.getenv("BOT_TOKEN") or "").strip()

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
except ValueError:
    ADMIN_ID = 0

CHANNEL = os.getenv("CHANNEL", "@Karmana_yoshlaruz").strip()
CHANNEL_LINK = os.getenv(
    "CHANNEL_LINK",
    "https://t.me/Karmana_yoshlaruz",
).strip()
DB_PATH = os.getenv("DB_PATH", "votes.db")

DEFAULT_QUESTION = (
    "Sizningcha, Karmana tumanidagi eng faol mahalla yettiligi?"
)
DEFAULT_OPTIONS = ["A variant", "B variant", "C variant"]

POLL_LIST_PAGE_SIZE = 10
OLD_POLL_PAGE_SIZE = 8
TELEGRAM_MESSAGE_LIMIT = 3500

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# =========================================================
# ESKI OVOZ BERISH BAZASI
# DIQQAT: mavjud votes yozuvlari o'chirilmaydi yoki yangilanmaydi.
# =========================================================

def legacy_db():
    folder = os.path.dirname(os.path.abspath(DB_PATH))
    os.makedirs(folder, exist_ok=True)

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_legacy_db():
    conn = legacy_db()
    try:
        # IF NOT EXISTS mavjud jadval yoki uning yozuvlarini o'zgartirmaydi.
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
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_setting(key, default):
    conn = legacy_db()
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
        ).fetchone()
        return row["value"] if row else default
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
            (key, value),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_question():
    return get_setting("question", DEFAULT_QUESTION)


def get_options():
    default_json = json.dumps(DEFAULT_OPTIONS, ensure_ascii=False)
    try:
        value = json.loads(get_setting("options", default_json))
        if not isinstance(value, list):
            return DEFAULT_OPTIONS.copy()

        options = [
            str(item).strip()
            for item in value
            if isinstance(item, str) and item.strip()
        ]
        if not options or len(options) > 50 or len(set(options)) != len(options):
            return DEFAULT_OPTIONS.copy()
        if any(len(item) > 64 for item in options):
            return DEFAULT_OPTIONS.copy()
        return options
    except (TypeError, ValueError, json.JSONDecodeError):
        return DEFAULT_OPTIONS.copy()


def is_admin(user_id):
    return bool(ADMIN_ID) and user_id == ADMIN_ID


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Yangi opros yaratish", callback_data="admin_new_poll")],
        [InlineKeyboardButton("📋 Barcha oproslar", callback_data="admin_all_polls")],
        [InlineKeyboardButton("✏️ Eski savolni o'zgartirish", callback_data="admin_question")],
        [InlineKeyboardButton("📝 Eski javoblarni o'zgartirish", callback_data="admin_options")],
        [InlineKeyboardButton("📊 Eski natijalarni ko'rish", callback_data="admin_results")],
        [InlineKeyboardButton("❌ Bekor qilish", callback_data="admin_cancel")],
    ])


# =========================================================
# UMUMIY YORDAMCHI FUNKSIYALAR
# =========================================================

def split_message(text, limit=TELEGRAM_MESSAGE_LIMIT):
    """Split long text at line boundaries to stay below Telegram's limit."""
    lines = str(text).splitlines() or [""]
    chunks = []
    current = ""

    for line in lines:
        candidate = line if not current else current + "\n" + line
        if len(candidate) <= limit:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        # A single line can itself be longer than the limit.
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current = line

    if current or not chunks:
        chunks.append(current)

    return chunks


async def send_result_chunks_from_message(message, text, edit_first=False):
    chunks = split_message(text)

    if edit_first:
        await message.edit_text(chunks[0])
    else:
        await message.reply_text(chunks[0])

    for chunk in chunks[1:]:
        await message.reply_text(chunk)


async def send_result_chunks_from_query(query, context, text, reply_markup=None):
    chunks = split_message(text)
    await query.edit_message_text(
        chunks[0],
        reply_markup=reply_markup,
    )

    chat_id = query.message.chat_id if query.message else None
    if chat_id is not None:
        for chunk in chunks[1:]:
            await context.bot.send_message(chat_id=chat_id, text=chunk)


# =========================================================
# ADMIN PANEL
# =========================================================

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin(update.effective_user.id):
        if update.effective_message:
            await update.effective_message.reply_text(
                "Bu bo'lim faqat administrator uchun."
            )
        return

    await update.effective_message.reply_text(
        "⚙️ ADMIN BOSHQARUV PANELI",
        reply_markup=admin_keyboard(),
    )


async def new_poll_start(query, context):
    context.user_data["new_poll"] = {}
    context.user_data["admin_action"] = "poll_title"

    await query.edit_message_text(
        "➕ YANGI OPROS YARATISH\n\n"
        "Avval opros nomini yuboring.\n\n"
        "Bekor qilish uchun /cancel yuboring."
    )


async def show_all_polls(query, page=0):
    polls = get_all_polls()

    if not polls:
        await query.edit_message_text(
            "Hozircha oproslar mavjud emas.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Admin panel", callback_data="admin_back")
            ]]),
        )
        return

    page_count = max(1, ceil(len(polls) / POLL_LIST_PAGE_SIZE))
    page = max(0, min(page, page_count - 1))
    start = page * POLL_LIST_PAGE_SIZE
    page_polls = polls[start:start + POLL_LIST_PAGE_SIZE]

    keyboard = []
    for poll in page_polls:
        status_icon = "🟢" if poll["status"] == "active" else "🔴"
        title = str(poll["title"])[:55]
        keyboard.append([InlineKeyboardButton(
            f"{status_icon} {title} · {poll['total_votes']} ovoz",
            callback_data=f"admin_view_poll_{poll['id']}",
        )])

    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton(
            "⬅️ Oldingi", callback_data=f"admin_polls_page_{page - 1}"
        ))
    if page < page_count - 1:
        navigation.append(InlineKeyboardButton(
            "Keyingi ➡️", callback_data=f"admin_polls_page_{page + 1}"
        ))
    if navigation:
        keyboard.append(navigation)

    keyboard.append([InlineKeyboardButton(
        "⬅️ Admin panel", callback_data="admin_back"
    )])

    await query.edit_message_text(
        f"📋 BARCHA OPROSLAR\nSahifa: {page + 1}/{page_count}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_poll_details(query, poll_id, context):
    poll = get_poll(poll_id)

    if not poll:
        await query.edit_message_text("Opros topilmadi.")
        return

    bot_info = await context.bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=poll_{poll_id}"

    status_text = "Faol" if poll["status"] == "active" else "Yopilgan"
    keyboard = [
        [InlineKeyboardButton("🔗 Opros havolasi", url=link)],
        [InlineKeyboardButton(
            "📊 Natijalarni ko'rish",
            callback_data=f"admin_poll_results_{poll_id}",
        )],
    ]

    if poll["status"] == "active":
        keyboard.append([InlineKeyboardButton(
            "🔒 Oprosni yopish",
            callback_data=f"admin_close_poll_{poll_id}",
        )])

    keyboard.append([InlineKeyboardButton(
        "⬅️ Barcha oproslar", callback_data="admin_all_polls"
    )])

    await query.edit_message_text(
        f"📊 {poll['title']}\n\n"
        f"❓ {poll['question']}\n\n"
        f"Holati: {status_text}\n"
        f"Yaratilgan vaqti (UTC): {poll['created_at']}\n\n"
        f"🔗 Havola:\n{link}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_new_results(query, poll_id, context):
    poll = get_poll(poll_id)
    if not poll:
        await query.edit_message_text("Opros topilmadi.")
        return

    rows = get_results(poll_id)
    total = sum(int(row["votes"]) for row in rows)

    lines = [
        f"📊 {poll['title']}",
        f"Holati: {'Faol' if poll['status'] == 'active' else 'Yopilgan'}",
        f"Jami ovozlar: {total}",
        "",
    ]

    for row in rows:
        count = int(row["votes"])
        percent = (count / total * 100) if total else 0
        lines.append(f"▪️ {row['option_text']}")
        lines.append(f"{count} ta ({percent:.1f}%)")
        lines.append("")

    await send_result_chunks_from_query(
        query,
        context,
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "⬅️ Oprosga qaytish",
                callback_data=f"admin_view_poll_{poll_id}",
            )
        ]]),
    )


# =========================================================
# ADMIN YUBORGAN MATNLARNI QABUL QILISH
# =========================================================

async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin(update.effective_user.id):
        return
    if not update.message or not update.message.text:
        return

    action = context.user_data.get("admin_action")
    if not action:
        return

    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("Matn bo'sh bo'lmasligi kerak.")
        return

    if action == "poll_title":
        if len(text) > 200:
            await update.message.reply_text("Opros nomi 200 belgidan oshmasin.")
            return
        context.user_data.setdefault("new_poll", {})["title"] = text
        context.user_data["admin_action"] = "poll_question"
        await update.message.reply_text("❓ Endi opros savolini yuboring:")
        return

    if action == "poll_question":
        if len(text) > 1000:
            await update.message.reply_text("Savol 1000 belgidan oshmasin.")
            return
        context.user_data.setdefault("new_poll", {})["question"] = text
        context.user_data["admin_action"] = "poll_options"
        await update.message.reply_text(
            "📝 Javob variantlarini yuboring.\n\n"
            "Har bir variantni alohida qatorda yozing.\n"
            "Kamida 2 ta, ko'pi bilan 50 ta variant kiriting."
        )
        return

    if action == "poll_options":
        options = [item.strip() for item in text.splitlines() if item.strip()]
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
            await update.message.reply_text("Takroriy variantlar kiritilmang.")
            return

        poll_data = context.user_data.get("new_poll", {})
        if not poll_data.get("title") or not poll_data.get("question"):
            context.user_data.pop("new_poll", None)
            context.user_data.pop("admin_action", None)
            await update.message.reply_text(
                "Opros ma'lumotlari topilmadi. Iltimos, qaytadan boshlang.",
                reply_markup=admin_keyboard(),
            )
            return

        try:
            bot_info = await context.bot.get_me()
            poll_id = create_poll(
                poll_data["title"],
                poll_data["question"],
                options,
            )
            link = f"https://t.me/{bot_info.username}?start=poll_{poll_id}"
        except Exception:
            logger.exception("Yangi opros yaratishda xatolik")
            await update.message.reply_text(
                "Opros yaratishda xatolik yuz berdi. Ma'lumotlarni tekshirib, qayta urinib ko'ring."
            )
            return

        context.user_data.pop("new_poll", None)
        context.user_data.pop("admin_action", None)

        await update.message.reply_text(
            "✅ YANGI OPROS YARATILDI!\n\n"
            f"📌 Nomi: {poll_data['title']}\n"
            f"❓ Savol: {poll_data['question']}\n"
            f"📝 Variantlar: {len(options)} ta\n\n"
            f"🔗 Havola:\n{link}",
            reply_markup=admin_keyboard(),
        )
        return

    if action == "question":
        if len(text) > 1000:
            await update.message.reply_text("Savol 1000 belgidan oshmasin.")
            return
        save_setting("question", text)
        context.user_data.pop("admin_action", None)
        await update.message.reply_text(
            "✅ Eski savol yangilandi.\nAvvalgi ovozlar saqlab qolindi.",
            reply_markup=admin_keyboard(),
        )
        return

    if action == "options":
        options = [item.strip() for item in text.splitlines() if item.strip()]
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
            await update.message.reply_text("Takroriy variantlar mavjud.")
            return

        save_setting("options", json.dumps(options, ensure_ascii=False))
        context.user_data.pop("admin_action", None)
        await update.message.reply_text(
            "✅ Eski variantlar yangilandi.\nAvvalgi ovozlar o'chirilmadi.",
            reply_markup=admin_keyboard(),
        )


# =========================================================
# KANALGA A'ZOLIKNI TEKSHIRISH
# =========================================================

async def is_subscriber(bot, user_id):
    try:
        member = await bot.get_chat_member(CHANNEL, user_id)
        return (
            member.status in ("creator", "administrator", "member")
            or (
                member.status == "restricted"
                and bool(getattr(member, "is_member", False))
            )
        )
    except Exception:
        logger.exception(
            "Kanal a'zoligini tekshirishda xatolik. "
            "Bot kanalga administrator sifatida qo'shilganini tekshiring."
        )
        return False


def subscription_keyboard(callback_data):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Kanalga a'zo bo'lish", url=CHANNEL_LINK)],
        [InlineKeyboardButton("✅ A'zolikni tekshirish", callback_data=callback_data)],
    ])


# =========================================================
# START VA DEEP-LINK
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    if context.args and context.args[0].startswith("poll_"):
        raw_id = context.args[0][5:]
        try:
            poll_id = int(raw_id)
            if poll_id <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Opros havolasi noto'g'ri.")
            return

        poll = get_poll(poll_id)
        if not poll or poll["status"] != "active":
            await update.message.reply_text("Bu opros mavjud emas yoki yopilgan.")
            return

        await update.message.reply_text(
            f"📊 {poll['title']}\n\n"
            f"❓ {poll['question']}\n\n"
            "Ovoz berish uchun avval kanalimizga a'zo bo'ling.",
            reply_markup=subscription_keyboard(f"check_poll_{poll_id}"),
        )
        return

    await update.message.reply_text(
        "Assalomu alaykum!\n\n"
        "Ovoz berishda qatnashish uchun avval kanalimizga a'zo bo'ling.",
        reply_markup=subscription_keyboard("check"),
    )


# =========================================================
# ESKI OPROS VA NATIJALAR
# =========================================================

def legacy_user_has_voted(user_id):
    conn = legacy_db()
    try:
        row = conn.execute(
            "SELECT 1 FROM votes WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


async def show_old_poll(query, page=0):
    options = get_options()
    question = get_question()
    page_count = max(1, ceil(len(options) / OLD_POLL_PAGE_SIZE))
    page = max(0, min(page, page_count - 1))

    if legacy_user_has_voted(query.from_user.id):
        await query.edit_message_text(
            "✅ Siz ushbu eski ovoz berishda avval qatnashgansiz.\n\n"
            "Rahmat!"
        )
        return

    start_index = page * OLD_POLL_PAGE_SIZE
    page_options = options[start_index:start_index + OLD_POLL_PAGE_SIZE]

    keyboard = []
    for index, option in enumerate(page_options, start=start_index):
        keyboard.append([InlineKeyboardButton(
            option,
            callback_data=f"vote_{index}",
        )])

    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton(
            "⬅️ Oldingi", callback_data=f"poll_page_{page - 1}"
        ))
    if page < page_count - 1:
        navigation.append(InlineKeyboardButton(
            "Keyingi ➡️", callback_data=f"poll_page_{page + 1}"
        ))
    if navigation:
        keyboard.append(navigation)

    await query.edit_message_text(
        f"📊 ESKI OVOZ BERISH\n\n❓ {question}\n\n"
        "Javobingizni tanlang:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_old_results(target, edit_first=False, context=None):
    conn = legacy_db()
    try:
        rows = conn.execute(
            """
            SELECT choice, COUNT(*) AS vote_count
            FROM votes
            GROUP BY choice
            ORDER BY choice
            """
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0]
    finally:
        conn.close()

    counts = {row["choice"]: int(row["vote_count"]) for row in rows}
    options = get_options()
    for choice in counts:
        if choice not in options:
            options.append(choice)

    lines = [
        "📊 ESKI OVOZ BERISH NATIJALARI",
        f"Jami ovozlar: {total}",
        "",
    ]

    for option in options:
        count = counts.get(option, 0)
        percent = count / total * 100 if total else 0
        lines.extend([f"▪️ {option}", f"{count} ta ({percent:.1f}%)", ""])

    text = "\n".join(lines)

    if edit_first and context is not None:
        await send_result_chunks_from_query(target, context, text)
    else:
        await send_result_chunks_from_message(target, text, edit_first=edit_first)


async def results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            "Bu buyruq faqat administrator uchun."
        )
        return
    await show_old_results(update.effective_message)


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        f"Sizning Telegram ID raqamingiz: {update.effective_user.id}"
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin(update.effective_user.id):
        return

    context.user_data.pop("admin_action", None)
    context.user_data.pop("new_poll", None)
    await update.effective_message.reply_text(
        "❌ Amal bekor qilindi.",
        reply_markup=admin_keyboard(),
    )


# =========================================================
# CALLBACK TUGMALARI
# =========================================================

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None:
        return

    user_id = query.from_user.id
    data = query.data or ""

    # ADMIN CALLBACKLARI
    if data.startswith("admin_"):
        if not is_admin(user_id):
            await query.answer("Ruxsat yo'q!", show_alert=True)
            return

        await query.answer()

        if data == "admin_new_poll":
            await new_poll_start(query, context)
            return

        if data == "admin_all_polls":
            await show_all_polls(query, 0)
            return

        if data.startswith("admin_polls_page_"):
            try:
                page = int(data[len("admin_polls_page_"):])
                await show_all_polls(query, page)
            except ValueError:
                await query.edit_message_text("Sahifa raqami noto'g'ri.")
            return

        if data.startswith("admin_view_poll_"):
            try:
                poll_id = int(data[len("admin_view_poll_"):])
                await show_poll_details(query, poll_id, context)
            except ValueError:
                await query.edit_message_text("Opros ID noto'g'ri.")
            return

        if data.startswith("admin_poll_results_"):
            try:
                poll_id = int(data[len("admin_poll_results_"):])
                await show_new_results(query, poll_id, context)
            except ValueError:
                await query.edit_message_text("Opros ID noto'g'ri.")
            return

        if data.startswith("admin_close_poll_"):
            try:
                poll_id = int(data[len("admin_close_poll_"):])
                poll = get_poll(poll_id)
                if poll is None:
                    await query.edit_message_text("Opros topilmadi.")
                else:
                    close_poll(poll_id)
                    await show_poll_details(query, poll_id, context)
            except ValueError:
                await query.edit_message_text("Opros ID noto'g'ri.")
            return

        if data == "admin_question":
            context.user_data["admin_action"] = "question"
            await query.edit_message_text(
                "✏️ Yangi savolni yuboring.\n\nBekor qilish: /cancel"
            )
            return

        if data == "admin_options":
            context.user_data["admin_action"] = "options"
            await query.edit_message_text(
                "📝 Yangi javob variantlarini alohida qatorlarda yuboring.\n\n"
                "Bekor qilish: /cancel"
            )
            return

        if data == "admin_results":
            await show_old_results(query.message, edit_first=True)
            return

        if data == "admin_back":
            context.user_data.pop("admin_action", None)
            context.user_data.pop("new_poll", None)
            await query.edit_message_text(
                "⚙️ ADMIN BOSHQARUV PANELI",
                reply_markup=admin_keyboard(),
            )
            return

        if data == "admin_cancel":
            context.user_data.pop("admin_action", None)
            context.user_data.pop("new_poll", None)
            await query.edit_message_text(
                "❌ Amal bekor qilindi.",
                reply_markup=admin_keyboard(),
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
            await query.edit_message_text("Sahifa raqami noto'g'ri.")
        return

    # YANGI OPROS: A'ZOLIKNI TEKSHIRISH
    if data.startswith("check_poll_"):
        try:
            poll_id = int(data[len("check_poll_"):])
            if poll_id <= 0:
                raise ValueError
        except ValueError:
            await query.answer("Opros ID noto'g'ri.", show_alert=True)
            return

        poll = get_poll(poll_id)
        if not poll or poll["status"] != "active":
            await query.answer("Bu opros yopilgan yoki mavjud emas.", show_alert=True)
            return

        if not await is_subscriber(context.bot, user_id):
            await query.answer("Avval kanalga a'zo bo'ling!", show_alert=True)
            return

        await query.answer("A'zolik tasdiqlandi!")
        keyboard = [
            [InlineKeyboardButton(
                option["option_text"],
                callback_data=f"new_vote_{poll_id}_{option['id']}",
            )]
            for option in poll["options"]
        ]

        await query.edit_message_text(
            f"📊 {poll['title']}\n\n"
            f"❓ {poll['question']}\n\n"
            "Javobingizni tanlang:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    # ESKI OPROS: A'ZOLIKNI TEKSHIRISH
    if data == "check":
        if not await is_subscriber(context.bot, user_id):
            await query.answer("Avval kanalga a'zo bo'ling!", show_alert=True)
            return

        await query.answer("A'zolik tasdiqlandi!")
        await show_old_poll(query, 0)
        return

    # YANGI OPROS: OVOZ BERISH
    if data.startswith("new_vote_"):
        try:
            parts = data.split("_")
            if len(parts) != 4:
                raise ValueError
            poll_id = int(parts[2])
            option_id = int(parts[3])
            if poll_id <= 0 or option_id <= 0:
                raise ValueError
        except ValueError:
            await query.answer("Ovoz ma'lumoti noto'g'ri.", show_alert=True)
            return

        if not await is_subscriber(context.bot, user_id):
            await query.answer(
                "Ovoz berish uchun kanalga a'zo bo'ling!",
                show_alert=True,
            )
            return

        try:
            vote_result = cast_vote(poll_id, user_id, option_id)
        except Exception:
            logger.exception("Ovoz saqlashda xatolik")
            await query.answer(
                "Ovoz saqlanmadi. Iltimos, keyinroq urinib ko'ring.",
                show_alert=True,
            )
            return

        if vote_result == "success":
            await query.answer("Ovozingiz qabul qilindi!")
            await query.edit_message_text("✅ Ovozingiz muvaffaqiyatli qabul qilindi!")
        elif vote_result == "already_voted":
            await query.answer(
                "Siz bu oprosda avval ovoz bergansiz!",
                show_alert=True,
            )
        elif vote_result == "closed":
            await query.answer("Bu opros yopilgan!", show_alert=True)
        else:
            await query.answer("Javob varianti topilmadi!", show_alert=True)
        return

    # ESKI OPROS: OVOZ BERISH
    if data.startswith("vote_"):
        if not await is_subscriber(context.bot, user_id):
            await query.answer(
                "Ovoz berish uchun kanalga a'zo bo'ling!",
                show_alert=True,
            )
            return

        try:
            index = int(data[len("vote_"):])
            options = get_options()
            if index < 0 or index >= len(options):
                raise ValueError
            choice = options[index]
        except (ValueError, IndexError):
            await query.answer("Javob varianti mavjud emas.", show_alert=True)
            return

        conn = legacy_db()
        try:
            conn.execute(
                "INSERT INTO votes (user_id, choice) VALUES (?, ?)",
                (user_id, choice),
            )
            conn.commit()
            await query.answer("Ovozingiz qabul qilindi!")
            await query.edit_message_text(
                "✅ Ovozingiz qabul qilindi!\n\n"
                f"Tanlovingiz: {choice}"
            )
        except sqlite3.IntegrityError:
            await query.answer(
                "Siz avval ovoz bergansiz!",
                show_alert=True,
            )
        except Exception:
            conn.rollback()
            logger.exception("Eski ovozni saqlashda xatolik")
            await query.answer(
                "Ovoz saqlanmadi. Iltimos, keyinroq urinib ko'ring.",
                show_alert=True,
            )
        finally:
            conn.close()
        return

    await query.answer("Noma'lum tugma.", show_alert=True)


# =========================================================
# XATOLARNI QAYD ETISH
# =========================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    error = context.error
    if error is not None:
        logger.error(
            "Botda ishlov berilmagan xatolik yuz berdi",
            exc_info=(type(error), error, error.__traceback__),
        )


# =========================================================
# BOTNI ISHGA TUSHIRISH
# =========================================================

def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN Railway Variables bo'limida belgilanmagan.")
    if ADMIN_ID <= 0:
        raise RuntimeError("ADMIN_ID to'g'ri Telegram ID bilan belgilanmagan.")
    if not CHANNEL or not CHANNEL_LINK:
        raise RuntimeError("CHANNEL va CHANNEL_LINK sozlamalarini tekshiring.")

    logger.info("Ma'lumotlar bazasi tayyorlanmoqda: %s", os.path.abspath(DB_PATH))

    # Ikkala tizim ham bir xil SQLite faylidan foydalanadi.
    # Bu funksiyalar mavjud legacy votes jadvalidagi yozuvlarni o'chirmaydi.
    init_legacy_db()
    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_menu))
    app.add_handler(CommandHandler("natija", results))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_admin_text,
        )
    )
    app.add_error_handler(error_handler)

    logger.info("Telegram bot ishga tushmoqda...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
