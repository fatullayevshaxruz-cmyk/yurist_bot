"""
🚗 AI AVTO-YURIST BOT
=======================
Uzbekiston yo'l harakati qonunchiligi bo'yicha AI maslahatchi.

📋 ISHGA TUSHIRISH UCHUN:
1. .env faylini yarating va quyidagi ma'lumotlarni kiriting:
   BOT_TOKEN=sizning_bot_tokeningiz
   OPENAI_API_KEY=sizning_openai_kalitingiz
   ADMIN_ID=admin_telegram_id
   CHANNEL_ID=@kanal_username_yoki_id
   CARD_NUMBER=to'lov_kartasi_raqami

2. Kerakli kutubxonalarni o'rnating:
   pip install aiogram openai python-dotenv aiohttp

3. Botni ishga tushiring:
   python main.py

💰 NARXLAR:
- Oddiy savol: 5,000 so'm
- Ariza yozish: 15,000 so'm

👨‍💼 ADMIN BUYRUQLARI:
- /add_money [user_id] [summa] - Foydalanuvchi balansini to'ldirish
- /stats - Statistika ko'rish
"""
import os
from aiohttp import web
import asyncio
import logging
import sqlite3
from datetime import datetime
from os import getenv
from typing import Any, Awaitable, Callable, Dict, Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from dotenv import load_dotenv
from openai import AsyncOpenAI

# ================= KONFIGURATSIYA =================
load_dotenv()

BOT_TOKEN = getenv("BOT_TOKEN")
OPENAI_API_KEY = getenv("OPENAI_API_KEY")
ADMIN_ID = int(getenv("ADMIN_ID", "0"))
CHANNEL_ID = getenv("CHANNEL_ID")  # @kanal_username yoki -100xxxxxxxxx
CARD_NUMBER = getenv("CARD_NUMBER", "8600 1234 5678 9012")

# Narxlar (so'mda)
PRICE_QUESTION = 5000  # Oddiy savol
PRICE_ARIZA = 15000    # Ariza yozish

# Logging sozlamalari
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Bot va OpenAI initsializatsiyasi
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
router = Router()


async def handle(request):
    return web.Response(text="Bot is running!")


async def start_webhook():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"✅ Web server {port}-portda ishga tushdi")


# ================= FSM STATES =================
class PaymentStates(StatesGroup):
    """To'lov holatlari"""
    waiting_for_receipt = State()  # Chek kutilmoqda


# ================= MA'LUMOTLAR BAZASI =================
def init_db():
    """
    Ma'lumotlar bazasini yaratish.
    Agar jadvallar mavjud bo'lmasa, yangi jadvallar yaratiladi.
    """
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    
    # Foydalanuvchilar jadvali
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            full_name TEXT,
            username TEXT,
            balance REAL DEFAULT 0.0,
            joined_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Tranzaksiyalar jadvali
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            type TEXT,
            date DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)
    
    conn.commit()
    conn.close()
    logger.info("✅ Ma'lumotlar bazasi tayyor!")


def get_user(user_id: int) -> Optional[dict]:
    """Foydalanuvchi ma'lumotlarini olish"""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            "user_id": row[0],
            "full_name": row[1],
            "username": row[2],
            "balance": row[3],
            "joined_at": row[4]
        }
    return None


def create_user(user_id: int, full_name: str, username: str):
    """Yangi foydalanuvchi qo'shish"""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, full_name, username) VALUES (?, ?, ?)",
        (user_id, full_name, username)
    )
    conn.commit()
    conn.close()


def update_balance(user_id: int, amount: float, transaction_type: str):
    """
    Balansni yangilash va tranzaksiya yozish.
    transaction_type: 'deposit' yoki 'expense'
    """
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    
    if transaction_type == "deposit":
        cursor.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id)
        )
    elif transaction_type == "expense":
        cursor.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ?",
            (amount, user_id)
        )
    
    # Tranzaksiyani yozish
    cursor.execute(
        "INSERT INTO transactions (user_id, amount, type) VALUES (?, ?, ?)",
        (user_id, amount, transaction_type)
    )
    
    conn.commit()
    conn.close()


