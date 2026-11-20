import json
import logging
import os
import re
import sqlite3
from datetime import datetime
from math import ceil
from zoneinfo import ZoneInfo

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from database import (
    cast_vote,
    close_poll,
    create_poll,
    get_all_polls,
    get_poll,
    get_results,
    get_user_phone,
    init_db,
    save_user_phone,
)


# =========================================================
# SOZLAMALAR
# =========================================================

TOKEN = (os.getenv("BOT_TOKEN") or "").strip()

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
except ValueError:
    ADMIN_ID = 0


def _parse_csv_list(raw_value):
    return [item.strip() for item in (raw_value or "").split(",") if item.strip()]


SINGLE_CHANNEL = (os.getenv("CHANNEL") or "@Karmana_yoshlaruz").strip()
SINGLE_CHANNEL_LINK = (
    os.getenv("CHANNEL_LINK") or "https://t.me/Karmana_yoshlaruz"
).strip()

CHANNELS = _parse_csv_list(os.getenv("CHANNELS")) or ([SINGLE_CHANNEL] if SINGLE_CHANNEL else [])
CHANNEL_LINKS = _parse_csv_list(os.getenv("CHANNEL_LINKS"))

DB_PATH = os.getenv("DB_PATH", "votes.db")
TZ_NAME = (os.getenv("BOT_TIMEZONE") or "Asia/Tashkent").strip()

DEFAULT_QUESTION = (
    "Sizningcha, Karmana tumanidagi eng faol mahalla yettiligi?"
)
DEFAULT_OPTIONS = ["A variant", "B variant", "C variant"]

POLL_LIST_PAGE_SIZE = 10
OLD_POLL_PAGE_SIZE = 8
TELEGRAM_MESSAGE_LIMIT = 3500

BTN_VOTE = "Ovoz berish"
BTN_PHONE = "📱 Telefon raqamni tasdiqlash"
BTN_CONFIRM_VOTE = "Ovoz berishni tasdiqlash"
BTN_CANCEL_VOTE = "Bekor qilish"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def _get_timezone():
    try:
        return ZoneInfo(TZ_NAME)
    except Exception:
        return ZoneInfo("Asia/Tashkent")


def _normalize_channel_chat_id(channel):
    value = str(channel).strip()
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if value.startswith("https://t.me/"):
        username = value.removeprefix("https://t.me/").strip("/").split("/")[0]
        if username:
            return f"@{username}"
    return value


def _build_channel_targets():
    targets = []
    for idx, ch in enumerate(CHANNELS):
        chat_id = _normalize_channel_chat_id(ch)
        if idx < len(CHANNEL_LINKS) and CHANNEL_LINKS[idx]:
            url = CHANNEL_LINKS[idx]
        elif str(ch).startswith("http"):
            url = str(ch).strip()
        elif str(ch).startswith("@"):
            url = f"https://t.me/{str(ch).lstrip('@')}"
        elif SINGLE_CHANNEL and ch == SINGLE_CHANNEL and SINGLE_CHANNEL_LINK:
            url = SINGLE_CHANNEL_LINK
        else:
            url = None
        targets.append((chat_id, url))
    return targets


CHANNEL_TARGETS = _build_channel_targets()


# =========================================================
# VAQT VA SO'ROVNOMA HOLATI YORDAMCHILARI
# =========================================================

def parse_deadline(ends_at_str):
    """Parse 'DD.MM.YYYY HH:MM' or ISO datetime into a timezone-aware datetime."""
    if not ends_at_str:
        return None
    raw = str(ends_at_str).strip()
    if not raw or raw == "-":
        return None

    tz = _get_timezone()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=tz)
        except ValueError:
            continue

    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=tz)
        return dt.astimezone(tz)
    except ValueError:
        return None


def is_poll_expired(poll):
    if not poll:
        return True
    if poll.get("status") != "active":
        return True
    deadline = parse_deadline(poll.get("ends_at"))
    if deadline is not None:
        now_local = datetime.now(_get_timezone())
        if now_local >= deadline:
            return True
    return False


def format_expired_message(poll):
    ends_text = (poll.get("ends_at") or "").strip()
    if ends_text and ends_text != "-":
        return (
            "Ushbu so'rovnoma o'z yakuniga yetgan.\n"
            f"Tugash vaqti: {ends_text}"
        )
    return "Ushbu so'rovnoma o'z yakuniga yetgan."


# =========================================================
# KLAVIATURALAR
# =========================================================

def phone_request_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton(BTN_PHONE, request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton(BTN_VOTE)]],
        resize_keyboard=True,
    )


