"""RAG система — поиск по базе знаний."""

import os
from pathlib import Path
import chromadb
from chromadb.errors import NotFoundError
from chromadb.utils import embedding_functions

from config.settings import KNOWLEDGE_DIR, BASE_DIR


class RAGSystem:
    """Система поиска по базе знаний с ChromaDB."""
    
    def __init__(self, park_id: str = "nn"):
        self.park_id = park_id
        self.collection_name = f"knowledge_{park_id}"
        
        # Инициализируем ChromaDB
        persist_dir = BASE_DIR / "data" / "chroma"
        persist_dir.mkdir(parents=True, exist_ok=True)
        
        self.client = chromadb.PersistentClient(path=str(persist_dir))
        
        # Используем OpenAI embeddings
        openai_api_key = os.getenv("OPENAI_API_KEY")
        self.embedding_fn = embedding_functions.OpenAIEmbeddingFunction(
            api_key=openai_api_key,
            model_name="text-embedding-3-small"
        )
        
        # Получаем или создаём коллекцию
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=self.embedding_fn,
            metadata={"park_id": park_id}
        )

    def _reload_collection(self):
        """Re-open collection if it was deleted/recreated while process is running."""
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=self.embedding_fn,
            metadata={"park_id": self.park_id}
        )
    
    def add_document(self, doc_id: str, content: str, category: str, title: str = ""):
        """Добавить документ в базу знаний."""
        self.collection.upsert(
            documents=[content],
            ids=[doc_id],
            metadatas=[{"category": category, "title": title, "park_id": self.park_id}]
        )
    
    def search(self, query: str, intent: str = None, n_results: int = 3) -> list[dict]:
        """
        Поиск релевантных документов.
        
        Args:
            query: Поисковый запрос
            intent: Намерение (birthday, general) для фильтрации
            n_results: Количество результатов
        
        Returns:
            Список документов с контентом и метаданными
        """
        # Формируем фильтр по категории
        where_filter = None
        if intent == "birthday":
            where_filter = {"$or": [{"category": "birthday"}, {"category": "shared"}, {"category": "services"}]}
        elif intent == "general":
            where_filter = {"$or": [{"category": "general"}, {"category": "shared"}, {"category": "services"}]}
        
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results,
                where=where_filter
            )
        except NotFoundError:
            # Collection was dropped/reindexed while running; reopen and retry once
            self._reload_collection()
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results,
                where=where_filter
            )
        
        documents = []
        if results["documents"] and results["documents"][0]:
            for i, doc in enumerate(results["documents"][0]):
                metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                documents.append({
                    "content": doc,
                    "category": metadata.get("category", ""),
                    "title": metadata.get("title", ""),
                    "distance": results["distances"][0][i] if results.get("distances") else None
                })
        
        return documents
    
    def get_context(self, query: str, intent: str = None) -> str:
        """Получить контекст для LLM из релевантных документов (с кешированием)."""
        from time import time
        
        # Инициализация кеша при первом вызове
        if not hasattr(self, '_cache'):
            self._cache = {}
            self._cache_ttl = 300  # 5 минут
        
        # Проверяем кеш
        cache_key = (query, intent)
        now = time()
        
        if cache_key in self._cache:
            result, timestamp = self._cache[cache_key]
            if now - timestamp < self._cache_ttl:
                return result
        
        # Очищаем старые записи (максимум 100)
        if len(self._cache) > 100:
            old_keys = [k for k, (_, ts) in self._cache.items() if now - ts > self._cache_ttl]
            for k in old_keys:
                del self._cache[k]
        
        # Получаем контекст
        docs = self.search(query, intent, n_results=3)
        
        if not docs:
            return ""
        
        context_parts = []
        for doc in docs:
            title = doc.get("title", "")
            content = doc["content"]
            if title:
                context_parts.append(f"### {title}\n{content}")
            else:
                context_parts.append(content)
        
        result = "\n\n".join(context_parts)
        
        # Кешируем
        self._cache[cache_key] = (result, now)
        
        return result
    
    def index_knowledge_files(self):
        """Индексировать файлы базы знаний из папки knowledge/."""
        knowledge_path = KNOWLEDGE_DIR / self.park_id
        
        if not knowledge_path.exists():
            print(f"Knowledge directory not found: {knowledge_path}")
            return 0
        
        count = 0
        
        for category in ["general", "birthday", "shared", "events", "services"]:
            category_path = knowledge_path / category
            if not category_path.exists():
                continue
            
            for file_path in category_path.glob("*.txt"):
                content = file_path.read_text(encoding="utf-8")
                
                # Разбиваем на чанки если документ большой
                chunks = self._split_into_chunks(content, max_chars=2000)
                
                for i, chunk in enumerate(chunks):
                    doc_id = f"{self.park_id}_{category}_{file_path.stem}_{i}"
                    self.add_document(
                        doc_id=doc_id,
                        content=chunk,
                        category=category,
                        title=f"{file_path.stem} (часть {i+1})" if len(chunks) > 1 else file_path.stem
                    )
                    count += 1
                    print(f"Indexed: {doc_id}")
        
        return count
    
    def _split_into_chunks(self, text: str, max_chars: int = 4000) -> list[str]:
        """Разбить текст на чанки по параграфам."""
        if len(text) <= max_chars:
            return [text]
        
        chunks = []
        paragraphs = text.split("\n\n")
        current_chunk = ""
        
        for para in paragraphs:
            if len(current_chunk) + len(para) + 2 <= max_chars:
                current_chunk += para + "\n\n"
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = para + "\n\n"
        
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        return chunks if chunks else [text[:max_chars]]
    
    def clear(self):
        """Очистить коллекцию."""
        self.client.delete_collection(f"knowledge_{self.park_id}")
        self.collection = self.client.get_or_create_collection(
            name=f"knowledge_{self.park_id}",
            embedding_function=self.embedding_fn
        )


# Глобальный экземпляр RAG
rag = RAGSystem()
