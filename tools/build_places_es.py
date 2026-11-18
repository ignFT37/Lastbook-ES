#!/usr/bin/env python3
"""Add Spain to the offline place finder (derived/geo/places.sqlite), keeping the rows already there.

  buildenv/python tools/build_places_es.py --out <data>/derived/geo/places.sqlite --geonames <sources>/geonames
        --names <sources>/build/osm-names.sqlite --addresses <data>/derived/geo/addresses/ES.sqlite

Sources (see docs/REGIONS-ES.md, "Places and languages"):
- GeoNames for Spain (export/dump/ES.zip and alternatenames/ES.zip, CC BY 4.0): towns and villages, municipalities,
  provinces and communities, peaks, passes, islands, lakes and reservoirs, with their Spanish, Catalan, Basque and
  Galician names. Province and community come from GeoNames' admin codes, named as in engine/address_search.py.
- Named OpenStreetMap places and help points (ODbL) from tools/build_map_es.py --names. A town in both is kept once,
  at the OSM point (where the map draws its name) with the GeoNames detail.
- Postcode centres: the mean of each postcode's addresses in the Spanish address index (Overture, CC BY 4.0). A point
  per postcode, not a postcode area.
The first run keeps the original file as places.sqlite.before-es and every run starts again from that copy.
"""
import argparse, csv, math, re, shutil, sqlite3, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from engine.address_search import ES_PROVINCES, ES_REGIONS, es_fold  # noqa: E402

csv.field_size_limit(10 ** 7)
# GeoNames admin2 codes for Spain (plate-style letters) -> INE province codes
G2INE = dict(pair.split(':') for pair in (
    'VI:01 AB:02 A:03 AL:04 AV:05 BA:06 PM:07 B:08 BU:09 CC:10 CA:11 CS:12 CR:13 CO:14 C:15 CU:16 GI:17 GR:18 GU:19 SS:20 '
    'H:21 HU:22 J:23 LE:24 L:25 LO:26 LU:27 M:28 MA:29 MU:30 NA:31 OR:32 O:33 P:34 GC:35 PO:36 SA:37 TF:38 S:39 SG:40 '
    'SE:41 SO:42 T:43 TE:44 TO:45 V:46 VA:47 BI:48 ZA:49 Z:50 CE:51 ME:52').split())
KINDS = {  # GeoNames feature code -> (kind shown, rank)
    'PPLC': ('city', 1), 'PPLA': ('city', 1), 'PPLA2': ('city', 1), 'PPLA3': ('town', 2), 'PPLA4': ('village', 3),
    'PPL': ('village', 3), 'PPLX': ('neighbourhood', 3), 'PPLL': ('locality', 3), 'PPLF': ('village', 3),
    'PPLQ': ('abandoned village', 3), 'ADM1': ('region', 2), 'ADM2': ('province', 2), 'ADM3': ('municipality', 2),
    'PK': ('peak', 3), 'PKS': ('peaks', 3), 'MT': ('mountain', 3), 'MTS': ('mountains', 3), 'PASS': ('mountain pass', 3),
    'HLL': ('hill', 3), 'RDGE': ('ridge', 3), 'VAL': ('valley', 3), 'GRGE': ('gorge', 3), 'CAPE': ('cape', 3),
    'ISL': ('island', 2), 'ISLS': ('islands', 2), 'ISLET': ('islet', 3), 'LK': ('lake', 3), 'LKS': ('lakes', 3),
    'RSV': ('reservoir', 3), 'SPNG': ('spring', 3), 'LGN': ('lagoon', 3), 'BAY': ('bay', 3), 'WTRF': ('waterfall', 3),
    'PRK': ('park', 3), 'RESN': ('nature reserve', 3), 'HUT': ('mountain hut', 3), 'HUTS': ('mountain huts', 3)}
LANGS = {'es', 'ca', 'eu', 'gl', 'ast', 'an', 'oc'}
OSM_KINDS = {'city': ('city', 1), 'town': ('town', 2), 'village': ('village', 3), 'hamlet': ('hamlet', 3),
             'suburb': ('neighbourhood', 3), 'locality': ('locality', 3), 'island': ('island', 2), 'farm': ('farm', 3),
             'county': ('county', 2), 'region': ('region', 1)}


def key(name):
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', es_fold(name)).split())


