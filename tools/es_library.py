#!/usr/bin/env python3
"""Lastbook ES: put Spain's official emergency documents into a Lastbook library (a data folder).

  python tools/es_library.py --data <data folder> --files <folder with the downloaded files> [--manifest FILE] [--dry-run]

The manifest (default content/es/library-es.json) lists each document with: id (starting "es-"), file (its name in
--files, or in content/es/ for the pages written for Lastbook ES), kind ("pdf" or "html"), title, publisher, edition,
collection, category (starting "spain", so the Spain shelf in server.py picks it up), source_url, final_url,
retrieved_at and notes.

Every run first takes out what the previous run put in (recorded in library-data/spain-addon.json), then adds the
manifest's documents again, so it can be run as often as needed:
  - copies each file to library-data/documents/spain/;
  - writes its text to library-data/text/<id>.txt, one page per form feed, as the pack's own text files are
    (PDF with pypdfium2, HTML with BeautifulSoup);
  - adds its record to catalog.json and its pages to search.sqlite (the FTS5 table "pages");
  - updates summary.json and pack.json (add_ons and counts).
Before its first change it keeps copies of those four files as *.before-es. Run it with the app closed.
"""
import argparse, hashlib, json, shutil, sqlite3, sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
ADDON = 'spain'
SUB = 'documents/spain'
FIELDS = ('id', 'title', 'kind', 'publisher', 'collection', 'category', 'path', 'bytes', 'sha256', 'pages', 'edition',
          'retrieved_at', 'source_url', 'final_url', 'searchability', 'notes', 'text_path')


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def pdf_pages(path):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(path))
    try:
        out = []
        for i in range(len(pdf)):
            page = pdf[i]
            tp = page.get_textpage()
            out.append(tp.get_text_range().replace('\r\n', '\n').replace('\r', '\n'))
            tp.close()
            page.close()
        return out
    finally:
        pdf.close()


def html_pages(path):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(path.read_bytes(), 'html.parser')
    for t in soup(['script', 'style']):
        t.decompose()
    lines = (l.strip() for l in soup.get_text('\n').splitlines())
    return ['\n'.join(l for l in lines if l)]


def load(p):
    return json.loads(p.read_text(encoding='utf-8'))


