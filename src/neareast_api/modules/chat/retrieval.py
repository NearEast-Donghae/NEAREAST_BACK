"""Place catalog and knowledge retrieval behind one small interface.

Content lives in files so people can review it: data/places.json (ids shared with
the map markers) and knowledge/*.md (front matter + body). Search order:
1) documents tied to the context ids, 2) keyword/proper-noun match,
3) vector similarity, only when an embedder is supplied and the embedding
cache (scripts/ingest.py) matches the documents and model.
Swapping the backend later (e.g. pgvector) only changes this module.
"""

import hashlib
import json
import logging
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

PLACES_FILE = Path("data") / "places.json"
KNOWLEDGE_DIR = Path("knowledge")
EMBEDDINGS_FILE = Path("data") / "embeddings.json"


class ContentError(Exception):
    """Content files are missing or malformed."""


@dataclass(frozen=True)
class Place:
    id: str
    name: str
    kind: str  # station | poi | heritage | spot
    passenger_stop: bool | None = None  # False: pass/freight, observe only; None: unknown
    aliases: tuple[str, ...] = ()  # other ways people say it ("묵호" for 묵호역)


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


def parse_doc(text: str, origin: str = "<doc>") -> Doc:
    """Front matter supports `key: value` and inline lists `[a, b]` only."""
    if not text.startswith("---\n"):
        raise ContentError(f"{origin}: missing front matter")
    try:
        header, body = text[4:].split("\n---\n", 1)
    except ValueError:
        raise ContentError(f"{origin}: unterminated front matter") from None
    meta: dict[str, str | tuple[str, ...]] = {}
    for line in header.splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise ContentError(f"{origin}: bad front matter line {line!r}")
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key.strip()] = tuple(v.strip() for v in value[1:-1].split(",") if v.strip())
        else:
            meta[key.strip()] = value
    for required in ("id", "title", "kind"):
        if not isinstance(meta.get(required), str) or not meta[required]:
            raise ContentError(f"{origin}: front matter needs {required}")
    return Doc(
        id=meta["id"],
        title=meta["title"],
        kind=meta["kind"],
        places=tuple(meta.get("places", ())),
        keywords=tuple(meta.get("keywords", ())),
        body=body.strip(),
    )


def load_places(root: Path) -> dict[str, Place]:
    try:
        raw = json.loads((root / PLACES_FILE).read_text("utf-8"))["places"]
        places = [
            Place(p["id"], p["name"], p["kind"], p.get("passenger_stop"),
                  tuple(p.get("aliases", ())))
            for p in raw
        ]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ContentError(f"cannot load {root / PLACES_FILE}: {exc}") from exc
    by_id = {p.id: p for p in places}
    if len(by_id) != len(places):
        raise ContentError(f"{PLACES_FILE}: duplicate place id")
    return by_id


def load_docs(root: Path, strict: bool = True) -> dict[str, Doc]:
    """strict=False (the server) skips a broken document and logs it, so one bad file
    does not take the whole chat down; strict=True (tests, ingest) raises."""
    directory = root / KNOWLEDGE_DIR
    if not directory.is_dir():
        raise ContentError(f"missing {directory}")
    docs: dict[str, Doc] = {}
    for path in sorted(directory.glob("*.md")):
        try:
            doc = parse_doc(path.read_text("utf-8"), str(path))
            if doc.id != path.stem:
                raise ContentError(f"{path}: id {doc.id!r} must match the file name")
        except (ContentError, OSError, UnicodeDecodeError) as exc:
            if strict:
                raise ContentError(str(exc)) from exc
            logger.error("skipping broken knowledge document: %s", exc)
            continue
        docs[doc.id] = doc
    return docs


def _load_vectors(root: Path, docs: dict[str, Doc]) -> tuple[str | None, dict | None]:
    path = root / EMBEDDINGS_FILE
    if not path.exists():
        return None, None
    try:
        cache = json.loads(path.read_text("utf-8"))
        model = cache["model"]
        entries = cache["docs"]
    except (OSError, ValueError, KeyError) as exc:
        logger.warning("ignoring unreadable %s: %s", path, exc)
        return None, None
    vectors = {}
    stale = []
    for doc_id, doc in docs.items():
        entry = entries.get(doc_id)
        if entry is None or entry.get("sha256") != doc.content_hash:
            stale.append(doc_id)
        else:
            vectors[doc_id] = tuple(entry["vector"])
    if stale:
        logger.warning("embeddings stale for %s; run scripts/ingest.py", ", ".join(stale))
    return model, vectors


@lru_cache(maxsize=4)
def _load(root: Path) -> Content:
    places = load_places(root)
    docs = load_docs(root, strict=False)
    model, vectors = _load_vectors(root, docs)
    return Content(places, docs, model, vectors)


def load_content(content_dir: str) -> Content:
    """Files are read once per directory and then reused (they change only on deploy)."""
    return _load(Path(content_dir).resolve())
