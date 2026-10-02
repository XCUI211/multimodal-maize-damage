from pathlib import Path
import json

ROOT = Path('.').resolve()
SENT = ROOT / 'raw' / 'EotG_data_final.tar' / 'EotG_data_final_no_git' / 'ancillary_data' / 'sentinel'

def main():
    zips = sorted(SENT.rglob('*.zip'))
    if not zips:
        print('no sentinel zips')
        return
    z = zips[0]
    print('inspecting', z.name)
    import zipfile
    with zipfile.ZipFile(z, 'r') as zf:
        names = zf.namelist()
        if not names:
            print('zip empty')
            return
        jname = names[0]
        print('json inside:', jname)
        data = json.load(zf.open(jname))

    # search for likely fields
    matches = []
    def walk(o, path=''):
        if isinstance(o, dict):
            for k,v in o.items():
                lk = k.lower()
                if any(t in lk for t in ('platform','satell','sensor','instrument','product','mission','orbit','polar')):
                    matches.append((path+'/'+k, v))
                walk(v, path+'/'+k)
        elif isinstance(o, list):
            for i,v in enumerate(o):
                walk(v, path+f'[{i}]')
        else:
            s = str(o)
            if any(tok in s.lower() for tok in ('sentinel-1','sentinel-2','s1','s2','vv','vh','b04','b08','band')):
                matches.append((path, o))

    walk(data)
    print('\nFound matches:')
    for k,v in matches:
        print(k, ':', v)

    print('\nTop-level keys:')
    print(list(data.keys())[:50])

if __name__ == '__main__':
    main()
