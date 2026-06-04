"""
Mira memory module - structured user preferences and facts in Qdrant.

Completely standalone: no imports from other Mira modules.
Gracefully disabled if Qdrant is unavailable.

Schema per point:
{
  "text":       "User prefers chill jazz in the evening.",
  "user_id":    "local",
  "type":       "preference",          # preference | fact | context
  "namespace":  "music",              # music | daily | food | general | ...
  "source":     "voice",
  "confidence": 0.8,
  "created_at": "2026-06-05T08:00:00+12:00",
  "updated_at": "2026-06-05T08:00:00+12:00"
}

Usage:
    from memory import Memory
    mem = Memory()
    mem.store("User prefers synthwave while coding", type="preference",
              namespace="music", confidence=0.9)
    hits = mem.retrieve(namespace="music", type="preference",
                        query="music for coding")
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

COLLECTION   = "mira_memory"
EMBED_MODEL  = "all-MiniLM-L6-v2"
QDRANT_URL   = "http://localhost:6333"
VECTOR_DIM   = 384
DEFAULT_TOP_K = 5
MIN_SCORE    = 0.40

VALID_TYPES      = {"preference", "fact", "context"}
VALID_NAMESPACES = {"music", "daily", "food", "people", "places", "general"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


@dataclass
class MemoryEntry:
    id:         str
    text:       str
    type:       str
    namespace:  str
    user_id:    str
    source:     str
    confidence: float
    created_at: str
    updated_at: str
    score:      float = 0.0   # set on search results only


class Memory:
    """
    Qdrant-backed structured memory for Mira.
    All methods are no-ops (return empty/None) if Qdrant is unavailable.
    """

    def __init__(self,
                 qdrant_url: str = QDRANT_URL,
                 collection: str = COLLECTION,
                 embed_model: str = EMBED_MODEL,
                 user_id: str = "local"):
        self._collection = collection
        self._user_id    = user_id
        self._ready      = False
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import (
                Distance, VectorParams,
                PayloadSchemaType,
            )
            from sentence_transformers import SentenceTransformer

            self._q   = QdrantClient(url=qdrant_url)
            self._enc = SentenceTransformer(embed_model, device="cpu")

            existing = {c.name for c in self._q.get_collections().collections}
            if collection not in existing:
                self._q.create_collection(
                    collection_name=collection,
                    vectors_config=VectorParams(size=VECTOR_DIM,
                                               distance=Distance.COSINE),
                )
                # Index payload fields used in filters for speed
                for field, ftype in [
                    ("user_id",   PayloadSchemaType.KEYWORD),
                    ("type",      PayloadSchemaType.KEYWORD),
                    ("namespace", PayloadSchemaType.KEYWORD),
                ]:
                    self._q.create_payload_index(
                        collection_name=collection,
                        field_name=field,
                        field_schema=ftype,
                    )
            self._ready = True
        except Exception as e:
            print(f"[memory]  disabled ({e})")

    # ------------------------------------------------------------------

    def store(self,
              text: str,
              type: str = "preference",
              namespace: str = "general",
              source: str = "voice",
              confidence: float = 0.8,
              dedup_threshold: float = 0.92) -> Optional[str]:
        """
        Store a memory. If a near-identical entry already exists
        (score >= dedup_threshold, same namespace+type), update it
        instead of creating a duplicate. Returns entry id or None.
        """
        if not self._ready or not text.strip():
            return None
        if type not in VALID_TYPES:
            type = "preference"
        if namespace not in VALID_NAMESPACES:
            namespace = "general"
        try:
            from qdrant_client.models import (
                PointStruct, Filter, FieldCondition, MatchValue, SetPayload,
            )
            vec = self._enc.encode(text).tolist()
            now = _now_iso()

            # Check for near-duplicate in same namespace+type
            resp = self._q.query_points(
                collection_name=self._collection,
                query=vec,
                limit=1,
                query_filter=Filter(must=[
                    FieldCondition(key="user_id",
                                   match=MatchValue(value=self._user_id)),
                    FieldCondition(key="namespace",
                                   match=MatchValue(value=namespace)),
                    FieldCondition(key="type",
                                   match=MatchValue(value=type)),
                ]),
                score_threshold=dedup_threshold,
                with_payload=False,
            )
            if resp.points:
                # Update existing entry instead of duplicating
                existing_id = str(resp.points[0].id)
                self._q.set_payload(
                    collection_name=self._collection,
                    payload={"text": text, "confidence": confidence,
                             "updated_at": now},
                    points=[existing_id],
                )
                return existing_id

            # New entry
            entry_id = str(uuid.uuid4())
            self._q.upsert(
                collection_name=self._collection,
                points=[PointStruct(
                    id=entry_id,
                    vector=vec,
                    payload={
                        "text":       text,
                        "user_id":    self._user_id,
                        "type":       type,
                        "namespace":  namespace,
                        "source":     source,
                        "confidence": confidence,
                        "created_at": now,
                        "updated_at": now,
                    },
                )],
            )
            return entry_id
        except Exception as e:
            print(f"[memory]  store error: {e}")
            return None

    # ------------------------------------------------------------------

    def retrieve(self,
                 query: str,
                 namespace: Optional[str] = None,
                 type: Optional[str] = None,
                 top_k: int = DEFAULT_TOP_K,
                 min_score: float = MIN_SCORE) -> list[MemoryEntry]:
        """
        Retrieve relevant memories. Filters are ANDed.
        Use namespace + type together for precise, low-noise results.
        """
        if not self._ready or not query.strip():
            return []
        try:
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            conditions = [
                FieldCondition(key="user_id",
                               match=MatchValue(value=self._user_id))
            ]
            if namespace:
                conditions.append(
                    FieldCondition(key="namespace",
                                   match=MatchValue(value=namespace)))
            if type:
                conditions.append(
                    FieldCondition(key="type",
                                   match=MatchValue(value=type)))

            vec  = self._enc.encode(query).tolist()
            resp = self._q.query_points(
                collection_name=self._collection,
                query=vec,
                limit=top_k,
                query_filter=Filter(must=conditions),
                score_threshold=min_score,
                with_payload=True,
            )
            return [_to_entry(h) for h in resp.points]
        except Exception as e:
            print(f"[memory]  retrieve error: {e}")
            return []

    # ------------------------------------------------------------------

    def forget(self, entry_id: str) -> bool:
        """Delete a memory by id."""
        if not self._ready:
            return False
        try:
            from qdrant_client.models import PointIdsList
            self._q.delete(
                collection_name=self._collection,
                points_selector=PointIdsList(points=[entry_id]),
            )
            return True
        except Exception as e:
            print(f"[memory]  forget error: {e}")
            return False

    # ------------------------------------------------------------------

    def format_context(self,
                       query: str,
                       namespace: Optional[str] = None,
                       type: Optional[str] = None,
                       top_k: int = 3) -> str:
        """
        Return a compact string for injection into the LLM context.
        Returns empty string if no relevant memories found.
        """
        hits = self.retrieve(query, namespace=namespace,
                             type=type, top_k=top_k)
        if not hits:
            return ""
        lines = "\n".join(f"- {h.text}" for h in hits)
        return f"Relevant things I know about this user:\n{lines}"

    # ------------------------------------------------------------------

    @property
    def ready(self) -> bool:
        return self._ready


def _to_entry(h) -> MemoryEntry:
    p = h.payload
    return MemoryEntry(
        id=str(h.id),
        text=p.get("text", ""),
        type=p.get("type", ""),
        namespace=p.get("namespace", ""),
        user_id=p.get("user_id", ""),
        source=p.get("source", ""),
        confidence=p.get("confidence", 0.0),
        created_at=p.get("created_at", ""),
        updated_at=p.get("updated_at", ""),
        score=h.score,
    )
