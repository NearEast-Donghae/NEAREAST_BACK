"""Retrieve PostgreSQL knowledge using the existing context, keyword and vector order."""

import hashlib
import logging
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from neareast_api.modules.places.service import Place

logger = logging.getLogger(__name__)

class ContentError(Exception):
    """Required knowledge has not been loaded into the database."""


@dataclass(frozen=True)
class Doc:
    id: str
    title: str
    kind: str
    places: tuple[str, ...]
    keywords: tuple[str, ...]
    body: str

    @property
    def summary(self) -> str:
        """First paragraph: what a short answer may quote."""
        return self.body.split("\n\n", 1)[0].strip()

    @property
    def embedding_text(self) -> str:
        return f"{self.title}\n{self.body}"

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.embedding_text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Hit:
    doc: Doc
    reason: str  # context | keyword | vector


# Embeds one query text; returns (model name, vector).
Embedder = Callable[[str], tuple[str, Sequence[float]]]


@dataclass(frozen=True)
class Content:
    places: dict[str, Place]
    docs: dict[str, Doc]
    embedding_model: str | None = None
    vectors: dict[str, tuple[float, ...]] | None = None

    def place(self, place_id: str) -> Place | None:
        return self.places.get(place_id)

    def place_in_text(self, text: str) -> Place | None:
        """Earliest catalog place named in free text, by name or alias; at the same
        position the longest match wins ("묵호항역" over the alias "묵호")."""
        found = []
        for place in self.places.values():
            for label in (place.name, *place.aliases):
                # An alias must not be the start of a city name: "동해시" is not 동해역.
                pattern = re.escape(label) + ("" if label == place.name else "(?!시)")
                match = re.search(pattern, text)
                if match:
                    found.append((match.start(), -len(label), place.id, place))
        return min(found, key=lambda f: f[:3])[3] if found else None

    def search(
        self,
        message: str,
        context_ids: Sequence[str] = (),
        limit: int = 3,  # minimum results wanted; only the vector step is bounded by it
        embed: Embedder | None = None,
    ) -> list[Hit]:
        hits: dict[str, Hit] = {}

        def add(doc: Doc, reason: str) -> None:
            if doc.id not in hits:
                hits[doc.id] = Hit(doc, reason)

        for place_id in context_ids:  # 1) direct lookup by the ids on screen
            if place_id in self.docs:
                add(self.docs[place_id], "context")
        for place_id in context_ids:
            for doc in self.docs.values():
                if place_id in doc.places:
                    add(doc, "context")
        keyword_hits = sorted(  # 2) proper nouns; longer matches are more specific
            (
                (max(len(k) for k in (doc.title, *doc.keywords) if k in message), doc)
                for doc in self.docs.values()
                if any(k in message for k in (doc.title, *doc.keywords))
            ),
            key=lambda pair: -pair[0],
        )
        for _, doc in keyword_hits:
            add(doc, "keyword")
        if embed is not None and len(hits) < limit:  # 3) semantic fallback fills up to limit
            for doc in self._vector_search(message, embed, limit - len(hits)):
                add(doc, "vector")
        # Context and keyword hits are never cut here: an explicit mention must not be
        # crowded out by many pins. Callers trim to their prompt budget.
        return list(hits.values())

    def _vector_search(self, message: str, embed: Embedder, limit: int) -> list[Doc]:
        if not self.vectors:
            return []
        model, query = embed(message)
        if model != self.embedding_model:
            logger.warning(
                "embedding cache model %s != %s; run scripts/ingest.py", self.embedding_model, model
            )
            return []
        scored = sorted(
            ((_cosine(query, vec), doc_id) for doc_id, vec in self.vectors.items()),
            reverse=True,
        )
        return [self.docs[doc_id] for _, doc_id in scored[:limit] if doc_id in self.docs]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def load_content(content_dir: str) -> Content:
    from neareast_api.db.content_repository import ContentRepository
    return ContentRepository.knowledge()
