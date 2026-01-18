"""
🧠 RAG ENGINE - QONUNLAR UCHUN
===============================
LlamaIndex asosida RAG tizimi.
SimpleVectorStore ishlatiladi (tashqi dependency yo'q).

Asosiy funksiyalar:
- Hujjatlarni vektor bazasiga yuklash
- Savollarga javob berish
- Manba ko'rsatish
"""

import asyncio
import json
import logging
import os
import pickle
from pathlib import Path
from typing import Dict, List, Optional, Any

from dotenv import load_dotenv

load_dotenv()

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Konfiguratsiya
INDEX_PATH = os.getenv("CHROMA_DB_PATH", "./chroma_db")
LAWS_DATA_PATH = os.getenv("LAWS_DATA_PATH", "./data/laws")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# LlamaIndex importlari
try:
    from llama_index.core import (
        VectorStoreIndex,
        Document,
        StorageContext,
        Settings,
        load_index_from_storage
    )
    from llama_index.core.node_parser import SentenceSplitter
    from llama_index.llms.gemini import Gemini
    from llama_index.embeddings.gemini import GeminiEmbedding
    import pdfplumber
    LLAMAINDEX_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Kutubxonalar topilmadi: {e}")
    LLAMAINDEX_AVAILABLE = False


class RAGEngine:
    """Qonunlar uchun RAG (Retrieval Augmented Generation) tizimi"""

    def __init__(self):
        self.index_path = Path(INDEX_PATH)
        self.laws_path = Path(LAWS_DATA_PATH)
        self.index = None
        self.is_initialized = False

        if not LLAMAINDEX_AVAILABLE:
            logger.error("⚠️ Kerakli kutubxonalar o'rnatilmagan!")
            return

        # LlamaIndex sozlamalari (Gemini ishlatiladi)
        try:
            google_api_key = os.getenv("GOOGLE_API_KEY")
            if not google_api_key:
                logger.error("❌ GOOGLE_API_KEY topilmadi!")
                return

            Settings.llm = Gemini(
                model_name="models/gemini-flash-latest",
                api_key=google_api_key,
                temperature=0.3
            )
            Settings.embed_model = GeminiEmbedding(
                model_name="models/text-embedding-004",
                api_key=google_api_key
            )
            Settings.node_parser = SentenceSplitter(
                chunk_size=1024,
                chunk_overlap=200
            )
        except Exception as e:
            logger.error(f"Settings xatolik: {e}")
            return

        self._initialize()

    def _initialize(self):
        """Vektor bazasini ishga tushirish"""
        try:
            self.index_path.mkdir(parents=True, exist_ok=True)
            
            # Mavjud indeksni yuklash
            storage_file = self.index_path / "docstore.json"
            if storage_file.exists():
                try:
                    storage_context = StorageContext.from_defaults(persist_dir=str(self.index_path))
                    self.index = load_index_from_storage(storage_context)
                    logger.info("✅ Mavjud indeks yuklandi")
                except Exception as e:
                    logger.warning(f"Indeks yuklashda xatolik: {e}")
                    self.index = None
            
            self.is_initialized = True
            logger.info("✅ RAG Engine ishga tushdi (Gemini)!")

        except Exception as e:
            logger.error(f"❌ RAG Engine xatolik: {e}")
            self.is_initialized = False

    def load_documents_from_files(self) -> List[Document]:
        """Qonun fayllaridan (JSON va PDF) dokumentlarni yuklash"""
        documents = []
        
        if not self.laws_path.exists():
            logger.warning(f"Qonunlar papkasi topilmadi: {self.laws_path}")
            return documents

        # 1. JSON fayllarni yuklash
        for json_file in self.laws_path.rglob("*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    law_data = json.load(f)

                # Document yaratish
                content = law_data.get("content", "")
                if not content or len(content) < 50:
                    continue

                title = law_data.get("title", "Noma'lum")
                law_id = law_data.get("id", "")
                url = law_data.get("url", "")
                category = json_file.parent.name

                # Metadata
                metadata = {
                    "title": title,
                    "law_id": law_id,
                    "url": url,
                    "category": category,
                    "source": "lex.uz"
                }

                doc = Document(
                    text=f"# {title}\n\n{content}",
                    metadata=metadata,
                    doc_id=f"law_{law_id}_{json_file.stem}"
                )
                documents.append(doc)

            except Exception as e:
                logger.warning(f"JSON o'qishda xatolik: {json_file} - {e}")

        # 2. PDF fayllarni yuklash (pdfplumber yordamida)
        for pdf_file in self.laws_path.rglob("*.pdf"):
            try:
                with pdfplumber.open(pdf_file) as pdf:
                    text = ""
                    for page in pdf.pages:
                        text += page.extract_text() or ""
                
                if len(text) < 100:
                    continue
                
                doc = Document(
                    text=f"# {pdf_file.stem}\n\n{text}",
                    metadata={
                        "title": pdf_file.stem,
                        "law_id": f"pdf_{pdf_file.stem}",
                        "source": "manual_upload"
                    },
                    doc_id=f"pdf_{pdf_file.stem}"
                )
                documents.append(doc)
                logger.info(f"📄 PDF yuklandi: {pdf_file.name}")
            except Exception as e:
                logger.warning(f"PDF o'qishda xatolik: {pdf_file} - {e}")

        logger.info(f"📚 {len(documents)} ta dokument yuklandi")
        return documents

    def index_documents(self, documents: List[Document]) -> bool:
        """Dokumentlarni indekslash"""
        if not self.is_initialized or not LLAMAINDEX_AVAILABLE:
            logger.error("RAG Engine ishga tushirilmagan")
            return False

        if not documents:
            logger.warning("Indekslash uchun dokument yo'q")
            return False

        try:
            logger.info(f"📊 {len(documents)} ta dokument indekslanmoqda...")

            # Yangi indeks yaratish
            self.index = VectorStoreIndex.from_documents(
                documents,
                show_progress=True
            )
            
            # Indeksni saqlash
            self.index.storage_context.persist(persist_dir=str(self.index_path))

            logger.info(f"✅ Indekslash tugadi!")
            return True

        except Exception as e:
            logger.error(f"❌ Indekslash xatolik: {e}")
            return False

    def add_documents(self, documents: List[Document]) -> int:
        """Mavjud indeksga yangi dokumentlar qo'shish"""
        if not self.index:
            return self.index_documents(documents)

        try:
            added = 0
            for doc in documents:
                self.index.insert(doc)
                added += 1
            
            # Indeksni saqlash
            self.index.storage_context.persist(persist_dir=str(self.index_path))
            
            logger.info(f"✅ {added} ta yangi dokument qo'shildi")
            return added

        except Exception as e:
            logger.error(f"❌ Dokument qo'shishda xatolik: {e}")
            return 0

    async def query(self, question: str, top_k: int = 5) -> Dict[str, Any]:
        """Savolga javob berish"""
        if not self.index or not self.is_initialized:
            return {
                "answer": "⚠️ RAG tizimi hali ishga tushmagan. Iltimos, /update_laws buyrug'ini ishlating.",
                "sources": [],
                "success": False
            }

        try:
            # Query engine yaratish
            query_engine = self.index.as_query_engine(
                similarity_top_k=top_k,
                response_mode="compact"
            )

            # Javob olish
            response = query_engine.query(question)

            # Manbalarni olish
            sources = []
            if hasattr(response, 'source_nodes'):
                for node in response.source_nodes[:3]:
                    metadata = node.node.metadata
                    sources.append({
                        "title": metadata.get("title", "Noma'lum"),
                        "url": metadata.get("url", ""),
                        "category": metadata.get("category", ""),
                        "score": round(node.score, 3) if hasattr(node, 'score') and node.score else None
                    })

            return {
                "answer": str(response),
                "sources": sources,
                "success": True
            }

        except Exception as e:
            logger.error(f"❌ Query xatolik: {e}")
            return {
                "answer": f"⚠️ Savol qayta ishlashda xatolik: {str(e)}",
                "sources": [],
                "success": False
            }

    def search_laws(self, keyword: str, limit: int = 10) -> List[Dict]:
        """Kalit so'z bo'yicha qonunlarni qidirish"""
        if not self.index:
            return []

        try:
            retriever = self.index.as_retriever(similarity_top_k=limit)
            nodes = retriever.retrieve(keyword)

            results = []
            for node in nodes:
                metadata = node.node.metadata
                results.append({
                    "title": metadata.get("title", "Noma'lum"),
                    "url": metadata.get("url", ""),
                    "category": metadata.get("category", ""),
                    "snippet": node.node.text[:300] + "...",
                    "score": round(node.score, 3) if hasattr(node, 'score') and node.score else None
                })

            return results

        except Exception as e:
            logger.error(f"Qidirishda xatolik: {e}")
            return []

    def get_stats(self) -> Dict[str, Any]:
        """RAG tizimi statistikasi"""
        try:
            # Indeks hajmini aniqlash
            chunk_count = 0
            if self.index:
                try:
                    chunk_count = len(self.index.docstore.docs)
                except:
                    pass
            
            return {
                "is_initialized": self.is_initialized,
                "total_chunks": chunk_count,
                "vector_store_path": str(self.index_path),
                "embedding_model": "text-embedding-3-small",
                "llm_model": "gpt-4o-mini"
            }
        except:
            return {
                "is_initialized": False,
                "error": "Statistika olishda xatolik"
            }

    def clear_index(self) -> bool:
        """Indeksni tozalash"""
        try:
            import shutil
            if self.index_path.exists():
                shutil.rmtree(self.index_path)
            self.index_path.mkdir(parents=True, exist_ok=True)
            self.index = None
            logger.info("✅ Indeks tozalandi")
            return True
        except Exception as e:
            logger.error(f"Indeks tozalashda xatolik: {e}")
            return False


# Singleton instance
_rag_engine = None


def get_rag_engine() -> RAGEngine:
    """RAG Engine singleton"""
    global _rag_engine
    if _rag_engine is None:
        _rag_engine = RAGEngine()
    return _rag_engine


# Test uchun
async def main():
    engine = get_rag_engine()
    print(f"Stats: {engine.get_stats()}")
    
    # Hujjatlarni yuklash va indekslash
    docs = engine.load_documents_from_files()
    if docs:
        engine.index_documents(docs)
    
    # Test query
    result = await engine.query("YHQ 12.1-bandi nima haqida?")
    print(f"Javob: {result['answer']}")
    print(f"Manbalar: {result['sources']}")


if __name__ == "__main__":
    asyncio.run(main())
