"""
⚖️ AI YURIST BOT + RAG
=======================
O'zbekiston qonunchiligi bo'yicha AI maslahatchi.
Lex.uz'dan qonunlarni avtomatik yuklab, RAG tizimi orqali aniq javoblar beradi.

📋 ISHGA TUSHIRISH UCHUN:
1. .env faylini yarating va quyidagi ma'lumotlarni kiriting:
   BOT_TOKEN=sizning_bot_tokeningiz
   OPENAI_API_KEY=sizning_openai_kalitingiz
   ADMIN_ID=admin_telegram_id
   CHANNEL_ID=@kanal_username_yoki_id
   CARD_NUMBER=to'lov_kartasi_raqami

2. Kerakli kutubxonalarni o'rnating:
   pip install -r requirements.txt

3. Botni ishga tushiring:
   python main.py

💰 NARXLAR:
- Oddiy savol: 5,000 so'm
- Ariza yozish: 15,000 so'm

👨‍💼 ADMIN BUYRUQLARI:
- /add_money [user_id] [summa] - Foydalanuvchi balansini to'ldirish
- /stats - Bot statistikasi
- /update_laws - Qonunlarni yangilash
- /law_stats - Qonunlar statistikasi
- /search_law [so'z] - Qonun qidirish
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
    KeyboardButton,
    BufferedInputFile
)
from auto_update_bot import AutoUpdateBot
from monitoring_dashboard import LawMonitor
from dotenv import load_dotenv
from openai import AsyncOpenAI

# RAG tizimi importlari
try:
    from law_scraper import LawScraper
    from rag_engine import get_rag_engine, RAGEngine
    RAG_AVAILABLE = True
except ImportError as e:
    RAG_AVAILABLE = False
    print(f"⚠️ RAG tizimi mavjud emas: {e}")

# OpenAI Assistants API (1-usul - soddaroq)
try:
    from openai_assistant import get_assistant, OpenAIAssistant
    ASSISTANT_AVAILABLE = True
except ImportError as e:
    ASSISTANT_AVAILABLE = False
    print(f"⚠️ OpenAI Assistant mavjud emas: {e}")

# Scheduler importi
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    SCHEDULER_AVAILABLE = True
except ImportError:
    SCHEDULER_AVAILABLE = False

# ================= KONFIGURATSIYA =================
load_dotenv()

BOT_TOKEN = getenv("BOT_TOKEN")
OPENAI_API_KEY = getenv("OPENAI_API_KEY")
GOOGLE_API_KEY = getenv("GOOGLE_API_KEY")
ADMIN_ID = int(getenv("ADMIN_ID", "0"))
CHANNEL_ID = getenv("CHANNEL_ID")  # @kanal_username yoki -100xxxxxxxxx
CARD_NUMBER = getenv("CARD_NUMBER", "8600 1234 5678 9012")

# Narxlar (so'mda)
PRICE_QUESTION = 5000  # Oddiy savol
PRICE_ARIZA = 15000    # Ariza yozish

# BHM (Bazaviy Hisoblash Miqdori) - jarimalarni hisoblash uchun
BHM_VALUE = int(getenv("BHM_VALUE", "412500"))  # 2026-yil uchun 412,500 so'm

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

class QuestionStates(StatesGroup):
    """Savol va ariza holatlari"""
    waiting_for_question = State()  # Savol matni kutilmoqda
    waiting_for_ariza = State()    # Ariza tavsifi kutilmoqda


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
            [KeyboardButton(text="🧾Tarifa kalkulyator"), KeyboardButton(text="ℹ️ Yordam")]
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


# ================= BHM KALKULYATOR =================
def format_bhm_amount(bhm_count: float) -> str:
    """BHM ni so'mga aylantirish va formatlash"""
    amount = bhm_count * BHM_VALUE
    return f"{amount:,.0f}".replace(",", " ")


