#!/usr/bin/env python3
"""Help points that Geofabrik's "free" format leaves out, added to derived/geo/map.sqlite from full OSM extracts.

  buildenv/python tools/build_osm_extra_es.py --map <data>/derived/geo/map.sqlite <spain-latest.osm.pbf> [<canary-islands-latest.osm.pbf> ...]

Reads OpenStreetMap (ODbL) nodes and closed ways:
- defibrillators: emergency=defibrillator -> poi class 'defibrillator';
- drinking fountains, taps and wells marked drinkable: amenity=fountain, man_made=water_tap or man_made=water_well
  with drinking_water=yes -> 'drinking_water', the class of the drinking-water layer (the free format files these
  fountains as decorative 'fountain' and has no taps or wells);
- unstaffed huts: tourism=wilderness_hut, and amenity=shelter with shelter_type=basic_hut -> 'wilderness_hut'.
Rows go into the poi table with src 'osm-extra', a column this adds; the previous ones are taken out first, so it can
be run again after tools/build_map_es.py. A way's point is the mean of its nodes.
"""
import argparse, sqlite3, time

import osmium


def classify(tags):
    if tags.get('emergency') == 'defibrillator':
        return 'defibrillator'
    if tags.get('drinking_water') in ('yes', 'treated') and (
            tags.get('amenity') == 'fountain' or tags.get('man_made') in ('water_tap', 'water_well')):
        return 'drinking_water'
    if tags.get('tourism') == 'wilderness_hut' or (tags.get('amenity') == 'shelter' and tags.get('shelter_type') == 'basic_hut'):
        return 'wilderness_hut'
    return None


def label(cls, tags):
    name = tags.get('name') or ''
    if cls == 'defibrillator' and not name:
        name = tags.get('defibrillator:location') or tags.get('operator') or ''
    return name.strip()[:120]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--map', required=True)
    ap.add_argument('pbf', nargs='+')
    a = ap.parse_args()
    t0 = time.time()
    found = []
    for pbf in a.pbf:
        fp = (osmium.FileProcessor(pbf, osmium.osm.NODE | osmium.osm.WAY)
              .with_locations('flex_mem')
              .with_filter(osmium.filter.KeyFilter('emergency', 'drinking_water', 'tourism', 'shelter_type')))
        for o in fp:
            cls = classify(o.tags)
            if cls is None:
                continue
            if o.is_node():
                lon, lat = o.location.lon, o.location.lat
            else:
                try:
                    pts = [(n.lon, n.lat) for n in o.nodes]
                except osmium.InvalidLocationError:
                    continue
                if not pts:
                    continue
                lon, lat = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
            found.append((cls, label(cls, o.tags), lon, lat))
        print(pbf, len(found), round(time.time() - t0), flush=True)
    db = sqlite3.connect(a.map)
    if 'src' not in [r[1] for r in db.execute('PRAGMA table_info(poi)')]:
        db.execute('ALTER TABLE poi ADD COLUMN src TEXT')
    db.execute("DELETE FROM poi WHERE src='osm-extra'")
    db.executemany("INSERT INTO poi(cls, name, lon, lat, src) VALUES (?,?,?,?,'osm-extra')", found)
    db.execute("INSERT OR REPLACE INTO meta VALUES ('extra', ?)", (
        'OpenStreetMap full extracts (ODbL): ' + ', '.join(f'{c} {sum(1 for f in found if f[0] == c)}'
                                                          for c in ('defibrillator', 'drinking_water', 'wilderness_hut')),))
    db.commit()
    db.close()
    print('done', {c: sum(1 for f in found if f[0] == c) for c in ('defibrillator', 'drinking_water', 'wilderness_hut')},
          round(time.time() - t0), 's', flush=True)


if __name__ == '__main__':
    main()
