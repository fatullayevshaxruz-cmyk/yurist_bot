"""
🤖 OpenAI Assistants API - MJtK & YHQ Qidiruv Tizimi
=====================================================
Bu modul OpenAI Assistants API yordamida qonunlardan qidiruv qiladi.

SETUP QILISH:
1. platform.openai.com/assistants ga kiring
2. + Create → Name: "AI Avto-Yurist"
3. Instructions: (pastda berilgan)
4. Tools: "File Search" yoqing
5. MJtK.docx va YHQ.docx fayllarini yuklang
6. Assistant ID ni .env ga qo'shing: OPENAI_ASSISTANT_ID=asst_xxx

Afzalliklari:
- Fayllar ichidan qidiradi (RAG shart emas)
- Har bir user uchun alohida suhbat (thread)
- Aniq modda va bandlarni topadi
"""

import os
import asyncio
from typing import Optional, Dict, Any
from openai import AsyncOpenAI
from dotenv import load_dotenv
import logging
import json
from pathlib import Path

load_dotenv()

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("OPENAI_ASSISTANT_ID", "")

# User threads faylda saqlanadi (bot restart bo'lganda ham saqlansin)
THREADS_FILE = Path("./data/user_threads.json")


class OpenAIAssistant:
    """OpenAI Assistants API bilan ishlash - File Search yoqilgan"""
    
    def __init__(self):
        self.client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        self.assistant_id = ASSISTANT_ID
        self.is_initialized = bool(ASSISTANT_ID)
        self.user_threads: Dict[int, str] = {}
        self._load_threads()
    
    def _load_threads(self):
        """Saqlangan threadlarni yuklash"""
        try:
            if THREADS_FILE.exists():
                with open(THREADS_FILE, "r") as f:
                    data = json.load(f)
                    # Keys ni int ga convert qilish
                    self.user_threads = {int(k): v for k, v in data.items()}
                logger.info(f"📂 {len(self.user_threads)} ta user thread yuklandi")
        except Exception as e:
            logger.warning(f"Thread yuklashda xatolik: {e}")
            self.user_threads = {}
    
    def _save_threads(self):
        """Threadlarni faylga saqlash"""
        try:
            THREADS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(THREADS_FILE, "w") as f:
                json.dump(self.user_threads, f)
        except Exception as e:
            logger.warning(f"Thread saqlashda xatolik: {e}")
    
    async def create_assistant(self, name: str = "AI Avto-Yurist") -> str:
        """
        Yangi assistant yaratish (File Search yoqilgan).
        Faqat bir marta ishlatiladi - keyin ID ni .env ga saqlang.
        """
        instructions = """Sening isming "AI YHQ Maslahatchisi". Sen O'zbekiston Respublikasining Yo'l harakati qonun-qoidalari (YHQ) bo'yicha ixtisoslashgan professional maslahatchisan.

SENING ASOSIY QOIDALARING:
1. FAQAT YO'L HARAKATI QOIDALARI (YHQ): Javoblaringni faqat O'zbekiston Respublikasi Yo'l harakati qoidalariga (Lex.uz) asoslanib ber. Qoidalar, belgilar va chiziqlar haqida batafsil ma'lumot ber.
2. TAQIQLANGAN MAVZULAR: Jarimalar miqdori, kodekslar yoki yo'l harakatiga aloqador bo'lmagan boshqa qonunlar haqida savol berilsa, "Men faqat yo'l harakati qonun-qoidalari (qoidalar, belgilar, chiziqlar) bo'yicha maslahat bera olaman" deb javob ber.
3. ANIQLIK: YHQ bandlari raqamlarini, belgilar va chiziqlar nomlarini aniq ko'rsat.
4. JAVOB STRUKTURASI:
   - 🚗 [Tegishli YHQ Bandi]: Qoida bandi raqami va mazmuni.
   - 🛑 [Belgi va Chiziqlar]: Agar savolga aloqador bo'lsa, tegishli belgilar.
   - 💡 [Maslahat]: Haydovchi ushbu qoidaga qanday rioya qilishi kerakligi haqida tavsiya.
5. OGOHLANTIRISH: Har bir javob oxirida "Ushbu ma'lumot tanishib chiqish uchun berildi, yakuniy qaror uchun rasmiy YHQ kitobiga yoki huquqshunosga murojaat qiling" degan ogohlantirishni qo'sh.

TIL:
- Foydalanuvchi so'ragan tilda (O'zbek yoki Rus) javob ber. Professional va tushunarli tilda gapir."""

        try:
            assistant = await self.client.beta.assistants.create(
                name=name,
                instructions=instructions,
                model="gpt-4o-mini",
                tools=[{"type": "file_search"}]
            )
            
            self.assistant_id = assistant.id
            self.is_initialized = True
            logger.info(f"✅ Assistant yaratildi: {assistant.id}")
            return assistant.id
            
        except Exception as e:
            logger.error(f"❌ Assistant yaratishda xatolik: {e}")
            raise
    
    async def get_or_create_thread(self, user_id: int) -> str:
        """Userning threadini olish yoki yaratish"""
        if user_id in self.user_threads:
            return self.user_threads[user_id]
        
        # Yangi thread yaratish
        thread = await self.client.beta.threads.create()
        self.user_threads[user_id] = thread.id
        self._save_threads()
        logger.info(f"🆕 Yangi thread yaratildi: user={user_id}")
        return thread.id
    
    async def query(self, user_id: int, question: str) -> Dict[str, Any]:
        """
        Savolga javob olish.
        Har bir user uchun alohida thread ishlatiladi.
        """
        if not self.is_initialized:
            return {
                "success": False,
                "answer": "⚠️ Assistant hali sozlanmagan.\n\n"
                          "Admin uchun: /setup_assistant buyrug'ini ishlating, "
                          "keyin OpenAI platformasida MJtK va YHQ fayllarini yuklang.",
                "sources": []
            }
        
        try:
            # 1. User uchun thread olish/yaratish
            thread_id = await self.get_or_create_thread(user_id)
            
            # 2. Savolni yuborish
            await self.client.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content=question
            )
            
            # 3. Run yaratish va ishga tushirish
            run = await self.client.beta.threads.runs.create(
                thread_id=thread_id,
                assistant_id=self.assistant_id
            )
            
            # 4. Javobni kutish (polling)
            max_attempts = 60  # 60 soniya max
            attempt = 0
            while run.status in ['queued', 'in_progress', 'cancelling']:
                await asyncio.sleep(1)
                run = await self.client.beta.threads.runs.retrieve(
                    thread_id=thread_id,
                    run_id=run.id
                )
                attempt += 1
                if attempt >= max_attempts:
                    return {
                        "success": False,
                        "answer": "⚠️ Javob olish vaqti tugadi. Qaytadan urinib ko'ring.",
                        "sources": []
                    }
            
            # 5. Javobni olish
            if run.status == 'completed':
                messages = await self.client.beta.threads.messages.list(
                    thread_id=thread_id,
                    limit=1
                )
                
                if messages.data:
                    msg = messages.data[0]
                    if msg.role == "assistant" and msg.content:
                        answer_text = ""
                        sources = []
                        
                        for content in msg.content:
                            if content.type == "text":
                                answer_text = content.text.value
                                
                                # Annotationslarni tozalash
                                if hasattr(content.text, 'annotations'):
                                    for ann in content.text.annotations:
                                        # [X] formatidagi manbalarni tozalash
                                        if hasattr(ann, 'text'):
                                            answer_text = answer_text.replace(ann.text, "")
                        
                        return {
                            "success": True,
                            "answer": answer_text.strip(),
                            "sources": sources
                        }
            
            # Xatolik holati
            error_details = f"Run status: {run.status}"
            if hasattr(run, 'last_error') and run.last_error:
                error_details += f" - {run.last_error.message}"
                
            return {
                "success": False,
                "answer": f"⚠️ Javob olishda xatolik yuz berdi ({error_details}).",
                "sources": []
            }
            
        except Exception as e:
            logger.error(f"❌ Query xatolik: {e}")
            return {
                "success": False,
                "answer": f"⚠️ Tizimda xatolik: {str(e)[:100]}",
                "sources": []
            }
    
    async def reset_thread(self, user_id: int) -> bool:
        """Userning threadini o'chirish (yangi suhbat boshlash)"""
        if user_id in self.user_threads:
            del self.user_threads[user_id]
            self._save_threads()
            return True
        return False
    
    async def upload_file(self, file_path: str) -> str:
        """Faylni OpenAI ga yuklash"""
        try:
            with open(file_path, "rb") as f:
                file = await self.client.files.create(
                    file=f,
                    purpose="assistants"
                )
            logger.info(f"✅ Fayl yuklandi: {file.id}")
            return file.id
        except Exception as e:
            logger.error(f"❌ Fayl yuklashda xatolik: {e}")
            raise
    
    async def create_vector_store_with_files(self, name: str, file_ids: list) -> str:
        """Vector store yaratish va fayllarni qo'shish"""
        try:
            vector_store = await self.client.beta.vector_stores.create(
                name=name,
                file_ids=file_ids
            )
            logger.info(f"✅ Vector Store yaratildi: {vector_store.id}")
            return vector_store.id
        except Exception as e:
            logger.error(f"❌ Vector Store yaratishda xatolik: {e}")
            raise
    
    async def attach_vector_store_to_assistant(self, vector_store_id: str):
        """Vector Storeni Assistantga ulash"""
        try:
            await self.client.beta.assistants.update(
                assistant_id=self.assistant_id,
                tool_resources={"file_search": {"vector_store_ids": [vector_store_id]}}
            )
            logger.info("✅ Vector Store Assistantga ulandi")
        except Exception as e:
            logger.error(f"❌ Vector Store ulashda xatolik: {e}")
            raise

    async def update_assistant_instructions(self):
        """Mavjud assistantning instruktsiyalarini yangilash"""
        if not self.assistant_id:
            return False
            
        instructions = """Sening isming "AI Avto-Yurist". Sen O'zbekiston Respublikasining Yo'l harakati qonun-qoidalari (YHQ) va Ma'muriy javobgarlik to'g'risidagi kodeksning (MJtK) yo'l harakatiga oid qismlari bo'yicha ixtisoslashgan professional huquqiy maslahatchisan.

SENING ASOSIY QOIDALARING:
1. FAQAT YO'L HARAKATI QONUNCHILIGI: Javoblaringni faqat O'zbekiston Respublikasi Yo'l harakati qoidalari va MJtKning yo'l harakatiga oid moddalariga (Lex.uz) asoslanib ber. Boshqa sohalar bo'yicha savol berilsa, "Men faqat yo'l harakati qonun-qoidalari bo'yicha yordam bera olaman" deb javob ber.
2. ANIQLIK: Modda raqamlarini va jarima miqdorlarini (BHMda) aniq ko'rsat.
3. CHEGIRMALAR: Yo'l harakati jarimalari haqida gap ketganda, doimo 15 kunlik (50%) va 30 kunlik (30%) chegirma muddatlarini eslatib o't.
4. OGOHLANTIRISH: Har bir javob oxirida "Ushbu ma'lumot tanishib chiqish uchun berildi, yakuniy qaror uchun professional huquqshunosga murojaat qiling" degan ogohlantirishni qo'sh.

JAVOB STRUKTURASI:
- ⚖️ [Tegishli Modda]: Kodeks/Qoida nomi va modda/band raqami.
- 💰 [Jarima/Chora]: Aniq miqdori (BHMda va so'mda). BHM = 412,500 so'm (2026-yil).
- 🕒 [Imtiyozlar]: To'lov muddati va chegirmalar.
- 💡 [Maslahat]: Foydalanuvchi vaziyatni qanday yengillashtirishi mumkinligi haqida qisqa tavsiya.

TIL:
- Foydalanuvchi so'ragan tilda (O'zbek yoki Rus) javob ber. Professional va tushunarli tilda gapir."""

        try:
            await self.client.beta.assistants.update(
                assistant_id=self.assistant_id,
                instructions=instructions
            )
            logger.info("✅ Assistant instruktsiyalari yangilandi")
            return True
        except Exception as e:
            logger.error(f"❌ Assistant yangilashda xatolik: {e}")
            return False


