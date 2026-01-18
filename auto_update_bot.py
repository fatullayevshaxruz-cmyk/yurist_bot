import asyncio
import logging
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict
from pathlib import Path

from enhanced_law_scraper import SmartLawScraper

# Logger sozlash
logger = logging.getLogger(__name__)

class AutoUpdateBot:
    def __init__(self, bot=None, admin_id=None, rag_engine=None):
        self.scraper = SmartLawScraper()
        self.update_interval = 3600  # 1 soatda bir
        self.last_update = None
        self.bot = bot
        self.admin_id = admin_id
        self.rag_engine = rag_engine
        
        # Yangilanish monitoringi
        self.is_updating = False
    
    async def start_auto_update(self):
        """Avtomatik yangilanishni ishga tushirish"""
        logger.info("🚀 Avtomatik yangilash tizimi ishga tushdi")
        while True:
            try:
                # 1. Muhim qonunlarni tekshirish
                updates = await self.scraper.monitor_priority_laws()
                
                if updates:
                    logger.info(f"📥 {len(updates)} ta qonun yangilandi")
                    
                    # 2. RAG tizimini yangilash
                    if self.rag_engine:
                        await self.update_rag_system(updates)
                    
                    # 3. Foydalanuvchilarga (admin) bildirish
                    if self.bot and self.admin_id:
                        await self.notify_updates(updates)
                
                # 4. Kesh tozalash
                await self.clean_old_cache()
                
                self.last_update = datetime.now()
                
            except Exception as e:
                logger.error(f"❌ Avtomatik yangilashda xatolik: {e}")
            
            # Kuting
            await asyncio.sleep(self.update_interval)
    
    async def update_rag_system(self, updates: List[Dict]):
        """RAG tizimini yangilash"""
        logger.info("🔄 RAG tizimi yangilanmoqda...")
        for update in updates:
            law_id = update["law_id"]
            
            # Eng so'nggi versiyani olish (arxivdan yoki yuklab)
            law_data = await self.scraper.get_latest_version(law_id)
            
            if law_data:
                # Document yaratish (LlamaIndex bo'lsa)
                try:
                    from llama_index.core import Document
                    doc = Document(
                        text=f"# {law_data['title']}\n\n{law_data['content']}",
                        metadata={
                            "title": law_data["title"],
                            "law_id": law_id,
                            "url": law_data.get("url", ""),
                            "version": law_data.get("metadata", {}).get("version", 1),
                            "last_updated": law_data.get("metadata", {}).get("last_verified", ""),
                            "source": "lex.uz"
                        },
                        doc_id=f"law_{law_id}_v{law_data.get('metadata', {}).get('version', 1)}"
                    )
                    
                    # RAG ga qo'shish
                    self.rag_engine.index.insert(doc)
                    self.rag_engine.index.storage_context.persist()
                    logger.info(f"✅ RAG ga qo'shildi: {law_data['title']}")
                except Exception as e:
                    logger.error(f"❌ RAG yangilashda xatolik: {e}")
        
        logger.info("✅ RAG tizimi yangilandi")
    
    async def notify_updates(self, updates: List[Dict]):
        """Yangilanishlar haqida xabar berish"""
        if not updates:
            return
        
        message = "📢 <b>Qonunlar yangilandi!</b>\n\n"
        
        for update in updates[:5]:
            message += f"• {update['title'][:50]}...\n"
            message += f"  🆕 Versiya: {update['new_version']}\n\n"
        
        message += "\n🤖 Bot endi eng so'nggi ma'lumotlar bilan ishlaydi."
        
        try:
            await self.bot.send_message(self.admin_id, message)
        except Exception as e:
            logger.warning(f"❌ Habar yuborishda xatolik: {e}")
    
    async def clean_old_cache(self):
        """Eski keshni tozalash"""
        cache_path = Path("./data/smart_laws/archive")
        if cache_path.exists():
            cutoff_date = datetime.now() - timedelta(days=30)
            for file in cache_path.rglob("*.json"):
                if file.stat().st_mtime < cutoff_date.timestamp():
                    file.unlink()

    def get_law_with_verification(self, law_id: str) -> Dict:
        """Qonunni tekshirib, versiyasini ko'rsatib berish"""
        # Bu mantiq scrapers metadata dan olinadi
        if law_id in self.scraper.metadata["laws"]:
            law_meta = self.scraper.metadata["laws"][law_id]
            # Faylni yuklash
            v = law_meta["version"]
            file_path = self.scraper.archive_path / f"{law_id}_v{v}.json"
            if file_path.exists():
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return {
                    "success": True,
                    "data": data,
                    "version_info": {
                        "version": v,
                        "last_verified": law_meta["last_updated"],
                        "is_latest": True
                    }
                }
        return {"success": False, "error": "Qonun topilmadi"}
