#!/usr/bin/env python3
"""
init_db.py — Initialize the Slarti pgvector database schema

Creates the timeline_events table and required indexes if they don't exist.
Safe to run multiple times (all operations are IF NOT EXISTS).

Usage:
  python3 scripts/init_db.py              # create schema
  python3 scripts/init_db.py --dry-run    # print SQL only
"""
import sys
import os
import argparse
import pathlib

from dotenv import load_dotenv

SCRIPT_DIR = pathlib.Path(__file__).parent
SLARTI_ROOT = SCRIPT_DIR.parent
load_dotenv(dotenv_path=SLARTI_ROOT / '.env')

SCHEMA_SQL = [
    "CREATE EXTENSION IF NOT EXISTS vector;",

    """CREATE TABLE IF NOT EXISTS timeline_events (
        id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        author TEXT NOT NULL,
        content TEXT NOT NULL,
        confidence REAL DEFAULT 0.5,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        embedding vector(768)
    );""",

    # HNSW index — works on empty tables (unlike IVFFlat)
    """CREATE INDEX IF NOT EXISTS idx_timeline_events_embedding
        ON timeline_events USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64);""",

    "CREATE INDEX IF NOT EXISTS idx_timeline_events_subject ON timeline_events (subject_id);",
    "CREATE INDEX IF NOT EXISTS idx_timeline_events_type ON timeline_events (event_type);",
    "CREATE INDEX IF NOT EXISTS idx_timeline_events_created ON timeline_events (created_at);",
]


def main():
    parser = argparse.ArgumentParser(description='Initialize Slarti pgvector schema')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print SQL statements without executing')
    args = parser.parse_args()

    if args.dry_run:
        print('-- Dry run: SQL statements that would be executed:\n')
        for stmt in SCHEMA_SQL:
            print(stmt)
            print()
        return

    try:
        import psycopg2
    except ImportError:
        print('ERROR: psycopg2 not installed. Run: pip install psycopg2-binary --break-system-packages',
              file=sys.stderr)
        sys.exit(1)

    db_url = (
        f"host=localhost port=5432 dbname={os.environ.get('POSTGRES_DB', 'slarti')} "
        f"user={os.environ.get('POSTGRES_USER', 'slarti')} "
        f"password={os.environ.get('POSTGRES_PASSWORD', '')}"
    )

    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cur = conn.cursor()

        for stmt in SCHEMA_SQL:
            label = stmt.strip().split('\n')[0][:60]
            try:
                cur.execute(stmt)
                print(f'  OK  {label}')
            except Exception as e:
                print(f'  WARN  {label}: {e}', file=sys.stderr)

        cur.close()
        conn.close()
        print('\nSchema initialization complete.')

    except Exception as e:
        print(f'ERROR: Could not connect to Postgres: {e}', file=sys.stderr)
        print('Is Docker running? Try: cd /mnt/c/Openclaw/slarti/db && docker compose up -d',
              file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
