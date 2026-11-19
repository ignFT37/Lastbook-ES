#!/usr/bin/env python3
"""Lastbook ES: cut the Spain map data by autonomous community and zip each cut as a map set.

  python tools/build_es_regions.py --data <data folder> --out <folder for the zips> [--regions ES-RI,ES-ML] [--jobs 3] [--keep]

Setup installs one community's zip in place of the library's map set (docs/SETUP-API.md, "State maps"; engine/setup/install.py
map_member lists what such a zip may hold). Each zip, Lastbook-Map-<code>.zip, holds map-set.json and:

  derived/geo/map.sqlite            all of Spain down to zoom 8 and the coast at every level, plus every feature, help point
                                    inside the community's box (docs/regions-es-proposed.json, box widened by 0.15 degrees)
  derived/geo/places.sqlite         cities and towns of all Spain, plus every place inside the box
  derived/geo/terrain.sqlite        relief tiles of all Spain up to zoom 8, and of the box up to zoom 12
  derived/geo/aerial.sqlite         PNOA photos of all Spain up to zoom 10, and of the box up to zoom 14
  derived/geo/buildings.sqlite      building heights inside the box (3D view)
  derived/geo/water.sqlite          reservoirs, lakes, ponds and dams inside the box
  derived/geo/addresses/ES.sqlite   addresses of the community's provinces (first two digits of the postcode)
  derived/geo/national-status.json, derived/geo/socal-cells.json

The source is a data folder as tools/build_map_es.py, build_places_es.py, build_addresses_es.py, build_terrain.py,
build_water_es.py, fetch_aerial_es.py and build_osm_heights.py leave it. It is only read. Results, with each zip's size and
SHA-256, go to <out>/regions.json, which packaging/release_manifest.py turns into the "maps" of the manifest.
"""
import argparse
import concurrent.futures
import hashlib
import json
import math
import shutil
import sqlite3
import sys
import time
import zipfile
import zlib
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGIONS_JSON = ROOT / 'docs' / 'regions-es-proposed.json'
SPAIN = (-19.0, 27.0, 5.0, 44.5)     # west, south, east, north: the whole map
BASE_FEAT_ZOOM = 8                    # features shown at this zoom or lower stay for all of Spain
COAST_MAX_Z = 10                      # the coast has levels that stop at zoom 7 and 10 (feat_idx.maxz); full detail has 30
BASE_PLACE_RANK = 2                   # cities and towns
BASE_TERRAIN_Z, TERRAIN_MAX_Z = 8, 12
BASE_AERIAL_Z, AERIAL_MAX_Z = 10, 14
STORED = {'derived/geo/terrain.sqlite', 'derived/geo/aerial.sqlite'}      # tiles are compressed already
STATUS = {'state': 'complete', 'stage': 'ready'}
INCLUDES = 'Local streets, buildings, terrain, aerial photos and addresses, plus water and help points'
TITLES = {'ES-AN': 'Andalucía', 'ES-AR': 'Aragón', 'ES-AS': 'Asturias', 'ES-IB': 'Illes Balears', 'ES-CN': 'Canarias',
          'ES-CB': 'Cantabria', 'ES-CL': 'Castilla y León', 'ES-CM': 'Castilla-La Mancha', 'ES-CT': 'Cataluña',
          'ES-VC': 'Comunitat Valenciana', 'ES-EX': 'Extremadura', 'ES-GA': 'Galicia', 'ES-MD': 'Comunidad de Madrid',
          'ES-MC': 'Región de Murcia', 'ES-NC': 'Navarra', 'ES-PV': 'País Vasco', 'ES-RI': 'La Rioja', 'ES-CE': 'Ceuta',
          'ES-ML': 'Melilla'}


def log(code, *a):
    print(time.strftime('%H:%M:%S'), f'[{code}]', *a, flush=True)


def load_regions(only=None):
    out = []
    for r in json.loads(REGIONS_JSON.read_text(encoding='utf-8'))['regions']:
        if only and r['code'] not in only:
            continue
        w, s, e, n = r['bbox']['padded_0_15']
        out.append({'code': r['code'], 'title': TITLES.get(r['code'], r['name']), 'name_en': r['name_en'], 'box': (w, s, e, n),
                    'provinces': sorted(r['provinces_ine'])})
    if only and {x['code'] for x in out} != set(only):
        sys.exit(f'unknown region in {sorted(only)}')
    return out


# ------------------------------------------------------------------------------------------------------------- helpers --
def tile_x(lon, z):
    return min((1 << z) - 1, max(0, int((lon + 180.0) / 360.0 * (1 << z))))


def tile_y(lat, z):
    lat = max(-85.05, min(85.05, lat))
    return min((1 << z) - 1, max(0, int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * (1 << z))))


