#!/usr/bin/env python3
"""Pulls the published Google Sheets (CSV) of every region into catalog_<region>_updates.csv.

For each non-empty <REGION>_UPDATES_SHEET_CSV_URL in sheets_config.json:
  * downloads the CSV,
  * checks that it is a CSV with a header row containing a `name` column (a sheet that is
    not published returns an HTML login page, which is rejected),
  * writes it to the region's updates file when the content changed,
  * increments `updates_version` of that region and refreshes `last_updated` in version.json.

Nothing is touched when a sheet is unchanged, so the workflow never makes empty commits.
Standard library only (Python 3.8+).
"""
import csv
import datetime
import io
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, 'sheets_config.json')
VERSION = os.path.join(ROOT, 'version.json')

# sheets_config.json key -> region key in version.json
URL_KEYS = {
    'PK_UPDATES_SHEET_CSV_URL': 'PK',
    'GCC_UPDATES_SHEET_CSV_URL': 'GCC',
    'IN_UPDATES_SHEET_CSV_URL': 'IN',
    'UK_US_UPDATES_SHEET_CSV_URL': 'UK_US',
}

STANDARD_HEADER = ['code', 'name', 'genericFormula', 'company', 'unitType', 'stripsPerBox', 'unitsPerBox', 'mrp', 'barcode']


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def load_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def save_json(path, data):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')


def download(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'smartpharmacycatalog-sync/1.0'})
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
    return raw.decode('utf-8-sig')


def validate(text):
    """Returns (normalized_text, data_row_count). Raises ValueError when the CSV is not usable."""
    stripped = text.lstrip()
    if not stripped:
        raise ValueError('the sheet is empty')
    if stripped[:1] == '<':
        raise ValueError('got an HTML page instead of CSV (is the sheet published to the web as CSV?)')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if not rows:
        raise ValueError('the sheet has no rows')
    header = [h.strip().lower() for h in rows[0]]
    if 'name' not in header:
        raise ValueError('the first row is not a header row with a "name" column: %r' % rows[0][:9])
    lowered_standard = [h.lower() for h in STANDARD_HEADER]
    missing = [h for h in lowered_standard if h not in header]
    if missing:
        print('  note: columns not in the standard header (the app still reads this file): %s' % ', '.join(missing))
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    writer.writerows(rows)
    return out.getvalue(), len(rows) - 1


def main():
    config = load_json(CONFIG)
    version = load_json(VERSION)
    regions = version.setdefault('regions', {})
    changed = []
    failed = []
    tried = 0

    for key, region in URL_KEYS.items():
        url = str(config.get(key, '') or '').strip()
        if not url:
            print('[%s] no link in sheets_config.json - skipped' % region)
            continue
        tried += 1
        info = regions.get(region)
        if info is None:
            print('[%s] not found in version.json - skipped' % region)
            failed.append(region)
            continue
        target = os.path.join(ROOT, info['updates_file'])
        try:
            text, count = validate(download(url))
        except Exception as e:  # network error, bad link, wrong content
            print('[%s] FAILED: %s' % (region, e))
            failed.append(region)
            continue

        old = ''
        if os.path.exists(target):
            with open(target, encoding='utf-8', newline='') as f:
                old = f.read()
        if old == text:
            print('[%s] unchanged (%d rows)' % (region, count))
            continue
        with open(target, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        info['updates_version'] = int(info.get('updates_version', 0)) + 1
        info['updates_rows'] = count
        info['updates_updated_at'] = now_iso()
        changed.append(region)
        print('[%s] updated: %d rows, updates_version=%d' % (region, count, info['updates_version']))

    if changed:
        version['last_updated'] = now_iso()
        save_json(VERSION, version)
        print('version.json updated for: %s' % ', '.join(changed))
    else:
        print('No changes.')

    if tried and len(failed) == tried:
        print('Every configured sheet failed.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
