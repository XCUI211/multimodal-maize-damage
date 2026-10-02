import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from PIL import Image


HEADERS = [
    'filename', 'image_relpath', 'label_relpath', 'farmer_unique_id', 'site_id',
    'crop_name', 'date', 'damage_raw', 'extent_raw', 'growth_stage',
    'image_match_count', 'image_readable', 'target_class', 'exclude_reason',
    'all_crops_eligible', 'label_extent_conflict'
]
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp'}
MISSING_DAMAGE = {'', 'na', 'n/a', 'null', 'none'}


def scalar_text(value):
    if value is None:
        return ''
    return str(value)


def is_missing(value):
    return scalar_text(value).strip().lower() in MISSING_DAMAGE


def parse_date(value):
    raw = scalar_text(value).strip()
    if not raw:
        return '', True
    try:
        parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        return '', True
    return parsed.date().isoformat(), False


def read_label_records(label_root):
    records = []
    for label_path in sorted(label_root.glob('*.json')):
        with label_path.open('r', encoding='utf-8') as handle:
            data = json.load(handle)
        values = data if isinstance(data, list) else [data]
        for index, value in enumerate(values):
            if not isinstance(value, dict):
                raise ValueError(f'Label record is not an object: {label_path} index {index}')
            records.append((label_path, value))
    return records


def check_image(path):
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image.load()
        return True
    except Exception:
        return False


def append_reason(reasons, reason):
    if reason not in reasons:
        reasons.append(reason)


def make_row():
    return {header: '' for header in HEADERS}


def label_row(label_path, label, image_paths, root, mapping, readable):
    row = make_row()
    filename = scalar_text(label.get('filename'))
    matches = image_paths.get(filename, [])
    row['filename'] = filename
    row['label_relpath'] = label_path.relative_to(root).as_posix()
    row['image_match_count'] = len(matches)
    row['image_readable'] = bool(matches) and all(readable[path] for path in matches)
    if len(matches) == 1:
        row['image_relpath'] = matches[0].relative_to(root).as_posix()
    row['farmer_unique_id'] = scalar_text(label.get('farmer_unique_id'))
    row['site_id'] = scalar_text(label.get('site_id'))
    row['crop_name'] = scalar_text(label.get('crop_name'))
    row['extent_raw'] = scalar_text(label.get('extent'))
    row['growth_stage'] = scalar_text(label.get('growth_stage'))
    row['damage_raw'] = scalar_text(label.get('damage'))
    row['date'], date_error = parse_date(label.get('date'))
    reasons = []
    if not matches:
        append_reason(reasons, 'missing_image')
    if len(matches) > 1:
        append_reason(reasons, 'multiple_image_matches')
    if matches and not row['image_readable']:
        append_reason(reasons, 'unreadable_image')
    if date_error:
        append_reason(reasons, 'date_parse_error')
    damage_key = row['damage_raw'].strip()
    if is_missing(damage_key):
        append_reason(reasons, 'missing_manual_damage')
    elif damage_key in mapping:
        row['target_class'] = mapping[damage_key]
    else:
        append_reason(reasons, 'unknown_damage_code')
    extent_missing = is_missing(row['extent_raw'])
    row['label_extent_conflict'] = (
        (damage_key == 'G' and not extent_missing and row['extent_raw'] not in {'0', '0.0'})
        or (is_missing(damage_key) and not extent_missing)
    )
    row['exclude_reason'] = ';'.join(reasons)
    row['all_crops_eligible'] = bool(
        row['image_match_count'] == 1 and row['image_readable']
        and row['target_class'] and row['date']
    )
    return row


def unmatched_image_row(image_path, root, readable):
    row = make_row()
    row['filename'] = image_path.name
    row['image_relpath'] = image_path.relative_to(root).as_posix()
    row['image_match_count'] = 1
    row['image_readable'] = readable
    row['exclude_reason'] = 'missing_label'
    if not readable:
        row['exclude_reason'] += ';unreadable_image'
    row['all_crops_eligible'] = False
    return row


