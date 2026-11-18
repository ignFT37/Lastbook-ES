#!/usr/bin/env python3
"""Detailed offline map of Spain: derived/geo/map.sqlite from Geofabrik's "free" GeoPackages (OpenStreetMap, ODbL), one
per autonomous community plus the Canary Islands, with the coast from OSM land polygons.

  buildenv/python tools/build_map_es.py --out <data>/derived/geo/map.sqlite --gpkg-dir <sources>/osm
        --land <sources>/osm/land-polygons-split-4326.zip --names <sources>/build/osm-names.sqlite [--only madrid,ceuta]

--gpkg-dir holds the downloaded <region>-latest-free.gpkg.zip files (only read); each is extracted once to
<gpkg-dir>/gpkg/<region>/. Same tables and rules as tools/build_map.py (feat, feat_idx, poi, meta), changed for Spain:
- several extracts: a feature on the border between two communities is in both, so each layer keeps an OSM id once;
- the "free" format has no coastline, so each area (the peninsula with the Balearic Islands, Ceuta and Melilla; the
  Canary Islands) gets a sea rectangle with OSM land polygons (osmdata.openstreetmap.de, ODbL) drawn over it as 'island'
  rings, in three levels of detail told apart by feat_idx.maxz (server.py reads it);
- national_capital places are stored as cities; fuel stations come from the traffic layers, where Geofabrik puts them;
  alpine_hut (mountain refuges) is kept.
Also writes socal-cells.json next to --out (server.py's cache of the cells the detailed map covers; a stale one must not
outlive a rebuilt map) and, with --names, the named places and points for tools/build_places_es.py.
"""
import argparse, json, math, sqlite3, struct, sys, time, zipfile
from array import array
from pathlib import Path

Q = 1e6
REGIONS = {  # Geofabrik extract -> (community code, name)
    'andalucia': ('AN', 'Andalucía'), 'aragon': ('AR', 'Aragón'), 'asturias': ('AS', 'Asturias'),
    'islas-baleares': ('IB', 'Illes Balears'), 'canary-islands': ('CN', 'Canarias'), 'cantabria': ('CB', 'Cantabria'),
    'castilla-y-leon': ('CL', 'Castilla y León'), 'castilla-la-mancha': ('CM', 'Castilla-La Mancha'),
    'cataluna': ('CT', 'Cataluña'), 'valencia': ('VC', 'Comunitat Valenciana'), 'extremadura': ('EX', 'Extremadura'),
    'galicia': ('GA', 'Galicia'), 'madrid': ('MD', 'Comunidad de Madrid'), 'murcia': ('MC', 'Región de Murcia'),
    'navarra': ('NC', 'Navarra'), 'pais-vasco': ('PV', 'País Vasco'), 'la-rioja': ('RI', 'La Rioja'),
    'ceuta': ('CE', 'Ceuta'), 'melilla': ('ML', 'Melilla'),
}
# Sea rectangles (W, S, E, N): every land polygon touching one is drawn over it, Portugal, France and Morocco included.
SEA = [(-10.0, 34.8, 5.0, 44.3), (-18.6, 27.3, -13.0, 29.7)]
# Coast levels of detail: (tolerance in degrees, minz, maxz)
COAST_LOD = [(0.004, 5, 7), (0.0004, 8, 10), (0.00001, 11, 30)]

ROAD_Z = {'motorway': 5, 'trunk': 6, 'motorway_link': 10, 'trunk_link': 10, 'primary': 8, 'primary_link': 12,
          'secondary': 10, 'secondary_link': 13, 'tertiary': 11, 'tertiary_link': 13, 'residential': 13,
          'unclassified': 13, 'living_street': 14, 'pedestrian': 14, 'busway': 14, 'service': 15,
          'track': 13, 'track_grade1': 13, 'track_grade2': 13, 'track_grade3': 13, 'track_grade4': 13, 'track_grade5': 13,
          'footway': 15, 'path': 14, 'cycleway': 14, 'steps': 16, 'bridleway': 14, 'unknown': 15}
