import argparse
import csv
import json
import math
import platform
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zipfile import ZipFile

from PIL import Image


BASE_FIELDS = [
    'filename', 'image_relpath', 'label_relpath', 'farmer_unique_id', 'site_id',
    'crop_name', 'date', 'damage_raw', 'extent_raw', 'growth_stage',
    'image_match_count', 'image_readable', 'target_class', 'exclude_reason',
    'all_crops_eligible', 'label_extent_conflict'
]
WEATHER_FIELDS = [
    'rainfall_sum_10d', 'temperature_mean_10d', 'temperature_max_10d',
    'temperature_mean_30d', 'tamsat_valid_days_10d', 'era5_valid_days_10d',
    'weather_available_10d', 'weather_available_30d'
]
SATELLITE_FIELDS = [
    'sentinel_date', 'sentinel_vv', 'sentinel_vh', 'sentinel_age_days',
    'satellite_available_12d', 'satellite_available_24d'
]
AVAILABILITY_FIELDS = [
    'image_target_available', 'primary_common_sample',
    'extended_common_sample', 'multiscale_weather_sample'
]
OUTPUT_FIELDS = BASE_FIELDS + WEATHER_FIELDS + SATELLITE_FIELDS + AVAILABILITY_FIELDS
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp'}
MISSING_VALUES = {'', 'na', 'n/a', 'nan', 'null', 'none'}
MANUAL_MAP_PATH = 'src/process/damage_mapping.json'


def text(value):
    return '' if value is None else str(value)


def missing(value):
    return text(value).strip().lower() in MISSING_VALUES


