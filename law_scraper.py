"""
📜 LEX.UZ QONUN SCRAPER
========================
O'zbekiston qonunchiligini lex.uz dan yuklab olish moduli.

Asosiy funksiyalar:
- Qonun kategoriyalarini olish
- Qonunlarni yuklash
- Yangi qonunlarni tekshirish
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# Logging sozlamalari
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Konfiguratsiya
LAWS_DATA_PATH = os.getenv("LAWS_DATA_PATH", "./data/laws")
BASE_URL = "https://lex.uz"

# Qonun kategoriyalari (lex.uz dan) - FAQAT YO'L HARAKATI QOIDALARI
LAW_CATEGORIES = {
    "yol_harakati": {"id": "-5953883", "name": "Yo'l harakati qoidalari"},
}



class LawScraper:
    """Lex.uz dan qonunlarni yuklab oluvchi klass"""

    def __init__(self):
        self.base_url = BASE_URL
        self.data_path = Path(LAWS_DATA_PATH)
        self.data_path.mkdir(parents=True, exist_ok=True)
        self.metadata_file = self.data_path / "metadata.json"
        self.metadata = self._load_metadata()

    def _load_metadata(self) -> Dict:
        """Metadata faylini yuklash"""
        if self.metadata_file.exists():
            with open(self.metadata_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "last_update": None,
            "total_laws": 0,
            "categories": {},
            "laws": {}
        }

    def _save_metadata(self):
        """Metadatani saqlash"""
        with open(self.metadata_file, "w", encoding="utf-8") as f:
            json.dump(self.metadata, f, ensure_ascii=False, indent=2)

    async def fetch_page(self, url: str) -> Optional[str]:
        """URL dan HTML olish"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "uz,ru;q=0.9,en;q=0.8",
                }
                response = await client.get(url, headers=headers, follow_redirects=True)
                response.raise_for_status()
                return response.text
        except Exception as e:
            logger.error(f"Sahifa yuklashda xatolik: {url} - {e}")
            return None

    async def fetch_laws_list(self, category_id: str, page: int = 1) -> List[Dict]:
        """Kategoriya bo'yicha qonunlar ro'yxatini olish"""
        url = f"{self.base_url}/uz/search/nat?classifiers_id={category_id}&lang=4&page={page}"
        html = await self.fetch_page(url)
        
        if not html:
            return []

        soup = BeautifulSoup(html, "lxml")
        laws = []

        # Qonunlar ro'yxatini topish
        law_items = soup.select("div.search-result-item, tr.doc-item, .document-item")
        
        for item in law_items:
            try:
                # Qonun nomini va linkini topish
                link_elem = item.select_one("a[href*='/docs/']")
                if not link_elem:
                    continue

                href = link_elem.get("href", "")
                law_id_match = re.search(r'/docs/[-]?(\d+)', href)
                if not law_id_match:
                    continue

                law_id = law_id_match.group(1)
                title = link_elem.get_text(strip=True)
                
                # Sanani topish
                date_elem = item.select_one(".date, .doc-date, time")
                date_text = date_elem.get_text(strip=True) if date_elem else ""

                laws.append({
                    "id": law_id,
                    "title": title,
                    "date": date_text,
                    "url": f"{self.base_url}/uz/docs/{law_id}"
                })
            except Exception as e:
                logger.warning(f"Qonun elementini parse qilishda xatolik: {e}")
                continue

        return laws

    async def fetch_law_content(self, law_id: str) -> Optional[Dict]:
        """Qonunning to'liq mazmunini olish"""
        url = f"{self.base_url}/uz/docs/{law_id}"
        html = await self.fetch_page(url)
        
        if not html:
            return None

        soup = BeautifulSoup(html, "lxml")

        try:
            # Sarlavha - ko'p usullar bilan qidirish
            title = "Noma'lum"
            
            # 1-usul: Lex.uz ning yangi strukturasidagi sarlavha
            title_selectors = [
                ".doc-title h1",
                ".document-header h1", 
                "h1.title",
                "[itemprop='name']",
                ".doc-header h1",
                "h1",
            ]
            
            for selector in title_selectors:
                title_elem = soup.select_one(selector)
                if title_elem:
                    text = title_elem.get_text(strip=True)
                    # To'g'ri sarlavha ekanligini tekshirish
                    if text and len(text) > 10 and "lex.uz" not in text.lower():
                        title = text
                        break
            
            # 2-usul: Meta tag dan olish
            if title == "Noma'lum":
                meta_title = soup.find("meta", attrs={"property": "og:title"})
                if meta_title and meta_title.get("content"):
                    title = meta_title.get("content")
                else:
                    # <title> tag dan olish
                    title_tag = soup.find("title")
                    if title_tag:
                        title_text = title_tag.get_text(strip=True)
                        # "| Lex.uz" qismini olib tashlash
                        if "|" in title_text:
                            title = title_text.split("|")[0].strip()
                        elif title_text:
                            title = title_text

            # Asosiy matn - yaxshilangan parsing
            content = ""
            content_selectors = [
                ".doc-body",
                ".document-content", 
                ".content-wrapper",
                "article.content",
                ".doc-content",
                "article",
                ".content"
            ]
            
            for selector in content_selectors:
                content_elem = soup.select_one(selector)
                if content_elem:
                    # Keraksiz elementlarni olib tashlash
                    for unwanted in content_elem.select("script, style, nav, header, footer, .share-buttons"):
                        unwanted.decompose()
                    content = content_elem.get_text(separator="\n", strip=True)
                    if len(content) > 100:  # Minimal content uzunligi
                        break
            
            if not content:
                # Fallback: body dan olish
                body = soup.find("body")
                if body:
                    for unwanted in body.select("script, style, nav, header, footer"):
                        unwanted.decompose()
                    content = body.get_text(separator="\n", strip=True)

            # Qo'shimcha ma'lumotlar
            meta_info = {}
            meta_items = soup.select(".doc-info dt, .meta-item, .doc-meta dt")
            for item in meta_items:
                key = item.get_text(strip=True)
                value_elem = item.find_next_sibling("dd") or item.find_next_sibling()
                if value_elem:
                    meta_info[key] = value_elem.get_text(strip=True)
            
            # Qonun sanasi va raqamini olish
            date_elem = soup.select_one(".doc-date, time, [itemprop='datePublished']")
            if date_elem:
                meta_info["sana"] = date_elem.get_text(strip=True)

            return {
                "id": law_id,
                "title": title,
                "content": content,
                "url": url,
                "meta": meta_info,
                "fetched_at": datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Qonun mazmunini olishda xatolik: {law_id} - {e}")
            return None

    async def fetch_recent_laws(self, days: int = 7) -> List[Dict]:
        """Oxirgi X kun ichida chiqgan qonunlarni olish"""
        url = f"{self.base_url}/uz/search/official?lang=4&pub_date=week"
        html = await self.fetch_page(url)
        
        if not html:
            return []

        soup = BeautifulSoup(html, "lxml")
        laws = []

        # Yangi qonunlar ro'yxatini topish
        items = soup.select("a[href*='/docs/']")
        seen_ids = set()

        for item in items:
            href = item.get("href", "")
            law_id_match = re.search(r'/docs/[-]?(\d+)', href)
            if not law_id_match:
                continue
            
            law_id = law_id_match.group(1)
            if law_id in seen_ids:
                continue
            seen_ids.add(law_id)

            title = item.get_text(strip=True)
            if len(title) < 10:  # Juda qisqa nomlarni o'tkazib yuborish
                continue

            laws.append({
                "id": law_id,
                "title": title,
                "url": f"{self.base_url}/uz/docs/{law_id}"
            })

        return laws[:50]  # Eng ko'p 50 ta

    def save_law(self, law: Dict, category: str = "general") -> str:
        """Qonunni faylga saqlash"""
        category_path = self.data_path / category
        category_path.mkdir(parents=True, exist_ok=True)

        file_path = category_path / f"{law['id']}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(law, f, ensure_ascii=False, indent=2)

        # Metadatani yangilash
        self.metadata["laws"][law["id"]] = {
            "title": law.get("title", ""),
            "category": category,
            "file": str(file_path),
            "updated_at": datetime.now().isoformat()
        }
        self._save_metadata()

        return str(file_path)

    async def download_category(self, category_key: str, max_pages: int = 5) -> int:
        """Bitta kategoriyaning qonunlarini yuklash"""
        if category_key not in LAW_CATEGORIES:
            logger.error(f"Noto'g'ri kategoriya: {category_key}")
            return 0

        category = LAW_CATEGORIES[category_key]
        logger.info(f"📂 Kategoriya yuklanmoqda: {category['name']}")

        total_downloaded = 0
        for page in range(1, max_pages + 1):
            laws_list = await self.fetch_laws_list(category["id"], page)
            
            if not laws_list:
                break

            for law_info in laws_list:
                # Agar allaqachon mavjud bo'lsa, o'tkazib yuborish
                if law_info["id"] in self.metadata["laws"]:
                    continue

                law_content = await self.fetch_law_content(law_info["id"])
                if law_content:
                    self.save_law(law_content, category_key)
                    total_downloaded += 1
                    logger.info(f"  ✅ Yuklandi: {law_content['title'][:50]}...")

                # Rate limiting
                await asyncio.sleep(0.5)

        self.metadata["categories"][category_key] = {
            "name": category["name"],
            "last_update": datetime.now().isoformat(),
            "count": total_downloaded
        }
        self._save_metadata()

        return total_downloaded

    async def download_all(self, max_pages_per_category: int = 3) -> Dict[str, int]:
        """Barcha kategoriyalardan qonunlarni yuklash"""
        logger.info("🚀 Barcha qonunlar yuklanmoqda...")
        
        results = {}
        for category_key in LAW_CATEGORIES:
            count = await self.download_category(category_key, max_pages_per_category)
            results[category_key] = count
            await asyncio.sleep(1)  # Kategoriyalar orasida kutish

        self.metadata["last_update"] = datetime.now().isoformat()
        self.metadata["total_laws"] = len(self.metadata["laws"])
        self._save_metadata()

        logger.info(f"✅ Jami yuklangan: {sum(results.values())} ta qonun")
        return results

    async def check_for_updates(self) -> List[Dict]:
        """Yangi qonunlarni tekshirish"""
        logger.info("🔍 Yangi qonunlar tekshirilmoqda...")
        
        new_laws = []
        recent = await self.fetch_recent_laws(days=7)

        for law_info in recent:
            if law_info["id"] not in self.metadata["laws"]:
                law_content = await self.fetch_law_content(law_info["id"])
                if law_content:
                    self.save_law(law_content, "recent")
                    new_laws.append(law_content)
                    logger.info(f"  🆕 Yangi qonun: {law_content['title'][:50]}...")
                await asyncio.sleep(0.5)

        if new_laws:
            logger.info(f"✅ {len(new_laws)} ta yangi qonun topildi")
        else:
            logger.info("ℹ️ Yangi qonunlar yo'q")

        # Metadata yangilash
        self.metadata["last_update"] = datetime.now().isoformat()
        self.metadata["total_laws"] = len(self.metadata["laws"])
        self._save_metadata()

        return new_laws

    async def download_mjtk(self) -> Dict:
        """
        MJtK (Ma'muriy javobgarlik to'g'risidagi kodeks) ni yuklash.
        2026-yilda yangilangan versiya.
        """
        logger.info("📚 MJtK yuklanmoqda...")
        
        result = {"downloaded": 0, "laws": []}
        
        # Asosiy MJtK kodeksini yuklash
        for doc_name, doc_id in MJTK_DOCS.items():
            if doc_id not in self.metadata["laws"]:
                law_content = await self.fetch_law_content(doc_id)
                if law_content:
                    self.save_law(law_content, "mjtk")
                    result["downloaded"] += 1
                    result["laws"].append(law_content)
                    logger.info(f"  ✅ MJtK yuklandi: {law_content['title'][:60]}...")
                await asyncio.sleep(0.5)
        
        # Statistikani yangilash
        self.metadata["last_update"] = datetime.now().isoformat()
        self.metadata["total_laws"] = len(self.metadata["laws"])
        self._save_metadata()
        
        logger.info(f"✅ MJtK yuklash tugadi: {result['downloaded']} ta hujjat")
        return result

    def get_all_documents(self) -> List[Dict]:
        """Barcha saqlangan qonunlarni o'qish"""
        documents = []
        
        for law_id, law_meta in self.metadata.get("laws", {}).items():
            file_path = law_meta.get("file")
            if file_path and os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        doc = json.load(f)
                        documents.append(doc)
                except Exception as e:
                    logger.warning(f"Fayl o'qishda xatolik: {file_path} - {e}")

        return documents

    def get_stats(self) -> Dict[str, Any]:
        """Statistika olish"""
        return {
            "total_laws": self.metadata.get("total_laws", 0),
            "last_update": self.metadata.get("last_update"),
            "categories": len(self.metadata.get("categories", {})),
            "categories_detail": self.metadata.get("categories", {})
        }


# Test uchun
async def main():
    scraper = LawScraper()
    
    # Yangi qonunlarni tekshirish
    new_laws = await scraper.check_for_updates()
    print(f"Yangi qonunlar: {len(new_laws)}")
    
    # Statistika
    stats = scraper.get_stats()
    print(f"Jami qonunlar: {stats['total_laws']}")


if __name__ == "__main__":
    asyncio.run(main())
