#!/usr/bin/env python3
"""
populate_plants.py — Seed the Slarti plant database from scripts/plant_sources/

Reads hand-curated JSON files from scripts/plant_sources/, validates required fields,
and copies them to data/plants/. Run whenever you add new plant source files.

Usage:
  python3 scripts/populate_plants.py           # validate + copy all sources
  python3 scripts/populate_plants.py --dry-run # validate only, no writes
"""

import sys
import os
import json
import argparse
import pathlib

SCRIPT_DIR   = pathlib.Path(__file__).parent
SLARTI_ROOT  = SCRIPT_DIR.parent
SOURCES_DIR  = SCRIPT_DIR / 'plant_sources'
PLANTS_DIR   = SLARTI_ROOT / 'data' / 'plants'

REQUIRED_FIELDS = [
    'schema_version', 'entity_type', 'plant_slug', 'common_name',
    'scientific_name', 'zone_6b_notes', 'source', 'confidence'
]


def validate(data: dict, filename: str) -> list[str]:
    errors = []
    for field in REQUIRED_FIELDS:
        if field not in data:
            errors.append(f'Missing required field: {field}')
    if data.get('entity_type') != 'plant':
        errors.append(f'entity_type must be "plant", got "{data.get("entity_type")}"')
    if data.get('schema_version') != '5.2':
        errors.append(f'schema_version must be "5.2", got "{data.get("schema_version")}"')
    slug = data.get('plant_slug', '')
    if slug and slug != pathlib.Path(filename).stem:
        errors.append(f'plant_slug "{slug}" does not match filename "{filename}"')
    return errors


def main():
    parser = argparse.ArgumentParser(description='Populate Slarti plant database')
    parser.add_argument('--dry-run', action='store_true',
                        help='Validate only — do not write to data/plants/')
    args = parser.parse_args()

    if not SOURCES_DIR.exists():
        print(f'ERROR: plant_sources/ directory not found at {SOURCES_DIR}', file=sys.stderr)
        sys.exit(1)

    source_files = sorted(SOURCES_DIR.glob('*.json'))
    if not source_files:
        print('No JSON files found in plant_sources/')
        sys.exit(0)

    PLANTS_DIR.mkdir(parents=True, exist_ok=True)

    ok = 0
    errors_total = 0
    for src in source_files:
        try:
            with open(src) as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            print(f'  FAIL {src.name}: invalid JSON — {e}')
            errors_total += 1
            continue

        errors = validate(data, src.name)
        if errors:
            print(f'  FAIL {src.name}:')
            for err in errors:
                print(f'       {err}')
            errors_total += 1
            continue

        if args.dry_run:
            print(f'  OK   {src.name} — {data["common_name"]}')
        else:
            dest = PLANTS_DIR / src.name
            tmp = str(dest) + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, dest)
            print(f'  OK   {src.name} -> data/plants/{src.name}')
        ok += 1

    print(f'\n{ok} plants {"validated" if args.dry_run else "written"}, {errors_total} errors')
    if errors_total:
        sys.exit(1)


if __name__ == '__main__':
    main()