def save(p, v, indent=1):
    tmp = p.with_name(p.name + '.tmp')
    tmp.write_text(json.dumps(v, ensure_ascii=False, indent=indent), encoding='utf-8')
    tmp.replace(p)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--data', required=True, help='the data folder, the one that holds library-data/')
    ap.add_argument('--files', required=True, help='folder holding the downloaded files named in the manifest')
    ap.add_argument('--manifest', default=str(HERE / 'content' / 'es' / 'library-es.json'))
    ap.add_argument('--dry-run', action='store_true', help='read and extract everything, change nothing')
    a = ap.parse_args()

    data = Path(a.data)
    lib = data / 'library-data'
    files = Path(a.files)
    own = Path(a.manifest).parent
    cat_p, db_p, sum_p, pack_p, side_p = (lib / 'catalog.json', lib / 'search.sqlite', lib / 'summary.json',
                                          data / 'pack.json', lib / 'spain-addon.json')
    if not cat_p.is_file() or not db_p.is_file():
        sys.exit(f'No library at {lib}')
    docs = load(Path(a.manifest))

    # ---- read and check the manifest, extract every text before touching the library
    prepared, seen = [], set()
    for d in docs:
        did, kind = d['id'], d.get('kind', 'pdf')
        if not did.startswith('es-') or did in seen:
            sys.exit(f'bad or repeated id: {did}')
        if not d.get('category', '').startswith('spain'):
            sys.exit(f'{did}: category must start with "spain"')
        if kind not in ('pdf', 'html'):
            sys.exit(f'{did}: kind must be pdf or html')
        seen.add(did)
        src = files / d['file']
        if not src.is_file():
            src = own / d['file']
        if not src.is_file():
            sys.exit(f'{did}: file not found in {files} or {own}: {d["file"]}')
        pages = pdf_pages(src) if kind == 'pdf' else html_pages(src)
        chars = sum(len(p.strip()) for p in pages)
        rec = {k: d[k] for k in FIELDS if d.get(k) not in (None, '')}
        rec.update(kind=kind, path=f'{SUB}/{d["file"]}', bytes=src.stat().st_size, sha256=sha256(src),
                   text_path=f'text/{did}.txt')
        if kind == 'pdf':
            rec['pages'] = len(pages)
            rec['searchability'] = 'text' if chars >= 40 * len(pages) else 'image-only: needs OCR'
        prepared.append((rec, src, pages))
        print(f'{did:<44} {kind:<4} {len(pages):>4} p {chars:>8} chars  {rec.get("searchability", "")}')

    if a.dry_run:
        print('dry run: nothing changed')
        return

    # ---- keep the originals once
    for p in (cat_p, db_p, sum_p, pack_p):
        b = p.with_name(p.name + '.before-es')
        if p.is_file() and not b.exists():
            shutil.copy2(p, b)

    catalog = load(cat_p)
    summary = load(sum_p) if sum_p.is_file() else {}
    pack = load(pack_p) if pack_p.is_file() else {}
    prev = load(side_p) if side_p.is_file() else {'ids': [], 'files': [], 'added': {}}

    # ---- take out the previous run
    old = set(prev['ids'])
    catalog = [r for r in catalog if r['id'] not in old]
    for k, v in prev.get('added', {}).items():
        if k.startswith('counts.'):
            pack.setdefault('counts', {})[k[7:]] = pack.get('counts', {}).get(k[7:], 0) - v
        else:
            summary[k] = summary.get(k, 0) - v
    for rel in prev.get('files', []):
        (lib / rel).unlink(missing_ok=True)
    db = sqlite3.connect(db_p)
    if old:
        db.executemany('DELETE FROM pages WHERE doc_id = ?', [(i,) for i in old])

    # ---- add the manifest's documents
    clash = {r['id'] for r in catalog} & seen
    if clash:
        sys.exit(f'ids already in the catalog: {sorted(clash)}')
    (lib / SUB).mkdir(parents=True, exist_ok=True)
    added = {'resources': 0, 'pdfs': 0, 'pdf_pages': 0, 'web_pages': 0, 'document_bytes': 0, 'counts.pdf': 0,
             'counts.html': 0}
    written = []
    for rec, src, pages in prepared:
        shutil.copy2(src, lib / rec['path'])
        (lib / rec['text_path']).write_text('\f'.join(pages), encoding='utf-8')
        written += [rec['path'], rec['text_path']]
        catalog.append(rec)
        db.executemany('INSERT INTO pages(doc_id, title, collection, category, path, page, body) VALUES (?,?,?,?,?,?,?)',
                       [(rec['id'], rec['title'], rec['collection'], rec['category'], rec['path'], n, body)
                        for n, body in enumerate(pages, 1) if body.strip()])
        added['resources'] += 1
        added['document_bytes'] += rec['bytes']
        if rec['kind'] == 'pdf':
            added['pdfs'] += 1
            added['pdf_pages'] += rec['pages']
            added['counts.pdf'] += 1
        else:
            added['web_pages'] += 1
            added['counts.html'] += 1
    db.commit()
    db.execute("INSERT INTO pages(pages) VALUES('optimize')")
    db.commit()
    db.close()

    for k, v in added.items():
        if k.startswith('counts.'):
            pack.setdefault('counts', {})[k[7:]] = pack.get('counts', {}).get(k[7:], 0) + v
        else:
            summary[k] = summary.get(k, 0) + v
    ons = [x for x in pack.get('add_ons', []) if x != ADDON]
    pack['add_ons'] = ons + ([ADDON] if prepared else [])

    save(cat_p, catalog)
    save(sum_p, summary)
    save(pack_p, pack)
    save(side_p, {'ids': sorted(seen), 'files': written, 'added': added})
    print(f'added {len(prepared)} documents ({added["pdf_pages"]} PDF pages) to {lib}')


if __name__ == '__main__':
    main()
