from pathlib import Path
ROOT = Path('.').resolve()
P = ROOT / 'raw' / 'EotG_data_final.tar' / 'EotG_data_final_no_git' / 'ancillary_data' / 'sentinel'
cnt = 0
if not P.exists():
    print('sentinel folder not found at', P)
else:
    for f in P.rglob('*.zip'):
        print(f.name)
        cnt += 1
        if cnt >= 50:
            break
    print('printed', cnt)