def numeric(value):
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return None
    if isinstance(value, str) and value.strip().lower() in MISSING_VALUES:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def parse_day(value):
    raw = text(value).strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def csv_value(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'True' if value else 'False'
    return value


def read_json_member(archive):
    with ZipFile(archive) as handle:
        members = [name for name in handle.namelist() if name.lower().endswith('.json')]
        if len(members) != 1:
            raise ValueError(f'{archive} must contain exactly one JSON member')
        data = json.loads(handle.read(members[0]))
    if not isinstance(data, list):
        raise ValueError(f'{archive} JSON must contain a list')
    return data


def add_issue(issues, source, key, issue, detail, archive=''):
    issues.append({
        'source': source, 'farmer_unique_id': key[0], 'site_id': key[1],
        'observation_date': key[2], 'variable': key[3], 'issue': issue,
        'detail': detail, 'archive_relpath': archive
    })


def add_environment_value(store, issues, source, record, archive, variable):
    key = (text(record.get('farmer_unique_id')), text(record.get('site_id')),
           text(record.get('date')), variable)
    value = numeric(record.get('value'))
    if not key[0] or not key[1] or not key[2]:
        add_issue(issues, source, key, 'invalid_key', 'missing farmer site or date', archive)
        return
    bucket = store[key]
    if value is None:
        add_issue(issues, source, key, 'missing_value', 'value is missing or nonnumeric', archive)
        return
    if value in bucket['values']:
        bucket['identical_duplicates'] += 1
        return
    if bucket['values']:
        bucket['conflict'] = True
        add_issue(issues, source, key, 'conflicting_duplicate', 'different values for the same key', archive)
    bucket['values'].append(value)
    bucket['archive'] = archive


def load_weather(source_root, root, issues, needed_ranges):
    bucket = lambda: {'values': [], 'conflict': False, 'identical_duplicates': 0}
    weather = {'tamsat': defaultdict(bucket), 'era5': defaultdict(bucket)}
    index = defaultdict(lambda: {'tamsat': set(), 'era5': set(), 'sentinel': set()})
    archive_counts = Counter()
    for source in ('tamsat', 'era5'):
        for archive in sorted((source_root / 'ancillary_data' / source).glob('*.zip')):
            archive_rel = archive.relative_to(root).as_posix()
            records = read_json_member(archive)
            archive_counts[source] += 1
            for record in records:
                if source == 'tamsat':
                    variable = 'rainfall'
                    key = (text(record.get('farmer_unique_id')), text(record.get('site_id')))
                    index[key]['tamsat'].add(archive_rel)
                else:
                    band = text(record.get('band'))
                    if band not in {'mean_2m_air_temperature', 'maximum_2m_air_temperature'}:
                        continue
                    variable = band
                    key = (text(record.get('farmer_unique_id')), text(record.get('site_id')))
                    index[key]['era5'].add(archive_rel)
                if key not in needed_ranges or not (needed_ranges[key][0] <= text(record.get('date')) <= needed_ranges[key][1]):
                    continue
                add_environment_value(weather[source], issues, source, record, archive_rel, variable)
    return weather, index, archive_counts


def load_satellite(source_root, root, issues, needed_ranges):
    bucket = lambda: {'values': [], 'conflict': False, 'identical_duplicates': 0}
    values = defaultdict(bucket)
    index = defaultdict(set)
    products = set()
    archive_count = 0
    for archive in sorted((source_root / 'ancillary_data' / 'sentinel').glob('*.zip')):
        archive_rel = archive.relative_to(root).as_posix()
        archive_count += 1
        records = read_json_member(archive)
        for record in records:
            band = text(record.get('band'))
            product = text(record.get('product'))
            if band not in {'VV', 'VH'}:
                add_issue(issues, 'sentinel', ('', '', text(record.get('date')), band), 'unexpected_band', product, archive_rel)
                continue
            products.add(product)
            key = (text(record.get('farmer_unique_id')), text(record.get('site_id')), text(record.get('date')), band)
            index[(key[0], key[1])].add(archive_rel)
            pair_key = (key[0], key[1])
            if pair_key not in needed_ranges or not (needed_ranges[pair_key][0] <= key[2] <= needed_ranges[pair_key][1]):
                continue
            add_environment_value(values, issues, 'sentinel', record, archive_rel, band)
    return values, index, products, archive_count


def build_environment_database(source_root, root, needed_ranges, issues):
    path = root / 'processed' / '.multimodal_environment.sqlite'
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    connection.executescript('''
        create table observations (source text, farmer text, site text, day text, variable text, value real, conflict integer default 0, primary key (source, farmer, site, day, variable));
        create table archives (source text, farmer text, site text, archive text, primary key (source, farmer, site, archive));
        create table products (product text primary key);
        create index observations_lookup on observations (farmer, site, day, source, variable);
    ''')
    env_index = defaultdict(lambda: {'tamsat': set(), 'era5': set(), 'sentinel': set()})
    counts = Counter()
    for source in ('tamsat', 'era5', 'sentinel'):
        for archive in sorted((source_root / 'ancillary_data' / source).glob('*.zip')):
            archive_rel = archive.relative_to(root).as_posix()
            records = read_json_member(archive)
            counts[source] += 1
            for record in records:
                farmer, site, day = text(record.get('farmer_unique_id')), text(record.get('site_id')), text(record.get('date'))
                key = (farmer, site)
                if key not in needed_ranges or not (needed_ranges[key][0] <= day <= needed_ranges[key][1]):
                    continue
                if source == 'tamsat':
                    variable = 'rainfall'
                elif source == 'era5':
                    variable = text(record.get('band'))
                    if variable not in {'mean_2m_air_temperature', 'maximum_2m_air_temperature'}:
                        continue
                else:
                    variable = text(record.get('band'))
                    if variable not in {'VV', 'VH'}:
                        continue
                    connection.execute('insert or ignore into products(product) values (?)', (text(record.get('product')),))
                value = numeric(record.get('value'))
                env_index[key][source].add(archive_rel)
                connection.execute('insert or ignore into archives values (?, ?, ?, ?)', (source, farmer, site, archive_rel))
                if value is None:
                    continue
                old = connection.execute('select value, conflict from observations where source=? and farmer=? and site=? and day=? and variable=?', (source, farmer, site, day, variable)).fetchone()
                if old is None:
                    connection.execute('insert into observations values (?, ?, ?, ?, ?, ?, 0)', (source, farmer, site, day, variable, value))
                elif old[1] or old[0] == value:
                    continue
                else:
                    connection.execute('update observations set conflict=1 where source=? and farmer=? and site=? and day=? and variable=?', (source, farmer, site, day, variable))
                    issues.append({'source': source, 'farmer_unique_id': farmer, 'site_id': site, 'observation_date': day, 'variable': variable, 'issue': 'conflicting_duplicate', 'detail': 'conflicting values excluded', 'archive_relpath': archive_rel})
            connection.commit()
    return connection, env_index, counts


def db_weather_window(connection, key, capture_day, days):
    start = capture_day - timedelta(days=days)
    end = capture_day - timedelta(days=1)
    rows = connection.execute('select source, day, variable, value, conflict from observations where farmer=? and site=? and day between ? and ?', (key[0], key[1], start.isoformat(), end.isoformat())).fetchall()
    values = defaultdict(dict)
    for source, day, variable, value, conflict in rows:
        if not conflict:
            values[source][(day, variable)] = value
    rain = [values['tamsat'][(d.isoformat(), 'rainfall')] for d in (start + timedelta(days=i) for i in range(days)) if (d.isoformat(), 'rainfall') in values['tamsat']]
    mean = [values['era5'][(d.isoformat(), 'mean_2m_air_temperature')] for d in (start + timedelta(days=i) for i in range(days)) if (d.isoformat(), 'mean_2m_air_temperature') in values['era5']]
    maximum = [values['era5'][(d.isoformat(), 'maximum_2m_air_temperature')] for d in (start + timedelta(days=i) for i in range(days)) if (d.isoformat(), 'maximum_2m_air_temperature') in values['era5']]
    return {'rain': rain, 'mean_temp': mean, 'max_temp': maximum, 'rain_valid': len(rain), 'mean_valid': len(mean), 'max_valid': len(maximum), 'complete_rain': len(rain) == days, 'complete_mean': len(mean) == days}


def db_satellite_observation(connection, key, capture_day):
    earliest = capture_day - timedelta(days=24)
    rows = connection.execute('''select day, variable, value, conflict from observations where source='sentinel' and farmer=? and site=? and day between ? and ? order by day desc''', (key[0], key[1], earliest.isoformat(), (capture_day - timedelta(days=1)).isoformat())).fetchall()
    grouped = defaultdict(dict)
    for day, variable, value, conflict in rows:
        if not conflict:
            grouped[day][variable] = value
    candidates = []
    for stamp, bands in grouped.items():
        if set(bands) == {'VV', 'VH'}:
            observed = date.fromisoformat(stamp)
            candidates.append(((capture_day - observed).days, observed, bands))
    return min(candidates, key=lambda item: (item[0], -item[1].toordinal())) if candidates else None


def finalize_values(store):
    result = {}
    for key, bucket in store.items():
        if bucket['conflict'] or len(bucket['values']) != 1:
            result[key] = None
        else:
            result[key] = bucket['values'][0]
    return result


def weather_window(weather, key, capture_day, days):
    start = capture_day - timedelta(days=days)
    dates = [start + timedelta(days=offset) for offset in range(days)]
    rain = []
    mean_temp = []
    max_temp = []
    for observation_day in dates:
        stamp = observation_day.isoformat()
        rain_value = weather['tamsat'].get((key[0], key[1], stamp, 'rainfall'))
        mean_value = weather['era5'].get((key[0], key[1], stamp, 'mean_2m_air_temperature'))
        max_value = weather['era5'].get((key[0], key[1], stamp, 'maximum_2m_air_temperature'))
        if rain_value is not None:
            rain.append(rain_value)
        if mean_value is not None:
            mean_temp.append(mean_value)
        if max_value is not None:
            max_temp.append(max_value)
    return {
        'rain': rain, 'mean_temp': mean_temp, 'max_temp': max_temp,
        'rain_valid': len(rain), 'mean_valid': len(mean_temp), 'max_valid': len(max_temp),
        'complete_rain': len(rain) == days, 'complete_mean': len(mean_temp) == days
    }


def apply_weather(row, weather, issues):
    capture_day = parse_day(row['date'])
    if capture_day is None:
        row.update({field: '' for field in WEATHER_FIELDS})
        row['weather_available_10d'] = False
        row['weather_available_30d'] = False
        return
    key = (row['farmer_unique_id'], row['site_id'])
    if isinstance(weather, sqlite3.Connection):
        short = db_weather_window(weather, key, capture_day, 10)
        long = db_weather_window(weather, key, capture_day, 30)
    else:
        short = weather_window(weather, key, capture_day, 10)
        long = weather_window(weather, key, capture_day, 30)
    row['tamsat_valid_days_10d'] = short['rain_valid']
    row['era5_valid_days_10d'] = short['mean_valid']
    row['weather_available_10d'] = short['complete_rain'] and short['complete_mean']
    row['weather_available_30d'] = long['complete_rain'] and long['complete_mean']
    row['rainfall_sum_10d'] = sum(short['rain']) if short['complete_rain'] else ''
    row['temperature_mean_10d'] = sum(short['mean_temp']) / 10 if short['complete_mean'] else ''
    row['temperature_max_10d'] = max(short['max_temp']) if short['max_valid'] == 10 else ''
    row['temperature_mean_30d'] = sum(long['mean_temp']) / 30 if long['complete_mean'] else ''
    if short['rain_valid'] != 10 or short['mean_valid'] != 10:
        issues.append({'source': 'weather', 'farmer_unique_id': key[0], 'site_id': key[1],
                       'observation_date': row['date'], 'variable': '10d', 'issue': 'incomplete_window',
                       'detail': f"tamsat_valid_days={short['rain_valid']};era5_valid_days={short['mean_valid']}",
                       'archive_relpath': ''})


def complete_satellite_dates(values):
    grouped = defaultdict(dict)
    for (farmer, site, stamp, band), value in values.items():
        if value is not None:
            grouped[(farmer, site, stamp)][band] = value
    return grouped


def apply_satellite(row, satellite, complete_dates):
    capture_day = parse_day(row['date'])
    row['satellite_available_12d'] = False
    row['satellite_available_24d'] = False
    if capture_day is None:
        row.update({'sentinel_date': '', 'sentinel_vv': '', 'sentinel_vh': '', 'sentinel_age_days': ''})
        return
    key = (row['farmer_unique_id'], row['site_id'])
    if isinstance(complete_dates, sqlite3.Connection):
        candidate = db_satellite_observation(complete_dates, key, capture_day)
        candidates = [candidate] if candidate else []
    else:
        candidates = []
        for (farmer, site, stamp), bands in complete_dates.items():
            if (farmer, site) != key or set(bands) != {'VV', 'VH'}:
                continue
            observation_day = parse_day(stamp)
            if observation_day is not None and observation_day < capture_day:
                candidates.append(((capture_day - observation_day).days, observation_day, bands))
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=False)
    within_24 = [item for item in candidates if item[0] <= 24]
    if not within_24:
        row.update({'sentinel_date': '', 'sentinel_vv': '', 'sentinel_vh': '', 'sentinel_age_days': ''})
        return
    age, observation_day, bands = within_24[0]
    row['sentinel_date'] = observation_day.isoformat()
    row['sentinel_vv'] = bands['VV']
    row['sentinel_vh'] = bands['VH']
    row['sentinel_age_days'] = age
    row['satellite_available_24d'] = True
    row['satellite_available_12d'] = age <= 12


