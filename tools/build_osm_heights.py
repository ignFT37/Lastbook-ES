"""Adds OpenStreetMap buildings that have a measured height (or storey count) to derived/geo/buildings.sqlite,
table bldg_osm, for the 3D view. Microsoft's satellite height estimates are poor for tall buildings
(Midtown Manhattan tops out near 37 m); OSM has surveyed heights and building parts in most big cities.

  derived/buildenv/bin/python tools/build_osm_heights.py [library-data/geography/openstreetmap/us-latest.osm.pbf]

Reads closed ways tagged building=* or building:part=* with height / building:levels (read only).
Multipolygon-relation buildings are skipped. Same z14 bucketing as tools/build_buildings.py; records are
[npts, height_dm, min_height_dm, is_part, X0, Y0, dX1, dY1, ...] in 2^-30 world units.
"""
import math, os, re, sqlite3, sys, time, zlib
from array import array
from pathlib import Path

import osmium

HERE = Path(__file__).resolve().parent.parent
# Lastbook ES: several extracts may be given (Spain's peninsula and the Canary Islands come apart); each is read in turn
SRCS = [Path(a) for a in sys.argv[1:]] or [HERE / 'library-data/geography/openstreetmap/us-latest.osm.pbf']
OUT = Path(os.environ.get('BUILDINGS_OUT', HERE / 'derived/geo/buildings.sqlite'))
WORLD = 2 ** 30
STOREY = 3.2
NUM = re.compile(r'-?\d+(?:\.\d+)?')


def metres(v):
    """'45', '45 m', '150 ft', "150'", '12;14' -> metres, or None."""
    if not v:
        return None
    m = NUM.search(v.replace(',', '.'))
    if not m:
        return None
    x = float(m[0])
    if "'" in v or 'ft' in v or 'feet' in v:
        x *= 0.3048
    return x if 0 <= x < 1000 else None


def main():
    t0 = time.time()
    tiles, n, seen = {}, 0, 0
    for w in (w for src in SRCS for w in (osmium.FileProcessor(str(src), osmium.osm.NODE | osmium.osm.WAY)
                                          .with_locations('flex_mem')
                                          .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
                                          .with_filter(osmium.filter.KeyFilter('height', 'building:levels')))):  # cheap C++ pre-filter
        tags = w.tags
        if 'building' not in tags and 'building:part' not in tags:
            continue
        seen += 1
        h = metres(tags.get('height'))
        lv = metres(tags.get('building:levels'))
        if h is None and lv is None:
            continue
        if lv is not None and lv > 130:
            lv = None  # typo or vandalism: no US building has more storeys
        if h is None and lv is None:
            continue
        if h is None:
            h = lv * STOREY + (1.5 if lv else 0)
        if h < 2 or h > 560:  # taller than any US building (One WTC is 541 m)
            continue
        mh = metres(tags.get('min_height'))
        if mh is None:
            ml = metres(tags.get('building:min_level'))
            mh = ml * STOREY if ml is not None else 0
        if mh >= h:
            mh = 0
        nodes = w.nodes
        if len(nodes) < 4 or nodes[0].ref != nodes[-1].ref:
            continue
        pts = []
        try:
            for i in range(len(nodes) - 1):
                nd = nodes[i]
                lon, lat = nd.lon, nd.lat
                s = math.sin(math.radians(max(-85, min(85, lat))))
                pts.append((int((lon + 180) / 360 * WORLD), int((0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * WORLD)))
        except osmium.InvalidLocationError:
            continue
        cx = sum(p[0] for p in pts) // len(pts); cy = sum(p[1] for p in pts) // len(pts)
        buf = tiles.get((cx >> 16, cy >> 16))
        if buf is None:
            buf = tiles[(cx >> 16, cy >> 16)] = array('i')
        buf.extend((len(pts), int(round(h * 10)), int(round(mh * 10)), 1 if 'building:part' in tags else 0, pts[0][0], pts[0][1]))
        lx, ly = pts[0]
        for x, y in pts[1:]:
            buf.extend((x - lx, y - ly)); lx, ly = x, y
        n += 1
        if n % 200000 == 0:
            print(f'{n:,} buildings with heights, {time.time() - t0:.0f}s', flush=True)
    db = sqlite3.connect(OUT)
    # a file made only from OSM (Lastbook ES) still needs the tables server.py reads, even if empty
    db.executescript('CREATE TABLE IF NOT EXISTS bldg(x INT, y INT, part INT, n INT, data BLOB, PRIMARY KEY(x,y,part)) WITHOUT ROWID;'
                     'CREATE TABLE IF NOT EXISTS bldg_socal(x INT, y INT, part INT, n INT, data BLOB, PRIMARY KEY(x,y,part)) WITHOUT ROWID;'
                     'CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);')
    db.executescript('DROP TABLE IF EXISTS bldg_osm; CREATE TABLE bldg_osm(x INT, y INT, n INT, data BLOB, PRIMARY KEY(x,y)) WITHOUT ROWID;')
    db.executemany('INSERT INTO bldg_osm VALUES (?,?,?,?)', ((x, y, 0, zlib.compress(b.tobytes(), 6)) for (x, y), b in tiles.items()))
    db.execute("INSERT OR REPLACE INTO meta VALUES ('osm_heights', ?)", (str(n),))
    db.commit()
    print(f'done: {n:,} OSM buildings with heights in {time.time() - t0:.0f}s', flush=True)


if __name__ == '__main__':
    main()
