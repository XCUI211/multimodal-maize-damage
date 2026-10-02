from pathlib import Path
from zipfile import ZipFile

ROOT = Path('.').resolve()
SENT = ROOT / 'raw' / 'EotG_data_final.tar' / 'EotG_data_final_no_git' / 'ancillary_data' / 'sentinel'

def main(n=3):
    if not SENT.exists():
        print('sentinel folder not found', SENT)
        return
    zips = sorted(SENT.rglob('*.zip'))
    print('found', len(zips), 'zip files under', SENT)
    for i, z in enumerate(zips[:n], 1):
        print('\n---- ZIP', i, z.name, 'fullpath:', z)
        try:
            with ZipFile(z, 'r') as zf:
                names = zf.namelist()
                for line in names[:50]:
                    print(' ', line)
                if len(names) > 50:
                    print('  ... (total files in zip:', len(names), ')')
        except Exception as e:
            print('  failed to open zip:', e)

if __name__ == '__main__':
    main(3)
