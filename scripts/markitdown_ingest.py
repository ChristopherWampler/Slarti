#!/usr/bin/env python3
"""
markitdown_ingest.py — Convert files to Markdown for Slarti

Usage:
  python3 scripts/markitdown_ingest.py --file <path>          # convert single file
  python3 scripts/markitdown_ingest.py --sources-dir <dir>    # convert all files in a directory
  python3 scripts/markitdown_ingest.py --audio <path>         # transcribe audio file (Mode V)

Outputs Markdown text. For plant database seeding, outputs go to data/plants/raw_converted/.
For voice notes, the transcript is returned on stdout for the orchestrator to consume.
"""

import sys
import os
import pathlib
import argparse

from dotenv import load_dotenv
load_dotenv(dotenv_path=pathlib.Path(__file__).parent.parent / '.env')

SLARTI_ROOT = pathlib.Path(os.environ.get('SLARTI_ROOT', '/mnt/c/Openclaw/slarti'))


def convert_file(file_path: str) -> str:
    """Convert any file to Markdown using MarkItDown."""
    try:
        from markitdown import MarkItDown
    except ImportError:
        print('ERROR: markitdown not installed. Run: pip install markitdown[all] --break-system-packages', file=sys.stderr)
        sys.exit(1)

    md = MarkItDown(enable_plugins=True)
    result = md.convert(file_path)
    return result.text_content


def ingest_sources_dir(sources_dir: pathlib.Path, output_dir: pathlib.Path):
    """Convert all files in sources_dir and write to output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    converted = 0
    for source_file in sorted(sources_dir.iterdir()):
        if source_file.is_dir():
            continue
        try:
            text = convert_file(str(source_file))
            out_path = output_dir / (source_file.stem + '.md')
            out_path.write_text(text)
            print(f'Converted: {source_file.name} -> {out_path.name}')
            converted += 1
        except Exception as e:
            print(f'ERROR converting {source_file.name}: {e}', file=sys.stderr)

    print(f'\nConverted {converted} files to {output_dir}')
    print('Next: send converted .md files to Claude with the extraction prompt from the spec.')


def extract_exif(image_path: str) -> str:
    """Extract EXIF metadata from a photo."""
    try:
        from markitdown import MarkItDown
    except ImportError:
        return ''

    md = MarkItDown()
    result = md.convert(image_path)
    return result.text_content


def main():
    parser = argparse.ArgumentParser(description='Slarti MarkItDown ingest')
    parser.add_argument('--file', help='Convert a single file to Markdown')
    parser.add_argument('--audio', help='Transcribe an audio file (Mode V)')
    parser.add_argument('--sources-dir', help='Convert all files in a directory')
    parser.add_argument('--exif', help='Extract EXIF from an image')
    parser.add_argument('--output-dir', help='Output directory (default: data/plants/raw_converted)')
    args = parser.parse_args()

    if args.file or args.audio:
        path = args.file or args.audio
        text = convert_file(path)
        print(text)
        return

    if args.exif:
        text = extract_exif(args.exif)
        print(text)
        return

    if args.sources_dir:
        sources = pathlib.Path(args.sources_dir)
        output = pathlib.Path(args.output_dir) if args.output_dir else (SLARTI_ROOT / 'data' / 'plants' / 'raw_converted')
        ingest_sources_dir(sources, output)
        return

    parser.print_help()


if __name__ == '__main__':
    main()
