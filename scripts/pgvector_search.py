#!/usr/bin/env python3
"""
pgvector_search.py — Semantic search over Slarti timeline events

Provides search_events() for use by other agents (heartbeat, weekly summary),
plus a CLI for manual testing and debugging.

Usage:
  python3 scripts/pgvector_search.py --query "tomato blight treatment"
  python3 scripts/pgvector_search.py --query "herb bed" --subject herb-bed --limit 5
  python3 scripts/pgvector_search.py --query "frost" --since-days 30
  python3 scripts/pgvector_search.py --query "test" --dry-run
"""
import sys
import os
import json
import argparse
import pathlib
import datetime
from typing import Optional

from dotenv import load_dotenv

SCRIPT_DIR = pathlib.Path(__file__).parent
SLARTI_ROOT = SCRIPT_DIR.parent
load_dotenv(dotenv_path=SLARTI_ROOT / '.env')


def get_embedding(text: str) -> Optional[list[float]]:
    """Get text embedding from Google text-embedding-004."""
    try:
        from google import genai
    except ImportError:
        return None

    api_key = os.environ.get('GOOGLE_API_KEY')
    if not api_key:
        return None

    try:
        client = genai.Client(api_key=api_key)
        result = client.models.embed_content(
            model='models/text-embedding-004',
            contents=text
        )
        return result.embeddings[0].values
    except Exception as e:
        print(f'WARNING: Embedding failed: {e}', file=sys.stderr)
        return None


def _connect():
    """Connect to Postgres. Returns connection or None."""
    try:
        import psycopg2
    except ImportError:
        print('WARNING: psycopg2 not installed', file=sys.stderr)
        return None

    db_url = (
        f"host=localhost port=5432 dbname={os.environ.get('POSTGRES_DB', 'slarti')} "
        f"user={os.environ.get('POSTGRES_USER', 'slarti')} "
        f"password={os.environ.get('POSTGRES_PASSWORD', '')}"
    )
    try:
        return psycopg2.connect(db_url)
    except Exception as e:
        print(f'WARNING: Could not connect to Postgres: {e}', file=sys.stderr)
        return None


def search_events(query: str, subject_id: str = None,
                  event_type: str = None, limit: int = 10,
                  since_days: int = None) -> list[dict]:
    """Search timeline events by semantic similarity.

    Falls back to ILIKE text search if embedding fails.

    Args:
        query: Natural language search query
        subject_id: Optional filter by subject (bed name, plant, etc.)
        event_type: Optional filter by event type (BED_FACT, TREATMENT, etc.)
        limit: Max results to return
        since_days: Optional filter to events within N days

    Returns:
        List of event dicts with keys: id, event_type, subject_id, author,
        content, confidence, created_at
    """
    conn = _connect()
    if not conn:
        return []

    try:
        cur = conn.cursor()

        # Try semantic search first
        embedding = get_embedding(query)

        if embedding:
            return _search_by_embedding(cur, embedding, subject_id, event_type,
                                        limit, since_days)
        else:
            print('WARNING: Embedding unavailable — falling back to text search',
                  file=sys.stderr)
            return _search_by_text(cur, query, subject_id, event_type,
                                   limit, since_days)
    except Exception as e:
        print(f'WARNING: Search failed: {e}', file=sys.stderr)
        return []
    finally:
        conn.close()


def _build_where(subject_id: str = None, event_type: str = None,
                 since_days: int = None) -> tuple[str, list]:
    """Build WHERE clause fragments and params."""
    clauses = []
    params = []

    if subject_id:
        clauses.append('subject_id = %s')
        params.append(subject_id)
    if event_type:
        clauses.append('event_type = %s')
        params.append(event_type)
    if since_days:
        clauses.append("created_at >= NOW() - INTERVAL '%s days'")
        params.append(since_days)

    where = ''
    if clauses:
        where = 'WHERE ' + ' AND '.join(clauses)

    return where, params


def _search_by_embedding(cur, embedding: list[float], subject_id: str = None,
                         event_type: str = None, limit: int = 10,
                         since_days: int = None) -> list[dict]:
    """Semantic search using cosine distance."""
    where, params = _build_where(subject_id, event_type, since_days)

    # Add embedding filter: only compare rows that have embeddings
    if where:
        where += ' AND embedding IS NOT NULL'
    else:
        where = 'WHERE embedding IS NOT NULL'

    sql = (
        f"SELECT id, event_type, subject_id, author, content, confidence, created_at "
        f"FROM timeline_events {where} "
        f"ORDER BY embedding <=> %s::vector LIMIT %s"
    )
    params.extend([json.dumps(embedding), limit])

    cur.execute(sql, params)
    return _rows_to_dicts(cur)


def _search_by_text(cur, query: str, subject_id: str = None,
                    event_type: str = None, limit: int = 10,
                    since_days: int = None) -> list[dict]:
    """Fallback text search using ILIKE."""
    where, params = _build_where(subject_id, event_type, since_days)

    # Add text search clause
    if where:
        where += ' AND content ILIKE %s'
    else:
        where = 'WHERE content ILIKE %s'
    params.append(f'%{query}%')

    sql = (
        f"SELECT id, event_type, subject_id, author, content, confidence, created_at "
        f"FROM timeline_events {where} "
        f"ORDER BY created_at DESC LIMIT %s"
    )
    params.append(limit)

    cur.execute(sql, params)
    return _rows_to_dicts(cur)


def _rows_to_dicts(cur) -> list[dict]:
    """Convert cursor rows to list of dicts."""
    columns = ['id', 'event_type', 'subject_id', 'author', 'content',
               'confidence', 'created_at']
    results = []
    for row in cur.fetchall():
        d = dict(zip(columns, row))
        if isinstance(d['created_at'], datetime.datetime):
            d['created_at'] = d['created_at'].isoformat()
        results.append(d)
    return results


def main():
    parser = argparse.ArgumentParser(description='Search Slarti timeline events')
    parser.add_argument('--query', required=True, help='Natural language search query')
    parser.add_argument('--subject', metavar='ID', help='Filter by subject_id')
    parser.add_argument('--type', metavar='TYPE', help='Filter by event_type')
    parser.add_argument('--limit', type=int, default=10, help='Max results (default: 10)')
    parser.add_argument('--since-days', type=int, metavar='N',
                        help='Only events from the past N days')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show query plan without executing')
    args = parser.parse_args()

    if args.dry_run:
        print(f'Query: "{args.query}"')
        print(f'Filters: subject={args.subject}, type={args.type}, '
              f'since_days={args.since_days}, limit={args.limit}')
        embedding = get_embedding(args.query)
        if embedding:
            print(f'Embedding: {len(embedding)} dimensions (first 5: {embedding[:5]})')
        else:
            print('Embedding: unavailable — would fall back to text search')
        return

    results = search_events(
        query=args.query,
        subject_id=args.subject,
        event_type=args.type,
        limit=args.limit,
        since_days=args.since_days,
    )

    if not results:
        print('No results found.')
        return

    print(f'Found {len(results)} result(s):\n')
    for r in results:
        print(f'  [{r["created_at"][:10]}] {r["event_type"]} ({r["author"]}, {r["subject_id"]})')
        print(f'    {r["content"]}')
        print(f'    confidence: {r["confidence"]}')
        print()


if __name__ == '__main__':
    main()
