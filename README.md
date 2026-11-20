# Telegram So'rovnoma Boti

`python-telegram-bot==22.5` va `SQLite` asosida yaratilgan Telegram so'rovnoma boti.

## Asosiy imkoniyatlar
- Eski `votes` jadvali va avvalgi ovozlarni 100% saqlab qolish.
- Bir nechta mustaqil so'rovnoma yaratish va har biri uchun `?start=poll_<id>` havola.
- Telefon raqamni kontakt orqali tasdiqlash va asosiy `Ovoz berish` menyusi.
- Bir nechta majburiy kanalga a'zolikni tekshirish (`CHANNELS` va `CHANNEL_LINKS` yoki bitta `CHANNEL` / `CHANNEL_LINK`).
- Banner rasm, tugash vaqti (`ends_at`), yakunlangan so'rovnoma xabari va ovoz berishdan oldin qayta tasdiqlash (`Ovoz berishni tasdiqlash` / `Bekor qilish`).
- So'rovnomani yopish, natijalarni son va foizlarda ko'rish hamda tarixni saqlash.

## Muhit o'zgaruvchilari (Environment Variables)
- `BOT_TOKEN` — Telegram bot tokeni.
- `ADMIN_ID` — Administrator Telegram ID raqami.
- `DB_PATH` — SQLite fayl yo'li (Railway Volume uchun `/data/votes.db`).
- `CHANNEL` va `CHANNEL_LINK` — bitta majburiy kanal uchun.
- `CHANNELS` va `CHANNEL_LINKS` — bir nechta kanal uchun vergul bilan ajratilgan ro'yxat (masalan: `@kanal1,@kanal2,@kanal3`).
- `BOT_TIMEZONE` — vaqt mintaqasi (standart: `Asia/Tashkent`).