def km(lat1, lon1, lat2, lon2):
    return math.hypot(lat2 - lat1, (lon2 - lon1) * math.cos(math.radians(lat1))) * 111.2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--geonames', required=True, help='folder with dump/ES.txt and alt/ES.txt')
    ap.add_argument('--names', required=True)
    ap.add_argument('--addresses', required=True)
    a = ap.parse_args()
    t0 = time.time()
    out = Path(a.out)
    base = out.with_name(out.name + '.before-es')
    if not base.exists():
        shutil.copy2(out, base)
    tmp = out.with_suffix('.building')
    shutil.copy2(base, tmp)
    db = sqlite3.connect(tmp)
    db.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);')
    ins = 'INSERT INTO place(name,kind,detail,lat,lon,rank,source) VALUES (?,?,?,?,?,?,?)'
    gdir = Path(a.geonames)

    # GeoNames: features, then admin names
    rows = []
    with open(gdir / 'dump' / 'ES.txt', encoding='utf-8', newline='') as f:
        for r in csv.reader(f, delimiter='\t', quoting=csv.QUOTE_NONE):
            if len(r) < 15 or r[7] not in KINDS or r[7] in ('PPLH', 'PPLW'):
                continue
            rows.append({'id': r[0], 'name': r[1], 'lat': float(r[4]), 'lon': float(r[5]), 'code': r[7], 'a1': r[10],
                         'a2': r[11], 'a3': r[12], 'pop': int(r[14] or 0)})
    adm3 = {(g['a2'], g['a3']): g['name'] for g in rows if g['code'] == 'ADM3'}

    def where(g):
        province, region = ES_PROVINCES.get(G2INE.get(g['a2'], ''), ('', ''))
        parts = [adm3.get((g['a2'], g['a3']), '')] if g['code'][0:3] == 'PPL' or g['code'] not in ('ADM1', 'ADM2', 'ADM3') else []
        parts += [province, ES_REGIONS.get(region, '')]
        if g['code'] == 'ADM2':
            parts = [ES_REGIONS.get(region, '')]
        if g['code'] == 'ADM1':
            parts = ['Spain']
        out, seen = [], set()
        for p in parts:
            if p and key(p) != key(g['name']) and key(p) not in seen:
                seen.add(key(p))
                out.append(p)
        return ', '.join(out)

    for g in rows:
        g['detail'] = where(g)
        kind, rank = KINDS[g['code']]
        if g['code'] in ('PPL', 'PPLA3', 'PPLA4', 'PPLF') and g['pop'] >= 50000:
            kind, rank = 'city', 1
        elif g['code'] in ('PPL', 'PPLA4', 'PPLF') and g['pop'] >= 5000:
            kind, rank = 'town', 2
        g['kind'], g['rank'] = kind, rank
        if g['code'] == 'ADM1':  # GeoNames names some communities in English or Basque: use the address index names
            province, region = ES_PROVINCES.get(G2INE.get(next((x['a2'] for x in rows if x['code'] == 'ADM2' and x['a1'] == g['a1']), ''), ''), ('', ''))
            if region:
                g['name'] = ES_REGIONS[region]
        if g['code'] == 'ADM2':
            province, region = ES_PROVINCES.get(G2INE.get(g['a2'], ''), ('', ''))
            if province:
                g['name'] = province
    # a municipality point is dropped when its seat (a town of the same name) is already there
    seats = {(g['a2'], g['a3'], key(g['name'])) for g in rows if g['code'].startswith('PPL')}
    rows = [g for g in rows if not (g['code'] == 'ADM3' and (g['a2'], g['a3'], key(g['name'])) in seats)]
    by_id = {g['id']: g for g in rows}
    alts = {}
    with open(gdir / 'alt' / 'ES.txt', encoding='utf-8', newline='') as f:
        for r in csv.reader(f, delimiter='\t', quoting=csv.QUOTE_NONE):
            if len(r) < 4 or r[1] not in by_id or r[2] not in LANGS or (len(r) > 7 and r[7] == '1'):
                continue
            g = by_id[r[1]]
            if g['kind'] in ('city', 'town', 'village', 'municipality', 'province', 'region', 'island', 'islands'):
                alts.setdefault(r[1], {})[key(r[3])] = r[3]
    print('geonames', len(rows), 'features', sum(len(v) for v in alts.values()), 'other names', round(time.time() - t0), flush=True)

    # GeoNames populated places on a 0.1° grid, to merge with OSM towns and to say where a help point is
    grid = {}
    for g in rows:
        if g['code'].startswith('PPL') and g['code'] not in ('PPLX', 'PPLQ'):
            grid.setdefault((int(g['lat'] * 10), int(g['lon'] * 10)), []).append(g)

    def nearby(lat, lon, reach=1):
        i, j = int(lat * 10), int(lon * 10)
        for di in range(-reach, reach + 1):
            for dj in range(-reach, reach + 1):
                yield from grid.get((i + di, j + dj), ())

    merged = set()
    n_osm = n_poi = 0
    src = sqlite3.connect(f'file:{a.names}?mode=ro', uri=True)
    for name, cls, kind, region, lat, lon, pop in src.execute('SELECT name,cls,kind,region,lat,lon,pop FROM named'):
        if kind == 'place':
            shown, rank = OSM_KINDS.get(cls, (cls.replace('_', ' '), 3))
            if cls in ('town', 'village') and pop >= 50000:
                shown, rank = 'city', 1
            k = key(name)
            twin = min((g for g in nearby(lat, lon) if key(g['name']) == k and km(lat, lon, g['lat'], g['lon']) < 5),
                       key=lambda g: km(lat, lon, g['lat'], g['lon']), default=None)
            if twin is not None:
                merged.add(twin['id'])
                detail, rank = twin['detail'], min(rank, twin['rank'])
                if twin['kind'] in ('city', 'town') and shown in ('village', 'hamlet', 'locality'):
                    shown = twin['kind']
            else:
                town = min(nearby(lat, lon), key=lambda g: km(lat, lon, g['lat'], g['lon']), default=None)
                detail = ', '.join(p for p in ((town['name'] if town and km(lat, lon, town['lat'], town['lon']) < 8 and key(town['name']) != k else ''),
                                               (town['detail'].split(', ')[-1] if town else '')) if p)
            db.execute(ins, (name, shown, detail or 'Spain', lat, lon, rank, 'OpenStreetMap'))
            n_osm += 1
        else:
            town = min(nearby(lat, lon), key=lambda g: km(lat, lon, g['lat'], g['lon']), default=None)
            detail = ', '.join(p for p in ((town['name'] if town and km(lat, lon, town['lat'], town['lon']) < 15 else ''),
                                           (town['detail'].split(', ')[-1] if town else '')) if p)
            db.execute(ins, (name, cls.replace('_', ' '), detail or 'Spain', lat, lon, 4, 'OpenStreetMap'))
            n_poi += 1
    print('osm', n_osm, 'places', n_poi, 'points', len(merged), 'merged with GeoNames', round(time.time() - t0), flush=True)
    n_geo = 0
    for g in rows:
        if g['id'] in merged:
            continue
        db.execute(ins, (g['name'], g['kind'], g['detail'], g['lat'], g['lon'], g['rank'], 'GeoNames'))
        n_geo += 1
    for gid, names in alts.items():
        g = by_id[gid]
        for k, alt in names.items():
            if k != key(g['name']):
                db.execute(ins, (alt, g['kind'], g['detail'], g['lat'], g['lon'], g['rank'], 'GeoNames'))
                n_geo += 1
    n_pc = 0
    adb = sqlite3.connect(f'file:{a.addresses}?mode=ro', uri=True)
    for zip_code, city, state, lat, lon, n in adb.execute('SELECT zip,city,state,lat,lon,n FROM postcode'):
        db.execute(ins, (zip_code, 'postcode', ', '.join(p for p in (city, ES_REGIONS.get(state, '')) if p), lat, lon, 2,
                         'Overture Maps addresses'))
        n_pc += 1
    total = n_osm + n_poi + n_geo + n_pc
    print('geonames rows', n_geo, 'postcodes', n_pc, 'rebuilding index', round(time.time() - t0), flush=True)
    db.execute("INSERT INTO place_fts(place_fts) VALUES ('rebuild')")
    db.executemany('INSERT OR REPLACE INTO meta VALUES (?,?)', [
        ('spain', str(total)), ('built', time.strftime('%Y-%m-%dT%H:%M:%S')),
        ('source_es', 'GeoNames (CC BY 4.0); OpenStreetMap contributors (ODbL); postcode centres from Overture Maps '
                      'addresses (scne.es, CC BY 4.0)')])
    db.commit()
    db.close()
    tmp.replace(out)
    print('done', total, 'Spanish places', round(time.time() - t0), 's', flush=True)


if __name__ == '__main__':
    main()
