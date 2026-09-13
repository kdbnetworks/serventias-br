# -*- coding: utf-8 -*-
"""
fetch_bundles.py (v2) — baixa TODOS os chunks JS da SPA do Justica Aberta.

Sao ~110 arquivos pequenos. Salva em data/raw/spa/.

Uso:  python tools/fetch_bundles.py
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

SPA = "https://justicaaberta.cnj.jus.br"
PAGE = SPA + "/produtividade-e-localizacao-de-serventias-extrajudiciais"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
H = {"User-Agent": UA, "Accept": "*/*"}

OUT = Path(__file__).resolve().parents[1] / "data" / "raw" / "spa"

RE_SCRIPT = re.compile(r'<script[^>]+src=["\']([^"\']+)["\']', re.I)
RE_ASSET = re.compile(r'["\'`](?:\./)?(assets/[A-Za-z0-9._\-]+\.js)["\'`]')

sess = requests.Session()
sess.headers.update(H)


def get(url: str):
    for _ in range(3):
        try:
            r = sess.get(url, timeout=60)
            if r.status_code == 200:
                return r
        except Exception:                               # noqa: BLE001
            pass
        time.sleep(1)
    return None


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    r = get(PAGE)
    if r is None:
        print("Nao consegui buscar a pagina. Abortando.")
        return
    (OUT / "index.html").write_text(r.text, encoding="utf-8")

    todos: set[str] = set()
    fila = [urljoin(SPA, s) for s in RE_SCRIPT.findall(r.text)]
    vistos: set[str] = set()

    while fila:
        url = fila.pop()
        if url in vistos:
            continue
        vistos.add(url)
        rb = get(url)
        nome = url.rsplit("/", 1)[-1]
        if rb is None:
            print(f"  [ERR] {nome}")
            continue
        (OUT / nome).write_text(rb.text, encoding="utf-8")
        todos.add(nome)
        print(f"  [ok] {nome}  ({len(rb.text)} chars)")
        for a in RE_ASSET.findall(rb.text):
            nova = urljoin(SPA + "/", a)
            if nova not in vistos:
                fila.append(nova)

    print(f"\n>>> {len(todos)} arquivo(s) em {OUT}")
    print("Pode avisar o Claude.")


if __name__ == "__main__":
    main()