def get_stats() -> Dict[str, Any]:
    """Statistika olish"""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT SUM(balance) FROM users")
    total_balance = cursor.fetchone()[0] or 0
    
    conn.close()
    
    return {
        "total_users": total_users,
        "total_balance": total_balance
    }


# ================= KLAVIATURALAR =================
def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Asosiy menyu klaviaturasi - pastda doim turadi"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Savol berish"), KeyboardButton(text="📄 Ariza yozish")],
            [KeyboardButton(text="💰 Balansim"), KeyboardButton(text="💳 Hisobni to'ldirish")],
            [KeyboardButton(text="ℹ️ Yordam")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )
    return keyboard


def get_top_up_keyboard() -> ReplyKeyboardMarkup:
    """Hisobni to'ldirish klaviaturasi"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔙 Orqaga")]
        ],
        resize_keyboard=True
    )


# ================= OPENAI FUNKSIYALARI =================
async def get_ai_response(question: str, is_ariza: bool = False) -> str:
    """
    OpenAI orqali javob olish.
    is_ariza: True bo'lsa, ariza shabloni so'ralgan
    """
    
    system_prompt = """Sen O'zbekiston Respublikasining Ma'muriy javobgarlik to'g'risidagi kodeksi (MJtK) bo'yicha mutaxassis yuristsanyu.

ASOSIY QOIDALAR:
1. Faqat O'zbekcha javob ber
2. Tegishli moddalarni keltir (masalan: MJtK 128-modda)
3. Qisqa va aniq javob ber
4. Agar "ariza" yoki "shikoyat" so'ralsa, rasmiy shablon tayyorla

