#!/usr/bin/env python3
"""
plant_lookup.py — Search the Missouri NRCS plant registry CSV

Looks up plants by common name, scientific name, or USDA symbol.
Use this when adding new plant entries to verify scientific names and families.

Usage:
  python3 scripts/plant_lookup.py "tomato"
  python3 scripts/plant_lookup.py "Solanum lycopersicum"
  python3 scripts/plant_lookup.py --symbol SOLY2
  python3 scripts/plant_lookup.py --family Rosaceae

Data source: docs/Missouri_NRCS_csv.txt
  Downloaded from https://plants.sc.egov.usda.gov/downloads
  Columns: Symbol, Synonym Symbol, Scientific Name with Author, State Common Name, Family
"""

import sys
import csv
import io
import argparse
import pathlib

SCRIPT_DIR = pathlib.Path(__file__).parent
ROOT       = SCRIPT_DIR.parent
CSV_PATH   = ROOT / 'docs' / 'Missouri_NRCS_csv.txt'


def load_csv() -> list[dict]:
    """Load and parse the NRCS CSV into a list of row dicts."""
    with open(CSV_PATH, encoding='utf-8') as f:
        content = f.read()
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    headers = rows[0]
    return [dict(zip(headers, row)) for row in rows[1:]]


def group_by_symbol(rows: list[dict]) -> dict[str, dict]:
    """
    Group rows by primary symbol. Primary rows have empty Synonym Symbol.
    Returns {symbol: {primary_row, synonyms: [...]}}
    """
    groups = {}
    for row in rows:
        sym = row.get('Symbol', '').strip()
        syn = row.get('Synonym Symbol', '').strip()
        if not sym:
            continue
        if sym not in groups:
            groups[sym] = {'primary': None, 'synonyms': []}
        if not syn:
            groups[sym]['primary'] = row
        else:
            groups[sym]['synonyms'].append(row)
    return groups


def search(rows: list[dict], query: str, by_symbol: str = None, by_family: str = None) -> list[dict]:
    """Search rows for matching entries. Returns primary rows only."""
    results = []
    query_lower = query.lower() if query else ''
    for row in rows:
        syn = row.get('Synonym Symbol', '').strip()
        if syn:
            continue  # skip synonym rows — show primaries only
        if by_symbol:
            if row.get('Symbol', '').upper() == by_symbol.upper():
                results.append(row)
            continue
        if by_family:
            if by_family.lower() in row.get('Family', '').lower():
                results.append(row)
            continue
        if not query_lower:
            continue
        # Match against common name and scientific name
        common = row.get('State Common Name', '').lower()
        sci    = row.get('Scientific Name with Author', '').lower()
        if query_lower in common or query_lower in sci:
            results.append(row)
    return results


def print_row(row: dict, groups: dict):
    sym    = row.get('Symbol', '')
    sci    = row.get('Scientific Name with Author', '')
    common = row.get('State Common Name', '') or '(no common name)'
    family = row.get('Family', '')
    print(f'  Symbol:  {sym}')
    print(f'  Name:    {sci}')
    print(f'  Common:  {common}')
    print(f'  Family:  {family}')
    syns = groups.get(sym, {}).get('synonyms', [])
    if syns:
        print(f'  Synonyms ({len(syns)}): ', end='')
        print(', '.join(s.get('Synonym Symbol', '') for s in syns[:5]))
    print()


def main():
    parser = argparse.ArgumentParser(description='Search Missouri NRCS plant registry')
    parser.add_argument('query', nargs='?', default='', help='Search term (common or scientific name)')
    parser.add_argument('--symbol', metavar='SYM', help='Look up by exact USDA symbol')
    parser.add_argument('--family', metavar='FAM', help='Filter by plant family')
    parser.add_argument('--limit', type=int, default=20, help='Max results to show (default 20)')
    args = parser.parse_args()

    if not CSV_PATH.exists():
        print(f'ERROR: {CSV_PATH} not found', file=sys.stderr)
        sys.exit(1)

    rows   = load_csv()
    groups = group_by_symbol(rows)

    results = search(rows, args.query, by_symbol=args.symbol, by_family=args.family)

    if not results:
        print(f'No results found.')
        if args.query:
            print(f'Try a shorter or different search term.')
        sys.exit(0)

    total = len(results)
    shown = results[:args.limit]
    print(f'Found {total} result(s){" — showing first " + str(args.limit) if total > args.limit else ""}:\n')
    for row in shown:
        print_row(row, groups)


if __name__ == '__main__':
    main()