def tiles(box, z):
    """x0, x1, y0, y1: the tiles at zoom z that a (west, south, east, north) box touches."""
    w, s, e, n = box
    return tile_x(w, z), tile_x(e, z), tile_y(n, z), tile_y(s, z)


def open_cut(dst, src):
    """A new database at dst with the source attached read-only as src."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.unlink(missing_ok=True)
    db = sqlite3.connect(str(dst), uri=True)
    db.execute('PRAGMA journal_mode=OFF')
    db.execute('PRAGMA synchronous=OFF')
    db.execute('PRAGMA cache_size=-262144')
    db.execute('ATTACH DATABASE ? AS src', (Path(src).resolve().as_uri() + '?mode=ro&immutable=1',))
    return db


def copy_schema(db, skip=()):
    """Makes every table of src here (SQLite makes the shadow tables of the virtual ones). Returns the CREATE INDEX statements,
    to run once the rows are in."""
    rows = db.execute("SELECT type, name, sql FROM src.sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'").fetchall()
    virtual = [n for t, n, s in rows if t == 'table' and s.upper().startswith('CREATE VIRTUAL')]
    indexes = []
    for kind, name, sql in rows:
        if kind == 'index':
            indexes.append(sql)
        elif kind == 'table' and name not in skip and not any(name.startswith(v + '_') for v in virtual):
            db.execute(sql)
    return indexes


def finish(db, indexes):
    for sql in indexes:
        db.execute(sql)
    db.commit()
    db.execute('DETACH DATABASE src')
    db.close()


def in_box(box):
    w, s, e, n = box
    return w, e, s, n


# --------------------------------------------------------------------------------------------------------------- cuts --
def cut_map(src, dst, reg):
    w, s, e, n = reg['box']
    db = open_cut(dst, src)
    indexes = copy_schema(db)
    # one query for each reason to keep a feature, so the R-tree answers each with its index instead of one scan for the OR
    db.execute('INSERT INTO feat_idx SELECT * FROM src.feat_idx WHERE maxx >= ? AND minx <= ? AND maxy >= ? AND miny <= ?', (w, e, s, n))
    db.execute('INSERT OR IGNORE INTO feat_idx SELECT * FROM src.feat_idx WHERE minz <= ?', (BASE_FEAT_ZOOM,))
    db.execute('INSERT OR IGNORE INTO feat_idx SELECT * FROM src.feat_idx WHERE maxz <= ?', (COAST_MAX_Z,))     # the coast's coarse levels
    db.execute('INSERT INTO feat SELECT f.* FROM src.feat f WHERE f.id IN (SELECT id FROM main.feat_idx)')
    db.execute('INSERT INTO poi SELECT * FROM src.poi WHERE lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?', in_box(reg['box']))
    db.execute('INSERT INTO meta SELECT * FROM src.meta')
    db.execute("INSERT OR REPLACE INTO meta VALUES ('bounds', ?)", (f'{w},{s},{e},{n}',))
    db.execute("INSERT OR REPLACE INTO meta VALUES ('regions', ?)", (reg['code'],))
    db.execute("INSERT OR REPLACE INTO meta VALUES ('built', ?)", (time.strftime('%Y-%m-%dT%H:%M:%S'),))
    counts = {'features': db.execute('SELECT count(*) FROM feat').fetchone()[0], 'points': db.execute('SELECT count(*) FROM poi').fetchone()[0]}
    finish(db, indexes)
    return counts


def cut_places(src, dst, reg):
    db = open_cut(dst, src)
    indexes = copy_schema(db)
    sw, ss, se, sn = SPAIN
    db.execute('INSERT INTO place SELECT * FROM src.place WHERE (lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?) '
               'OR (rank <= ? AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?)', (*in_box(reg['box']), BASE_PLACE_RANK, sw, se, ss, sn))
    db.execute("INSERT INTO place_fts(place_fts) VALUES ('rebuild')")
    db.execute('INSERT INTO meta SELECT * FROM src.meta')
    counts = {'places': db.execute('SELECT count(*) FROM place').fetchone()[0]}
    finish(db, indexes)
    return counts


def cut_tiles(src, dst, reg, table, first, base_z, max_z, skip=()):
    """Terrain and aerial photos: whole Spain up to base_z, the box up to max_z."""
    db = open_cut(dst, src)
    indexes = copy_schema(db, skip)
    for z in range(first, max_z + 1):
        x0, x1, y0, y1 = tiles(SPAIN if z <= base_z else reg['box'], z)
        db.execute(f'INSERT INTO {table} SELECT * FROM src.{table} WHERE z=? AND x BETWEEN ? AND ? AND y BETWEEN ? AND ?', (z, x0, x1, y0, y1))
        if db.execute("SELECT 1 FROM main.sqlite_master WHERE name='miss'").fetchone():
            db.execute('INSERT INTO miss SELECT * FROM src.miss WHERE z=? AND x BETWEEN ? AND ? AND y BETWEEN ? AND ?', (z, x0, x1, y0, y1))
    db.execute('INSERT INTO meta SELECT * FROM src.meta')
    counts = {table + 's': db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]}
    finish(db, indexes)
    return counts


def cut_buildings(src, dst, reg):
    db = open_cut(dst, src)
    indexes = copy_schema(db)
    x0, x1, y0, y1 = tiles(reg['box'], 14)
    db.execute('INSERT INTO bldg_osm SELECT * FROM src.bldg_osm WHERE x BETWEEN ? AND ? AND y BETWEEN ? AND ?', (x0, x1, y0, y1))
    db.execute('INSERT INTO meta SELECT * FROM src.meta')
    heights = 0
    for (blob,) in db.execute('SELECT data FROM bldg_osm'):     # records [npts, height, min height, part, x0, y0, npts-1 deltas of 2]
        a = array('i')
        a.frombytes(zlib.decompress(blob))
        i = 0
        while i < len(a):
            i += 4 + 2 * a[i]
            heights += 1
    db.execute("INSERT OR REPLACE INTO meta VALUES ('osm_heights', ?)", (str(heights),))
    finish(db, indexes)
    return {'building_heights': heights}


def cut_water(src, dst, reg):
    """Spain's rows only (origin 'es'): the NHD and dam rows of the US stay in the source."""
    db = open_cut(dst, src)
    indexes = copy_schema(db)
    db.execute("INSERT INTO resource SELECT * FROM src.resource WHERE origin='es' AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?", in_box(reg['box']))
    db.execute('INSERT INTO spatial SELECT * FROM src.spatial WHERE id IN (SELECT id FROM main.resource)')
    db.execute('INSERT INTO water_fts(rowid, name, other) SELECT rowid, name, other FROM src.water_fts WHERE rowid IN (SELECT id FROM main.resource)')
    kinds = dict(db.execute('SELECT kind, count(*) FROM resource GROUP BY kind').fetchall())
    meta = json.loads(db.execute("SELECT value FROM src.meta WHERE key='es'").fetchone()[0])
    meta.update(kinds, built=time.strftime('%Y-%m-%dT%H:%M:%S'))
    meta.update({k: kinds.get(k, 0) for k in ('pond', 'reservoir', 'lake', 'dam')})
    db.execute("INSERT INTO meta VALUES ('es', ?)", (json.dumps(meta),))
    finish(db, indexes)
    return {'water_points': sum(kinds.values())}