ARIZA SHABLONI (agar so'ralsa):
- Murojaat qiluvchining F.I.Sh va manzili
- Qayerga murojaat (IIB, sud, prokuratura)
- Voqea bayoni
- Huquqiy asoslar (moddalar)
- So'rov/talab
- Sana va imzo joyi

Har doim professional va hurmatli ohangda yoz."""
    
    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question}
            ],
            max_tokens=1500,
            temperature=0.7
        )
        
        return response.choices[0].message.content
        
    except Exception as e:
        logger.error(f"OpenAI xatolik: {e}")
        return "⚠️ AI xizmatida xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring."


# ================= HANDLERS =================

@router.message(CommandStart())
async def cmd_start(message: Message):
    """Start komandasi"""
    user = message.from_user
    
    # Foydalanuvchini bazaga qo'shish
    create_user(user.id, user.full_name, user.username or "")
    
    await message.answer(
        f"🚗 <b>Assalomu alaykum, {user.first_name}!</b>\n\n"
        "Men <b>AI Avto-Yurist</b> botiman.\n"
        "O'zbekiston yo'l harakati qonunchiligi bo'yicha sizga yordam beraman.\n\n"
        "💡 <b>Xizmatlarim:</b>\n"
        f"• 📝 Savol berish — {PRICE_QUESTION:,} so'm\n"
        f"• 📄 Ariza/Shikoyat yozish — {PRICE_ARIZA:,} so'm\n\n"
        "⬇️ Quyidagi tugmalardan birini tanlang:",
        reply_markup=get_main_keyboard()
    )


# ================= REPLY KEYBOARD HANDLERS =================

@router.message(F.text == "📝 Savol berish")
async def ask_question_start(message: Message):
    """Savol berish"""
    user = get_user(message.from_user.id)
    balance = user["balance"] if user else 0
    
    if balance < PRICE_QUESTION:
        await message.answer(
            f"❌ <b>Mablag' yetarli emas!</b>\n\n"
            f"💰 Sizda: <code>{balance:,.0f}</code> so'm\n"
            f"💵 Kerak: <code>{PRICE_QUESTION:,}</code> so'm\n\n"
            "Iltimos, hisobingizni to'ldiring.",
            reply_markup=get_main_keyboard()
        )
        return
    
    await message.answer(
        f"📝 <b>Savolingizni yozing</b>\n\n"
        f"💰 Narxi: {PRICE_QUESTION:,} so'm\n"
        f"💵 Balansingiz: {balance:,.0f} so'm\n\n"
        "⬇️ Savolingizni matn sifatida yuboring:",
        reply_markup=get_main_keyboard()
    )


@router.message(F.text == "📄 Ariza yozish")
async def write_ariza_start(message: Message):
    """Ariza yozish"""
    user = get_user(message.from_user.id)
    balance = user["balance"] if user else 0
    
    if balance < PRICE_ARIZA:
        await message.answer(
            f"❌ <b>Mablag' yetarli emas!</b>\n\n"
            f"💰 Sizda: <code>{balance:,.0f}</code> so'm\n"
            f"💵 Kerak: <code>{PRICE_ARIZA:,}</code> so'm\n\n"
            "Iltimos, hisobingizni to'ldiring.",
            reply_markup=get_main_keyboard()
        )
        return
    
    await message.answer(
        f"📄 <b>Ariza/Shikoyat yozish</b>\n\n"
        f"💰 Narxi: {PRICE_ARIZA:,} so'm\n"
        f"💵 Balansingiz: {balance:,.0f} so'm\n\n"
        "📋 <b>Quyidagi ma'lumotlarni yozing:</b>\n"
        "• Nima sodir bo'ldi?\n"
        "• Qachon va qayerda?\n"
        "• Qanday hujjat kerak? (shikoyat, ariza)\n\n"
        "⬇️ Batafsil yozing:",
        reply_markup=get_main_keyboard()
    )


@router.message(F.text == "💰 Balansim")
async def show_balance(message: Message):
    """Balansni ko'rsatish"""
    user = get_user(message.from_user.id)
    balance = user["balance"] if user else 0
    
    await message.answer(
        f"💰 <b>Sizning balansingiz:</b>\n\n"
        f"<code>{balance:,.0f}</code> so'm\n\n"
        f"💡 1 ta savol — {PRICE_QUESTION:,} so'm\n"
        f"📄 Ariza yozish — {PRICE_ARIZA:,} so'm",
        reply_markup=get_main_keyboard()
    )


@router.message(F.text == "💳 Hisobni to'ldirish")
async def top_up_balance(message: Message, state: FSMContext):
    """Hisobni to'ldirish"""
    await state.set_state(PaymentStates.waiting_for_receipt)
    
    await message.answer(
        "💳 <b>Hisobni to'ldirish</b>\n\n"
        f"📌 Karta raqami:\n<code>{CARD_NUMBER}</code>\n\n"
        "📸 <b>To'lov qilgandan so'ng, chek rasmini yuboring.</b>\n\n"
        "⚠️ <i>Admin tekshirgandan so'ng, hisobingiz to'ldiriladi.</i>",
        reply_markup=get_top_up_keyboard()
    )


@router.message(F.text == "🔙 Orqaga")
async def go_back(message: Message, state: FSMContext):
    """Orqaga qaytish"""
    await state.clear()
    await message.answer(
        "🚗 <b>Asosiy menyu</b>\n\n"
        "Quyidagi xizmatlardan birini tanlang:",
        reply_markup=get_main_keyboard()
    )


@router.message(F.text == "ℹ️ Yordam")
async def show_help(message: Message):
    """Yordam"""
    await message.answer(
        "ℹ️ <b>AI Avto-Yurist Bot</b>\n\n"
        "🚗 Bu bot O'zbekiston yo'l harakati qonunchiligi "
        "bo'yicha huquqiy maslahat beradi.\n\n"
        "<b>📋 Xizmatlar:</b>\n"
        f"• 📝 Savol berish — {PRICE_QUESTION:,} so'm\n"
        f"• 📄 Ariza yozish — {PRICE_ARIZA:,} so'm\n\n"
        "<b>💡 Qanday foydalanish:</b>\n"
        "1. Hisobingizni to'ldiring\n"
        "2. Xizmat turini tanlang\n"
        "3. Savolingizni yozing\n"
        "4. AI javobini oling\n\n"
        "📞 <b>Muammo bo'lsa:</b> @admin_username",
        reply_markup=get_main_keyboard()
    )


# ================= PAYMENT HANDLERS =================

@router.message(PaymentStates.waiting_for_receipt, F.photo)
async def process_receipt_fsm(message: Message, state: FSMContext):
    """To'lov cheki (FSM orqali)"""
    await process_receipt_photo(message)
    await state.clear()


@router.message(PaymentStates.waiting_for_receipt)
async def waiting_receipt_invalid(message: Message):
    """Rasm kutilmoqda, boshqa narsa keldi"""
    if message.text == "🔙 Orqaga":
        return  # Bu handler boshqa joyda ishlaydi
    
    await message.answer(
        "📸 <b>Iltimos, to'lov cheki rasmini yuboring!</b>\n\n"
        "Faqat rasm qabul qilinadi.",
        reply_markup=get_top_up_keyboard()
    )


async def process_receipt_photo(message: Message):
    """To'lov cheki rasmini qayta ishlash"""
    user = message.from_user
    username_display = user.username if user.username else "yo'q"
    
    # Foydalanuvchini bazaga qo'shish (agar yo'q bo'lsa)
    if not get_user(user.id):
        create_user(user.id, user.full_name, user.username or "")
    
    # Chekni KANALGA yuborish
    caption = (
        f"💰 <b>Yangi to'lov!</b>\n\n"
        f"👤 Foydalanuvchi: <a href='tg://user?id={user.id}'>{user.full_name}</a>\n"
        f"🆔 User ID: <code>{user.id}</code>\n"
        f"📱 Username: @{username_display}\n\n"
        f"⬇️ <b>Summani tanlang yoki rad eting:</b>"
    )
    
    # Admin tugmalari - summa variantlari
    admin_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💵 10,000", callback_data=f"approve_{user.id}_10000"),
            InlineKeyboardButton(text="💵 20,000", callback_data=f"approve_{user.id}_20000"),
        ],
        [
            InlineKeyboardButton(text="💵 50,000", callback_data=f"approve_{user.id}_50000"),
            InlineKeyboardButton(text="💵 100,000", callback_data=f"approve_{user.id}_100000"),
        ],
        [
            InlineKeyboardButton(text="❌ Rad etish", callback_data=f"reject_{user.id}")
        ]
    ])
    
    try:
        # Kanalga yuborish
        await bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=message.photo[-1].file_id,
            caption=caption,
            reply_markup=admin_keyboard
        )
        
        # Adminga ham yuborish (backup)
        await bot.send_photo(
            chat_id=ADMIN_ID,
            photo=message.photo[-1].file_id,
            caption=caption,
            reply_markup=admin_keyboard
        )
        
        await message.answer(
            "✅ <b>Chek qabul qilindi!</b>\n\n"
            "⏳ Admin tekshirgandan so'ng, hisobingiz to'ldiriladi.\n"
            "Odatda bu 5-30 daqiqa vaqt oladi.",
            reply_markup=get_main_keyboard()
        )
        
    except Exception as e:
        logger.error(f"Chek yuborishda xatolik: {e}")
        await message.answer(
            "⚠️ Xatolik yuz berdi. Iltimos, adminga to'g'ridan-to'g'ri murojaat qiling.",
            reply_markup=get_main_keyboard()
        )


