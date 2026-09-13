# -*- coding: utf-8 -*-
"""
sniff_spa.py — descobre a URL da API lendo os bundles JS da SPA do Justica Aberta.

Roda na maquina que tem acesso ao site. Nao depende de HAR.
Grava tudo em diag_spa.txt (UTF-8) na pasta do projeto.

Uso:  python tools/sniff_spa.py
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin

import requests

SPA = "https://justicaaberta.cnj.jus.br"
PAGE = SPA + "/produtividade-e-localizacao-de-serventias-extrajudiciais"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
H = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*"}

OUT = Path(__file__).resolve().parents[1] / "diag_spa.txt"

RE_SCRIPT = re.compile(r'<script[^>]+src=["\']([^"\']+)["\']', re.I)
RE_MODULE = re.compile(r'["\']([^"\']*\.js)["\']')
RE_ABS_URL = re.compile(r'https?://[A-Za-z0-9._\-]+\.(?:jus\.br|gov\.br)[^\s"\'`)<>]*')
RE_API_PATH = re.compile(r'["\'`](/(?:v\d+/)?api/[^"\'`\s<>]{0,120})["\'`]')
RE_BASEURL = re.compile(
    r'(?:baseURL|baseUrl|BASE_URL|API_URL|apiUrl|VITE_[A-Z_]*API[A-Z_]*|'
    r'REACT_APP_[A-Z_]*API[A-Z_]*)\s*[:=]\s*["\'`]([^"\'`]{0,200})["\'`]')
RE_SERVENTIA = re.compile(r'["\'`]([^"\'`\s<>]{0,80}serventia[^"\'`\s<>]{0,80})["\'`]', re.I)

lines: list[str] = []


def say(s: str = "") -> None:
    print(s)
    lines.append(s)


def fetch(url: str):
    try:
        r = requests.get(url, headers=H, timeout=45)
        return r
    except Exception as e:                              # noqa: BLE001
        say(f"  ERRO ao buscar {url}: {e}")
        return None


def main() -> None:
    say("=" * 70)
    say("PAGINA DA SPA")
    say("=" * 70)
    r = fetch(PAGE)
    if r is None:
        _flush()
        return
    html = r.text
    say(f"status={r.status_code}  bytes={len(r.content)}  ct={r.headers.get('Content-Type')}")
    say(f"url final: {r.url}")
    say("\n--- HTML COMPLETO (ate 4000 chars) ---")
    say(html[:4000])

    # ---- bundles ------------------------------------------------------
    srcs = set(RE_SCRIPT.findall(html))
    for m in RE_MODULE.findall(html):
        if "/" in m or m.endswith(".js"):
            srcs.add(m)
    say("\n" + "=" * 70)
    say(f"BUNDLES ENCONTRADOS NO HTML: {len(srcs)}")
    say("=" * 70)
    for s in sorted(srcs):
        say(f"  {s}")

    if not srcs:
        say("\n[!] Nenhum <script src> no HTML. A pagina pode ser um shell de")
        say("    manutencao ou exigir JS para montar. Veja o HTML acima.")

    abs_urls: set[str] = set()
    api_paths: set[str] = set()
    baseurls: set[str] = set()
    serventia_hits: set[str] = set()

    # bundles de 1o nivel + os que eles importarem (1 nivel de profundidade)
    fila = [urljoin(SPA, s) for s in srcs]
    vistos: set[str] = set()
    nivel2: list[str] = []

    for depth, alvos in ((1, fila), (2, nivel2)):
        for url in alvos:
            if url in vistos or not url.endswith(".js"):
                continue
            vistos.add(url)
            rb = fetch(url)
            if rb is None or rb.status_code != 200:
                say(f"  [{rb.status_code if rb else 'ERR'}] {url}")
                continue
            js = rb.text
            say(f"  [200] {url}  ({len(js)} chars)")
            abs_urls.update(RE_ABS_URL.findall(js))
            api_paths.update(RE_API_PATH.findall(js))
            baseurls.update(RE_BASEURL.findall(js))
            serventia_hits.update(RE_SERVENTIA.findall(js))
            if depth == 1:
                for m in RE_MODULE.findall(js)[:40]:
                    if m.startswith("/") or m.startswith("./") or m.startswith("../"):
                        nivel2.append(urljoin(url, m))

    def bloco(titulo: str, itens, limite: int = 60) -> None:
        say("\n" + "=" * 70)
        say(f"{titulo} ({len(itens)})")
        say("=" * 70)
        for i in sorted(itens)[:limite]:
            say(f"  {i}")
        if len(itens) > limite:
            say(f"  ... (+{len(itens) - limite})")

    bloco("URLs ABSOLUTAS .jus.br / .gov.br NOS BUNDLES", abs_urls)
    bloco("BASE URL / API URL DECLARADAS", baseurls)
    bloco("CAMINHOS /api/ OU /vN/api/", api_paths)
    bloco("STRINGS COM 'SERVENTIA'", serventia_hits, 40)

    _flush()


def _flush() -> None:
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n>>> gravado em {OUT}")


if __name__ == "__main__":
    main()