def cut_addresses(src, dst, reg):
    db = open_cut(dst, src)
    indexes = copy_schema(db)
    marks = ','.join('?' * len(reg['provinces']))
    db.execute(f'INSERT INTO addr SELECT * FROM src.addr WHERE substr(zip, 1, 2) IN ({marks})', reg['provinces'])
    db.execute(f'INSERT INTO postcode SELECT * FROM src.postcode WHERE substr(zip, 1, 2) IN ({marks})', reg['provinces'])
    db.execute('INSERT INTO meta SELECT * FROM src.meta')
    n = db.execute('SELECT count(*) FROM addr').fetchone()[0]
    w, s, e, nn = reg['box']
    db.execute("INSERT OR REPLACE INTO meta VALUES ('count', ?)", (str(n),))
    db.execute("INSERT OR REPLACE INTO meta VALUES ('bbox', ?)", (f'{w},{s},{e},{nn}',))
    db.execute("INSERT OR REPLACE INTO meta VALUES ('built', ?)", (time.strftime('%Y-%m-%dT%H:%M:%S'),))
    finish(db, indexes)
    return {'addresses': n}


# ----------------------------------------------------------------------------------------------------------------- zip --
def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while b := f.read(1 << 24):
            h.update(b)
    return h.hexdigest()


