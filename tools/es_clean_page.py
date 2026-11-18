#!/usr/bin/env python3
"""Lastbook ES: keep only the readable content of a saved official web page, as a small self-contained HTML file.

  python tools/es_clean_page.py SAVED.html OUT.html --url <original address> --retrieved YYYY-MM-DD [--title "..."]

Keeps the main article's headings, paragraphs, lists and tables, with their text exactly as published, and drops menus,
scripts, styles, forms, images and links' targets. A line at the top says where the page came from and when.
"""
import argparse, html, re
from pathlib import Path
from bs4 import BeautifulSoup

KEEP = ('h1', 'h2', 'h3', 'h4', 'p', 'ul', 'ol', 'li', 'table', 'tr', 'td', 'th', 'strong', 'em', 'b', 'i', 'br')


def main_block(soup):
    for sel in ('main', 'article', '[role=main]', '#main-content', '.journal-content-article', '#content'):
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) > 300:
            return el
    best, score = soup.body or soup, 0
    for el in soup.find_all(['div', 'section']):
        s = sum(len(p.get_text(strip=True)) for p in el.find_all('p', recursive=False))
        if s > score:
            best, score = el, s
    return best


def clean(el):
    for t in el.find_all(['script', 'style', 'noscript', 'form', 'nav', 'header', 'footer', 'aside', 'img', 'svg',
                          'iframe', 'button', 'input', 'select']):
        t.decompose()
    for t in el.find_all(True):
        if t.name not in KEEP:
            t.unwrap()
        else:
            t.attrs = {}
    return el


def run():
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('out')
    ap.add_argument('--url', required=True)
    ap.add_argument('--retrieved', required=True)
    ap.add_argument('--title')
    a = ap.parse_args()
    soup = BeautifulSoup(Path(a.src).read_bytes(), 'html.parser')
    title = a.title or (soup.title.get_text(strip=True) if soup.title else 'Página oficial')
    body = clean(main_block(soup))
    inner = re.sub(r'\n\s*\n+', '\n', body.decode_contents()).strip()
    page = (f'<!doctype html>\n<html lang="es"><head><meta charset="utf-8"><title>{html.escape(title)}</title></head>\n'
            f'<body>\n<p><small>Copia de la página oficial {html.escape(a.url)}, descargada el {html.escape(a.retrieved)} '
            f'para leerla sin conexión. Solo se ha quitado la navegación del sitio.</small></p>\n<h1>{html.escape(title)}</h1>\n'
            f'{inner}\n</body></html>\n')
    Path(a.out).write_text(page, encoding='utf-8')
    text = BeautifulSoup(page, 'html.parser').get_text(' ', strip=True)
    print(f'{a.out}: {len(page):,} bytes, {len(text):,} characters of text')


if __name__ == '__main__':
    run()
