from pathlib import Path
import csv
import re

ROOT = Path('.').resolve()
PROC_OUT = ROOT / 'processed_out'
ALT_PROC = ROOT / 'processed'

def normalize_label(s):
    s = s.strip().lower()
    return s

def map_to_class(raw):
    if not raw:
        return None
    s = normalize_label(raw)
    if any(k in s for k in ('healthy','health','ok','no damage','none')):
        return 'healthy'
    if any(k in s for k in ('drought','dry','water stress','water-stress')):
        return 'drought'
    if any(k in s for k in ('weed','weeds','grass','weediness')):
        return 'weed'
    return 'other_damage'

def extract_first_date(raw_dates_field):
    if not raw_dates_field:
        return None
    # raw_dates is like path=YYYY-MM-DD or path=YYYYMMDD; split and find first date pattern
    parts = raw_dates_field.split(';')
    for p in parts:
        m = re.search(r'(\d{4}-\d{2}-\d{2})', p)
        if m:
            return m.group(1)
        m2 = re.search(r'(\d{8})', p)
        if m2:
            d = m2.group(1)
            return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
    return None

def ancillary_exists(root, anc_type, date_str):
    if not date_str:
        return False
    folder = root / 'raw'
    # find folder under raw that contains ancillary_data/{anc_type}
    matches = list(folder.rglob(f'*ancillary_data*{anc_type}*'))
    if matches:
        # check any file under that folder contains date_str
        for m in matches:
            for f in m.rglob('*.zip'):
                if date_str.replace('-','') in f.name or date_str in f.name:
                    return True
    # fallback: search all zip names under raw for anc_type and date
    for f in folder.rglob('*.zip'):
        if anc_type in f.parts or anc_type in f.name:
            if date_str.replace('-','') in f.name or date_str in f.name:
                return True
    return False

def main():
    csv_path = PROC_OUT / 'master_table.csv'
    if not csv_path.exists():
        csv_path = ALT_PROC / 'master_table.csv'
    if not csv_path.exists():
        print('master_table.csv not found under processed_out or processed')
        return

    counts = {
        'healthy': {'total':0,'labeled':0,'with_date':0,'era5':0,'tamsat':0,'sentinel':0,'all3':0},
        'drought': {'total':0,'labeled':0,'with_date':0,'era5':0,'tamsat':0,'sentinel':0,'all3':0},
        'weed': {'total':0,'labeled':0,'with_date':0,'era5':0,'tamsat':0,'sentinel':0,'all3':0},
        'other_damage': {'total':0,'labeled':0,'with_date':0,'era5':0,'tamsat':0,'sentinel':0,'all3':0},
        'unknown': {'total':0}
    }

    with open(csv_path, 'r', encoding='utf8') as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            raw = r.get('candidate_damage_values','')
            # extract first value after '=' if present
            lab = None
            if raw:
                parts = [p for p in raw.split(';') if '=' in p]
                if parts:
                    val = parts[0].split('=',1)[1]
                    lab = val.strip()
            cls = map_to_class(lab) if lab else None
            if not cls:
                counts['unknown']['total'] += 1
                continue
            counts[cls]['total'] += 1
            if r.get('label_json_paths'):
                counts[cls]['labeled'] += 1
            date = extract_first_date(r.get('raw_dates',''))
            if date:
                counts[cls]['with_date'] += 1
            # check ancillary presence
            era5_ok = ancillary_exists(ROOT, 'era5', date)
            tamsat_ok = ancillary_exists(ROOT, 'tamsat', date)
            sentinel_ok = ancillary_exists(ROOT, 'sentinel', date)
            if era5_ok:
                counts[cls]['era5'] += 1
            if tamsat_ok:
                counts[cls]['tamsat'] += 1
            if sentinel_ok:
                counts[cls]['sentinel'] += 1
            if era5_ok and tamsat_ok and sentinel_ok:
                counts[cls]['all3'] += 1

    # print summary
    print('Class summary:')
    for k in ['healthy','drought','weed','other_damage']:
        v = counts[k]
        print(f"{k}: total={v['total']}, labeled={v['labeled']}, with_date={v['with_date']}, era5={v['era5']}, tamsat={v['tamsat']}, sentinel={v['sentinel']}, all3={v['all3']}")

    print('\nUnknown/no-candidate labels:', counts['unknown']['total'])

if __name__ == '__main__':
    main()
