#!/usr/bin/env python3
"""Reservoirs, lakes, ponds and dams of Spain for the map's water layers, added to derived/geo/water.sqlite.

  buildenv/python tools/build_water_es.py --data <data folder> --gpkg-dir <sources>/osm [--miteco <sources>/miteco]

The water layers ("Reservoirs & named lakes", "Unnamed ponds", "Dams") read the resource table of water.sqlite; the
packs ship it empty outside their own data. This adds Spain's rows, marked origin 'es', and takes out the previous
ones first, so it can be run again. The first run keeps the original file as water.sqlite.before-es.
- Reservoirs, lakes and ponds: OpenStreetMap water areas (ODbL) from Geofabrik's extracts (already extracted by
  tools/build_map_es.py): reservoirs named or of 2 ha or more, named lakes of 0.5 ha or more, the rest from 0.4 ha
  (about one acre, as the US layer) as ponds. River areas are left out by name and by shape.
- Dams: the MITECO inventory (Inventario de Presas y Embalses; "© Ministerio para la Transición Ecológica y el Reto
  Demográfico", free reuse with that credit) when its shapefile is in --miteco, with owner, river, use, height and
  capacity. Otherwise, and for dams the inventory lacks, named OpenStreetMap dams.
  The inventory's egis_presa_geoetrs89.zip (Nov 2026) is in ETRS89 / UTM zone 30N, extended to the Canaries, despite its
  name, and in UTF-8: both are read from its .prj and .cpg. Only dams that stand: not projects, abandoned projects or dams
  under another reservoir; one with no phase given only where OpenStreetMap also has a dam.
Each row also says the community and the nearest town (from places.sqlite).
"""
import argparse, json, math, re, shutil, sqlite3, sys, time, zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from tools.build_map_es import REGIONS, gpkg_parts  # noqa: E402

RIVER = re.compile(r'^(r[ií]o|riu|rio|arroyo|regato|canal|acequia|r[ií]a|barranco|rambla|riera|ibaia|erreka|rego)\b', re.I)
LAKE_WORDS = re.compile(r'(lag[oua]|laguna|estany|ib[oó]n|lagoa|charca|balsa|pantano|embalse|estanque|alberca|bassa)', re.I)


def ring_area_centroid(flat):
    """(area m², lon, lat) of one ring, on a local flat projection."""
    n = len(flat) // 2
    if n < 3:
        return 0.0, flat[0], flat[1]
    lat0 = flat[1]
    kx, ky = 111320 * math.cos(math.radians(lat0)), 110540
    a = cx = cy = 0.0
    for i in range(n):
        x0, y0 = flat[2 * i] * kx, flat[2 * i + 1] * ky
        x1, y1 = flat[2 * ((i + 1) % n)] * kx, flat[2 * ((i + 1) % n) + 1] * ky
        c = x0 * y1 - x1 * y0
        a += c
        cx += (x0 + x1) * c
        cy += (y0 + y1) * c
    if abs(a) < 1e-9:
        return 0.0, flat[0], flat[1]
    return abs(a) / 2, cx / (3 * a) / kx, cy / (3 * a) / ky


def perimeter(flat):
    lat0 = flat[1]
    kx, ky = 111320 * math.cos(math.radians(lat0)), 110540
    return sum(math.hypot((flat[i + 2] - flat[i]) * kx, (flat[i + 3] - flat[i + 1]) * ky) for i in range(0, len(flat) - 2, 2))


def inside(flat, x, y):
    n, c = len(flat) // 2, False
    for i in range(n):
        x0, y0, x1, y1 = flat[2 * i], flat[2 * i + 1], flat[2 * ((i + 1) % n)], flat[2 * ((i + 1) % n) + 1]
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / ((y1 - y0) or 1e-12) + x0:
            c = not c
    return c


