"""Retrieve PostgreSQL knowledge using the existing context, keyword and vector order."""

import hashlib
import logging
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from neareast_api.modules.places.service import Place

logger = logging.getLogger(__name__)

# Size of every stored and query vector (the knowledge_chunks column is vector(1536)).
EMBEDDING_DIMENSIONS = 1536


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



def embedding_text(doc: Doc, places: Mapping[str, Place]) -> str:
    """What is embedded for a document: its title, the other names it goes by (its own
    keywords and the aliases of the places it is about), those places' names and the body.
    Ingestion and the server both use this, so a stored vector is used only while the text
    it was made from is still the current one."""
    related = [places[place_id] for place_id in doc.places if place_id in places]
    aliases = list(dict.fromkeys([*doc.keywords, *(a for p in related for a in p.aliases)]))
    names = list(dict.fromkeys(p.name for p in related))
    lines = [doc.title]
    if aliases:
        lines.append("별칭: " + ", ".join(aliases))
    if names:
        lines.append("관련 장소: " + ", ".join(names))
    return "\n".join([*lines, doc.body])


def content_hash(doc: Doc, places: Mapping[str, Place]) -> str:
    return hashlib.sha256(embedding_text(doc, places).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Hit:
    doc: Doc
    reason: str  # context | keyword | vector
    score: float | None = None  # cosine similarity, vector hits only


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

    def places_in_text(self, text: str) -> list[Place]:
        """Every catalog place named in free text, in the order they are written. Where
        names overlap the longest wins ("묵호항역" is not also 묵호역)."""
        found = []
        for place in self.places.values():
            for label in (place.name, *place.aliases):
                pattern = re.escape(label) + ("" if label == place.name else "(?!시)")
                for match in re.finditer(pattern, text):
                    found.append((match.start(), -len(label), match.end(), place))
        named, covered = [], -1
        for start, _, end, place in sorted(found, key=lambda f: f[:2]):
            if start >= covered:
                covered = end
                if place not in named:
                    named.append(place)
        return named

    def search(
        self,
        message: str,
        context_ids: Sequence[str] = (),
        limit: int = 3,  # minimum results wanted; only the vector step is bounded by it
        embed: Embedder | None = None,
        min_similarity: float = 0.0,  # vector hits below this are not evidence
    ) -> list[Hit]:
        hits: dict[str, Hit] = {}

        def add(doc: Doc, reason: str, score: float | None = None) -> None:
            if doc.id not in hits:
                hits[doc.id] = Hit(doc, reason, score)

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
            for score, doc in self.vector_scores(message, embed):
                if len(hits) >= limit or score < min_similarity:
                    break  # best first: everything after this is weaker still
                add(doc, "vector", score)
        # Context and keyword hits are never cut here: an explicit mention must not be
        # crowded out by many pins. Callers trim to their prompt budget.
        return list(hits.values())

    def vector_ready(self, model: str) -> bool:
        """Stored vectors can answer a query embedded with `model`. Vectors from another
        model live in another space: comparing them would be noise, so they are not used."""
        if not self.vectors:
            return False
        if model != self.embedding_model:
            logger.warning(
                "vector search skipped: documents were embedded with %s, settings say %s; "
                "run scripts/ingest.py", self.embedding_model, model,
            )
            return False
        return True

    def vector_scores(self, message: str, embed: Embedder) -> list[tuple[float, Doc]]:
        """Every embedded document with its similarity to the message, best first."""
        if not self.vectors:
            return []
        model, query = embed(message)
        if not self.vector_ready(model):
            return []
        scored = sorted(
            ((_cosine(query, vec), doc_id) for doc_id, vec in self.vectors.items()),
            reverse=True,
        )
        return [(score, self.docs[doc_id]) for score, doc_id in scored if doc_id in self.docs]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def load_content(content_dir: str) -> Content:
    from neareast_api.db.content_repository import ContentRepository
    return ContentRepository.knowledge()
