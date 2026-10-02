import csv
import hashlib
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / 'processed'
TARGETS = {'train': 0.80, 'val': 0.10, 'test': 0.10}
TABLES = ['sample.csv', 'weather_daily.csv', 'sar_features.csv']


def read_csv(path):
    with path.open('r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if not reader.fieldnames:
        raise ValueError(f'No header in {path}')
    return reader.fieldnames, rows


def assign_farmers(rows):
    classes = sorted({row['target_class'] for row in rows})
    farmer_counts = defaultdict(Counter)
    for row in rows:
        farmer_counts[row['farmer_unique_id']][row['target_class']] += 1
    images_by_farmer = Counter({farmer: sum(counts.values()) for farmer, counts in farmer_counts.items()})
    total_images = len(rows)
    target_images = {name: total_images * fraction for name, fraction in TARGETS.items()}
    overall_classes = Counter(row['target_class'] for row in rows)
    target_classes = {
        split: {label: overall_classes[label] * fraction for label in classes}
        for split, fraction in TARGETS.items()
    }
    assignments = {}
    totals = Counter()
    class_totals = {split: Counter() for split in TARGETS}

    def objective():
        size_error = sum(
            ((totals[split] - target_images[split]) / max(target_images[split], 1)) ** 2
            for split in TARGETS
        )
        class_error = sum(
            ((class_totals[split][label] - target_classes[split][label]) / max(target_classes[split][label], 1)) ** 2
            for split in TARGETS for label in classes
        )
        return size_error + class_error

    farmer_order = sorted(
        images_by_farmer,
        key=lambda farmer: (
            -images_by_farmer[farmer],
            hashlib.sha256(farmer.encode('utf-8')).hexdigest()
        )
    )
    for farmer in farmer_order:
        count = images_by_farmer[farmer]
        counts = farmer_counts[farmer]
        chosen = min(
            TARGETS,
            key=lambda split: assignment_score(
                split, count, counts, totals, class_totals,
                target_images, target_classes, classes
            )
        )
        assignments[farmer] = chosen
        totals[chosen] += count
        class_totals[chosen].update(counts)

    improved = True
    while improved:
        improved = False
        current = objective()
        for farmer in farmer_order:
            source = assignments[farmer]
            counts = farmer_counts[farmer]
            count = images_by_farmer[farmer]
            totals[source] -= count
            class_totals[source].subtract(counts)
            for destination in TARGETS:
                if destination == source:
                    continue
                totals[destination] += count
                class_totals[destination].update(counts)
                candidate = objective()
                totals[destination] -= count
                class_totals[destination].subtract(counts)
                if candidate + 1e-12 < current:
                    assignments[farmer] = destination
                    totals[destination] += count
                    class_totals[destination].update(counts)
                    current = candidate
                    improved = True
                    break
            else:
                totals[source] += count
                class_totals[source].update(counts)
            if improved:
                break
    return assignments, totals, images_by_farmer


def assignment_score(split, count, counts, totals, class_totals, target_images, target_classes, classes):
    size_error = sum(
        ((totals[other] + (count if other == split else 0) - target_images[other]) / max(target_images[other], 1)) ** 2
        for other in TARGETS
    )
    class_error = 0
    for other in TARGETS:
        for label in classes:
            value = class_totals[other][label] + (counts[label] if other == split else 0)
            class_error += ((value - target_classes[other][label]) / max(target_classes[other][label], 1)) ** 2
    return size_error + class_error, split


def write_csv(path, fieldnames, rows):
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    sample_fields, sample_rows = read_csv(PROCESSED / 'sample.csv')
    if 'split' in sample_fields:
        sample_fields = [field for field in sample_fields if field != 'split']
        sample_rows = [{key: value for key, value in row.items() if key != 'split'} for row in sample_rows]
    filenames = [row['filename'] for row in sample_rows]
    if len(filenames) != len(set(filenames)):
        raise ValueError('sample.csv contains duplicate filename values')
    assignments, image_totals, images_by_farmer = assign_farmers(sample_rows)
    split_rows = [{'filename': row['filename'], 'split': assignments[row['farmer_unique_id']]} for row in sample_rows]
    write_csv(PROCESSED / 'split_assignments.csv', ['filename', 'split'], split_rows)

    assignment_by_filename = {row['filename']: row['split'] for row in split_rows}
    for table_name in TABLES:
        path = PROCESSED / table_name
        fields, rows = read_csv(path)
        if 'split' in fields:
            fields = [field for field in fields if field != 'split']
            rows = [{key: value for key, value in row.items() if key != 'split'} for row in rows]
        table_filenames = {row['filename'] for row in rows}
        if table_name != 'weather_daily.csv' and table_filenames != set(assignment_by_filename):
            raise ValueError(f'{table_name} filename set differs from sample.csv')
        if table_name == 'weather_daily.csv' and not table_filenames.issubset(set(assignment_by_filename)):
            raise ValueError('weather_daily.csv contains a filename absent from sample.csv')
        merged_fields = fields + ['split']
        merged_rows = [dict(row, split=assignment_by_filename[row['filename']]) for row in rows]
        write_csv(path, merged_fields, merged_rows)

    print('sample_images', len(sample_rows))
    print('farmers', len(images_by_farmer))
    print('image_totals', dict(image_totals))
    print('image_fractions', {split: image_totals[split] / len(sample_rows) for split in TARGETS})


if __name__ == '__main__':
    main()