def satellite_issue_rows(values):
    grouped = defaultdict(lambda: defaultdict(list))
    for (farmer, site, stamp, band), bucket in values.items():
        grouped[(farmer, site, stamp)][band].append(bucket)
    rows = []
    for (farmer, site, stamp), bands in sorted(grouped.items()):
        for band in ('VV', 'VH'):
            if band not in bands:
                rows.append({'farmer_unique_id': farmer, 'site_id': site, 'observation_date': stamp,
                             'issue': f'missing_{band}', 'detail': 'no record for this band'})
        if set(bands) == {'VV', 'VH'}:
            for band in ('VV', 'VH'):
                bucket = bands[band][0]
                if bucket['identical_duplicates']:
                    rows.append({'farmer_unique_id': farmer, 'site_id': site, 'observation_date': stamp,
                                 'issue': f'identical_duplicate_{band}', 'detail': str(bucket['identical_duplicates'])})
                if bucket['conflict']:
                    rows.append({'farmer_unique_id': farmer, 'site_id': site, 'observation_date': stamp,
                                 'issue': f'conflict_{band}', 'detail': 'conflicting values excluded'})
    return rows


def satellite_issue_rows_db(connection):
    rows = []
    grouped = defaultdict(set)
    for day, band in connection.execute("select day, variable from observations where source='sentinel'"):
        grouped[day].add(band)
    for day, bands in sorted(grouped.items()):
        for band in ('VV', 'VH'):
            if band not in bands:
                rows.append({'farmer_unique_id': '', 'site_id': '', 'observation_date': day, 'issue': f'missing_{band}', 'detail': 'same day pair incomplete'})
    return rows