def confirm_vote_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_CONFIRM_VOTE)],
            [KeyboardButton(BTN_CANCEL_VOTE)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def multi_channel_keyboard(check_callback_data):
    rows = []
    idx = 1
    for _, url in CHANNEL_TARGETS:
        if url:
            rows.append([InlineKeyboardButton(f"{idx} - Kanalga a'zo bo'lish", url=url)])
            idx += 1

    if not rows and SINGLE_CHANNEL_LINK:
        rows.append([InlineKeyboardButton("📢 Kanalga a'zo bo'lish", url=SINGLE_CHANNEL_LINK)])

    rows.append([InlineKeyboardButton("✅ Obuna bo'ldim", callback_data=check_callback_data)])
    return InlineKeyboardMarkup(rows)


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
# KANALGA A'ZOLIKNI TEKSHIRISH
# =========================================================

async def _check_single_channel(bot, chat_id, user_id):
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return (
            member.status in ("creator", "administrator", "member")
            or (
                member.status == "restricted"
                and bool(getattr(member, "is_member", False))
            )
        )
    except Exception:
        logger.exception(
            "Kanal a'zoligini tekshirishda xatolik (%s). "
            "Bot kanalga administrator sifatida qo'shilganini tekshiring.",
            chat_id,
        )
        return False


async def is_subscriber(bot, user_id):
    if not CHANNEL_TARGETS:
        return True
    for chat_id, _ in CHANNEL_TARGETS:
        ok = await _check_single_channel(bot, chat_id, user_id)
        if not ok:
            return False
    return True


def subscription_prompt_text():
    count = len(CHANNEL_TARGETS)
    if count > 1:
        return (
            f"❗ Iltimos, so'rovnomada ishtirok etish uchun quyidagi {count} ta "
            "kanalga a'zo bo'ling."
        )
    return "Ovoz berishda qatnashish uchun avval kanalimizga a'zo bo'ling."


# =========================================================
# TELEFON RAQAMNI TASDIQLASH VA ASOSIY MENYU
# =========================================================

async def prompt_phone_verification(message):
    await message.reply_text(
        "Hurmatli foydalanuvchi! So'rovnomalarda qatnashish uchun "
        "telefon raqamingizni tasdiqlang.",
        reply_markup=phone_request_keyboard(),
    )


async def send_welcome_menu(message):
    await message.reply_text(
        "Assalomu Alaykum.\nSo'rovnoma botga hush kelibsiz!",
        reply_markup=main_menu_keyboard(),
    )


async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.contact or not update.effective_user:
        return

    contact = update.message.contact
    user_id = update.effective_user.id

    if contact.user_id and contact.user_id != user_id:
        await update.message.reply_text(
            "Iltimos, faqat o'zingizning telefon raqamingizni yuboring.",
            reply_markup=phone_request_keyboard(),
        )
        return

    phone = (contact.phone_number or "").strip()
    if not phone:
        await update.message.reply_text(
            "Telefon raqami aniqlanmadi. Qayta urinib ko'ring.",
            reply_markup=phone_request_keyboard(),
        )
        return

    if not phone.startswith("+"):
        phone = f"+{phone}"

    save_user_phone(user_id, phone)

    await update.message.reply_text(
        "Rahmat, telefon raqamingiz muvaffaqiyatli tasdiqlandi!",
        reply_markup=ReplyKeyboardRemove(),
    )
    await send_welcome_menu(update.message)

    pending_poll_id = context.user_data.pop("pending_poll_id", None)
    if pending_poll_id:
        poll = get_poll(pending_poll_id)
        if not poll:
            await update.message.reply_text(
                "Bu opros topilmadi.",
                reply_markup=main_menu_keyboard(),
            )
        elif is_poll_expired(poll):
            await update.message.reply_text(
                format_expired_message(poll),
                reply_markup=main_menu_keyboard(),
            )
        else:
            await open_poll_for_user(
                update.message,
                context,
                user_id,
                poll,
                from_callback_query=None,
            )


async def show_active_polls_menu(message):
    polls = [p for p in get_all_polls() if not is_poll_expired(p)]
    if not polls:
        await message.reply_text(
            "Hozircha faol so'rovnomalar mavjud emas.",
            reply_markup=main_menu_keyboard(),
        )
        return

    rows = []
    for poll in polls[:20]:
        title = str(poll["title"])[:60]
        rows.append([InlineKeyboardButton(title, callback_data=f"open_poll_{poll['id']}")])

    await message.reply_text(
        "Quyidagi ko'rsatilgan so'rovnomalardan birini tanlang 👇",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def open_poll_for_user(target_message, context, user_id, poll, from_callback_query=None):
    if is_poll_expired(poll):
        msg = format_expired_message(poll)
        if from_callback_query:
            await from_callback_query.edit_message_text(msg)
        else:
            await target_message.reply_text(msg, reply_markup=main_menu_keyboard())
        return

    if not await is_subscriber(context.bot, user_id):
        markup = multi_channel_keyboard(f"check_poll_{poll['id']}")
        text = subscription_prompt_text()
        if from_callback_query:
            await from_callback_query.edit_message_text(text, reply_markup=markup)
        else:
            await target_message.reply_text(text, reply_markup=markup)
        return

    context.user_data["active_poll_id"] = poll["id"]
    context.user_data.pop("pending_vote", None)

    option_rows = [
        [KeyboardButton(opt["option_text"])]
        for opt in poll["options"]
    ]
    option_rows.append([KeyboardButton(BTN_VOTE)])

    caption_lines = [f"📊 {poll['title']}"]
    if poll.get("question"):
        caption_lines.extend(["", f"❓ {poll['question']}"])
    ends_text = (poll.get("ends_at") or "").strip()
    if ends_text and ends_text != "-":
        caption_lines.extend(["", f"⏰ Tugash vaqti: {ends_text}"])
    caption_lines.extend(["", "Quyidagi tugmalardan birini tanlang 👇"])
    caption = "\n".join(caption_lines)

    chat_id = target_message.chat_id if target_message else None
    photo_id = (poll.get("photo_file_id") or "").strip()

    if photo_id and chat_id is not None:
        if from_callback_query:
            try:
                await from_callback_query.delete_message()
            except Exception:
                pass
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=photo_id,
            caption=caption[:1024],
            reply_markup=ReplyKeyboardMarkup(option_rows, resize_keyboard=True),
        )
    else:
        if from_callback_query:
            await from_callback_query.edit_message_text(caption)
        if chat_id is not None:
            await context.bot.send_message(
                chat_id=chat_id,
                text=caption,
                reply_markup=ReplyKeyboardMarkup(option_rows, resize_keyboard=True),
            )


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
        is_closed = is_poll_expired(poll)
        status_icon = "🔴" if is_closed else "🟢"
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

    is_closed = is_poll_expired(poll)
    status_text = "Yopilgan / Yakunlangan" if is_closed else "Faol"
    ends_line = f"Tugash vaqti: {poll['ends_at']}\n" if poll.get("ends_at") else ""
    photo_line = "Rasm: Biriktirilgan\n" if poll.get("photo_file_id") else ""

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
        f"{ends_line}"
        f"{photo_line}"
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
    is_closed = is_poll_expired(poll)

    lines = [
        f"📊 {poll['title']}",
        f"Holati: {'Yopilgan / Yakunlangan' if is_closed else 'Faol'}",
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


async def _finalize_new_poll_creation(message, context):
    poll_data = context.user_data.get("new_poll", {})
    title = poll_data.get("title")
    question = poll_data.get("question")
    options = poll_data.get("options")
    photo_file_id = poll_data.get("photo_file_id")
    ends_at = poll_data.get("ends_at")

    if not title or not question or not options:
        context.user_data.pop("new_poll", None)
        context.user_data.pop("admin_action", None)
        await message.reply_text(
            "Opros ma'lumotlari topilmadi. Iltimos, qaytadan boshlang.",
            reply_markup=admin_keyboard(),
        )
        return

    try:
        bot_info = await context.bot.get_me()
        poll_id = create_poll(
            title,
            question,
            options,
            photo_file_id=photo_file_id,
            ends_at=ends_at,
        )
        link = f"https://t.me/{bot_info.username}?start=poll_{poll_id}"
    except Exception:
        logger.exception("Yangi opros yaratishda xatolik")
        await message.reply_text(
            "Opros yaratishda xatolik yuz berdi. Ma'lumotlarni tekshirib, qayta urinib ko'ring."
        )
        return

    context.user_data.pop("new_poll", None)
    context.user_data.pop("admin_action", None)

    extra_info = []
    if ends_at:
        extra_info.append(f"⏰ Tugash vaqti: {ends_at}")
    if photo_file_id:
        extra_info.append("🖼 Rasm: Biriktirilgan")
    extra_block = ("\n" + "\n".join(extra_info)) if extra_info else ""

    await message.reply_text(
        "✅ YANGI OPROS YARATILDI!\n\n"
        f"📌 Nomi: {title}\n"
        f"❓ Savol: {question}\n"
        f"📝 Variantlar: {len(options)} ta"
        f"{extra_block}\n\n"
        f"🔗 Havola:\n{link}",
        reply_markup=admin_keyboard(),
    )


# =========================================================
# MATNLAR VA RASMLARNI QABUL QILISH
# =========================================================

async def handle_admin_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin(update.effective_user.id):
        return
    if not update.message or not update.message.photo:
        return

    action = context.user_data.get("admin_action")
    if action != "poll_photo":
        return

    photo = update.message.photo[-1]
    context.user_data.setdefault("new_poll", {})["photo_file_id"] = photo.file_id
    context.user_data["admin_action"] = "poll_ends_at"

    await update.message.reply_text(
        "⏰ Tugash vaqtini yuboring (masalan: 25.11.2026 23:59).\n"
        "Muddat belgilamaslik uchun '-' yuboring.\n\n"
        "Bekor qilish: /cancel"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text or not update.effective_user:
        return

    user_id = update.effective_user.id
    text = update.message.text.strip()
    if not text:
        return

    # 1. ADMIN JARAYONLARI
    action = context.user_data.get("admin_action")
    if action and is_admin(user_id):
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

            context.user_data.setdefault("new_poll", {})["options"] = options
            context.user_data["admin_action"] = "poll_photo"
            await update.message.reply_text(
                "🖼 So'rovnoma uchun banner rasm yuboring.\n"
                "Rasm qo'shmaslik uchun '-' yuboring.\n\n"
                "Bekor qilish: /cancel"
            )
            return

        if action == "poll_photo":
            if text == "-":
                context.user_data.setdefault("new_poll", {})["photo_file_id"] = None
                context.user_data["admin_action"] = "poll_ends_at"
                await update.message