def get_bhm_info() -> str:
    """BHM haqida ma'lumot"""
    return f"1 BHM = {BHM_VALUE:,} so'm".replace(",", " ")


# ================= OPENAI VA RAG FUNKSIYALARI =================
async def get_ai_response(question: str, user_id: int = 0, is_ariza: bool = False) -> str:
    """
    Savolga javob olish.
    1-usul: OpenAI Assistants API (File Search) - eng sodda va ishonchli
    2-usul: RAG tizimi (LlamaIndex) - backup sifatida
    BHM ni avtomatik so'mga aylantiradi.
    """
    
    # 1-USUL: OpenAI Assistants API (tavsiya etiladi)
    if ASSISTANT_AVAILABLE:
        try:
            assistant = get_assistant()
            if assistant.is_initialized:
                result = await assistant.query(user_id, question)
                if result["success"]:
                    # Agar "topmadim" desa, GPT-4o-mini promptiga o'tamiz
                    answer = result["answer"]
                    # Bir nechta variantlar va qisqa javoblarni tekshirish
                    not_found_keywords = [
                        "ma'lumot topmadim", "topilmadi", "not found", 
                        "bazamda yo'q", "kechirasiz", "ma'lumot yo'q",
                        "javob topa olmadim"
                    ]
                    
                    is_not_found = any(kw in answer.lower() for kw in not_found_keywords)
                    
                    if is_not_found or len(answer) < 40:
                        logger.info(f"⚠️ Assistant javobi qoniqarsiz (len={len(answer)}), zaxira modelga o'tkazildi: {answer[:60]}...")
                    else:
                        return answer
        except Exception as e:
            logger.warning(f"Assistant xatolik: {e}")
    
    # RAG tizimidan kontekst olish
    rag_context = ""
    sources_text = ""
    
    if RAG_AVAILABLE:
        try:
            rag_engine = get_rag_engine()
            if rag_engine.is_initialized and rag_engine.index:
                result = await rag_engine.query(question, top_k=3)
                if result["success"] and result["answer"]:
                    rag_context = f"\n\nQONUNLARDAN MA'LUMOT:\n{result['answer']}"
                    if result["sources"]:
                        sources_text = "\n\n📚 Manbalar:\n"
                        for src in result["sources"][:2]:
                            sources_text += f"• {src['title'][:60]}...\n  🔗 {src['url']}\n"
        except Exception as e:
            logger.warning(f"RAG xatolik: {e}")
    
    system_prompt = f"""Sening isming "AI YHQ Maslahatchisi". Sen O'zbekiston Respublikasining Yo'l harakati qonun-qoidalari (YHQ) bo'yicha ixtisoslashgan professional maslahatchisan.

SENING ASOSIY QOIDALARING:
1. FAQAT YO'L HARAKATI QOIDALARI (YHQ): Javoblaringni faqat O'zbekiston Respublikasi Yo'l harakati qoidalariga (Lex.uz) asoslanib ber. Qoidalar, belgilar va chiziqlar haqida batafsil ma'lumot ber.
2. TAQIQLANGAN MAVZULAR: Jarimalar miqdori, kodekslar yoki yo'l harakatiga aloqador bo'lmagan boshqa qonunlar haqida savol berilsa, "Men faqat yo'l harakati qonun-qoidalari (qoidalar, belgilar, chiziqlar) bo'yicha maslahat bera olaman" deb javob ber.
3. ANIQLIK: YHQ bandlari raqamlarini, belgilar va chiziqlar nomlarini aniq ko'rsat.

{rag_context if rag_context else ""}

JAVOB STRUKTURASI:
- 🚗 [Tegishli YHQ Bandi]: Qoida bandi raqami va mazmuni.
- 🛑 [Belgi va Chiziqlar]: Agar savolga aloqador bo'lsa, tegishli belgilar.
- 💡 [Maslahat]: Haydovchi ushbu qoidaga qanday rioya qilishi kerakligi haqida tavsiya.
- ⚠️ [Ogohlantirish]: "Ushbu ma'lumot tanishib chiqish uchun berildi, yakuniy qaror uchun rasmiy YHQ kitobiga yoki huquqshunosga murojaat qiling."

TIL:
- Foydalanuvchi so'ragan tilda (O'zbek yoki Rus) javob ber. Professional va tushunarli tilda gapir."""
    
    # FINAL JAVOB: Google Gemini (OpenAI o'rniga)
    try:
        import google.generativeai as genai
        genai.configure(api_key=GOOGLE_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        full_prompt = f"{system_prompt}\n\nFOYDALANUVCHI SAVOLI: {question}"
        
        response = model.generate_content(full_prompt)
        answer = response.text
        
        return answer + sources_text
        
    except Exception as e:
        logger.error(f"Gemini xatolik: {e}")
        # Agar Gemini xato bersa, eski usulda (OpenAI) urinib ko'ramiz
        try:
            response = await openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question}
                ],
                max_tokens=1500,
                temperature=0.5
            )
            return response.choices[0].message.content + sources_text
        except Exception as oai_e:
            logger.error(f"OpenAI fallback xatolik: {oai_e}")
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
async def ask_question_start(message: Message, state: FSMContext):
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
    
    await state.set_state(QuestionStates.waiting_for_question)
    await message.answer(
        f"📝 <b>Savolingizni yozing</b>\n\n"
        f"💰 Narxi: {PRICE_QUESTION:,} so'm\n"
        f"💵 Balansingiz: {balance:,.0f} so'm\n\n"
        "⬇️ Savolingizni matn sifatida yuboring:",
        reply_markup=get_top_up_keyboard()  # Orqaga tugmasi bor klaviatura
    )


