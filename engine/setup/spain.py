"""Lastbook ES: Spain's own documents, added to every library setup installs.

The official packs know nothing of Spain. Once a pack is unpacked, add_library() puts the Spain add-on that ships with this app
(a folder called spain-library, made by tools/es_library.py --export) into the new library: each document under
library-data/documents/spain/, its text under library-data/text/, its record in catalog.json and its pages in search.sqlite (the
FTS5 table "pages"). It does what tools/es_library.py does in a data folder, and keeps summary.json, pack.json and the pack's own
pack-manifest.tsv in step, so that "repair" still finds every file as it should be.

The add-on's records name no path outside library-data and no folder above it; each file is checked against its SHA-256 first.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

ADDON = 'spain'
PAYLOAD_JSON = 'spain-library.json'
SIDE_FILE = 'spain-addon.json'       # in library-data/: what the add-on put in, as tools/es_library.py keeps it


def payload_dir():
    """The add-on's folder: next to the app's other files when packaged, derived/spain-library in the source tree."""
    if os.environ.get('LASTBOOK_SPAIN_LIBRARY'):
        return Path(os.environ['LASTBOOK_SPAIN_LIBRARY'])
    if getattr(sys, 'frozen', False):
        return Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent)) / 'spain-library'
    return Path(__file__).resolve().parents[2] / 'derived' / 'spain-library'


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def _load(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))


def _save(p, v):
    p = Path(p)
    tmp = p.with_name(p.name + '.tmp')
    tmp.write_text(json.dumps(v, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, p)


def _safe(rel):
    parts = Path(rel).parts
    return bool(parts) and not Path(rel).is_absolute() and '..' not in parts and '\\' not in rel and ':' not in rel


def _manifest_rows(lib, rels):
    """Brings pack-manifest.tsv (path, bytes, sha256, how, changes) in step with the files the add-on added or changed."""
    p = lib / 'pack-manifest.tsv'
    if not p.is_file():
        return
    lines = p.read_text(encoding='utf-8').splitlines()
    head, rows = lines[0], [l.split('\t') for l in lines[1:] if l.strip()]
    keep = [r for r in rows if r[0] not in rels]
    for rel, how in rels.items():
        f = lib / rel
        keep.append([rel, str(f.stat().st_size), _sha256(f), how, ''])
    keep.sort(key=lambda r: r[0])
    p.write_text('\n'.join([head] + ['\t'.join(r) for r in keep]) + '\n', encoding='utf-8')


def add_library(lib, payload=None):
    """Puts the add-on into the library folder `lib` (a LastbookData still being staged). Returns the number of documents added,
    0 when this app carries no add-on. Raises ValueError for an add-on that does not check out; nothing is half-written then."""
    lib = Path(lib)
    src = Path(payload) if payload else payload_dir()
    if not (src / PAYLOAD_JSON).is_file():
        print(f'setup: no Spain library add-on in {src}', flush=True)
        return 0
    spec = _load(src / PAYLOAD_JSON)
    if spec.get('version') != 1:
        raise ValueError('the Spain add-on has a version this app does not know')
    data = lib / 'library-data'
    cat_p, db_p, sum_p, pack_p = data / 'catalog.json', data / 'search.sqlite', data / 'summary.json', lib / 'pack.json'
    if not cat_p.is_file() or not db_p.is_file():
        raise ValueError('the library has no catalog or search index to add to')
    records = spec['records']
    if (data / SIDE_FILE).is_file():        # an earlier run of this step got as far as writing its note: it is all there
        return len(records)
    catalog = _load(cat_p)
    clash = {r['id'] for r in catalog} & {r['id'] for r in records}
    if clash:
        raise ValueError(f'the library already holds {sorted(clash)[:3]}')
    # Every file, checked before anything is written.
    for r in records:
        for key in ('path', 'text_path'):
            if not _safe(r[key]):
                raise ValueError(f'a path in the Spain add-on is not allowed: {r[key]!r}')
        f = src / r['path']
        if not f.is_file() or f.stat().st_size != r['bytes'] or _sha256(f) != r['sha256']:
            raise ValueError(f'{r["path"]} in the Spain add-on is missing or damaged')
        if not (src / r['text_path']).is_file():
            raise ValueError(f'{r["text_path"]} in the Spain add-on is missing')
    written = []
    for r in records:
        for key in ('path', 'text_path'):
            dest = data / r[key]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src / r[key], dest)
            written.append(r[key])
    db = sqlite3.connect(db_p)
    try:
        for r in records:
            pages = (src / r['text_path']).read_text(encoding='utf-8').split('\f')
            db.executemany('INSERT INTO pages(doc_id, title, collection, category, path, page, body) VALUES (?,?,?,?,?,?,?)',
                           [(r['id'], r['title'], r['collection'], r['category'], r['path'], n, body)
                            for n, body in enumerate(pages, 1) if body.strip()])
        db.commit()
        db.execute("INSERT INTO pages(pages) VALUES ('optimize')")
        db.commit()
    finally:
        db.close()
    added = spec['added']
    summary = _load(sum_p) if sum_p.is_file() else {}
    pack = _load(pack_p) if pack_p.is_file() else {}
    for k, v in added.items():
        if k.startswith('counts.'):
            pack.setdefault('counts', {})[k[7:]] = pack.get('counts', {}).get(k[7:], 0) + v
        else:
            summary[k] = summary.get(k, 0) + v
    pack['add_ons'] = [x for x in pack.get('add_ons', []) if x != ADDON] + [ADDON]
    _save(cat_p, catalog + records)
    _save(sum_p, summary)
    _save(pack_p, pack)
    _save(data / SIDE_FILE, {'ids': sorted(r['id'] for r in records), 'files': written, 'added': added})
    _manifest_rows(lib, {**{f'library-data/{rel}': 'spain-addon' for rel in written}, 'library-data/catalog.json': 'generated',
                         'library-data/search.sqlite': 'generated', 'library-data/summary.json': 'generated', 'pack.json': 'generated',
                         f'library-data/{SIDE_FILE}': 'spain-addon'})
    print(f'setup: added {len(records)} Spanish documents to the library', flush=True)
    return len(records)