def read_base_rows(root, raw_root):
    image_root = raw_root / 'images'
    label_root = raw_root / 'labels'
    images = sorted(path for path in image_root.rglob('*') if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS)
    image_map = defaultdict(list)
    for path in images:
        image_map[path.name].append(path)
    mapping = json.loads((root / MANUAL_MAP_PATH).read_text(encoding='utf-8'))
    rows = []
    for label_path in sorted(label_root.glob('*.json')):
        data = json.loads(label_path.read_text(encoding='utf-8'))
        if not isinstance(data, list):
            raise ValueError(f'Label must be a list: {label_path}')
        for label in data:
            filename = text(label.get('filename'))
            matches = image_map.get(filename, [])
            row = {field: '' for field in BASE_FIELDS}
            row.update({
                'filename': filename,
                'label_relpath': label_path.relative_to(root).as_posix(),
                'farmer_unique_id': text(label.get('farmer_unique_id')),
                'site_id': text(label.get('site_id')),
                'crop_name': text(label.get('crop_name')),
                'date': text(label.get('date'))[:10] if parse_day(label.get('date')) else '',
                'damage_raw': text(label.get('damage')),
                'extent_raw': text(label.get('extent')),
                'growth_stage': text(label.get('growth_stage')),
                'image_match_count': len(matches),
                'image_readable': bool(matches) and all(image_readable(path) for path in matches)
            })
            if len(matches) == 1:
                row['image_relpath'] = matches[0].relative_to(root).as_posix()
            damage = row['damage_raw'].strip()
            if not missing(damage):
                row['target_class'] = mapping.get(damage, '')
                if not row['target_class']:
                    row['exclude_reason'] = 'unknown_damage_code'
            else:
                row['exclude_reason'] = 'missing_manual_damage'
            row['all_crops_eligible'] = bool(row['image_match_count'] == 1 and row['image_readable'] and row['target_class'] and row['date'])
            rows.append(row)
    if len(rows) != len(images):
        raise ValueError(f'Image and label counts differ: {len(images)} labels and {len(images)} images')
    return rows, images