# ================= ADMIN CALLBACK HANDLERS =================

@router.callback_query(F.data.startswith("approve_"))
async def approve_payment(callback: CallbackQuery):
    """To'lovni tasdiqlash"""
    data = callback.data.split("_")
    user_id = int(data[1])
    amount = float(data[2])
    
    # Balansni yangilash
    update_balance(user_id, amount, "deposit")
    
    # Foydalanuvchiga xabar
    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"✅ <b>Hisobingiz to'ldirildi!</b>\n\n"
                 f"💰 Qo'shilgan summa: <code>{amount:,.0f}</code> so'm\n\n"
                 f"Endi botdan foydalanishingiz mumkin.\n"
                 f"/start - Asosiy menyu"
        )
    except Exception as e:
        logger.error(f"Foydalanuvchiga xabar yuborishda xatolik: {e}")
    
    # Xabarni yangilash
    await callback.message.edit_caption(
        caption=f"✅ <b>TASDIQLANDI!</b>\n\n"
                f"👤 User ID: <code>{user_id}</code>\n"
                f"💰 Summa: <code>{amount:,.0f}</code> so'm\n"
                f"👨‍💼 Tasdiqladi: {callback.from_user.full_name}"
    )
    await callback.answer("✅ To'lov tasdiqlandi!", show_alert=True)


