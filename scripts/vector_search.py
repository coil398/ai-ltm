#!/usr/bin/env python3
"""
ai-ltm vector search: TF-IDF + cosine similarity (Python stdlib only).

Usage:
  # Search episodes by vector similarity
  python3 vector_search.py search --db ~/ai-ltm/memory.db --query "some query" --limit 5

  # Rebuild embeddings for all episodes (run after bulk import or schema migration)
  python3 vector_search.py rebuild --db ~/ai-ltm/memory.db

  # Combined search: FTS + vector, weighted by config table values
  python3 vector_search.py combined --db ~/ai-ltm/memory.db --query "some query" --limit 10
"""

import argparse
import json
import math
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path


_RE_CJK = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\u3400-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f]")
_RE_WORD = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Split text into tokens. ASCII words stay as-is; CJK uses character bigrams."""
    if not text:
        return []
    text = text.lower()
    tokens: list[str] = []
    # Extract ASCII words
    tokens.extend(_RE_WORD.findall(text))
    # Extract CJK character bigrams for better partial matching
    cjk_chars = _RE_CJK.findall(text)
    for i in range(len(cjk_chars)):
        tokens.append(cjk_chars[i])  # unigram
        if i + 1 < len(cjk_chars):
            tokens.append(cjk_chars[i] + cjk_chars[i + 1])  # bigram
    return tokens


def build_idf(corpus: list[list[str]]) -> dict[str, float]:
    """Compute inverse document frequency for each token in the corpus."""
    n = len(corpus)
    if n == 0:
        return {}
    df: Counter = Counter()
    for tokens in corpus:
        df.update(set(tokens))
    return {token: math.log((n + 1) / (freq + 1)) + 1 for token, freq in df.items()}


def tfidf_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    """Compute TF-IDF vector as a sparse dict."""
    tf = Counter(tokens)
    total = len(tokens) if tokens else 1
    return {t: (c / total) * idf.get(t, 1.0) for t, c in tf.items()}


def cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """Compute cosine similarity between two sparse vectors."""
    if not a or not b:
        return 0.0
    keys = set(a) & set(b)
    dot = sum(a[k] * b[k] for k in keys)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def vector_to_json(vec: dict[str, float]) -> str:
    """Serialize sparse vector to compact JSON."""
    return json.dumps(vec, ensure_ascii=False, separators=(",", ":"))


def json_to_vector(s: str) -> dict[str, float]:
    """Deserialize sparse vector from JSON."""
    if not s:
        return {}
    return json.loads(s)


def episode_text(row: sqlite3.Row) -> str:
    """Concatenate episode fields into a single searchable string."""
    parts = []
    for field in ("summary", "context", "tags"):
        val = row[field]
        if val:
            parts.append(val)
    return " ".join(parts)


def get_config(conn: sqlite3.Connection) -> dict[str, float]:
    """Read config values as floats (skip internal keys starting with '_')."""
    cur = conn.execute("SELECT key, value FROM config WHERE key NOT LIKE '\\_%' ESCAPE '\\'")
    return {row[0]: float(row[1]) for row in cur.fetchall()}


def rebuild_embeddings(conn: sqlite3.Connection) -> int:
    """Rebuild TF-IDF embeddings for all episodes."""
    rows = conn.execute(
        "SELECT id, summary, context, tags FROM episodes"
    ).fetchall()
    if not rows:
        return 0

    corpus = [tokenize(episode_text(r)) for r in rows]
    idf = build_idf(corpus)

    for row, tokens in zip(rows, corpus):
        vec = tfidf_vector(tokens, idf)
        conn.execute(
            "UPDATE episodes SET embedding = ? WHERE id = ?",
            (vector_to_json(vec), row["id"]),
        )

    # Store IDF as a special config entry for incremental updates
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('_idf', ?)",
        (json.dumps(idf, ensure_ascii=False, separators=(",", ":")),),
    )
    conn.commit()
    return len(rows)


def get_idf(conn: sqlite3.Connection) -> dict[str, float]:
    """Get stored IDF, rebuilding if missing."""
    row = conn.execute(
        "SELECT value FROM config WHERE key = '_idf'"
    ).fetchone()
    if row and row[0]:
        return json.loads(row[0])
    # IDF not cached yet; rebuild
    rebuild_embeddings(conn)
    row = conn.execute(
        "SELECT value FROM config WHERE key = '_idf'"
    ).fetchone()
    return json.loads(row[0]) if row and row[0] else {}


def embed_single(conn: sqlite3.Connection, episode_id: int) -> None:
    """Generate and store embedding for a single episode using cached IDF."""
    idf = get_idf(conn)
    row = conn.execute(
        "SELECT summary, context, tags FROM episodes WHERE id = ?",
        (episode_id,),
    ).fetchone()
    if not row:
        return
    tokens = tokenize(episode_text(row))
    vec = tfidf_vector(tokens, idf)
    conn.execute(
        "UPDATE episodes SET embedding = ? WHERE id = ?",
        (vector_to_json(vec), episode_id),
    )
    conn.commit()


def search_vector(
    conn: sqlite3.Connection, query: str, limit: int = 10
) -> list[dict]:
    """Search episodes by vector similarity."""
    idf = get_idf(conn)
    query_tokens = tokenize(query)
    query_vec = tfidf_vector(query_tokens, idf)

    rows = conn.execute(
        "SELECT id, summary, context, tags, embedding, created_at FROM episodes"
    ).fetchall()

    results = []
    for row in rows:
        ep_vec = json_to_vector(row["embedding"])
        sim = cosine_similarity(query_vec, ep_vec)
        if sim > 0:
            results.append(
                {
                    "id": row["id"],
                    "summary": row["summary"],
                    "tags": row["tags"],
                    "created_at": row["created_at"],
                    "vector_score": round(sim, 4),
                }
            )

    results.sort(key=lambda x: x["vector_score"], reverse=True)
    return results[:limit]


def search_combined(
    conn: sqlite3.Connection, query: str, limit: int = 10
) -> list[dict]:
    """Combined FTS + vector search with configurable weights and time decay."""
    cfg = get_config(conn)
    fts_weight = cfg.get("fts_weight", 0.5)
    vec_weight = cfg.get("vector_weight", 0.5)
    decay_days = cfg.get("time_decay_days", 30)

    # FTS results — join tokens with OR for broader matching
    fts_query = " OR ".join(tokenize(query)) if tokenize(query) else query
    fts_scores: dict[int, float] = {}
    try:
        fts_rows = conn.execute(
            """
            SELECT rowid, rank * -1 AS fts_score
            FROM episodes_fts WHERE episodes_fts MATCH ?
            """,
            (fts_query,),
        ).fetchall()
        if fts_rows:
            max_fts = max(r["fts_score"] for r in fts_rows)
            for r in fts_rows:
                fts_scores[r["rowid"]] = r["fts_score"] / max_fts if max_fts > 0 else 0
    except sqlite3.OperationalError:
        pass  # FTS match syntax may fail; fall back to vector only

    # Vector results
    idf = get_idf(conn)
    query_tokens = tokenize(query)
    query_vec = tfidf_vector(query_tokens, idf)

    rows = conn.execute(
        "SELECT id, summary, context, tags, embedding, created_at FROM episodes"
    ).fetchall()

    vec_scores: dict[int, float] = {}
    episode_data: dict[int, dict] = {}
    for row in rows:
        ep_vec = json_to_vector(row["embedding"])
        sim = cosine_similarity(query_vec, ep_vec)
        vec_scores[row["id"]] = sim
        episode_data[row["id"]] = {
            "id": row["id"],
            "summary": row["summary"],
            "tags": row["tags"],
            "created_at": row["created_at"],
        }

    # Normalize vector scores
    max_vec = max(vec_scores.values()) if vec_scores else 0
    if max_vec > 0:
        vec_scores = {k: v / max_vec for k, v in vec_scores.items()}

    # Combine scores with time decay
    all_ids = set(fts_scores) | set(vec_scores)
    results = []
    for eid in all_ids:
        data = episode_data.get(eid)
        if not data:
            continue

        fts_s = fts_scores.get(eid, 0)
        vec_s = vec_scores.get(eid, 0)
        combined = fts_weight * fts_s + vec_weight * vec_s

        # Time decay
        if data["created_at"]:
            try:
                created = conn.execute(
                    "SELECT julianday('now') - julianday(?)",
                    (data["created_at"],),
                ).fetchone()[0]
                decay = 1.0 / (1.0 + created / decay_days)
                combined *= decay
            except (sqlite3.OperationalError, TypeError):
                pass

        if combined > 0:
            results.append(
                {
                    **data,
                    "fts_score": round(fts_s, 4),
                    "vector_score": round(vec_s, 4),
                    "combined_score": round(combined, 4),
                }
            )

    results.sort(key=lambda x: x["combined_score"], reverse=True)
    return results[:limit]


def main():
    parser = argparse.ArgumentParser(description="ai-ltm vector search")
    parser.add_argument("command", choices=["search", "rebuild", "combined", "embed"])
    parser.add_argument("--db", required=True, help="Path to memory.db")
    parser.add_argument("--query", help="Search query")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--id", type=int, help="Episode ID (for embed command)")
    args = parser.parse_args()

    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        print(f"Error: database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    if args.command == "rebuild":
        count = rebuild_embeddings(conn)
        print(f"Rebuilt embeddings for {count} episodes.")

    elif args.command == "embed":
        if not args.id:
            print("Error: --id required for embed command", file=sys.stderr)
            sys.exit(1)
        embed_single(conn, args.id)
        print(f"Embedded episode {args.id}.")

    elif args.command == "search":
        if not args.query:
            print("Error: --query required", file=sys.stderr)
            sys.exit(1)
        results = search_vector(conn, args.query, args.limit)
        print(json.dumps(results, ensure_ascii=False, indent=2))

    elif args.command == "combined":
        if not args.query:
            print("Error: --query required", file=sys.stderr)
            sys.exit(1)
        results = search_combined(conn, args.query, args.limit)
        print(json.dumps(results, ensure_ascii=False, indent=2))

    conn.close()


if __name__ == "__main__":
    main()