def make_zip(folder, out):
    rels = sorted(p.relative_to(folder).as_posix() for p in Path(folder).rglob('*') if p.is_file())
    tmp = Path(str(out) + '.partial')
    with zipfile.ZipFile(tmp, 'w', allowZip64=True) as z:
        for rel in rels:
            info = zipfile.ZipInfo(rel, date_time=(2026, 11, 19, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED if rel in STORED else zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(Path(folder) / rel, 'rb') as src, z.open(info, 'w', force_zip64=True) as dst:
                while b := src.read(1 << 24):
                    dst.write(b)
    with zipfile.ZipFile(tmp) as z:
        if z.testzip() is not None:
            sys.exit(f'{out}: the zip is damaged')
    tmp.replace(out)


def build_region(args):
    data, out, reg, keep = args
    code, t0 = reg['code'], time.time()
    geo = Path(data) / 'derived' / 'geo'
    work = Path(out) / 'build' / code
    shutil.rmtree(work, ignore_errors=True)
    g = work / 'derived' / 'geo'
    counts = {}
    steps = [('map.sqlite', lambda: cut_map(geo / 'map.sqlite', g / 'map.sqlite', reg)),
             ('places.sqlite', lambda: cut_places(geo / 'places.sqlite', g / 'places.sqlite', reg)),
             ('terrain.sqlite', lambda: cut_tiles(geo / 'terrain.sqlite', g / 'terrain.sqlite', reg, 'tile', 4, BASE_TERRAIN_Z, TERRAIN_MAX_Z, skip=('done',))),
             ('aerial.sqlite', lambda: cut_tiles(geo / 'aerial.sqlite', g / 'aerial.sqlite', reg, 'tile', 6, BASE_AERIAL_Z, AERIAL_MAX_Z)),
             ('buildings.sqlite', lambda: cut_buildings(geo / 'buildings.sqlite', g / 'buildings.sqlite', reg)),
             ('water.sqlite', lambda: cut_water(geo / 'water.sqlite', g / 'water.sqlite', reg)),
             ('addresses/ES.sqlite', lambda: cut_addresses(geo / 'addresses' / 'ES.sqlite', g / 'addresses' / 'ES.sqlite', reg))]
    for name, step in steps:
        t = time.time()
        got = step()
        counts.update(got)
        log(code, f'{name}: {(g / name).stat().st_size / 1e6:,.0f} MB in {time.time() - t:.0f} s {got}')
    (g / 'national-status.json').write_text(json.dumps({**STATUS, 'features': counts['features']}), encoding='utf-8')
    (g / 'socal-cells.json').write_text('[]', encoding='utf-8')
    files = {p.relative_to(work).as_posix(): p.stat().st_size for p in sorted(work.rglob('*')) if p.is_file()}
    (work / 'map-set.json').write_text(json.dumps({'name': code, 'files': files, 'bytes': sum(files.values()),
                                                   'seconds': round(time.time() - t0)}), encoding='utf-8')
    installed = sum(p.stat().st_size for p in work.rglob('*') if p.is_file())
    zip_path = Path(out) / f'Lastbook-Map-{code}.zip'
    make_zip(work, zip_path)
    digest = sha256(zip_path)
    if not keep:
        shutil.rmtree(work, ignore_errors=True)
    size = zip_path.stat().st_size
    log(code, f'zip {size / 1e6:,.0f} MB (installed {installed / 1e6:,.0f} MB) sha256 {digest[:16]} in {time.time() - t0:.0f} s')
    if size >= 2 * 1024 ** 3:
        log(code, 'WARNING: over 2 GiB, GitHub Releases would refuse it')
    return {'id': code, 'title': reg['title'], 'name_en': reg['name_en'], 'kind': 'community', 'includes': INCLUDES,
            'box': list(reg['box']), 'provinces': reg['provinces'], 'installed_bytes': installed,
            'files': [{'name': zip_path.name, 'size': size, 'sha256': digest, 'kind': 'zip'}], 'counts': counts}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--data', required=True, help='the data folder holding derived/geo/')
    ap.add_argument('--out', required=True, help='where the zips and regions.json go')
    ap.add_argument('--regions', help='comma-separated codes, e.g. ES-RI,ES-ML (default: all 19)')
    ap.add_argument('--jobs', type=int, default=3, help='regions built at once')
    ap.add_argument('--keep', action='store_true', help='keep each region\'s unzipped folder (out/build/<code>)')
    a = ap.parse_args()
    only = {x.strip() for x in a.regions.split(',')} if a.regions else None
    regions = load_regions(only)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    results_path = out / 'regions.json'
    done = {r['id']: r for r in json.loads(results_path.read_text(encoding='utf-8'))['regions']} if results_path.is_file() else {}
    t0 = time.time()
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, a.jobs)) as pool:
        for res in pool.map(build_region, [(a.data, str(out), r, a.keep) for r in regions]):
            done[res['id']] = res
            results_path.write_text(json.dumps({'built': time.strftime('%Y-%m-%d %H:%M:%S'), 'regions': sorted(done.values(), key=lambda r: r['id'])},
                                               indent=1, ensure_ascii=False), encoding='utf-8')
    print(f'{len(regions)} regions in {time.time() - t0:.0f} s: {results_path}')


if __name__ == '__main__':
    main()
