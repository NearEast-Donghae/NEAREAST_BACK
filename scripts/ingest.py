"""Embed knowledge/*.md once and cache the vectors in data/embeddings.json.

The server only loads the cache; it never embeds documents at startup.
Only documents whose text changed (sha256) or whose model differs are re-embedded,
so re-running after a small edit costs a few tokens.

    python scripts/ingest.py --dry-run     # no key, no network: shows what would change
    NEAREAST_OPENAI_API_KEY=... python scripts/ingest.py
"""

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from neareast_api.modules.chat.retrieval import EMBEDDINGS_FILE, load_docs

DEFAULT_MODEL = "text-embedding-3-small"

# Embeds a batch of texts with a model; returns one vector per text.
BatchEmbedder = Callable[[str, list[str]], list[Sequence[float]]]


def openai_embedder(model: str, texts: list[str]) -> list[Sequence[float]]:
    try:
        from openai import OpenAI  # added to dependencies in the LLM step
    except ImportError:
        sys.exit("openai 패키지가 없습니다: pip install openai")
    key = os.environ.get("NEAREAST_OPENAI_API_KEY")
    if not key:
        sys.exit("NEAREAST_OPENAI_API_KEY가 없습니다 (.env에만 두고 커밋하지 마세요)")
    response = OpenAI(api_key=key).embeddings.create(model=model, input=texts)
    print(f"embedding usage: {response.usage.total_tokens} tokens ({len(texts)} docs)")
    return [item.embedding for item in sorted(response.data, key=lambda d: d.index)]


def ingest(
    content_dir: Path,
    model: str,
    embed: BatchEmbedder | None,
) -> dict[str, list[str]]:
    """Update the cache; embed=None only reports. Returns ids by outcome."""
    docs = load_docs(content_dir)
    path = content_dir / EMBEDDINGS_FILE
    cache = json.loads(path.read_text("utf-8")) if path.exists() else {}
    old = cache.get("docs", {}) if cache.get("model") == model else {}

    keep = {
        doc_id: old[doc_id]
        for doc_id, doc in docs.items()
        if doc_id in old and old[doc_id].get("sha256") == doc.content_hash
    }
    todo = [doc for doc_id, doc in docs.items() if doc_id not in keep]
    removed = sorted(set(cache.get("docs", {})) - set(docs))
    report = {"kept": sorted(keep), "embedded": [d.id for d in todo], "removed": removed}
    if embed is None or (not todo and not removed and cache.get("model") == model):
        return report

    vectors = embed(model, [d.embedding_text for d in todo]) if todo else []
    if len(vectors) != len(todo):
        raise RuntimeError("embedder returned a different number of vectors")
    entries = dict(keep)
    for doc, vector in zip(todo, vectors):
        entries[doc.id] = {"sha256": doc.content_hash, "vector": list(vector)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"model": model, "docs": dict(sorted(entries.items()))}, ensure_ascii=False),
        "utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--content-dir", default=".", type=Path)
    parser.add_argument(
        "--model", default=os.environ.get("NEAREAST_EMBEDDING_MODEL", DEFAULT_MODEL)
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    report = ingest(args.content_dir, args.model, None if args.dry_run else openai_embedder)
    for outcome, ids in report.items():
        print(f"{outcome}: {len(ids)}" + (f" ({', '.join(ids)})" if ids else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
