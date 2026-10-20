#!/usr/bin/env python3
"""Builds the four regional base catalogs of the Smart Pharmacy POS cloud CDN.

    python scripts/harvest_all_regions.py --pk-json <master_medicines.json> [--steps pk,in,fda,uk_us,gcc,version]

Every output is a CSV with EXACTLY these 9 columns and stays well under 8 MB:

    code,name,genericFormula,company,unitType,stripsPerBox,unitsPerBox,mrp,barcode

Sources (all public, nothing is invented):
  PK      the 41,000+ DRAP list that used to ship inside the app (--pk-json) + the Pakistani
          FMCG / baby milk / diaper / Hamdard / Qarshi items in scripts/data/pk_fmcg_items.json
  IN      Indian Medicine Dataset (junioralive/Indian-Medicine-Dataset, indian_medicine_data.csv),
          discontinued items dropped, big Indian pharma companies first + curated OTC / baby items
  UK_US   US FDA openFDA NDC directory (public domain) + curated UK / US OTC brands.
          The UK NHS dm+d is only available with a TRUD account, so it is NOT included.
  GCC     curated Gulf brands (scripts/data/gcc_curated.json + the extra brands below) + the generic
          formulary (international INN name + strength + form) taken from the same openFDA data.
          The SFDA / MOHAP lists block automated downloads, so they are NOT included, and no price or
          barcode is made up: `mrp` is 0 when unknown and `barcode` stays empty.

Only the Python standard library is used. Downloads are cached in --cache so a rerun is fast.
"""
import argparse
import collections
import csv
import datetime
import gzip
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'scripts', 'data')
HEADER = ['code', 'name', 'genericFormula', 'company', 'unitType', 'stripsPerBox', 'unitsPerBox', 'mrp', 'barcode']
VALID_TYPES = {'Tablet', 'Capsule', 'Syrup', 'Injection', 'Cream', 'Drops', 'Sachet', 'Inhaler', 'Other'}
MAX_BYTES = 8 * 1024 * 1024
INDIA_URL = 'https://raw.githubusercontent.com/junioralive/Indian-Medicine-Dataset/main/DATA/indian_medicine_data.csv'
FDA_URL = 'https://api.fda.gov/drug/ndc.json'


# --------------------------------------------------------------------------- helpers
def clean(s):
    s = (s or '').replace('…', ' ').replace(' ', ' ').replace('�', ' ')
    return re.sub(r'\s+', ' ', s).strip(' ,;-')


def clean_formula(s):
    """DRAP formulas read 'Each film coated tablet contains:- Cetirizine 10mg (USP Specification)'."""
    s = clean(s)
    s = re.sub(r'^each\b.*?\bcontains?\s*:?\s*-*\s*', '', s, flags=re.I)
    s = re.sub(r"\((?:usp|bp|ip|ph\.?\s*eur|in[- ]house|manufacturer.?s?)[^)]*specifications?[^)]*\)", '', s, flags=re.I)
    return clean(s)


def title_if_upper(s):
    return s.title() if s and s == s.upper() and len(s) > 3 else s


def fmt_price(p):
    try:
        p = float(p)
    except (TypeError, ValueError):
        return '0'
    if p <= 0:
        return '0'
    return ('%.2f' % p).rstrip('0').rstrip('.')


def write_csv(path, rows):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(HEADER)
        w.writerows(rows)
    size = os.path.getsize(path)
    if size > MAX_BYTES:
        raise SystemExit('%s is %.1f MB: over the 8 MB limit' % (os.path.basename(path), size / 1048576))
    print('  wrote %s: %d rows, %.2f MB' % (os.path.basename(path), len(rows), size / 1048576))
    return len(rows)


def finish_rows(prefix, items, start=1):
    """items: dicts name/formula/company/type/strips/units/mrp/barcode -> unique rows with codes."""
    rows = []
    seen = set()
    n = start
    for it in items:
        name = clean(it['name'])[:100]
        if len(name) < 2 or not re.search(r'[A-Za-z]', name):
            continue
        company = clean(it.get('company', ''))[:80]
        key = (name.lower(), company.lower())
        if key in seen:
            continue
        seen.add(key)
        utype = it.get('type', 'Other')
        if utype not in VALID_TYPES:
            utype = 'Other'
        rows.append([
            '%s-%05d' % (prefix, n), name, clean(it.get('formula', ''))[:150], company, utype,
            max(1, int(it.get('strips', 1) or 1)), max(1, int(it.get('units', 1) or 1)),
            fmt_price(it.get('mrp', 0)), clean(it.get('barcode', '')),
        ])
        n += 1
    return rows