LANDUSE_KEEP = {'park': 'park', 'forest': 'green', 'grass': 'green', 'meadow': 'green', 'scrub': 'green', 'heath': 'green',
                'recreation_ground': 'park', 'cemetery': 'park', 'allotments': 'farm', 'farmland': 'farm',
                'orchard': 'farm', 'vineyard': 'farm', 'farmyard': 'farm', 'residential': 'res', 'commercial': 'com',
                'retail': 'com', 'industrial': 'ind', 'military': 'ind', 'quarry': 'ind', 'landfill': 'ind'}
PLACE_Z = {'county': 7, 'city': 6, 'town': 9, 'suburb': 12, 'village': 11, 'hamlet': 14, 'locality': 14, 'island': 11,
           'farm': 15, 'region': 5, 'national_capital': 4}
POI_KEEP = {'hospital', 'clinic', 'doctors', 'pharmacy', 'chemist', 'dentist', 'veterinary', 'fire_station', 'police',
            'drinking_water', 'water_tower', 'water_works', 'fountain', 'toilet', 'shelter', 'community_centre',
            'town_hall', 'school', 'library', 'supermarket', 'convenience', 'doityourself', 'hardware', 'garden_centre',
            'post_office', 'camp_site', 'wastewater_plant', 'comms_tower', 'spring', 'park', 'courthouse', 'embassy',
            'nursing_home', 'kindergarten', 'college', 'university', 'bicycle_shop', 'car_repair', 'fuel', 'beach',
            'alpine_hut'}
POI_TABLES = {'gis_osm_pois_free': None, 'gis_osm_pois_a_free': None, 'gis_osm_natural_free': {'spring'},
              'gis_osm_natural_a_free': None, 'gis_osm_traffic_free': {'fuel'}, 'gis_osm_traffic_a_free': {'fuel'}}


def wkb_parts(b, o):
    """Return (list of parts as flat [x,y,...] lists, offset). Polygons yield their rings as parts."""
    le = b[o] == 1
    e = '<' if le else '>'
    t = struct.unpack_from(e + 'I', b, o + 1)[0] % 1000
    o += 5
    if t == 1:
        x, y = struct.unpack_from(e + 'dd', b, o)
        return [[x, y]], o + 16
    if t == 2:
        n = struct.unpack_from(e + 'I', b, o)[0]
        return [list(struct.unpack_from(e + '%dd' % (2 * n), b, o + 4))], o + 4 + 16 * n
    if t == 3:
        nr = struct.unpack_from(e + 'I', b, o)[0]
        o += 4
        parts = []
        for _ in range(nr):
            n = struct.unpack_from(e + 'I', b, o)[0]
            parts.append(list(struct.unpack_from(e + '%dd' % (2 * n), b, o + 4)))
            o += 4 + 16 * n
        return parts, o
    if t in (4, 5, 6, 7):
        n = struct.unpack_from(e + 'I', b, o)[0]
        o += 4
        parts = []
        for _ in range(n):
            p, o = wkb_parts(b, o)
            parts.extend(p)
        return parts, o
    raise ValueError('geometry type %d' % t)


