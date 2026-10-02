import argparse
import csv
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from zipfile import ZipFile

OUTPUT_FIELDS = [
    'filename', 'image_relpath', 'label_relpath', 'farmer_unique_id', 'site_id',
    'sentinel_date', 'sentinel_vv', 'sentinel_vh', 'sentinel_age_days', 'failure_reason'
]


def parse_date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def read_json_archive(path):
    with ZipFile(path) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith('.json')]
        if len(members) != 1:
            raise ValueError(f'{path} must contain exactly one JSON member')
        data = json.loads(archive.read(members[0]))
    if not isinstance(data, list):
        raise ValueError(f'{path} JSON must be a list')
    return data


def load_sentinel(source_root, key):
    farmer, site = key
    path = source_root / 'ancillary_data' / 'sentinel' / f'{farmer}_{site}_S2_R.zip'
    if not path.is_file():
        return {}, 'missing_sentinel_file'
    records = read_json_archive(path)
    if not records:
        return {}, 'empty_sentinel_file'
    required = {'date', 'product', 'band', 'farmer_unique_id', 'site_id'}
    observed_fields = set().union(*(set(record) for record in records))
    if not required.issubset(observed_fields):
        raise ValueError(f'Sentinel schema missing fields for {key}: {sorted(required - observed_fields)}')
    products = {str(record.get('product')) for record in records}
    if products != {'COPERNICUS/S1_GRD'}:
        raise ValueError(f'Unexpected Sentinel product for {key}: {sorted(products)}')
    observations = defaultdict(lambda: defaultdict(list))
    for record in records:
        band = record.get('band')
        if band not in {'VV', 'VH'}:
            continue
        observation_date = parse_date(record.get('date'))
        if observation_date is None:
            continue
        value = record.get('value')
        if value is None or (isinstance(value, str) and value.strip().lower() in {'', 'na', 'nan', 'null'}):
            continue
        try:
            observations[observation_date][band].append(float(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f'Non numeric Sentinel value in record {record}') from exc
    return observations, ''


def choose_observation(observations, image_date):
    if image_date is None:
        return None, 'invalid_image_date'
    for age in range(1, 13):
        observation_date = image_date - timedelta(days=age)
        bands = observations.get(observation_date, {})
        if set(bands) != {'VV', 'VH'}:
            continue
        if any(len(set(values)) > 1 for values in (bands['VV'], bands['VH'])):
            return None, 'conflicting_duplicate_observation'
        return (observation_date, bands['VV'][0], bands['VH'][0], age), ''
    return None, 'no_complete_same_day_vv_vh_within_12d'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--raw-root', type=Path, default=None)
    args = parser.parse_args()
    root = args.root.resolve()
    source_root = (args.raw_root or root / 'raw' / 'EotG_data_final.tar' / 'EotG_data_final_no_git').resolve()
    sample_path = root / 'processed' / 'sample.csv'
    output_path = root / 'processed' / 'sar_features.csv'
    with sample_path.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        required = {'filename', 'image_relpath', 'label_relpath', 'farmer_unique_id', 'site_id', 'date'}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f'sample.csv missing fields: {sorted(required - set(reader.fieldnames or []))}')
        samples = list(reader)
    if len({row['filename'] for row in samples}) != len(samples):
        raise ValueError('sample.csv contains duplicate filename values')
    cache = {}
    rows = []
    matched = 0
    for sample in samples:
        key = (str(sample['farmer_unique_id']), str(sample['site_id']))
        if key not in cache:
            cache[key] = load_sentinel(source_root, key)
        observations, source_failure = cache[key]
        selected, selection_failure = choose_observation(observations, parse_date(sample['date']))
        failure = ';'.join(dict.fromkeys(reason for reason in (source_failure, selection_failure) if reason))
        row = {
            'filename': sample['filename'],
            'image_relpath': sample['image_relpath'],
            'label_relpath': sample['label_relpath'],
            'farmer_unique_id': sample['farmer_unique_id'],
            'site_id': sample['site_id'],
            'sentinel_date': selected[0].isoformat() if selected else '',
            'sentinel_vv': selected[1] if selected else '',
            'sentinel_vh': selected[2] if selected else '',
            'sentinel_age_days': selected[3] if selected else '',
            'failure_reason': failure
        }
        if selected:
            matched += 1
        rows.append(row)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({
        'output': output_path.relative_to(root).as_posix(),
        'sample_images': len(samples),
        'matched_sar_images': matched,
        'failed_sar_images': len(samples) - matched,
        'product': 'COPERNICUS/S1_GRD',
        'matching_rule': 'strictly earlier same-day VV and VH within 12 days'
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