@router.callback_query(F.data.startswith("reject_"))
async def reject_payment(callback: CallbackQuery):
    """To'lovni rad etish"""
    data = callback.data.split("_")
    user_id = int(data[1])
    
    # Foydalanuvchiga xabar
    try:
        await bot.send_message(
            chat_id=user_id,
            text="❌ <b>To'lov rad etildi!</b>\n\n"
                 "Chekingiz tasdiqlanmadi. Iltimos, to'g'ri chek yuboring yoki admin bilan bog'laning.\n\n"
                 "/start - Asosiy menyu"
        )
    except Exception as e:
        logger.error(f"Foydalanuvchiga xabar yuborishda xatolik: {e}")
    
    # Xabarni yangilash
    await callback.message.edit_caption(
        caption=f"❌ <b>RAD ETILDI!</b>\n\n"
                f"👤 User ID: <code>{user_id}</code>\n"
                f"👨‍💼 Rad etdi: {callback.from_user.full_name}"
    )
    await callback.answer("❌ To'lov rad etildi!", show_alert=True)


# ================= ADMIN COMMANDS =================

@router.message(Command("add_money"))
async def admin_add_money(message: Message, command: CommandObject):
    """Admin: Pul qo'shish"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔️ Bu buyruq faqat admin uchun!")
        return
    
    if not command.args:
        await message.answer(
            "❌ Noto'g'ri format!\n\n"
            "✅ To'g'ri: <code>/add_money USER_ID SUMMA</code>\n"
            "Misol: <code>/add_money 123456789 50000</code>"
        )
        return
    
    try:
        args = command.args.split()
        user_id = int(args[0])
        amount = float(args[1])
        
        # Balansni yangilash
        update_balance(user_id, amount, "deposit")
        
        # Foydalanuvchiga xabar
        try:
            await bot.send_message(
                chat_id=user_id,
                text=f"✅ <b>Hisobingiz to'ldirildi!</b>\n\n"
                     f"💰 Qo'shilgan summa: <code>{amount:,.0f}</code> so'm\n\n"
                     f"Endi botdan foydalanishingiz mumkin.\n"
                     f"/start - Asosiy menyu"
            )
        except Exception as e:
            logger.error(f"Foydalanuvchiga xabar yuborishda xatolik: {e}")
        
        await message.answer(
            f"✅ Muvaffaqiyatli!\n\n"
            f"👤 User ID: <code>{user_id}</code>\n"
            f"💰 Summa: <code>{amount:,.0f}</code> so'm"
        )
        
    except (ValueError, IndexError):
        await message.answer(
            "❌ Xatolik!\n\n"
            "✅ To'g'ri format: <code>/add_money USER_ID SUMMA</code>"
        )


@router.message(Command("stats"))
async def admin_stats(message: Message):
    """Admin: Statistika"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔️ Bu buyruq faqat admin uchun!")
        return
    
    stats = get_stats()
    
    await message.answer(
        "📊 <b>BOT STATISTIKASI</b>\n\n"
        f"👥 Jami foydalanuvchilar: <code>{stats['total_users']}</code>\n"
        f"💰 Jami balans: <code>{stats['total_balance']:,.0f}</code> so'm"
    )


