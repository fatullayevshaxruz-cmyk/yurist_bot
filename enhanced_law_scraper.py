import asyncio
import json
import logging
import os
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

LAWS_DATA_PATH = os.getenv("LAWS_DATA_PATH", "./data/smart_laws")

class SmartLawScraper:
    """Lex.uz dan yo'l harakati qonunlarini yuklab oluvchi klass"""

    def __init__(self):
        self.data_path = Path(LAWS_DATA_PATH)
        self.data_path.mkdir(parents=True, exist_ok=True)
        self.archive_path = self.data_path / "archive"
        self.archive_path.mkdir(parents=True, exist_ok=True)
        self.metadata_file = self.data_path / "smart_metadata.json"
        self.metadata = self._load_metadata()
        
        # Kuzatiladigan muhim qonun: FAQAT YO'L HARAKATI QOIDALARI
        self.priority_laws = {
            "-5953883": "Yo'l harakati qoidalari (YHQ)"
        }

    def _load_metadata(self) -> Dict:
        if self.metadata_file.exists():
            with open(self.metadata_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"laws": {}}

    def _save_metadata(self):
        with open(self.metadata_file, "w", encoding="utf-8") as f:
            json.dump(self.metadata, f, ensure_ascii=False, indent=2)

    async def get_latest_version(self, law_id: str) -> Optional[Dict]:
        """Qonunni eng so'nggi versiyasini olish"""
        page_url = f"https://lex.uz/docs/{law_id}"
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(page_url)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'lxml')
                    title = soup.find('h1').text.strip() if soup.find('h1') else "Nomsiz qonun"
                    content = soup.find('div', class_='lex-content').text if soup.find('div', class_='lex-content') else response.text
                    
                    return {
                        "law_id": law_id,
                        "title": title,
                        "content": content,
                        "url": page_url,
                        "metadata": {
                            "last_verified": datetime.now().isoformat(),
                            "version": self.metadata["laws"].get(law_id, {}).get("version", 1)
                        }
                    }
        except Exception as e:
            logger.error(f"❌ Qonun yuklashda xatolik ({law_id}): {e}")
        return None

    async def monitor_priority_laws(self) -> List[Dict]:
        """Muhim qonunlarni yangilanishga tekshirish"""
        updates = []
        for law_id, name in self.priority_laws.items():
            logger.info(f"🔍 Tekshirilmoqda: {name} (ID: {law_id})")
            law_data = await self.get_latest_version(law_id)
            
            if law_data:
                current_hash = hashlib.md5(law_data["content"].encode()).hexdigest()
                old_hash = self.metadata["laws"].get(law_id, {}).get("hash")
                
                if old_hash != current_hash:
                    old_version = self.metadata["laws"].get(law_id, {}).get("version", 0)
                    new_version = old_version + 1
                    
                    updates.append({
                        "law_id": law_id,
                        "title": law_data["title"],
                        "old_version": old_version,
                        "new_version": new_version
                    })
                    
                    self.metadata["laws"][law_id] = {
                        "title": law_data["title"],
                        "hash": current_hash,
                        "version": new_version,
                        "last_updated": datetime.now().isoformat()
                    }
                    
                    file_name = f"{law_id}_v{new_version}.json"
                    with open(self.archive_path / file_name, "w", encoding="utf-8") as f:
                        json.dump(law_data, f, ensure_ascii=False, indent=2)
        
        if updates:
            self._save_metadata()
        return updates
