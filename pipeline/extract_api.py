# -*- coding: utf-8 -*-
"""
extract_api.py — coleta completa via API descoberta (sources.yaml).

Depois que `parse_har.py` gerar `sources.discovered.yaml`, revise os campos
`pagination` e `records_path`, salve como `sources.yaml` e rode:

    python -m pipeline.extract_api            # todas as UFs
    python -m pipeline.extract_api SC PR RS   # apenas algumas

Boas práticas embutidas (dados públicos ≠ carga ilimitada no servidor):
    - 1 requisição por vez, com pausa (RATE_SLEEP);
    - retry exponencial em 429/5xx;
    - User-Agent identificado;
    - respostas brutas preservadas em data/raw/api/ (reprocessáveis offline).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests
import yaml

from .normalize import UFS, build_record
from .parse_har import _iter_dicts, _score_dict  # reaproveita heurística de mapeamento

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "sources.yaml"
RAW_DIR = ROOT / "data" / "raw" / "api"
STAGING = ROOT / "data" / "staging" / "api_records.jsonl"

RATE_SLEEP = 1.0          # segundos entre requisições
TIMEOUT = 60
MAX_RETRIES = 5
HEADERS = {
    "User-Agent": "serventias-br/0.1 (dataset civico de serventias; +https://github.com/kdbnetworks/serventias-br)",
    "Accept": "application/json",
}


def _dig(payload, path: str):
    """records_path estilo 'a.b.c' — retorna a lista de registros."""
    node = payload
    if path and path not in ("", ".", "TODO"):
        for part in path.split("."):
            if isinstance(node, dict):
                node = node.get(part)
            else:
                return []
            if node is None:
                return []
    if isinstance(node, list):
        return node
    return [node] if isinstance(node, dict) else []


def _request(sess: requests.Session, ep: dict, params: dict):
    method = ep.get("method", "GET").upper()
    url = ep["url"]
    for attempt in range(MAX_RETRIES):
        try:
            if method == "GET":
                r = sess.get(url, params=params, timeout=TIMEOUT)
            else:
                body = ep.get("post_data_template") or {}
                body = json.loads(json.dumps(body).format(**params)) if body else params
                r = sess.post(url, json=body, timeout=TIMEOUT)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, json.JSONDecodeError) as exc:
            wait = 2 ** attempt
            print(f"    [retry {attempt+1}/{MAX_RETRIES}] {exc} — aguardando {wait}s")
            time.sleep(wait)
    print(f"    [erro] desistindo de {url} params={params}")
    return None


def extract(ufs: list[str] | None = None) -> None:
    if not SOURCES.exists():
        sys.exit("sources.yaml não encontrado. Gere com parse_har.py e revise. "
                 "Veja sources.example.yaml para o formato.")
    cfg = yaml.safe_load(SOURCES.read_text(encoding="utf-8"))
    endpoints = cfg.get("endpoints", [])
    if not endpoints:
        sys.exit("Nenhum endpoint em sources.yaml.")

    ufs = [u.upper() for u in (ufs or sorted(UFS))]
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    STAGING.parent.mkdir(parents=True, exist_ok=True)

    sess = requests.Session()
    sess.headers.update(HEADERS)
    sess.headers.update(cfg.get("headers", {}) or {})
    total = 0

    with STAGING.open("w", encoding="utf-8") as out:
        for ep in endpoints:
            pag = ep.get("pagination", {}) or {}
            page_param = pag.get("page_param")
            size_param = pag.get("size_param")
            page_size = int(pag.get("page_size", 100))
            start_page = int(pag.get("start_page", 0))
            uf_param = ep.get("uf_param")           # nome do parâmetro de UF, se houver
            fixed = dict(ep.get("fixed_params", {}) or {})
            rpath = ep.get("records_path", "")

            loops = ufs if uf_param else [None]     # sem uf_param: uma varredura única
            for uf in loops:
                page, vazias = start_page, 0
                while True:
                    params = dict(fixed)
                    if uf_param and uf:
                        params[uf_param] = uf
                    if page_param:
                        params[page_param] = page
                    if size_param:
                        params[size_param] = page_size

                    payload = _request(sess, ep, params)
                    time.sleep(RATE_SLEEP)
                    if payload is None:
                        break

                    tag = f"{ep.get('name','ep')}_{uf or 'BR'}_p{page}"
                    (RAW_DIR / f"{tag}.json").write_text(
                        json.dumps(payload, ensure_ascii=False), encoding="utf-8")

                    rows = _dig(payload, rpath)
                    if not rows:  # fallback: heurística do HAR sobre o payload todo
                        rows = [d for d in _iter_dicts(payload)
                                if _score_dict(d)[0] >= 3 and "cns" in _score_dict(d)[1]]

                    novos = 0
                    for row in rows:
                        _, mapped = _score_dict(row) if isinstance(row, dict) else (0, {})
                        rec = build_record(
                            cns=mapped.get("cns"),
                            denominacao=mapped.get("denominacao"),
                            status=mapped.get("status"),
                            municipio=mapped.get("municipio"),
                            uf=mapped.get("uf") or uf,
                            atribuicoes_raw=mapped.get("atribuicoes"),
                            fonte=f"justica_aberta_api:{ep.get('name','ep')}",
                            extras=mapped,
                        )
                        if rec:
                            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                            novos += 1
                    total += novos
                    print(f"[{ep.get('name','ep')}] uf={uf or '-'} page={page}: {novos} registros")

                    if not page_param:
                        break                       # endpoint sem paginação
                    if novos == 0:
                        vazias += 1
                        if vazias >= 2:
                            break                   # duas páginas vazias = fim
                    else:
                        vazias = 0
                    if len(rows) < page_size and size_param:
                        break                       # página incompleta = última
                    page += 1

    print(f"\n[ok] {total} registros → {STAGING}")
    print("Agora rode: python -m pipeline.build")


if __name__ == "__main__":
    extract(sys.argv[1:] or None)
