from pathlib import Path
import json
import re

ROOT = Path('.').resolve()
RAW = ROOT / 'raw'

def extract_strings(obj, prefix=''):
    results = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            results.extend(extract_strings(v, prefix + '/' + str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            results.extend(extract_strings(v, prefix + f'[{i}]'))
    else:
        if isinstance(obj, (str, int, float)):
            results.append((prefix, str(obj)))
    return results

def map_to_class(s):
    if not s:
        return None
    s = s.strip().lower()
    if any(k in s for k in ('healthy','health','ok','no damage','none','healthy')):
        return 'healthy'
    if any(k in s for k in ('drought','dry','water stress','water-stress')):
        return 'drought'
    if any(k in s for k in ('weed','weeds','grass','weediness')):
        return 'weed'
    return 'other_damage'

def find_date(strings):
    for path, val in strings:
        if re.search(r'\d{4}-\d{2}-\d{2}', val):
            return re.search(r'(\d{4}-\d{2}-\d{2})', val).group(1)
        if re.search(r'\d{8}', val):
            d = re.search(r'(\d{8})', val).group(1)
            return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
    return None

def ancillary_present(root, anc, date_str):
    # replaced by preindexed lookup
    return False


def build_anc_index(root):
    """Return dict anc_type -> set of zip filename strings (lowercased)"""
    idx = {'era5': set(), 'tamsat': set(), 'sentinel': set()}
    for f in root.rglob('*.zip'):
        name = f.name.lower()
        if 'era5' in str(f).lower() or 'era5' in name:
            idx['era5'].add(name)
        if 'tamsat' in str(f).lower() or 'tamsat' in name:
            idx['tamsat'].add(name)
        if 'sentinel' in str(f).lower() or 'sentinel' in name:
            idx['sentinel'].add(name)
    return idx

def main():
    # find labels JSONs
    labels = list(RAW.rglob('*/labels/*.json'))
    if not labels:
        # try deeper
        labels = list(RAW.rglob('*labels*.json'))
    print('Found label json count', len(labels))

    stats = {k:0 for k in ['healthy','drought','weed','other_damage','unknown']}
    multimodal = {k: {'era5':0,'tamsat':0,'sentinel':0,'all3':0} for k in ['healthy','drought','weed','other_damage']}
    labeled_with_date = {k:0 for k in ['healthy','drought','weed','other_damage']}

    # build ancillary index once
    anc_index = build_anc_index(RAW)

    for i, jf in enumerate(labels, 1):
        if i % 5000 == 0:
            print('processed labels', i)
        try:
            j = json.load(open(jf, 'r', encoding='utf8'))
        except Exception:
            continue
        strs = extract_strings(j)
        # find candidate damage
        candidate = None
        for path, val in strs:
            key = path.lower()
            if any(k in key for k in ('damage','damage_type','label','cat','class')):
                if val and not val.isnumeric():
                    candidate = val
                    break
        cls = map_to_class(candidate) if candidate else None
        if not cls:
            stats['unknown'] += 1
            continue
        stats[cls] += 1
        date = find_date(strs)
        if date:
            labeled_with_date[cls] += 1
        era = False
        tam = False
        sen = False
        if date:
            dcmp = date.replace('-', '')
            # check membership in index
            for name in anc_index.get('era5', []):
                if date in name or dcmp in name:
                    era = True
                    break
            for name in anc_index.get('tamsat', []):
                if date in name or dcmp in name:
                    tam = True
                    break
            for name in anc_index.get('sentinel', []):
                if date in name or dcmp in name:
                    sen = True
                    break
        if era:
            multimodal[cls]['era5'] += 1
        if tam:
            multimodal[cls]['tamsat'] += 1
        if sen:
            multimodal[cls]['sentinel'] += 1
        if era and tam and sen:
            multimodal[cls]['all3'] += 1

    print('\nClass counts (from label JSONs):')
    for k in ['healthy','drought','weed','other_damage']:
        print(f"{k}: {stats[k]} labeled, with_date={labeled_with_date[k]}, era5={multimodal[k]['era5']}, tamsat={multimodal[k]['tamsat']}, sentinel={multimodal[k]['sentinel']}, all3={multimodal[k]['all3']}")
    print('\nUnknown labels (could not map):', stats['unknown'])

if __name__ == '__main__':
    main()