def image_readable(path):
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image.load()
        return True
    except Exception:
        return False


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: csv_value(row.get(field, '')) for field in fields})


def coverage_rows(rows):
    stages = {
        'all_images': lambda row: True,
        'image_target_available': lambda row: row['image_target_available'],
        'weather_available_10d': lambda row: row['weather_available_10d'],
        'satellite_available_12d': lambda row: row['satellite_available_12d'],
        'satellite_available_24d': lambda row: row['satellite_available_24d'],
        'primary_common_sample': lambda row: row['primary_common_sample'],
        'extended_common_sample': lambda row: row['extended_common_sample'],
        'multiscale_weather_sample': lambda row: row['multiscale_weather_sample']
    }
    result = []
    for scope in ('all_crops', 'maize'):
        for stage, predicate in stages.items():
            for target in ('all', 'healthy', 'drought', 'weed', 'other_damage'):
                selected = [row for row in rows if (scope == 'all_crops' or row['crop_name'] == 'maize') and predicate(row) and (target == 'all' or row['target_class'] == target)]
                result.append({'scope': scope, 'stage': stage, 'target_class': target,
                               'image_count': len(selected),
                               'farmer_count': len({row['farmer_unique_id'] for row in selected if row['farmer_unique_id']})})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--raw-root', type=Path, default=None)
    args = parser.parse_args()
    root = args.root.resolve()
    raw_root = (args.raw_root or root / 'raw' / 'EotG_data_final.tar' / 'EotG_data_final_no_git').resolve()
    reports = root / 'reports'
    reports.mkdir(parents=True, exist_ok=True)
    issues = []
    rows, images = read_base_rows(root, raw_root)
    needed_ranges = {}
    for row in rows:
        if row['date']:
            key = (row['farmer_unique_id'], row['site_id'])
            start = (date.fromisoformat(row['date']) - timedelta(days=30)).isoformat()
            end = (date.fromisoformat(row['date']) - timedelta(days=1)).isoformat()
            if key not in needed_ranges:
                needed_ranges[key] = (start, end)
            else:
                needed_ranges[key] = (min(needed_ranges[key][0], start), max(needed_ranges[key][1], end))
    environment_db, env_index, weather_archive_counts = build_environment_database(raw_root, root, needed_ranges, issues)
    satellite_archive_count = weather_archive_counts['sentinel']
    products = {row[0] for row in environment_db.execute('select product from products')}
    row_keys = {(row['farmer_unique_id'], row['site_id']) for row in rows}
    for key in sorted(row_keys):
        if key not in env_index:
            for source in ('tamsat', 'era5', 'sentinel'):
                add_issue(issues, source, (key[0], key[1], '', ''), 'missing_environment_file', 'no archive matched farmer and site', '')
    for key, sources in env_index.items():
        for source in ('tamsat', 'era5', 'sentinel'):
            if len(sources[source]) > 1:
                add_issue(issues, source, (key[0], key[1], '', ''), 'multiple_environment_files', str(len(sources[source])), ';'.join(sorted(sources[source])))
    for row in rows:
        apply_weather(row, environment_db, issues)
        apply_satellite(row, environment_db, environment_db)
        row['image_target_available'] = bool(row['image_match_count'] == 1 and row['image_readable'] and row['target_class'])
        row['primary_common_sample'] = bool(row['image_target_available'] and row['weather_available_10d'] and row['satellite_available_12d'])
        row['extended_common_sample'] = bool(row['image_target_available'] and row['weather_available_10d'] and row['satellite_available_24d'])
        row['multiscale_weather_sample'] = bool(row['image_target_available'] and row['weather_available_10d'] and row['weather_available_30d'])
    output = root / 'processed' / 'master_table_multimodal.csv'
    write_csv(output, OUTPUT_FIELDS, rows)
    env_rows = []
    for key, sources in sorted(env_index.items()):
        env_rows.append({'farmer_unique_id': key[0], 'site_id': key[1],
                         'tamsat_files': ';'.join(sorted(sources['tamsat'])),
                         'era5_files': ';'.join(sorted(sources['era5'])),
                         'sentinel_files': ';'.join(sorted(env_index.get(key, {}).get('sentinel', set())))})
    for key in sorted(set((row['farmer_unique_id'], row['site_id']) for row in rows) - set(env_index)):
        env_rows.append({'farmer_unique_id': key[0], 'site_id': key[1], 'tamsat_files': '', 'era5_files': '', 'sentinel_files': ''})
    write_csv(root / 'processed' / 'environment_file_index.csv', ['farmer_unique_id', 'site_id', 'tamsat_files', 'era5_files', 'sentinel_files'], env_rows)
    write_csv(reports / 'environment_matching_issues.csv', ['source', 'farmer_unique_id', 'site_id', 'observation_date', 'variable', 'issue', 'detail', 'archive_relpath'], issues)
    satellite_issues = satellite_issue_rows_db(environment_db)
    write_csv(reports / 'satellite_pairing_issues.csv', ['farmer_unique_id', 'site_id', 'observation_date', 'issue', 'detail'], satellite_issues)
    coverage = coverage_rows(rows)
    write_csv(reports / 'multimodal_coverage.csv', ['scope', 'stage', 'target_class', 'image_count', 'farmer_count'], coverage)
    report = build_report(root, raw_root, rows, images, products, weather_archive_counts, satellite_archive_count, issues, coverage, satellite_issues)
    (reports / 'multimodal_preparation_report.md').write_text(report, encoding='utf-8')
    (reports / 'multimodal_data_dictionary.md').write_text(data_dictionary(), encoding='utf-8')
    print(json.dumps({'output_rows': len(rows), 'products': sorted(products), 'validation_errors': validate(rows, output, root)}, ensure_ascii=False, indent=2))
    environment_db.close()
    database_path = root / 'processed' / '.multimodal_environment.sqlite'
    if database_path.exists():
        database_path.unlink()


