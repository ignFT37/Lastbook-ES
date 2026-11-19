"""Aerial photos of Spain for the offline map: derived/geo/aerial.sqlite from the IGN's PNOA orthophotos.

  buildenv/python tools/fetch_aerial_es.py --data <data folder> [--minz 6] [--maxz 14] [--workers 8]

Source: PNOA "máxima actualidad" (Plan Nacional de Ortofotografía Aérea), served by the Instituto Geográfico Nacional
as WMTS in the GoogleMapsCompatible tile matrix (256 px web-mercator JPEG, z/x/y). Licence CC BY 4.0 (Orden
FOM/2807/2015); credit "PNOA cedido por © Instituto Geográfico Nacional", shown at the foot of the map.

Which tiles: every tile, from --minz to --maxz, that touches a 0.05° cell holding detailed map features, read from
derived/geo/socal-cells.json, which tools/build_map_es.py writes. That is Spain's land, without open sea. Same tables
as tools/fetch_aerial.py (tile, miss, meta). Resumable: tiles fetched or missed are recorded and skipped next time.
Zoom 14 is about 7 m per pixel in Spain; above that the map enlarges the zoom 14 photo.
"""
import argparse, http.client, json, math, queue, sqlite3, threading, time
from pathlib import Path

HOST = 'www.ign.es'
PATH = ('/wmts/pnoa-ma?request=GetTile&service=WMTS&VERSION=1.0.0&Layer=OI.OrthoimageCoverage&Style=default'
        '&Format=image/jpeg&TileMatrixSet=GoogleMapsCompatible&TileMatrix={z}&TileRow={y}&TileCol={x}')
UA = 'LastbookES-offline-map/1.0 (offline cache for a personal emergency map)'
CELL = 0.05


def tile_range(z, w, s, e, n):
    k = 2 ** z

    def ty(lat):
        r = math.radians(max(-85, min(85, lat)))
        return int((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * k)
    return range(int((w + 180) / 360 * k), int((e + 180 - 1e-9) / 360 * k) + 1), range(ty(n - 1e-9), ty(s) + 1)


def wanted(cells, minz, maxz):
    out = []
    for z in range(minz, maxz + 1):
        tiles = set()
        for c in cells:
            i, j = divmod(c, 100000)
            w, s = i * CELL - 180, j * CELL - 90
            xs, ys = tile_range(z, w, s, w + CELL, s + CELL)
            tiles.update((x, y) for x in xs for y in ys)
        out += [(z, x, y) for x, y in sorted(tiles, key=lambda t: (t[1], t[0]))]
    return out


def worker(todo, done, stop):
    conn = None
    while not stop.is_set():
        try:
            z, x, y = todo.get(timeout=1)
        except queue.Empty:
            return
        data, status = None, 0
        for attempt in range(4):
            try:
                if conn is None:
                    conn = http.client.HTTPSConnection(HOST, timeout=60)
                conn.request('GET', PATH.format(z=z, x=x, y=y), headers={'User-Agent': UA})
                r = conn.getresponse()
                body = r.read()
                status = r.status
                if status == 200 and r.getheader('Content-Type', '').startswith('image/'):
                    data = body
                    break
                if status in (400, 404):
                    break
            except (OSError, http.client.HTTPException):
                conn = None
            time.sleep(2 * (attempt + 1))
        done.put((z, x, y, data, status))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--minz', type=int, default=6)
    ap.add_argument('--maxz', type=int, default=14)
    ap.add_argument('--workers', type=int, default=8)
    a = ap.parse_args()
    geo = Path(a.data) / 'derived' / 'geo'
    cells = json.loads((geo / 'socal-cells.json').read_text(encoding='utf-8'))
    db = sqlite3.connect(geo / 'aerial.sqlite')
    db.executescript('PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;'
                     'CREATE TABLE IF NOT EXISTS tile(z INT, x INT, y INT, jpg BLOB, PRIMARY KEY(z,x,y)) WITHOUT ROWID;'
                     'CREATE TABLE IF NOT EXISTS miss(z INT, x INT, y INT, why INT, PRIMARY KEY(z,x,y)) WITHOUT ROWID;'
                     'CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);')
    db.executemany('INSERT OR REPLACE INTO meta VALUES (?,?)', [
        ('attribution', 'PNOA cedido por © Instituto Geográfico Nacional (CC BY 4.0)'),
        ('area', f'Spain, zoom {a.minz}-{a.maxz}'), ('source', 'IGN WMTS pnoa-ma, layer OI.OrthoimageCoverage')])
    db.commit()
    have = {(z, x, y) for z, x, y in db.execute('SELECT z,x,y FROM tile UNION ALL SELECT z,x,y FROM miss')}
    todo_list = [t for t in wanted(cells, a.minz, a.maxz) if t not in have]
    print(f'{len(have):,} tiles already done, {len(todo_list):,} to fetch', flush=True)
    todo, done, stop = queue.Queue(), queue.Queue(), threading.Event()
    for t in todo_list:
        todo.put(t)
    threads = [threading.Thread(target=worker, args=(todo, done, stop), daemon=True) for _ in range(a.workers)]
    for t in threads:
        t.start()
    t0, got, missed, size = time.time(), 0, 0, 0
    for n in range(1, len(todo_list) + 1):
        z, x, y, data, status = done.get()
        if data:
            db.execute('INSERT OR REPLACE INTO tile VALUES (?,?,?,?)', (z, x, y, data))
            got += 1
            size += len(data)
        else:
            db.execute('INSERT OR REPLACE INTO miss VALUES (?,?,?,?)', (z, x, y, status))
            missed += 1
        if n % 2000 == 0:
            db.commit()
            rate = n / (time.time() - t0)
            print(f'{n:,}/{len(todo_list):,} z{z} · {got:,} photos ({size / 1e9:.2f} GB), {missed} missed · '
                  f'{rate:.1f}/s, about {(len(todo_list) - n) / rate / 3600:.1f} h left', flush=True)
    db.execute("INSERT OR REPLACE INTO meta VALUES ('built', ?)", (time.strftime('%Y-%m-%dT%H:%M:%S'),))
    db.commit()
    db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    db.execute('PRAGMA journal_mode=DELETE')
    db.close()
    print(f'done: {got:,} photos ({size / 1e9:.2f} GB), {missed} missed, {time.time() - t0:.0f}s', flush=True)


if __name__ == '__main__':
    main()
