import argparse
import csv
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from zipfile import ZipFile

OUTPUT_FIELDS = [
    'filename', 'farmer_unique_id', 'site_id', 'image_date',
    'weather_date', 'day_offset', 'temperature', 'rainfall', 'failure_reason'
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


def values_by_day(records, source, expected_band=None):
    result = defaultdict(list)
    for record in records:
        if not isinstance(record, dict):
            raise ValueError(f'{source} contains a non-object record')
        if source == 'era5' and record.get('band') != expected_band:
            continue
        observation_date = parse_date(record.get('date'))
        if observation_date is None:
            continue
        value = record.get('value')
        if value is None or (isinstance(value, str) and value.strip().lower() in {'', 'na', 'nan', 'null'}):
            continue
        try:
            numeric_value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'Non numeric {source} value in record {record}') from exc
        result[observation_date].append(numeric_value)
    return result


def resolve_value(values, observation_date):
    candidates = values.get(observation_date, [])
    if not candidates:
        return None, 'missing_observation'
    if len(set(candidates)) > 1:
        return None, 'conflicting_duplicate_observation'
    return candidates[0], ''


def load_environment(source_root, key):
    farmer, site = key
    ancillary = source_root / 'ancillary_data'
    era5_path = ancillary / 'era5' / f'{farmer}_{site}_ERA5.zip'
    tamsat_path = ancillary / 'tamsat' / f'{farmer}_{site}_TAMSAT.zip'
    failures = []
    if not era5_path.is_file():
        failures.append('missing_era5_file')
    if not tamsat_path.is_file():
        failures.append('missing_tamsat_file')
    if failures:
        return {}, {}, ';'.join(failures)
    era5_records = read_json_archive(era5_path)
    tamsat_records = read_json_archive(tamsat_path)
    era5_keys = set(era5_records[0]) if era5_records else set()
    tamsat_keys = set(tamsat_records[0]) if tamsat_records else set()
    required_era5 = {'date', 'band', 'value', 'farmer_unique_id', 'site_id'}
    required_tamsat = {'date', 'value', 'farmer_unique_id', 'site_id'}
    if not required_era5.issubset(era5_keys):
        raise ValueError(f'ERA5 schema missing fields for {key}: {sorted(required_era5 - era5_keys)}')
    if not required_tamsat.issubset(tamsat_keys):
        raise ValueError(f'TAMSAT schema missing fields for {key}: {sorted(required_tamsat - tamsat_keys)}')
    era5 = values_by_day(era5_records, 'era5', 'mean_2m_air_temperature')
    tamsat = values_by_day(tamsat_records, 'tamsat')
    return era5, tamsat, ''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--raw-root', type=Path, default=None)
    args = parser.parse_args()
    root = args.root.resolve()
    source_root = (args.raw_root or root / 'raw' / 'EotG_data_final.tar' / 'EotG_data_final_no_git').resolve()
    sample_path = root / 'processed' / 'sample.csv'
    output_path = root / 'processed' / 'weather_daily.csv'
    with sample_path.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        required = {'filename', 'farmer_unique_id', 'site_id', 'date'}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f'sample.csv missing fields: {sorted(required - set(reader.fieldnames or []))}')
        samples = list(reader)
    if len({row['filename'] for row in samples}) != len(samples):
        raise ValueError('sample.csv contains duplicate filename values')
    cache = {}
    rows = []
    complete_images = 0
    for sample in samples:
        key = (str(sample['farmer_unique_id']), str(sample['site_id']))
        if key not in cache:
            cache[key] = load_environment(source_root, key)
        era5, tamsat, source_failure = cache[key]
        image_date = parse_date(sample['date'])
        image_failure = '' if image_date else 'invalid_image_date'
        values_for_image = []
        for offset in range(10, 0, -1):
            weather_date = image_date - timedelta(days=offset) if image_date else None
            temperature, temperature_failure = resolve_value(era5, weather_date) if weather_date else (None, 'invalid_image_date')
            rainfall, rainfall_failure = resolve_value(tamsat, weather_date) if weather_date else (None, 'invalid_image_date')
            reasons = [reason for reason in (source_failure, image_failure, temperature_failure, rainfall_failure) if reason]
            reason = ';'.join(dict.fromkeys(reasons))
            row = {
                'filename': sample['filename'],
                'farmer_unique_id': sample['farmer_unique_id'],
                'site_id': sample['site_id'],
                'image_date': sample['date'],
                'weather_date': weather_date.isoformat() if weather_date else '',
                'day_offset': offset,
                'temperature': temperature if not temperature_failure else '',
                'rainfall': rainfall if not rainfall_failure else '',
                'failure_reason': reason
            }
            rows.append(row)
            values_for_image.append(row)
        if all(not row['failure_reason'] and row['day_offset'] in range(1, 11) for row in values_for_image):
            complete_images += 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({
        'output': output_path.relative_to(root).as_posix(),
        'sample_images': len(samples),
        'output_rows': len(rows),
        'complete_weather_images': complete_images,
        'failed_weather_images': len(samples) - complete_images,
        'weather_schema': {
            'era5_temperature_band': 'mean_2m_air_temperature',
            'tamsat_variable': 'value',
            'window': 'image_date minus 10 through image_date minus 1'
        }
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