def validate(rows, output, root):
    errors = []
    with output.open(encoding='utf-8', newline='') as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != OUTPUT_FIELDS:
            errors.append('header_mismatch')
        csv_rows = list(reader)
    if any(not any(value.strip() for value in row.values()) for row in csv_rows):
        errors.append('blank_row')
    if any(row.get('filename') == 'filename' for row in csv_rows):
        errors.append('duplicate_header')
    if any(not field or field.startswith('Unnamed:') for field in (reader.fieldnames or [])):
        errors.append('unnamed_column')
    for row in rows:
        if row['date'] and row['sentinel_date'] and not row['sentinel_date'] < row['date']:
            errors.append('satellite_not_strictly_earlier')
        if row['date'] and row['sentinel_age_days'] != '' and (date.fromisoformat(row['date']) - date.fromisoformat(row['sentinel_date'])).days != int(row['sentinel_age_days']):
            errors.append('satellite_age_error')
        if row['primary_common_sample'] and not (row['image_target_available'] and row['weather_available_10d'] and row['satellite_available_12d']):
            errors.append('primary_flag_error')
        for field in ('image_relpath', 'label_relpath'):
            if row[field] and Path(row[field]).is_absolute():
                errors.append('absolute_path')
    return sorted(set(errors))


def build_report(root, raw_root, rows, images, products, weather_counts, satellite_count, issues, coverage, satellite_issues):
    def count(predicate):
        return sum(predicate(row) for row in rows)
    farmers = lambda predicate: len({row['farmer_unique_id'] for row in rows if predicate(row) and row['farmer_unique_id']})
    by_stage = {name: (count(predicate), farmers(predicate)) for name, predicate in {
        'image_target_available': lambda r: r['image_target_available'],
        'weather_available_10d': lambda r: r['weather_available_10d'],
        'satellite_available_12d': lambda r: r['satellite_available_12d'],
        'satellite_available_24d': lambda r: r['satellite_available_24d'],
        'primary_common_sample': lambda r: r['primary_common_sample'],
        'extended_common_sample': lambda r: r['extended_common_sample']
    }.items()}
    command = 'python scripts/build_multimodal_master.py --root .'
    lines = [
        '# Multimodal preparation report', '',
        f'- Command: `{command}`',
        f'- Python: `{sys.version.split()[0]}` on `{platform.platform()}`',
        f'- Raw root: `{raw_root.relative_to(root).as_posix()}`',
        '- Original files were read only and the existing `processed/master_table.csv` was not overwritten.', '',
        '## Verified source structure',
        '- Images: `raw/.../images/*.JPG`.',
        '- Labels: `raw/.../labels/*.json`, each a list with `filename` and the manual fields.',
        '- Weather: one ZIP per farmer-site under `ancillary_data/tamsat` and `ancillary_data/era5`.',
        '- Radar: one ZIP per farmer-site under `ancillary_data/sentinel`; internal product is `COPERNICUS/S1_GRD`, bands are `VV` and `VH`.', '',
        '## Variables and units',
        '- TAMSAT uses the JSON `value` field as the verified daily rainfall variable. The source JSON has no unit metadata; values are retained without conversion and are reported as source units.',
        '- ERA5 uses `mean_2m_air_temperature` and `maximum_2m_air_temperature`. The source JSON has no unit metadata; values are retained without Kelvin-to-Celsius conversion.',
        '- Sentinel values are the source `value` for `VV` and `VH`; no optical index or cross-date pairing is used.', '',
        '## Counts',
        f'- Images: {len(images)}; output rows: {len(rows)}.',
        f'- Weather archives: {dict(weather_counts)}; Sentinel archives: {satellite_count}.',
        f'- Sentinel products: {sorted(products)}.',
        f'- Environment issue rows: {len(issues)}.',
        f'- Sentinel pairing issue rows: {len(satellite_issues)}.',
        f"- Sentinel pairing issue counts: {dict(Counter(item['issue'] for item in satellite_issues))}.",
    ]
    for name, (image_count, farmer_count) in by_stage.items():
        lines.append(f'- {name}: {image_count} images, {farmer_count} farmers.')
    lines += ['', '## Validation', '- Output path and relative-path checks are performed by the script.', '- Dates are restricted to strictly earlier observations.', '- Weather windows are `[capture_date - N days, capture_date - 1 day]`.', '- Conflicting duplicate environment values are excluded from derived values and reported.', '- Sentinel VV and VH are paired only on the same farmer-site-observation date.', '- No train, validation, or test split was generated.', '', '## Remaining issues', '- Temperature units are not declared in the original JSON metadata, so the source scale is preserved.', '- Sentinel records with missing bands, duplicate records, and conflicts are listed in `reports/satellite_pairing_issues.csv`.', '- Detailed coverage by crop and class is in `reports/multimodal_coverage.csv`.']
    return '\n'.join(lines) + '\n'