@router.message(F.text == "📄 Ariza yozish")
async def write_ariza_start(message: Message, state: FSMContext):
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
    
    await state.set_state(QuestionStates.waiting_for_ariza)
    await message.answer(
        f"📄 <b>Ariza/Shikoyat yozish</b>\n\n"
        f"💰 Narxi: {PRICE_ARIZA:,} so'm\n"
        f"💵 Balansingiz: {balance:,.0f} so'm\n\n"
        "📋 <b>Quyidagi ma'lumotlarni yozing:</b>\n"
        "• Nima sodir bo'ldi?\n"
        "• Qachon va qayerda?\n"
        "• Qanday hujjat kerak? (shikoyat, ariza)\n\n"
        "⬇️ Batafsil yozing:",
        reply_markup=get_top_up_keyboard()
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
        "ℹ️ <b>AI Yurist Bot</b>\n\n"
        "⚖️ Bu bot O'zbekiston qonunchiligi "
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


@router.message(F.text.startswith("🧾"))
async def show_tariff_calculator(message: Message):
    """Tarifa kalkulyator - barcha jarimalar ro'yxati"""
    bhm = BHM_VALUE
    
    await message.answer(
        f"🧾 <b>JARIMA KALKULYATOR</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>1 BHM = {bhm:,} so'm</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        f"🚦 <b>SVETOFOR QOIDALARI:</b>\n"
        f"• Qizil chiroqdan o'tish:\n"
        f"  <code>2 BHM = {2*bhm:,} so'm</code>\n"
        f"• Takroran (1 yil ichida):\n"
        f"  <code>5 BHM = {5*bhm:,} so'm</code>\n\n"
        
        f"🚗 <b>TEZLIK UCHUN:</b>\n"
        f"• 10-20 km/soat oshirish:\n"
        f"  <code>1 BHM = {bhm:,} so'm</code>\n"
        f"• 20-40 km/soat oshirish:\n"
        f"  <code>2 BHM = {2*bhm:,} so'm</code>\n"
        f"• 40+ km/soat oshirish:\n"
        f"  <code>5 BHM = {5*bhm:,} so'm</code>\n\n"
        
        f"🔒 <b>BOSHQA JARIMALAR:</b>\n"
        f"• Xavfsizlik kamari: <code>{bhm:,}</code>\n"
        f"• Telefon bilan gaplashish: <code>{bhm:,}</code>\n"
        f"• Mast haydash: <code>{10*bhm:,}</code>\n\n"
        
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💸 <b>CHEGIRMALAR:</b>\n"
        f"• 15 kunda to'lash: <b>50%</b> chegirma\n"
        f"• 30 kunda to'lash: <b>30%</b> chegirma\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        f"📌 Aniq savol uchun '📝 Savol berish' tugmasini bosing.",
        reply_markup=get_main_keyboard()
    )


@router.message(Command("reset"))
async def cmd_reset_thread(message: Message):
    """Userning suhbatini tozalash"""
    if ASSISTANT_AVAILABLE:
        assistant = get_assistant()
        await assistant.reset_thread(message.from_user.id)
    
    await message.answer(
        "🔄 <b>Suhbat tarixi tozalandi!</b>\n\n"
        "Endi yangi savol berishingiz mumkin.",
        reply_markup=get_main_keyboard()
    )


@router.message(Command("bhm"))
async def cmd_bhm_calculator(message: Message, command: CommandObject):
    """BHM kalkulyator buyrug'i"""
    if command.args:
        try:
            bhm_count = float(command.args.replace(",", "."))
            amount = bhm_count * BHM_VALUE
            await message.answer(
                f"🧮 <b>BHM Kalkulyator</b>\n\n"
                f"📊 {bhm_count} BHM = <code>{amount:,.0f}</code> so'm\n\n"
                f"💰 1 BHM = {BHM_VALUE:,} so'm",
                reply_markup=get_main_keyboard()
            )
        except ValueError:
            await message.answer(
                "❌ Noto'g'ri format!\n\n"
                "✅ To'g'ri: <code>/bhm 5</code>\n"
                "Natija: 5 BHM = 2,062,500 so'm",
                reply_markup=get_main_keyboard()
            )
    else:
        # Kalkulyator jadvalini ko'rsatish
        await show_tariff_calculator(message)


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

@router.message(QuestionStates.waiting_for_question)
@router.message(QuestionStates.waiting_for_ariza)
@router.message(F.text)
async def handle_text(message: Message, state: FSMContext):
    """Matn xabarlarini qayta ishlash"""
    current_state = await state.get_state()
    logger.info(f"📩 Yangi xabar: user={message.from_user.id}, state={current_state}, text='{message.text[:50]}'")
    
    user = get_user(message.from_user.id)
    if not user:
        create_user(message.from_user.id, message.from_user.full_name, message.from_user.username or "")
        user = get_user(message.from_user.id)
    
    text = message.text
    
    # Avtomatik aniqlash (holat bo'lmasa ham)
    is_ariza_keyword = any(word in text.lower() for word in ["ariza", "shikoyat", "da'vo", "murojaat", "shablon", "template"])
    is_question_likely = len(text) > 15 or any(word in text.lower() for word in ["jarima", "qonun", "mjtk", "modda", "qoida", "yol", "sud", "ment", "gai", "prava"])
    
    if not current_state:
        if message.text in ["📝 Savol berish", "📄 Ariza yozish", "💰 Balansim", "💳 Hisobni to'ldirish", "ℹ️ Yordam", "🔙 Orqaga"] or message.text.startswith("🧾"):
            return
            
        if not is_question_likely and not is_ariza_keyword:
            await message.answer(
                "💡 Savol berish yoki ariza yozish uchun quyidagi tugmalardan birini bosing:",
                reply_markup=get_main_keyboard()
            )
            return

    is_ariza = current_state == QuestionStates.waiting_for_ariza or (not current_state and is_ariza_keyword)
    price = PRICE_ARIZA if is_ariza else PRICE_QUESTION
    
    # Balans tekshiruvi (yana bir bor)
    if user["balance"] < price:
        await message.answer(
            f"❌ <b>Mablag' yetarli emas!</b>\n\n"
            f"💰 Sizda: <code>{user['balance']:,.0f}</code> so'm\n"
            f"💵 Kerak: <code>{price:,}</code> so'm\n\n"
            "Iltimos, hisobingizni to'ldiring.",
            reply_markup=get_main_keyboard()
        )
        await state.clear()
        return
    
    # AI javobini olish
    waiting_msg = await message.answer("⏳ Javob tayyorlanmoqda...")
    
    response = await get_ai_response(text, message.from_user.id, is_ariza)
    
    await waiting_msg.delete()
    
    # Agar xatolik bo'lsa, pul yechmaymiz
    if response.startswith("⚠️"):
        await message.answer(response, reply_markup=get_main_keyboard())
        await state.clear()
        return

    # Muvaffaqiyatli bo'lsagina pul yechish
    update_balance(message.from_user.id, price, "expense")
    
    new_balance = user["balance"] - price
    
    await message.answer(
        f"{response}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 Yechildi: {price:,} so'm\n"
        f"💰 Qoldiq: {new_balance:,.0f} so'm",
        reply_markup=get_main_keyboard()
    )
    await state.clear()


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


# ================= RAG BUYRUQLARI =================

@router.message(Command("update_laws"))
async def cmd_update_laws(message: Message):
    """Admin: Qonunlarni yangilash"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔️ Bu buyruq faqat admin uchun!")
        return
    
    if not RAG_AVAILABLE:
        await message.answer("⚠️ RAG tizimi mavjud emas. Kutubxonalarni o'rnating.")
        return
    
    status_msg = await message.answer("⏳ Qonunlar yuklanmoqda...")
    
    try:
        # Qonunlarni yuklash
        scraper = LawScraper()
        new_laws = await scraper.check_for_updates()
        
        await status_msg.edit_text(f"📥 {len(new_laws)} ta yangi qonun yuklandi.\n⏳ Indekslanmoqda...")
        
        # RAG ga yuklash
        rag_engine = get_rag_engine()
        docs = rag_engine.load_documents_from_files()
        if docs:
            rag_engine.index_documents(docs)
        
        stats = rag_engine.get_stats()
        await status_msg.edit_text(
            f"✅ <b>Yangilash tugadi!</b>\n\n"
            f"📥 Yangi qonunlar: {len(new_laws)} ta\n"
            f"📚 Jami chunks: {stats.get('total_chunks', 0)} ta"
        )
    except Exception as e:
        logger.error(f"Yangilash xatolik: {e}")
        await status_msg.edit_text(f"❌ Xatolik: {str(e)[:100]}")


@router.message(Command("search_law"))
async def cmd_search_law(message: Message, command: CommandObject):
    """Qonun qidirish"""
    if not command.args:
        await message.answer(
            "🔍 <b>Qonun qidirish</b>\n\n"
            "Foydalanish: <code>/search_law [kalit so'z]</code>\n"
            "Misol: <code>/search_law yo'l harakati</code>"
        )
        return
    
    if not RAG_AVAILABLE:
        await message.answer("⚠️ RAG tizimi mavjud emas.")
        return
    
    keyword = command.args
    rag_engine = get_rag_engine()
    
    if not rag_engine.is_initialized:
        await message.answer("⚠️ Qonunlar hali yuklanmagan. Admin /update_laws buyrug'ini ishlatishi kerak.")
        return
    
    results = rag_engine.search_laws(keyword, limit=5)
    
    if not results:
        await message.answer(f"❌ '{keyword}' bo'yicha hech narsa topilmadi.")
        return
    
    response = f"🔍 <b>'{keyword}' bo'yicha natijalar:</b>\n\n"
    for i, r in enumerate(results[:5], 1):
        title = r['title'][:60] + "..." if len(r['title']) > 60 else r['title']
        response += f"{i}. <b>{title}</b>\n"
        if r.get('url'):
            response += f"   🔗 {r['url']}\n"
        response += "\n"
    
    await message.answer(response, disable_web_page_preview=True)


@router.message(Command("law_stats"))
async def cmd_law_stats(message: Message):
    """Qonunlar statistikasi"""
    if not RAG_AVAILABLE:
        await message.answer("⚠️ RAG tizimi mavjud emas.")
        return
    
    rag_engine = get_rag_engine()
    rag_stats = rag_engine.get_stats()
    
    scraper = LawScraper()
    scraper_stats = scraper.get_stats()
    
    last_update = scraper_stats.get('last_update')
    last_update_text = last_update[:10] if last_update else "Hali bo'lmagan"
    
    await message.answer(
        "📊 <b>QONUNLAR STATISTIKASI</b>\n\n"
        f"📚 Jami qonunlar: <code>{scraper_stats.get('total_laws', 0)}</code>\n"
        f"📂 Kategoriyalar: <code>{scraper_stats.get('categories', 0)}</code>\n"
        f"🧠 RAG chunks: <code>{rag_stats.get('total_chunks', 0)}</code>\n"
        f"📅 Oxirgi yangilanish: {last_update_text}\n\n"
        f"🤖 Embedding: {rag_stats.get('embedding_model', 'N/A')}\n"
        f"🧠 LLM: {rag_stats.get('llm_model', 'N/A')}"
    )


@router.message(Command("update_mjtk"))
async def cmd_update_mjtk(message: Message):
    """Admin: MJtK (Ma'muriy javobgarlik kodeksi) ni yuklash"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔️ Bu buyruq faqat admin uchun!")
        return
    
    if not RAG_AVAILABLE:
        await message.answer("⚠️ RAG tizimi mavjud emas. Kutubxonalarni o'rnating.")
        return
    
    status_msg = await message.answer("📚 MJtK yuklanmoqda...")
    
    try:
        # MJtK ni yuklash
        scraper = LawScraper()
        result = await scraper.download_mjtk()
        
        await status_msg.edit_text(f"📥 {result['downloaded']} ta MJtK hujjati yuklandi.\n⏳ Indekslanmoqda...")
        
        # RAG ga yuklash
        rag_engine = get_rag_engine()
        docs = rag_engine.load_documents_from_files()
        if docs:
            rag_engine.index_documents(docs)
        
        stats = rag_engine.get_stats()
        await status_msg.edit_text(
            f"✅ <b>MJtK yuklandi!</b>\n\n"
            f"📥 Yuklangan: {result['downloaded']} ta hujjat\n"
            f"📚 Jami chunks: {stats.get('total_chunks', 0)} ta\n\n"
            f"💡 Endi botga MJtK moddalarini so'rashingiz mumkin."
        )
    except Exception as e:
        logger.error(f"MJtK yuklash xatolik: {e}")
        await status_msg.edit_text(f"❌ Xatolik: {str(e)[:100]}")


@router.message(Command("setup_assistant"))
async def cmd_setup_assistant(message: Message):
    """
    Admin: OpenAI Assistant yaratish (1-usul).
    Bu buyruq faqat bir marta ishlatiladi.
    """
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔️ Bu buyruq faqat admin uchun!")
        return
    
    if not ASSISTANT_AVAILABLE:
        await message.answer("⚠️ OpenAI Assistant moduli mavjud emas.")
        return
    
@router.message(Command("update_assistant"))
async def cmd_update_assistant(message: Message):
    """Admin: OpenAI Assistant instruktsiyalarini yangilash"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔️ Bu buyruq faqat admin uchun!")
        return
    
    if not ASSISTANT_AVAILABLE:
        await message.answer("⚠️ OpenAI Assistant moduli mavjud emas.")
        return
        
    status_msg = await message.answer("🔄 Assistant yangilanmoqda...")
    
    try:
        assistant = get_assistant()
        success = await assistant.update_assistant_instructions()
        
        if success:
            await status_msg.edit_text("✅ <b>Assistant instruktsiyalari yangilandi!</b>\n\nEndi u umumiy jarimalarni biladi.")
        else:
            await status_msg.edit_text("❌ Yangilashda xatolik yuz berdi. Assistant ID .env da borligini tekshiring.")
            
    except Exception as e:
        logger.error(f"Assistant yangilashda xatolik: {e}")
        await status_msg.edit_text(f"❌ Xatolik: {str(e)[:100]}")


@router.message(Command("law_version"))
async def cmd_law_version(message: Message, command: CommandObject):
    """Qonun versiyasini tekshirish"""
    if not command.args:
        await message.answer(
            "🔍 <b>Qonun versiyasini tekshirish</b>\n\n"
            "Foydalanish: <code>/law_version [qonun_id]</code>\n"
            "Misol: <code>/law_version 97661</code>"
        )
        return
    
    law_id = command.args.strip()
    updater = AutoUpdateBot()
    result = updater.get_law_with_verification(law_id)
    
    if result["success"]:
        data = result["data"]
        version_info = result["version_info"]
        await message.answer(
            f"📄 <b>{data.get('title', 'Qonun')}</b>\n"
            f"🆔 ID: <code>{law_id}</code>\n"
            f"📊 Versiya: <b>{version_info['version']}</b>\n"
            f"📅 Yangilandi: {version_info['last_verified'][:10]}\n"
            f"🔗 Manba: {data.get('url', '')}"
        )
    else:
        await message.answer(f"❌ Qonun topilmadi: {law_id}")

@router.message(Command("force_update"))
async def cmd_force_update(message: Message):
    """Majburiy yangilash (admin)"""
    if message.from_user.id != ADMIN_ID: return
    
    status_msg = await message.answer("🔄 Yangilanmoqda...")
    try:
        rag = get_rag_engine() if RAG_AVAILABLE else None
        updater = AutoUpdateBot(bot=message.bot, admin_id=ADMIN_ID, rag_engine=rag)
        updates = await updater.scraper.monitor_priority_laws()
        await updater.update_rag_system(updates)
        await status_msg.edit_text(f"✅ Yangilash tugadi. {len(updates)} ta yangilanish.")
    except Exception as e:
        await status_msg.edit_text(f"❌ Xatolik: {e}")

@router.message(Command("monitor"))
async def cmd_monitor(message: Message):
    """Monitoring hisoboti (admin)"""
    if message.from_user.id != ADMIN_ID: return
    
    monitor = LawMonitor()
    img = monitor.generate_report_image()
    if img:
        await message.answer_photo(
            photo=BufferedInputFile(img.read(), filename="monitor.png"),
            caption="📊 <b>Monitoring hisoboti</b>"
        )
    else:
        await message.answer("📊 Ma'lumotlar etarli emas.")

async def start_background_tasks(bot, rag_engine):
    """Fon vazifalarini ishga tushirish"""
    updater = AutoUpdateBot(bot=bot, admin_id=ADMIN_ID, rag_engine=rag_engine)
    asyncio.create_task(updater.start_auto_update())
    
    # Monitoring uchun periodik checkpoint
    async def monitoring_loop():
        monitor = LawMonitor()
        while True:
            try:
                # Statistikani qo'shish
                monitor.add_checkpoint(0, len(updater.scraper.metadata.get("laws", {})))
            except: pass
            await asyncio.sleep(86400) # 24 soat
            
    asyncio.create_task(monitoring_loop())


# ================= SCHEDULED TASKS =================

async def scheduled_law_update():
    """Har kuni avtomatik qonunlarni yangilash"""
    if not RAG_AVAILABLE:
        return
    
    logger.info("📅 Rejali qonun yangilanishi boshlanmoqda...")
    
    try:
        scraper = LawScraper()
        new_laws = await scraper.check_for_updates()
        
        if new_laws:
            rag_engine = get_rag_engine()
            docs = rag_engine.load_documents_from_files()
            if docs:
                rag_engine.index_documents(docs)
            
            # Adminga xabar
            try:
                await bot.send_message(
                    ADMIN_ID,
                    f"📅 <b>Avtomatik yangilanish</b>\n\n"
                    f"✅ {len(new_laws)} ta yangi qonun yuklandi va indekslandi."
                )
            except:
                pass
        
        logger.info(f"✅ Rejali yangilanish tugadi: {len(new_laws)} ta yangi qonun")
    except Exception as e:
        logger.error(f"Rejali yangilanish xatolik: {e}")


async def startup_law_update():
    """Bot ishga tushganda qonunlarni yuklash"""
    if not RAG_AVAILABLE:
        return
    
    logger.info("📥 Boshlang'ich qonun yuklash boshlanmoqda...")
    
    try:
        # Qonunlarni yuklash
        scraper = LawScraper()
        new_laws = await scraper.check_for_updates()
        
        # RAG ga yuklash
        rag_engine = get_rag_engine()
        docs = rag_engine.load_documents_from_files()
        if docs:
            rag_engine.index_documents(docs)
            logger.info(f"✅ {len(docs)} ta qonun indekslandi")
        
        # Adminga xabar
        try:
            await bot.send_message(
                ADMIN_ID,
                f"🚀 <b>Bot ishga tushdi!</b>\n\n"
                f"📥 Yuklangan qonunlar: {len(new_laws)} ta\n"
                f"📚 Indekslangan hujjatlar: {len(docs) if docs else 0} ta"
            )
        except:
            pass
        
    except Exception as e:
        logger.error(f"Boshlang'ich yuklash xatolik: {e}")


# ================= ASOSIY FUNKSIYA =================

async def main():
    """Botni ishga tushirish"""
    # Ma'lumotlar bazasini yaratish
    init_db()
    
    # Dispatcher yaratish
    dp = Dispatcher()
    
    # Router qo'shish
    dp.include_router(router)
    
    # Web serverni ishga tushirish (Render uchun)
    asyncio.create_task(start_webhook())
    
    # Keep-alive mexanizmini ishga tushirish
    asyncio.create_task(keep_alive())
    
    # Scheduler ishga tushirish (har 24 soatda qonunlarni yangilash)
    if SCHEDULER_AVAILABLE:
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            scheduled_law_update,
            'interval',
            hours=int(getenv("LAW_UPDATE_INTERVAL_HOURS", "24")),
            id='law_update_job'
        )
        scheduler.start()
        logger.info("📅 Scheduler ishga tushdi (har 24 soatda yangilanadi)")
    
    # RAG tizimini ishga tushirish va boshlang'ich yangilanish
    if RAG_AVAILABLE:
        try:
            rag_engine = get_rag_engine()
            logger.info(f"🧠 RAG tizimi: {'✅ tayyor' if rag_engine.is_initialized else '⚠️ ishga tushmadi'}")
            
            # Agar indeks bo'sh bo'lsa, avtomatik yuklash
            if rag_engine.is_initialized:
                stats = rag_engine.get_stats()
                if stats.get('total_chunks', 0) == 0:
                    logger.info("📥 Qonunlar yuklanmoqda (birinchi ishga tushirish)...")
                    asyncio.create_task(startup_law_update())
        except Exception as e:
            logger.warning(f"RAG ishga tushirishda xatolik: {e}")
    
    # Fon vazifalarini boshlash
    rag = get_rag_engine() if RAG_AVAILABLE else None
    await start_background_tasks(bot, rag)
    
    # Botni ishga tushirish
    logger.info("🚀 Bot ishga tushdi!")
    
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