def gpkg_parts(g):
    flags = g[3]
    env = (flags >> 1) & 7
    off = 8 + {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[env]
    return wkb_parts(g, off)[0]


def simplify(flat, tol):
    """Drop points closer than tol (degrees) to the previous kept point; keeps endpoints."""
    if len(flat) <= 4 or tol <= 0:
        return flat
    out = [flat[0], flat[1]]
    px, py = flat[0], flat[1]
    for i in range(2, len(flat) - 2, 2):
        x, y = flat[i], flat[i + 1]
        if abs(x - px) > tol or abs(y - py) > tol:
            out += [x, y]
            px, py = x, y
    out += [flat[-2], flat[-1]]
    return out


def pack(parts):
    """int32 array: nparts, then for each part npts followed by quantized x,y deltas."""
    a = array('i', [len(parts)])
    for p in parts:
        n = len(p) // 2
        a.append(n)
        lx = ly = 0
        for i in range(n):
            x, y = int(round(p[2 * i] * Q)), int(round(p[2 * i + 1] * Q))
            a.append(x - lx); a.append(y - ly)
            lx, ly = x, y
    return a.tobytes()


def bbox(parts):
    xs = [v for p in parts for v in p[0::2]]
    ys = [v for p in parts for v in p[1::2]]
    return min(xs), max(xs), min(ys), max(ys)


def area_z(minx, maxx, miny, maxy, base):
    """Small polygons only appear when zoomed in."""
    size = max(maxx - minx, maxy - miny)
    if size <= 0:
        return 17
    z = int(math.log2(360 / size)) - 4  # visible when ~1/16 of the screen-ish
    return max(base, min(16, z))


def extract(zpath, root):
    """<region>.gpkg from its Geofabrik zip, extracted once (the zip is only read)."""
    region = zpath.name.replace('-latest-free.gpkg.zip', '')
    out = root / region
    done = list(out.glob('*.gpkg'))
    if done and (out / '.ok').exists():
        return region, done[0]
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zpath) as z:
        if z.testzip() is not None:
            raise SystemExit(f'{zpath.name}: damaged zip, download it again')
        z.extractall(out)
    (out / '.ok').write_text('ok')
    return region, next(out.glob('*.gpkg'))