# Singleton
_assistant = None

def get_assistant() -> OpenAIAssistant:
    """Assistant singleton olish"""
    global _assistant
    if _assistant is None:
        _assistant = OpenAIAssistant()
    return _assistant


# Test
async def main():
    assistant = get_assistant()
    
    if not assistant.is_initialized:
        print("⚠️ OPENAI_ASSISTANT_ID .env da yo'q!")
        print("\nAssistant yaratish uchun:")
        print("1. python openai_assistant.py --create")
        print("2. ID ni .env ga qo'shing")
        print("3. platform.openai.com da fayllarni yuklang")
    else:
        print(f"✅ Assistant tayyor: {assistant.assistant_id}")
        
        # Test query
        result = await assistant.query(12345, "MJtK 128-modda nima haqida gapiradi?")
        print(f"\nJavob: {result['answer']}")


if __name__ == "__main__":
    import sys
    if "--create" in sys.argv:
        async def create():
            assistant = get_assistant()
            aid = await assistant.create_assistant()
            print(f"\n✅ Assistant yaratildi!")
            print(f"🆔 ID: {aid}")
            print(f"\n.env fayliga qo'shing:")
            print(f"OPENAI_ASSISTANT_ID={aid}")
        asyncio.run(create())
    else:
        asyncio.run(main())