# ================= MATN XABARLARI =================

@router.message(F.text)
async def handle_text(message: Message):
    """Matn xabarlarini qayta ishlash"""
    user = get_user(message.from_user.id)
    
    if not user:
        create_user(message.from_user.id, message.from_user.full_name, message.from_user.username or "")
        user = get_user(message.from_user.id)
    
    text = message.text.lower()
    
    # Ariza so'ralyaptimi?
    is_ariza = any(word in text for word in ["ariza", "shikoyat", "da'vo", "murojaat", "template", "shablon"])
    
    price = PRICE_ARIZA if is_ariza else PRICE_QUESTION
    
    # Balans tekshiruvi
    if user["balance"] < price:
        await message.answer(
            f"❌ <b>Mablag' yetarli emas!</b>\n\n"
            f"💰 Sizda: <code>{user['balance']:,.0f}</code> so'm\n"
            f"💵 Kerak: <code>{price:,}</code> so'm\n\n"
            "Iltimos, hisobingizni to'ldiring.",
            reply_markup=get_main_keyboard()
        )
        return
    
    # Pul yechish
    update_balance(message.from_user.id, price, "expense")
    
    # AI javobini olish
    waiting_msg = await message.answer("⏳ Javob tayyorlanmoqda...")
    
    response = await get_ai_response(message.text, is_ariza)
    
    await waiting_msg.delete()
    
    new_balance = user["balance"] - price
    
    await message.answer(
        f"{response}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 Yechildi: {price:,} so'm\n"
        f"💰 Qoldiq: {new_balance:,.0f} so'm",
        reply_markup=get_main_keyboard()
    )


@router.message(F.voice)
async def handle_voice(message: Message):
    """Ovozli xabarni qayta ishlash"""
    await message.answer(
        "🎤 Ovozli xabarlar hozircha qo'llab-quvvatlanmaydi.\n\n"
        "Iltimos, savolingizni matn sifatida yuboring.",
        reply_markup=get_main_keyboard()
    )


@router.message(F.photo)
async def handle_photo(message: Message):
    """Umumiy rasm handler - to'lov cheki sifatida qabul qilish"""
    await process_receipt_photo(message)

# ================= KEEP-ALIVE MEXANIZMI =================

async def keep_alive():
    """
    Botni uxlatmaslik uchun har 10 daqiqada o'zini ping qiladi.
    Render bepul rejasida 15 daqiqadan keyin uxlab qoladi.
    """
    import aiohttp
    
    # Render URL (avtomatik aniqlanadi)
    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    
    while True:
        await asyncio.sleep(600)  # 10 daqiqa (600 soniya)
        
        if render_url:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{render_url}/") as response:
                        logger.info(f"🔄 Keep-alive ping: {response.status}")
            except Exception as e:
                logger.warning(f"⚠️ Keep-alive xatolik: {e}")
        else:
            logger.info("🔄 Keep-alive: lokal rejim (ping o'tkazilmadi)")


# ================= ASOSIY FUNKSIYA =================

async def main():
    """Botni ishga tushirish"""
    # Ma'lumotlar bazasini yaratish
    init_db()
    
    # Dispatcher yaratish
    dp = Dispatcher()
    
    # Router qo'shish (middleware yo'q - majburiy obuna olib tashlandi)
    dp.include_router(router)
    
    # Web serverni ishga tushirish (Render uchun)
    asyncio.create_task(start_webhook())
    
    # Keep-alive mexanizmini ishga tushirish
    asyncio.create_task(keep_alive())
    
    # Botni ishga tushirish
    logger.info("🚀 Bot ishga tushdi!")
    
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