def land_shapes(zpath, work):
    """Land polygons touching a sea rectangle, as lists of flat rings (OSM land polygons, split, EPSG:4326)."""
    import shapefile
    shp = next(work.rglob('land_polygons.shp'), None)
    if shp is None:
        with zipfile.ZipFile(zpath) as z:
            z.extractall(work)
        shp = next(work.rglob('land_polygons.shp'))
    out = []
    with shapefile.Reader(str(shp)) as r:
        for box in SEA:
            try:
                shapes = r.iterShapes(bbox=[box[0], box[1], box[2], box[3]])
            except TypeError:
                shapes = r.iterShapes()
            for s in shapes:
                x0, y0, x1, y1 = s.bbox
                if x1 < box[0] or x0 > box[2] or y1 < box[1] or y0 > box[3]:
                    continue
                idx = list(s.parts) + [len(s.points)]
                rings = []
                for a, b in zip(idx[:-1], idx[1:]):
                    rings.append([v for pt in s.points[a:b] for v in pt])
                out.append(((x0, x1, y0, y1), rings))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--gpkg-dir', required=True)
    ap.add_argument('--land', help='land-polygons-split-4326.zip; without it the build has no coast or sea')
    ap.add_argument('--names', help='side file of named places and points for tools/build_places_es.py')
    ap.add_argument('--only', help='comma-separated Geofabrik extract names, e.g. madrid')
    ap.add_argument('--as-of', default='2026-11-17', help='date of the Geofabrik extracts')
    a = ap.parse_args()
    t0 = time.time()
    gdir = Path(a.gpkg_dir)
    zips = sorted(gdir.glob('*-latest-free.gpkg.zip'))
    if a.only:
        wanted = set(a.only.split(','))
        zips = [z for z in zips if z.name.replace('-latest-free.gpkg.zip', '') in wanted]
    sources = []
    for z in zips:
        region, g = extract(z, gdir / 'gpkg')
        if region not in REGIONS:
            print('skipping unknown extract', region)
            continue
        sources.append((region, sqlite3.connect(f'file:{g}?mode=ro', uri=True)))
        print('extract', region, round(time.time() - t0), flush=True)
    if not sources:
        raise SystemExit('no extracts found in ' + str(gdir))
    out = Path(a.out)
    tmp = out.with_suffix('.building')
    tmp.unlink(missing_ok=True)
    dst = sqlite3.connect(tmp)
    dst.executescript('''PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-400000;
      CREATE TABLE feat(id INTEGER PRIMARY KEY, layer TEXT, cls TEXT, name TEXT, minz INTEGER, geom BLOB);
      CREATE VIRTUAL TABLE feat_idx USING rtree(id, minx, maxx, miny, maxy, minz, maxz);
      CREATE TABLE poi(id INTEGER PRIMARY KEY, cls TEXT, name TEXT, lon REAL, lat REAL);
      CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);''')
    names = None
    if a.names:
        Path(a.names).parent.mkdir(parents=True, exist_ok=True)
        Path(a.names).unlink(missing_ok=True)
        names = sqlite3.connect(a.names)
        names.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;'
                            'CREATE TABLE named(name TEXT, cls TEXT, kind TEXT, region TEXT, lat REAL, lon REAL, pop INTEGER);')
    fid = [0]

    def add(layer, cls, name, minz, parts, maxz=30):
        if not parts:
            return
        fid[0] += 1
        bx = bbox(parts)
        dst.execute('INSERT INTO feat VALUES (?,?,?,?,?,?)', (fid[0], layer, cls, name, minz, pack(parts)))
        dst.execute('INSERT INTO feat_idx VALUES (?,?,?,?,?,?,?)', (fid[0], *bx, minz, maxz))

    seen = {}

    def each(table, cols='fclass,name'):
        """(parts, columns, region) for every feature of a layer, across extracts, each OSM id once."""
        keep = seen.setdefault(table, set())
        for region, src in sources:
            try:
                cur = src.execute(f'SELECT geom,osm_id,{cols} FROM {table}')
            except sqlite3.OperationalError:
                continue
            for row in cur:
                if row[0] is None:
                    continue
                try:
                    oid = int(row[1])
                except (TypeError, ValueError):
                    oid = None
                if oid is not None:
                    if oid in keep:
                        continue
                    keep.add(oid)
                try:
                    yield gpkg_parts(row[0]), row[2:], region
                except Exception:
                    continue
        seen[table] = set()  # free the ids once a layer is done

    # Sea and land: rectangles of sea with the land polygons over them (both or neither)
    if a.land and Path(a.land).is_file():
        for w, s, e, n in SEA:
            add('ocean', 'ocean', None, 5, [[w, s, e, s, e, n, w, n, w, s]])
        shapes = land_shapes(Path(a.land), Path(a.land).parent / 'land-polygons')
        for (x0, x1, y0, y1), rings in shapes:
            for tol, minz, maxz in COAST_LOD:
                if max(x1 - x0, y1 - y0) < tol * 4:
                    continue  # an islet smaller than this level's detail
                parts = [r for r in (simplify(r, tol) for r in rings) if len(r) >= 8]
                add('island', 'island', None, minz, parts, maxz)
        print('coast', len(shapes), 'land polygons', fid[0], round(time.time() - t0), flush=True)
    else:
        print('no land polygons: no coast or sea in this build', flush=True)
    # Water bodies
    for parts, (cls, name), _ in each('gis_osm_water_a_free'):
        layer = 'wet' if cls.startswith('wetland') else 'water'
        b = bbox(parts)
        add(layer, cls, name, area_z(*b, 7), [simplify(p, 0.00001) for p in parts])
    print('water', fid[0], round(time.time() - t0), flush=True)
    for parts, (cls, name), _ in each('gis_osm_natural_a_free'):
        if cls == 'beach':
            add('beach', cls, name, area_z(*bbox(parts), 10), parts)
    for parts, (cls, name), _ in each('gis_osm_landuse_a_free'):
        k = LANDUSE_KEEP.get(cls)
        if k:
            add('land', k, name, area_z(*bbox(parts), 9), [simplify(p, 0.00001) for p in parts])
    for parts, (cls,), _ in each('gis_osm_protected_areas_a_free', 'fclass'):
        add('land', 'park', None, area_z(*bbox(parts), 8), [simplify(p, 0.00002) for p in parts])
    print('land', fid[0], round(time.time() - t0), flush=True)
    dst.commit()
    for parts, (cls, name), _ in each('gis_osm_waterways_free'):
        add('waterway', cls, name, 10 if cls == 'river' else 13, parts)
    for parts, (cls, name), _ in each('gis_osm_railways_free'):
        add('rail', cls, name, 10 if cls == 'rail' else 13, parts)
    print('lines', fid[0], round(time.time() - t0), flush=True)
    for parts, (cls, name, ref), _ in each('gis_osm_roads_free', 'fclass,name,ref'):
        add('road', cls, name or ref, ROAD_Z.get(cls, 15), parts)
    print('roads', fid[0], round(time.time() - t0), flush=True)
    dst.commit()
    for parts, (cls, name), _ in each('gis_osm_buildings_a_free'):
        add('bldg', cls or '', None, 15, [simplify(parts[0], 0.000002)])
    print('buildings', fid[0], round(time.time() - t0), flush=True)
    dst.commit()
    for table in ('gis_osm_places_free', 'gis_osm_places_a_free'):
        for parts, (cls, pop, name), region in each(table, 'fclass,population,name'):
            if not name:
                continue
            try:
                pop = int(pop or 0)
            except (TypeError, ValueError):
                pop = 0
            z = PLACE_Z.get(cls, 14)
            if cls in ('city', 'town', 'national_capital') and pop > 300000:
                z = min(z, 5)
            if cls == 'national_capital':
                cls = 'city'
            minx, maxx, miny, maxy = bbox(parts)
            x, y = (minx + maxx) / 2, (miny + maxy) / 2
            if table == 'gis_osm_places_free':
                add('place', cls, name, z, [[x, y]])
            if names is not None:
                names.execute('INSERT INTO named VALUES (?,?,?,?,?,?,?)', (name, cls, 'place', region, y, x, pop))
    pid = 0
    for table, only in POI_TABLES.items():
        for parts, (cls, name), region in each(table):
            if cls not in POI_KEEP or (only and cls not in only):
                continue
            minx, maxx, miny, maxy = bbox(parts)
            x, y = (minx + maxx) / 2, (miny + maxy) / 2
            pid += 1
            dst.execute('INSERT INTO poi VALUES (?,?,?,?,?)', (pid, cls, name, x, y))
            if names is not None and name:
                names.execute('INSERT INTO named VALUES (?,?,?,?,?,?,?)', (name, cls, 'poi', region, y, x, 0))
    dst.execute('CREATE INDEX poi_cls ON poi(cls, lon, lat)')
    xs = dst.execute('SELECT min(lon),min(lat),max(lon),max(lat) FROM poi').fetchone()
    dst.executemany('INSERT INTO meta VALUES (?,?)', [
        ('source', 'OpenStreetMap contributors (ODbL 1.0), via Geofabrik <region>-latest-free.gpkg.zip: '
                   + ', '.join(r for r, _ in sources) + '; coast from OSM land polygons (osmdata.openstreetmap.de)'),
        ('data_as_of', a.as_of),
        ('bounds', ','.join(f'{v:.2f}' for v in xs) if xs[0] is not None else ''),
        ('regions', ','.join(REGIONS[r][0] for r, _ in sources)),
        ('built', time.strftime('%Y-%m-%dT%H:%M:%S')),
    ])
    dst.commit()
    print('pois', pid, round(time.time() - t0), flush=True)
    cell = 0.05  # server.py SOCAL_CELL
    cells = sorted(r[0] for r in dst.execute(
        'SELECT DISTINCT CAST((minx+180)/? AS INT)*100000+CAST((miny+90)/? AS INT) FROM feat_idx WHERE maxx-minx<?',
        (cell, cell, cell)))
    dst.close()
    if names is not None:
        names.execute('CREATE INDEX named_name ON named(name)')
        names.commit()
        names.close()
    tmp.replace(out)
    (out.parent / 'socal-cells.json').write_text(json.dumps(cells))
    print('done features', fid[0], 'pois', pid, 'cells', len(cells), round(time.time() - t0), 's', flush=True)


if __name__ == '__main__':
    main()