def pin(flat):
    """A point for the pin: the centroid, or, for a U-shaped lake, the shore point nearest to it."""
    area, x, y = ring_area_centroid(flat)
    if not inside(flat, x, y):
        i = min(range(len(flat) // 2), key=lambda k: (flat[2 * k] - x) ** 2 + (flat[2 * k + 1] - y) ** 2)
        x, y = flat[2 * i], flat[2 * i + 1]
    return area, x, y


def towns(places):
    """Nearest Spanish town (name, community) on a 0.1° grid, from the place finder; ('', '') when none is near."""
    from engine.address_search import ES_REGIONS
    communities = set(ES_REGIONS.values())
    grid = {}
    db = sqlite3.connect(f'file:{places}?mode=ro', uri=True)
    for name, lat, lon, detail in db.execute("SELECT name,lat,lon,detail FROM place WHERE kind IN ('city','town','village','municipality') "
                                             "AND lon > -20 AND lon < 5 AND lat > 27 AND lat < 44.5"):
        region = next((p for p in reversed((detail or '').split(', ')) if p in communities), '')
        if region:  # Spanish places only: the border extracts also hold Portugal, France and Andorra
            grid.setdefault((int(lat * 10), int(lon * 10)), []).append((name, region, lat, lon))

    def near(lat, lon):
        best, bd = ('', ''), 1e9
        i, j = int(lat * 10), int(lon * 10)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for name, region, la, lo in grid.get((i + di, j + dj), ()):
                    d = (la - lat) ** 2 + ((lo - lon) * math.cos(math.radians(lat))) ** 2
                    if d < bd:
                        best, bd = (name, region), d
        return best
    return near


def tm_inverse(x, y, lon0, k0, fe, fn, a=6378137.0, f=1 / 298.257222101):
    """Transverse Mercator metres (UTM) to degrees (lon, lat) on GRS80, by Krüger's series."""
    n = f / (2 - f)
    big_a = a / (1 + n) * (1 + n * n / 4 + n ** 4 / 64)
    beta = (n / 2 - 2 * n * n / 3 + 37 * n ** 3 / 96, n * n / 48 + n ** 3 / 15, 17 * n ** 3 / 480)
    delta = (2 * n - 2 * n * n / 3 - 2 * n ** 3, 7 * n * n / 3 - 8 * n ** 3 / 5, 56 * n ** 3 / 15)
    xi, eta = (y - fn) / (k0 * big_a), (x - fe) / (k0 * big_a)
    xi2 = xi - sum(b * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j, b in enumerate(beta, 1))
    eta2 = eta - sum(b * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j, b in enumerate(beta, 1))
    chi = math.asin(math.sin(xi2) / math.cosh(eta2))
    lat = chi + sum(d * math.sin(2 * j * chi) for j, d in enumerate(delta, 1))
    return lon0 + math.degrees(math.atan2(math.sinh(eta2), math.cos(xi2))), math.degrees(lat)


def to_degrees(prj):
    """(x, y) -> (lon, lat) for a shapefile's .prj text: degrees stay, Transverse Mercator (UTM) is converted."""
    if not prj.lstrip().upper().startswith('PROJCS'):
        return lambda x, y: (x, y)
    if 'TRANSVERSE_MERCATOR' not in prj.upper():
        sys.exit(f'Unexpected projection in the MITECO shapefile: {prj[:160]}')

    def par(name):
        return float(re.search(r'PARAMETER\["%s",\s*(-?[\d.]+)\]' % name, prj, re.I).group(1))
    lon0, k0, fe, fn = par('Central_Meridian'), par('Scale_Factor'), par('False_Easting'), par('False_Northing')
    return lambda x, y: tm_inverse(x, y, lon0, k0, fe, fn)


def shapefile_rows(folder, stem):
    """Records of the MITECO shapefile whose name starts with stem, as (fields dict, lon, lat, rings), in degrees."""
    import shapefile
    for z in sorted(Path(folder).glob(f'{stem}*.zip')):
        out = Path(folder) / z.stem
        if not out.exists():
            with zipfile.ZipFile(z) as f:
                f.extractall(out)
    for shp in sorted(Path(folder).rglob(f'*{stem.split("_")[1]}*.shp')):
        prj, cpg = shp.with_suffix('.prj'), shp.with_suffix('.cpg')
        deg = to_degrees(prj.read_text(encoding='latin-1') if prj.exists() else '')
        enc = (cpg.read_text(encoding='ascii', errors='ignore').strip() if cpg.exists() else '') or 'latin-1'
        with shapefile.Reader(str(shp), encoding=enc) as r:
            names = [f[0] for f in r.fields[1:]]
            for sr in r.iterShapeRecords():
                rec = dict(zip(names, sr.record))
                pts = [deg(p[0], p[1]) for p in sr.shape.points]
                if not pts:
                    continue
                if sr.shape.shapeType in (1, 11, 21):
                    yield rec, pts[0][0], pts[0][1], None
                else:
                    idx = list(sr.shape.parts) + [len(pts)]
                    rings = [[v for p in pts[a:b] for v in p] for a, b in zip(idx[:-1], idx[1:])]
                    big = max(rings, key=len)
                    _, x, y = pin(big)
                    yield rec, x, y, rings
        return


DAM_STANDS = {'explotación', 'puesta en carga', 'fuera de servicio', 'construcción', 'aterrada'}   # FASE of a dam that is there
SMALL_WORDS = {'de', 'del', 'la', 'las', 'los', 'el', 'y', 'e', 'o', 'u', 'a', 'al', 'en', 'por', 'con', 'da', 'do', 'das', 'dos',
               'les', 'els'}


def tidy_name(name):
    """The inventory's 'SIBERIA, LA' as 'La Siberia' and 'ALGERRI-BALAGUER, CANAL' as 'Canal Algerri-Balaguer'."""
    name = ' '.join(name.split())
    head, sep, tail = name.rpartition(', ')
    if sep and head and 0 < len(tail.split()) <= 2:
        name = f'{tail} {head}'
    if name.isupper():
        name = ' '.join(w if i and w in SMALL_WORDS else w.title() for i, w in enumerate(name.lower().split(' ')))
    return name


def field(rec, *names):
    for n in names:
        for k, v in rec.items():
            if k.upper() == n and v not in (None, '', ' '):
                return v.strip() if isinstance(v, str) else v
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--gpkg-dir', required=True)
    ap.add_argument('--miteco', help='folder with egis_presa_geoetrs89.zip (and egis_embalse_geoetrs89.zip)')
    a = ap.parse_args()
    t0 = time.time()
    geo = Path(a.data) / 'derived' / 'geo'
    water = geo / 'water.sqlite'
    backup = water.with_name(water.name + '.before-es')
    if not backup.exists():
        shutil.copy2(water, backup)
    near = towns(geo / 'places.sqlite')
    db = sqlite3.connect(water)
    ids = [r[0] for r in db.execute("SELECT id FROM resource WHERE origin='es'")]
    for k in range(0, len(ids), 500):
        chunk = ids[k:k + 500]
        q = ','.join('?' * len(chunk))
        db.execute(f'DELETE FROM spatial WHERE id IN ({q})', chunk)
        db.execute(f'DELETE FROM water_fts WHERE rowid IN ({q})', chunk)
    db.execute("DELETE FROM resource WHERE origin='es'")
    rows = []  # (source_id, kind, name, lat, lon, detail, source)
    seen = set()
    regions = sorted(p for p in (Path(a.gpkg_dir) / 'gpkg').iterdir() if p.name in REGIONS)
    for folder in regions:
        region = REGIONS[folder.name][1]
        src = sqlite3.connect(f'file:{next(folder.glob("*.gpkg"))}?mode=ro', uri=True)
        for g, oid, cls, name in src.execute("SELECT geom,osm_id,fclass,name FROM gis_osm_water_a_free WHERE fclass IN ('water','reservoir')"):
            if g is None or ('w', oid) in seen:
                continue
            seen.add(('w', oid))
            try:
                rings = gpkg_parts(g)
            except Exception:
                continue
            big = max(rings, key=len)
            area, lon, lat = pin(big)
            ha = area / 10000
            name = (name or '').strip()
            if name and RIVER.match(name) and not LAKE_WORDS.search(name):
                continue
            if cls == 'water' and ha >= 0.4 and 4 * math.pi * area / max(perimeter(big), 1) ** 2 < 0.04 and not (name and LAKE_WORDS.search(name)):
                continue  # long and thin: a river or canal drawn as an area
            if cls == 'reservoir' and (name or ha >= 2):
                kind = 'reservoir'
            elif cls == 'water' and name and ha >= 0.5:
                kind = 'lake'
            elif ha >= 0.4:
                kind = 'pond'
            else:
                continue
            town, community = near(lat, lon)
            if not town:
                continue  # no Spanish town around: across the border (Alqueva is in Portugal)
            detail = {'area_ha': round(ha, 1) if ha < 100 else round(ha), 'region': community, 'near': town}
            rows.append((f'osm:w{oid}', kind, name, lat, lon, detail, 'OpenStreetMap'))
        for table in ('gis_osm_traffic_free', 'gis_osm_traffic_a_free'):
            for g, oid, name in src.execute(f"SELECT geom,osm_id,name FROM {table} WHERE fclass='dam' AND name IS NOT NULL AND name<>''"):
                if g is None or ('d', oid) in seen:
                    continue
                seen.add(('d', oid))
                try:
                    parts = gpkg_parts(g)
                except Exception:
                    continue
                xs = [v for p in parts for v in p[0::2]]
                ys = [v for p in parts for v in p[1::2]]
                lon, lat = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
                town, community = near(lat, lon)
                if town:
                    rows.append((f'osm:d{oid}', 'dam', name.strip(), lat, lon, {'region': community, 'near': town}, 'OpenStreetMap'))
        print(folder.name, len(rows), round(time.time() - t0), flush=True)
    n_miteco = 0
    if a.miteco and any(Path(a.miteco).glob('egis_presa*')):
        def cell(lat, lon):
            return round(lat / 0.01), round(lon / 0.013)

        def close(lat, lon, grid):
            """Rows of grid within about 1 km: 0.01° of latitude and 0.013° of longitude."""
            cy, cx = cell(lat, lon)
            return [r for dy in (-1, 0, 1) for dx in (-1, 0, 1) for r in grid.get((cy + dy, cx + dx), ())
                    if abs(r[3] - lat) < 0.01 and abs(r[4] - lon) < 0.013]
        osm_dams = {}
        for r in rows:
            if r[1] == 'dam':
                osm_dams.setdefault(cell(r[3], r[4]), []).append(r)
        official, left_out = [], 0
        for rec, lon, lat, _ in shapefile_rows(a.miteco, 'egis_presa'):
            phase = (field(rec, 'FASE') or '').lower()
            if (phase and phase not in DAM_STANDS) or (not phase and not close(lat, lon, osm_dams)):
                left_out += 1
                continue
            name = tidy_name(field(rec, 'NOMBRE', 'NOM_PRESA', 'DENOMINACI') or '')
            town, community = near(lat, lon)
            detail = {k: v for k, v in {
                'owner': field(rec, 'TITULAR'), 'river': tidy_name(field(rec, 'CAUCE', 'RIO') or ''),
                'purpose': field(rec, 'USO', 'USOS'),
                'dam_type': field(rec, 'TIPO', 'TIPOLOGIA'), 'category': field(rec, 'CATEGORIA', 'CATEG'),
                'height_m': field(rec, 'ALT_CIMIEN', 'ALTURA'), 'capacity_hm3': field(rec, 'NMN_CAPAC', 'CAPACIDAD'),
                'province': field(rec, 'PROVINCIA'), 'region': community or field(rec, 'CCAA'),
                'near': town}.items() if v not in (None, '')}
            official.append((f'miteco:{field(rec, "CODIGO", "COD_PRESA", "ID") or len(official)}', 'dam', name,
                             lat, lon, detail, 'MITECO, Inventario de Presas y Embalses'))
        # an OSM dam within 1 km of an inventory dam is the same dam
        official_grid = {}
        for o in official:
            official_grid.setdefault(cell(o[3], o[4]), []).append(o)
        rows = [r for r in rows if not (r[1] == 'dam' and close(r[3], r[4], official_grid))] + official
        n_miteco = len(official)
        print('MITECO dams:', n_miteco, 'kept,', left_out, 'left out (projects, under a reservoir, or no phase and no OSM dam)', flush=True)
    nid = (db.execute('SELECT max(id) FROM resource').fetchone()[0] or 0) + 1
    counts = {}
    for source_id, kind, name, lat, lon, detail, source in rows:
        db.execute('INSERT INTO resource VALUES (?,?,?,?,?,?,?,?,?)',
                   (nid, source_id, kind, name, lat, lon, json.dumps(detail, ensure_ascii=False), source, 'es'))
        db.execute('INSERT INTO spatial VALUES (?,?,?,?,?)', (nid, lon, lon, lat, lat))
        if name:
            db.execute('INSERT INTO water_fts(rowid, name, other) VALUES (?,?,?)', (nid, name, ''))
        counts[kind] = counts.get(kind, 0) + 1
        nid += 1
    db.execute("INSERT OR REPLACE INTO meta VALUES ('es', ?)", (json.dumps({
        **counts, 'miteco_dams': n_miteco, 'built': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'sources': 'OpenStreetMap (ODbL)' + ('; © Ministerio para la Transición Ecológica y el Reto Demográfico' if n_miteco else '')}),))
    db.commit()
    db.close()
    print('done', counts, 'MITECO dams', n_miteco, round(time.time() - t0), 's', flush=True)


if __name__ == '__main__':
    main()