def http_get(url, accept_gzip=True, timeout=150, tries=4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'smartpharmacycatalog-harvest/1.0',
                'Accept-Encoding': 'gzip' if accept_gzip else 'identity',
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get('Content-Encoding') == 'gzip':
                    raw = gzip.decompress(raw)
                return raw
        except Exception as e:  # noqa: BLE001 - retry on any network error
            last = e
            time.sleep(2 + 3 * i)
    raise RuntimeError('download failed: %s (%s)' % (url, last))


# --------------------------------------------------------------------------- PK
PK_TYPE = {'Suspension': 'Syrup', 'Infusion': 'Injection', 'Ointment': 'Cream', 'Gel': 'Cream', 'Lotion': 'Cream'}


def build_pk(pk_json):
    print('PK: reading', pk_json)
    data = json.load(open(pk_json, encoding='utf-8'))
    items = []
    for x in data:
        name = clean((x.get('name') or '').lstrip('*'))
        if not name:
            continue
        t = PK_TYPE.get(x.get('itemType'), x.get('itemType') or 'Other')
        items.append({
            'name': name, 'formula': clean_formula(x.get('genericFormula')), 'company': clean(x.get('company')),
            'type': t, 'strips': x.get('stripsPerBox', 1), 'units': x.get('unitsPerBox', 1),
            'mrp': x.get('defaultSalePrice', 0),
        })
    for x in json.load(open(os.path.join(DATA, 'pk_fmcg_items.json'), encoding='utf-8')):
        items.append({'name': x['name'], 'formula': x['formula'], 'company': x['company'], 'type': x['type'],
                      'strips': x['strips'], 'units': x['units'], 'mrp': 0})
    rows = finish_rows('PK', items)
    return write_csv(os.path.join(ROOT, 'catalog_pk_base.csv'), rows)


# --------------------------------------------------------------------------- IN
PRIORITY_COMPANIES = [
    # the owner's list first
    'sun pharma', 'cipla', 'mankind', 'alkem', 'lupin', 'abbott', 'glaxo', 'gsk', 'micro labs', 'macleods', 'aristo',
    'torrent', "dr reddy", 'intas', 'zydus', 'cadila pharmaceuticals', 'glenmark', 'himalaya', 'dabur',
    # other large Indian and multinational makers
    'alembic', 'emcure', 'ipca', 'ajanta', 'wockhardt', 'sanofi', 'pfizer', 'novartis', 'merck', 'bayer', 'johnson',
    'astrazeneca', 'franco-indian', 'indoco', 'win-medicare', 'piramal', 'biocon', 'natco', 'hetero', 'mylan', 'eris',
    'unichem', 'ranbaxy', 'usv', 'blue cross', 'fdc', 'jb chemicals', 'troikaa', 'serum', 'bharat serums', 'apex',
    'sanzyme', 'centaur', 'brooks', 'ordain', 'systopic', 'wallace', 'knoll', 'cadila healthcare', 'pharmed',
]


def priority_of(company):
    c = (company or '').lower()
    for i, p in enumerate(PRIORITY_COMPANIES):
        if p in c:
            return i
    return len(PRIORITY_COMPANIES)


def parse_pack(label, name):
    """'strip of 10 tablets' -> (type, strips, units)."""
    l = (label or '').lower()
    n = (name or '').lower()
    m = re.search(r'(\d+)\s*(tablet|capsule|tab|cap)', l)
    count = int(m.group(1)) if m else 0
    if 'capsule' in l or re.search(r'\bcap(sule)?s?\b', n):
        t = 'Capsule'
    elif 'tablet' in l or re.search(r'\btab(let)?s?\b', n):
        t = 'Tablet'
    elif 'inhaler' in l or 'rotacap' in l or 'respule' in n or 'inhaler' in n:
        t = 'Inhaler'
    elif any(k in l for k in ('injection', 'vial', 'ampoule', 'syringe', 'infusion', 'cartridge')) or 'inj' in n:
        t = 'Injection'
    elif any(k in l for k in ('drop', 'eye', 'ear', 'nasal')) or 'drop' in n:
        t = 'Drops'
    elif any(k in l for k in ('cream', 'gel', 'ointment', 'lotion', 'tube', 'paste', 'balm', 'spray')) or any(
            k in n for k in ('cream', 'gel', 'ointment', 'lotion')):
        t = 'Cream'
    elif any(k in l for k in ('sachet', 'powder', 'granule')) or 'sachet' in n:
        t = 'Sachet'
    elif any(k in l for k in ('syrup', 'suspension', 'solution', 'bottle', 'liquid', 'elixir')) or any(
            k in n for k in ('syrup', 'suspension', 'solution')):
        t = 'Syrup'
    else:
        t = 'Other'
    if t in ('Tablet', 'Capsule') and count > 0:
        return t, 1, count
    return t, 1, 1


def build_in(cache):
    path = os.path.join(cache, 'indian_medicine_data.csv')
    if not os.path.exists(path) or os.path.getsize(path) < 1000000:
        print('IN: downloading the Indian Medicine Dataset (about 32 MB) ...')
        with open(path, 'wb') as f:
            f.write(http_get(INDIA_URL, accept_gzip=False, timeout=900))
    print('IN: reading', path)
    csv.field_size_limit(10 ** 7)
    candidates = []
    with open(path, encoding='utf-8', errors='replace', newline='') as f:
        for r in csv.DictReader(f):
            if (r.get('Is_discontinued') or '').strip().upper() == 'TRUE':
                continue
            name = clean(r.get('name'))
            if len(name) < 2:
                continue
            try:
                price = float(r.get('price(₹)') or 0)
            except ValueError:
                price = 0
            company = clean(r.get('manufacturer_name'))
            comp = [clean(r.get('short_composition1')), clean(r.get('short_composition2'))]
            formula = ' + '.join(c for c in comp if c)
            t, strips, units = parse_pack(r.get('pack_size_label'), name)
            candidates.append((priority_of(company), 0 if price > 0 else 1, name.lower(), {
                'name': name, 'formula': formula, 'company': company, 'type': t, 'strips': strips,
                'units': units, 'mrp': price,
            }))
    print('  active rows in the dataset: %d' % len(candidates))
    candidates.sort(key=lambda c: (c[0], c[1], c[2]))
    curated = json.load(open(os.path.join(DATA, 'in_curated.json'), encoding='utf-8'))
    items = [{'name': c['name'], 'formula': c['formula'], 'company': c['company'], 'type': c['type'],
              'strips': c['strips'], 'units': c['units'], 'mrp': c.get('mrp', 0)} for c in curated]
    items += [c[3] for c in candidates[:30000]]
    rows = finish_rows('IN', items)
    # stay between 25,000 and 30,000 rows
    rows = rows[:30000]
    return write_csv(os.path.join(ROOT, 'catalog_in_base.csv'), rows)


# --------------------------------------------------------------------------- openFDA
FDA_QUERIES = [
    ('otc', 'product_type:"HUMAN OTC DRUG"+AND+finished:true', 26),
    ('anda', 'product_type:"HUMAN PRESCRIPTION DRUG"+AND+marketing_category:"ANDA"+AND+finished:true', 26),
    ('nda', 'marketing_category:"NDA"+AND+finished:true', 26),
    ('ndaag', 'marketing_category:"NDA AUTHORIZED GENERIC"+AND+finished:true', 12),
]


def fetch_fda(cache):
    os.makedirs(os.path.join(cache, 'fda'), exist_ok=True)
    total_pages = 0
    for tag, search, pages in FDA_QUERIES:
        for p in range(pages):
            skip = p * 1000
            out = os.path.join(cache, 'fda', '%s_%02d.json' % (tag, p))
            if os.path.exists(out):
                total_pages += 1
                continue
            url = '%s?search=%s&limit=1000&skip=%d' % (FDA_URL, search.replace('"', '%22').replace(' ', '%20'), skip)
            try:
                raw = http_get(url)
            except RuntimeError as e:
                print('  %s page %d: %s - stopping this query' % (tag, p, e))
                break
            data = json.loads(raw)
            results = data.get('results') or []
            with open(out, 'w', encoding='utf-8') as f:
                json.dump(results, f)
            total_pages += 1
            print('  openFDA %s page %d: %d records' % (tag, p, len(results)))
            if len(results) < 1000:
                break
            time.sleep(0.4)
    print('openFDA pages cached:', total_pages)


FORM_RULES = [
    ('TABLET', 'Tablet', 'Tab'), ('CAPLET', 'Tablet', 'Tab'), ('LOZENGE', 'Tablet', 'Lozenge'),
    ('CAPSULE', 'Capsule', 'Cap'), ('SOFTGEL', 'Capsule', 'Cap'),
    ('INJECTION', 'Injection', 'Inj'), ('INJECTABLE', 'Injection', 'Inj'), ('SYRINGE', 'Injection', 'Inj'),
    ('INHAL', 'Inhaler', 'Inhaler'), ('AEROSOL', 'Inhaler', 'Aerosol'), ('SPRAY', 'Drops', 'Spray'),
    ('CREAM', 'Cream', 'Cream'), ('OINTMENT', 'Cream', 'Ointment'), ('GEL', 'Cream', 'Gel'),
    ('LOTION', 'Cream', 'Lotion'), ('PASTE', 'Cream', 'Paste'), ('PATCH', 'Other', 'Patch'),
    ('SUSPENSION', 'Syrup', 'Suspension'), ('SYRUP', 'Syrup', 'Syrup'), ('ELIXIR', 'Syrup', 'Elixir'),
    ('SOLUTION', 'Syrup', 'Solution'), ('LIQUID', 'Syrup', 'Liquid'), ('DROPS', 'Drops', 'Drops'),
    ('POWDER', 'Sachet', 'Powder'), ('GRANULE', 'Sachet', 'Granules'), ('SUPPOSITORY', 'Other', 'Suppository'),
]


# cosmetics / personal-care listings that the FDA files as "OTC drugs" but a pharmacy catalog does not need
EXCLUDED_WORDS = ('antiperspirant', 'deodorant', 'sunscreen', 'sun screen', ' spf', 'spf ', 'sanitizer', 'wipes',
                  'makeup', 'foundation', 'lipstick', 'body wash')


def map_form(dosage_form, routes):
    f = (dosage_form or '').upper()
    r = ' '.join(routes or []).upper()
    if 'AEROSOL' in f or 'SPRAY' in f:
        if 'RESPIRATORY' in r or 'INHAL' in r:
            return 'Inhaler', 'Inhaler'
        if any(k in r for k in ('NASAL', 'OPHTHALMIC', 'OTIC', 'ORAL', 'SUBLINGUAL')):
            return 'Drops', 'Spray'
        return 'Other', 'Spray'
    for key, utype, short in FORM_RULES:
        if key in f:
            if utype == 'Syrup' and any(k in r for k in ('OPHTHALMIC', 'OTIC', 'NASAL')):
                return 'Drops', short
            return utype, short
    return 'Other', (dosage_form or '').title()


def norm_strength(s):
    s = (s or '').strip()
    s = re.sub(r'/1$', '', s)
    s = re.sub(r'(\d)\s*(mg|mcg|g|ml|iu|meq|%)', r'\1 \2', s, flags=re.I)
    return s.replace('mL', 'ml')


def fda_records(cache):
    d = os.path.join(cache, 'fda')
    if not os.path.isdir(d):
        return []
    out = []
    for fn in sorted(os.listdir(d)):
        out += json.load(open(os.path.join(d, fn), encoding='utf-8'))
    return out


def fda_items(records):
    """One dict per (brand, strength, form, labeler) plus the generic combos."""
    items = []
    generics = collections.Counter()
    generic_meta = {}
    per_combo = collections.Counter()
    for r in records:
        if not r.get('finished', True) or r.get('product_type') not in ('HUMAN OTC DRUG', 'HUMAN PRESCRIPTION DRUG'):
            continue
        ings = r.get('active_ingredients') or []
        if not ings:
            continue
        utype, short = map_form(r.get('dosage_form'), r.get('route'))
        parts = []
        for i in ings[:4]:
            ing = clean(i.get('name'))
            ing = ing.title() if ing.isupper() else ing
            parts.append('%s %s' % (ing, norm_strength(i.get('strength'))))
        formula = clean(' + '.join(parts))
        first_strength = norm_strength(ings[0].get('strength'))
        brand = title_if_upper(clean(r.get('brand_name_base') or r.get('brand_name') or r.get('generic_name')))
        if not brand:
            continue
        if brand.islower():
            brand = brand.title()
        probe = (' %s %s ' % (brand, r.get('generic_name') or '')).lower()
        if any(w in probe for w in EXCLUDED_WORDS):
            continue
        name = clean('%s %s %s' % (brand, first_strength, short))
        company = clean(r.get('labeler_name'))
        pack = (r.get('packaging') or [{}])[0].get('description', '')
        m = re.match(r'(\d+)\s+(TABLET|CAPSULE|CAPLET|LOZENGE|PATCH|SUPPOSITORY)', pack.upper())
        units = int(m.group(1)) if m and utype in ('Tablet', 'Capsule') else 1
        combo = (brand.lower(), first_strength.lower(), short.lower())
        per_combo[combo] += 1
        if per_combo[combo] <= 3:  # up to 3 labelers per product keeps the file diverse and small
            items.append({'name': name, 'formula': formula, 'company': company, 'type': utype, 'strips': 1,
                          'units': units, 'mrp': 0})
        gname = clean((r.get('generic_name') or ings[0].get('name') or ''))
        if gname:
            gname = gname.title() if gname.isupper() else gname
            gkey = (gname.lower(), first_strength.lower(), short.lower())
            generics[gkey] += 1
            generic_meta.setdefault(gkey, ('%s %s %s' % (gname, first_strength, short), formula, utype))
    return items, generics, generic_meta


def build_uk_us(cache):
    records = fda_records(cache)
    print('UK_US: %d openFDA records' % len(records))
    curated = json.load(open(os.path.join(DATA, 'uk_us_curated.json'), encoding='utf-8'))
    items = [{'name': c['name'], 'formula': c['formula'], 'company': c['company'], 'type': c['type'],
              'strips': c['strips'], 'units': c['units'], 'mrp': 0} for c in curated]
    fda, _, _ = fda_items(records)
    items += fda
    rows = finish_rows('UKUS', items)
    return write_csv(os.path.join(ROOT, 'catalog_uk_us_base.csv'), rows)


# --------------------------------------------------------------------------- GCC
# Brands named by the owner that are not yet in scripts/data/gcc_curated.json. Only facts that are certain
# are filled in; the formula is left empty when it is not.
GCC_EXTRA_BRANDS = [
    ('Suprax 400mg Cap', 'Cefixime 400mg', 'Capsule'), ('Suprax Suspension 100mg/5ml', 'Cefixime 100mg/5ml', 'Syrup'),
    ('Tavanic 500mg Tab', 'Levofloxacin 500mg', 'Tablet'), ('Amlor 5mg Cap', 'Amlodipine 5mg', 'Capsule'),
    ('Co-Diovan 160/12.5mg Tab', 'Valsartan 160mg + Hydrochlorothiazide 12.5mg', 'Tablet'),
    ('Vi-De 3 Drops', 'Cholecalciferol (Vitamin D3)', 'Drops'), ('Fusibact Cream 2%', 'Fusidic Acid 2%', 'Cream'),
    ('Mebo Ointment', 'Herbal burn ointment', 'Cream'), ('Almarai Nuralac Infant Formula 400g', 'Baby Milk / Infant Formula', 'Other'),
    ('S-26 Gold Infant Formula 400g', 'Baby Milk / Infant Formula', 'Other'), ('Similac Gold Infant Formula 400g', 'Baby Milk / Infant Formula', 'Other'),
    ('NAN Optipro 1 Infant Formula 400g', 'Baby Milk / Infant Formula Stage 1', 'Other'),
    ('Cerelac Wheat with Milk 250g', 'Baby Cereal / Infant Nutrition', 'Other'),
    ('Molfix Diapers Size 4', 'Baby Diaper / Size 4 Large', 'Other'), ('Molfix Diapers Size 5', 'Baby Diaper / Size 5 Junior', 'Other'),
    ('Bambi Diapers Size 4', 'Baby Diaper / Size 4 Large', 'Other'), ('Bambi Diapers Size 3', 'Baby Diaper / Size 3 Medium', 'Other'),
    ('Cetaphil Moisturizing Cream 250g', 'Skin Moisturiser', 'Cream'), ('QV Cream 500g', 'Skin Moisturiser', 'Cream'),
    ('Bioderma Sensibio H2O 250ml', 'Micellar Water', 'Other'), ('Eucerin Urea Repair Lotion', 'Urea Skin Lotion', 'Cream'),
    ('PanOxyl Acne Gel 5%', 'Benzoyl Peroxide 5%', 'Cream'), ('Panadol Extra Advance Tab', 'Paracetamol + Caffeine', 'Tablet'),
    ('Voltaren 50mg Tab', 'Diclofenac 50mg', 'Tablet'), ('Brufen 600mg Tab', 'Ibuprofen 600mg', 'Tablet'),
]


def build_gcc(cache):
    curated = json.load(open(os.path.join(DATA, 'gcc_curated.json'), encoding='utf-8'))
    items = [{'name': c['name'], 'formula': c['formula'], 'company': c['company'], 'type': c['type'],
              'strips': c['strips'], 'units': c['units'], 'mrp': c.get('mrp', 0)} for c in curated]
    for name, formula, utype in GCC_EXTRA_BRANDS:
        items.append({'name': name, 'formula': formula, 'company': '', 'type': utype, 'strips': 1, 'units': 1, 'mrp': 0})
    records = fda_records(cache)
    print('GCC: generic formulary from %d openFDA records' % len(records))
    _, generics, meta = fda_items(records)
    # a generic product that two or more labelers market is a mainstream medicine, not a one-off listing
    common = sorted(k for k, n in generics.items() if n >= 2)
    for k in common:
        name, formula, utype = meta[k]
        items.append({'name': name, 'formula': formula, 'company': '', 'type': utype, 'strips': 1, 'units': 1, 'mrp': 0})
    rows = finish_rows('GCC', items)
    return write_csv(os.path.join(ROOT, 'catalog_gcc_base.csv'), rows)


# --------------------------------------------------------------------------- version.json
def update_version(counts):
    path = os.path.join(ROOT, 'version.json')
    v = json.load(open(path, encoding='utf-8'))
    now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    for region, n in counts.items():
        info = v['regions'][region]
        sha = hashlib.sha256(open(os.path.join(ROOT, info['base_file']), 'rb').read()).hexdigest()
        info['base_rows'] = n
        if info.get('base_sha256') != sha:  # a new base version only when the file really changed
            info['base_version'] = int(info.get('base_version', 0)) + 1
            info['base_sha256'] = sha
            info['base_updated_at'] = now
    v['last_updated'] = now
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(v, f, indent=2, ensure_ascii=False)
        f.write('\n')
    print('version.json updated:', {r: (i['base_version'], i.get('base_rows')) for r, i in v['regions'].items()})


def count_rows(name):
    with open(os.path.join(ROOT, name), encoding='utf-8', newline='') as f:
        return sum(1 for _ in csv.reader(f)) - 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pk-json', default='')
    ap.add_argument('--cache', default=os.path.join(ROOT, '.harvest_cache'))
    ap.add_argument('--steps', default='pk,in,fda,uk_us,gcc,version')
    a = ap.parse_args()
    os.makedirs(a.cache, exist_ok=True)
    steps = set(a.steps.split(','))
    counts = {}
    if 'pk' in steps:
        if not a.pk_json:
            sys.exit('--pk-json is needed for the pk step')
        counts['PK'] = build_pk(a.pk_json)
    if 'in' in steps:
        counts['IN'] = build_in(a.cache)
    if 'fda' in steps:
        fetch_fda(a.cache)
    if 'uk_us' in steps:
        counts['UK_US'] = build_uk_us(a.cache)
    if 'gcc' in steps:
        counts['GCC'] = build_gcc(a.cache)
    if 'version' in steps:
        counts = {r: count_rows(f) for r, f in [('PK', 'catalog_pk_base.csv'), ('GCC', 'catalog_gcc_base.csv'),
                                                ('IN', 'catalog_in_base.csv'), ('UK_US', 'catalog_uk_us_base.csv')]}
        update_version(counts)


if __name__ == '__main__':
    main()
