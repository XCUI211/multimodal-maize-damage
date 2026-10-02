from pathlib import Path
import tarfile
import sys
import hashlib

def is_safe_path(member_name: str) -> bool:
    p = Path(member_name)
    if p.is_absolute():
        return False
    if '..' in p.parts:
        return False
    return True

def list_archive(archive_path: Path):
    with tarfile.open(archive_path, 'r:gz') as tf:
        members = tf.getmembers()
        print('Total members', len(members))
        for m in members[:200]:
            print(m.name, m.size)
        return members

def extract_archive(archive_path: Path, dest_dir: Path):
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, 'r:gz') as tf:
        members = tf.getmembers()
        safe = [m for m in members if is_safe_path(m.name)]
        unsafe = [m.name for m in members if not is_safe_path(m.name)]
        print('Safe members', len(safe), 'Unsafe members', len(unsafe))
        if unsafe:
            print('Unsafe paths detected sample', unsafe[:10])
        for m in safe:
            tf.extract(m, path=dest_dir)
    return len(safe), len(unsafe)

def sha256(path: Path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()

def main():
    archive = Path('downloads/EotG_data_final.tar.gz')
    if not archive.exists():
        print('Archive not found', archive)
        sys.exit(1)
    print('Archive sha256', sha256(archive))
    members = list_archive(archive)
    dest = Path('raw') / archive.stem
    safe_count, unsafe_count = extract_archive(archive, dest)
    print('Extracted safe count', safe_count)
    print('Extraction destination', dest)

if __name__ == '__main__':
    main()
