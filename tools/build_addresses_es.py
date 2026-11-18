"""Spanish street addresses for address search: derived/geo/addresses/ES.sqlite, from Overture Maps' address theme.

  buildenv/python tools/build_addresses_es.py --out <data>/derived/geo/addresses/ES.sqlite [--release 2026-10-21.0]

Overture's Spanish rows (about 15.6 million) come from one source, "scne.es, distributed by OpenAddresses" (CC BY 4.0).
They are read straight from Overture's public S3 bucket with DuckDB. The community comes from the postcode, whose first
two digits are the INE province; the town from address_levels[1] (postal_city is empty in Spain); the street key from
engine.address_search.es_street(), the same function the search runs on what people type (see docs/REGIONS-ES.md,
"How addresses are written"). Rows without a number are left out; "S-N" (sin número) is kept as a number.

Also writes postcode(zip, city, state, lat, lon, n): the centre of each postcode's addresses, for the place finder
(tools/build_places_es.py). That is a point per postcode, not a postcode area.
"""
import argparse, re, sqlite3, sys, time
from collections import Counter
from functools import lru_cache
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from engine.address_search import es_fold, es_region, es_street  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True, help='derived/geo/addresses/ES.sqlite in the data folder')
    ap.add_argument('--release', default='2026-10-21.0')
    ap.add_argument('--bbox', default='-18.5,27.4,4.6,44.0', help='W,S,E,N: the Canary Islands to the Pyrenees')
    a = ap.parse_args()
    w, s, e, n = map(float, a.bbox.split(','))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix('.tmp')
    tmp.unlink(missing_ok=True)
    t0 = time.time()
    c = duckdb.connect()
    c.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial; SET s3_region='us-west-2';")
    cur = c.execute(f"""
        SELECT number, coalesce(unit, ''), street, coalesce(address_levels[1].value, ''), coalesce(left(postcode, 5), ''),
               round(ST_Y(geometry), 6), round(ST_X(geometry), 6)
        FROM read_parquet('s3://overturemaps-us-west-2/release/{a.release}/theme=addresses/type=address/*', hive_partitioning=1)
        WHERE country = 'ES' AND bbox.xmin BETWEEN {w} AND {e} AND bbox.ymin BETWEEN {s} AND {n}
          AND number IS NOT NULL AND street IS NOT NULL""")
    street_key = lru_cache(maxsize=3_000_000)(es_street)
    town_key = lru_cache(maxsize=100_000)(lambda town: ' '.join(re.sub(r'[^a-z0-9]+', ' ', es_fold(town)).split()))
    db = sqlite3.connect(tmp)
    db.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-500000;'
                     'CREATE TABLE addr(num TEXT, unit TEXT, street TEXT, norm TEXT, kind TEXT, city TEXT, cnorm TEXT,'
                     ' state TEXT, zip TEXT, lat REAL, lon REAL);'
                     'CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);')
    postcodes = {}
    total = skipped = 0
    while rows := cur.fetchmany(250_000):
        batch = []
        for num, unit, street, town, zip_code, lat, lon in rows:
            street = street.strip()
            kind, key = street_key(street)
            if not key:
                skipped += 1
                continue
            region = es_region(zip_code)[1]
            batch.append((num.strip().upper(), unit.strip().upper(), street, key, kind, town.strip(), town_key(town),
                          region, zip_code, lat, lon))
            if zip_code:
                p = postcodes.get(zip_code)
                if p is None:
                    p = postcodes[zip_code] = [0, 0.0, 0.0, Counter()]
                p[0] += 1
                p[1] += lat
                p[2] += lon
                p[3][town.strip()] += 1
        db.executemany('INSERT INTO addr VALUES (?,?,?,?,?,?,?,?,?,?,?)', batch)
        total += len(batch)
        print(f'{total:,} addresses, {time.time() - t0:.0f}s', flush=True)
    print('indexing', flush=True)
    db.execute('CREATE INDEX addr_norm ON addr(norm, num)')
    db.execute('CREATE TABLE postcode(zip TEXT PRIMARY KEY, city TEXT, state TEXT, lat REAL, lon REAL, n INTEGER)')
    db.executemany('INSERT INTO postcode VALUES (?,?,?,?,?,?)',
                   [(z, p[3].most_common(1)[0][0], es_region(z)[1], round(p[1] / p[0], 6), round(p[2] / p[0], 6), p[0])
                    for z, p in sorted(postcodes.items())])
    db.executemany('INSERT INTO meta VALUES (?,?)', [
        ('country', 'ES'), ('bbox', a.bbox), ('count', str(total)), ('skipped', str(skipped)),
        ('built', time.strftime('%Y-%m-%dT%H:%M:%S')),
        ('source', f'Overture Maps addresses {a.release}: scne.es, distributed by OpenAddresses (CC BY 4.0)')])
    db.commit()
    db.close()
    tmp.replace(out)
    print(f'done: {total:,} addresses, {len(postcodes):,} postcodes, {skipped} without a street key, '
          f'{time.time() - t0:.0f}s', flush=True)


if __name__ == '__main__':
    main()