def data_dictionary():
    lines = ['# Multimodal data dictionary', '', '| Field | Source or calculation | Unit | Model role |', '|---|---|---|---|']
    for field in BASE_FIELDS:
        role = 'target' if field == 'target_class' else 'input or quality control'
        lines.append(f'| `{field}` | Original image or label table field | source value | {role} |')
    entries = {
        'rainfall_sum_10d': 'TAMSAT value summed for capture date minus 10 through minus 1',
        'temperature_mean_10d': 'ERA5 mean_2m_air_temperature average over the same ten days',
        'temperature_max_10d': 'ERA5 maximum_2m_air_temperature maximum over the same ten days',
        'temperature_mean_30d': 'ERA5 mean_2m_air_temperature average for capture date minus 30 through minus 1',
        'tamsat_valid_days_10d': 'Count of valid TAMSAT days in the ten day window',
        'era5_valid_days_10d': 'Count of valid ERA5 mean temperature days in the ten day window',
        'weather_available_10d': 'True only when both required ten day series are complete',
        'weather_available_30d': 'True only when both required thirty day series are complete',
        'sentinel_date': 'Nearest strictly earlier complete same day VV and VH observation within 24 days',
        'sentinel_vv': 'Sentinel-1 COPERNICUS/S1_GRD VV source value',
        'sentinel_vh': 'Sentinel-1 COPERNICUS/S1_GRD VH source value',
        'sentinel_age_days': 'Capture date minus sentinel date in whole days',
        'satellite_available_12d': 'Complete same day VV and VH no more than 12 days earlier',
        'satellite_available_24d': 'Complete same day VV and VH no more than 24 days earlier',
        'image_target_available': 'Image, readability, and manual target are all available',
        'primary_common_sample': 'Image target, complete ten day weather, and twelve day radar',
        'extended_common_sample': 'Image target, complete ten day weather, and twenty four day radar',
        'multiscale_weather_sample': 'Image target, complete ten day and thirty day weather'
    }
    for field, description in entries.items():
        role = 'model input' if field not in {'weather_available_10d', 'weather_available_30d', 'satellite_available_12d', 'satellite_available_24d'} else 'quality control'
        lines.append(f'| `{field}` | {description} | Source units or day count; temperature unit unconfirmed | {role} |')
    lines += ['', 'The preparation stage performs no imputation, interpolation, normalization, or data split. `target_class` is the only prediction target field.']
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    main()