def validate_output(rows, output_path, image_paths, mapping):
    with output_path.open('r', newline='', encoding='utf-8') as handle:
        reader = csv.reader(handle)
        header = next(reader)
        body = list(reader)
    errors = []
    if header != HEADERS:
        errors.append('header_mismatch')
    if any(not any(cell.strip() for cell in row) for row in body):
        errors.append('blank_row')
    if any(row == HEADERS for row in body):
        errors.append('duplicate_header')
    if any(header_name.startswith('Unnamed:') or not header_name for header_name in header):
        errors.append('unnamed_column')
    for row in rows:
        for field in ('image_relpath', 'label_relpath'):
            value = row[field]
            if value and Path(value).is_absolute():
                errors.append('absolute_path')
        if row['image_match_count'] > 1:
            errors.append('one_to_many_association')
        if row['damage_raw'] and row['damage_raw'] not in mapping and not is_missing(row['damage_raw']):
            errors.append('unrecognized_damage_code')
    return sorted(set(errors))


def report_counts(rows, images, labels, validation_errors, unknown_codes):
    reason_counts = Counter()
    for row in rows:
        for reason in filter(None, row['exclude_reason'].split(';')):
            reason_counts[reason] += 1
    return {
        'original_image_count': len(images),
        'label_record_count': len(labels),
        'row_count': len(rows),
        'one_to_one_match_count': sum(row['image_match_count'] == 1 for row in rows if row['label_relpath']),
        'unmatched_count': sum(row['image_match_count'] == 0 for row in rows),
        'unreadable_image_count': sum(not row['image_readable'] for row in rows if row['image_relpath'] or row['image_match_count'] == 1),
        'damage_raw_counts': dict(Counter(row['damage_raw'] for row in rows if row['label_relpath'])),
        'target_class_counts': dict(Counter(row['target_class'] for row in rows if row['target_class'])),
        'exclude_reason_counts': dict(reason_counts),
        'all_crops_eligible_true_count': sum(row['all_crops_eligible'] is True for row in rows),
        'unknown_damage_codes': sorted(unknown_codes),
        'validation_errors': validation_errors
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--raw-root', type=Path, default=None)
    args = parser.parse_args()
    root = args.root.resolve()
    raw_root = (args.raw_root or root / 'raw' / 'EotG_data_final.tar' / 'EotG_data_final_no_git').resolve()
    image_root = raw_root / 'images'
    label_root = raw_root / 'labels'
    mapping_path = root / 'src' / 'process' / 'damage_mapping.json'
    mapping = json.loads(mapping_path.read_text(encoding='utf-8'))
    images = sorted(path for path in image_root.rglob('*') if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS)
    labels = read_label_records(label_root)
    image_paths = defaultdict(list)
    for image_path in images:
        image_paths[image_path.name].append(image_path)
    readable = {path: check_image(path) for path in images}
    rows = [label_row(path, label, image_paths, root, mapping, readable) for path, label in labels]
    labelled_filenames = {row['filename'] for row in rows if row['label_relpath']}
    rows.extend(unmatched_image_row(path, root, readable[path]) for path in images if path.name not in labelled_filenames)
    output_dir = root / 'processed'
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / 'master_table.csv'
    with output_path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    unknown_codes = {row['damage_raw'] for row in rows if row['damage_raw'] and row['damage_raw'] not in mapping and not is_missing(row['damage_raw'])}
    validation_errors = validate_output(rows, output_path, image_paths, mapping)
    report = report_counts(rows, images, labels, validation_errors, unknown_codes)
    report['source_root'] = raw_root.relative_to(root).as_posix()
    report['mapping_config'] = mapping_path.relative_to(root).as_posix()
    (output_dir / 'master_table_validation.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if validation_errors:
        raise SystemExit('Output validation failed')


if __name__ == '__main__':
    main()
