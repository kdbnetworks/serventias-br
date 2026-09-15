# -*- coding: utf-8 -*-
"""
extract_justica_aberta.py — extrator da API pública do Justiça Aberta (CNJ).

Endpoint confirmado em 06/08/2026 lendo o bundle da SPA
(`assets/registry-*.js`, módulo `registry.search`):

    POST https://justicaabertaapi.cnj.jus.br/v1/api/serventias
         ?assignments=&page=1&perPage=20&search=
    Content-Type: application/json
    {"cidade_id": 0, "uf": null, "cns": null}

ATENÇÃO — é POST, não GET. O gateway responde 404 (não 405) a GET nessa
rota; foi essa a causa do "endpoint sumiu" diagnosticado antes. Os
parâmetros de paginação vão na QUERY STRING e os filtros no CORPO JSON.

A SPA envia `Authorization: ""` nas rotas públicas; replicamos.

Resposta: {"data": [ ... ], "meta": {current_page, last_page, per_page, total}}

Campos por registro (nomes reais da API):
    cns, status, situacao_juridica_cartorio{descricao}, denominacao_fantasia,
    denominacao_padrao, responsavel{nome}, natureza, website, endereco,
    numero, bairro, cidade, uf, telefone, email

Uso:
    python -m pipeline.extract_justica_aberta --dump-first   # inspeciona e sai
    python -m pipeline.extract_justica_aberta                # varredura nacional
    python -m pipeline.extract_justica_aberta --por-uf       # shard por UF
    python -m pipeline.extract_justica_aberta --uf SC        # só um estado
    python -m pipeline.extract_justica_aberta --max-pages 3  # teste rápido

Saídas:
    data/raw/api/ja_{escopo}_p{N}.json     páginas brutas (reprocessáveis)
    data/staging/justica_aberta.jsonl      registros canônicos p/ o build
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import requests

from .normalize import UFS, build_record, norm_text

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "api"
STAGING = ROOT / "data" / "staging" / "justica_aberta.jsonl"

BASE_URL = "https://justicaabertaapi.cnj.jus.br/v1/api/serventias"
SPA_ORIGIN = "https://justicaaberta.cnj.jus.br"

RATE_SLEEP = 0.8
TIMEOUT = 90
MAX_RETRIES = 5

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": SPA_ORIGIN,
    "Referer": SPA_ORIGIN + "/produtividade-e-localizacao-de-serventias-extrajudiciais",
    "Authorization": "",          # a SPA manda vazio nas rotas públicas
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0 Safari/537.36"),
}


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def fetch_page(sess: requests.Session, page: int, per_page: int, *,
               assignments: str = "", search: str = "",
               uf: str | None = None, cidade_id: int = 0,
               cns: str | None = None) -> dict | None:
    params = {"assignments": assignments, "page": page,
              "perPage": per_page, "search": search}
    body = {"cidade_id": cidade_id or 0, "uf": uf, "cns": cns}

    for attempt in range(MAX_RETRIES):
        try:
            r = sess.post(BASE_URL, params=params, json=body, timeout=TIMEOUT)
            if r.status_code == 404 and attempt == 0:
                print("  [!] 404 — confirme que a rota ainda aceita POST "
                      "(rode sniff_spa.py para remapear).")
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, json.JSONDecodeError) as exc:
            wait = 2 ** attempt
            print(f"  [retry {attempt + 1}/{MAX_RETRIES}] {exc} — {wait}s")
            time.sleep(wait)
    return None


def unwrap(payload) -> tuple[list[dict], dict]:
    """Devolve (registros, meta) tolerando pequenas variações do envelope."""
    if not isinstance(payload, dict):
        return ([r for r in payload if isinstance(r, dict)] if isinstance(payload, list) else []), {}
    data = payload.get("data")
    if isinstance(data, dict):                      # {"data": {"data": [...]}}
        meta = data.get("meta") or payload.get("meta") or data
        inner = data.get("data")
        if isinstance(inner, list):
            return [r for r in inner if isinstance(r, dict)], meta or {}
    registros = data if isinstance(data, list) else []
    meta = payload.get("meta") or {}
    if not meta:                                    # Laravel às vezes achata na raiz
        meta = {k: payload[k] for k in
                ("current_page", "last_page", "per_page", "total")
                if k in payload}
    return [r for r in registros if isinstance(r, dict)], meta


# --------------------------------------------------------------------------- #
# Mapeamento -> registro canônico
# --------------------------------------------------------------------------- #
CHAVES_CONHECIDAS = {
    "cns", "status", "situacao_juridica_cartorio", "denominacao_fantasia",
    "denominacao_padrao", "responsavel", "natureza", "website", "endereco",
    "numero", "bairro", "cidade", "uf", "telefone", "email", "id",
    "cidade_id", "cep", "latitude", "longitude", "complemento",
}


def map_row(row: dict) -> dict | None:
    resp = row.get("responsavel") or {}
    sit = row.get("situacao_juridica_cartorio") or {}
    denom = row.get("denominacao_fantasia") or row.get("denominacao_padrao")

    natureza = row.get("natureza")
    if isinstance(natureza, (list, tuple)):
        natureza = ", ".join(str(x) for x in natureza if x)
    elif isinstance(natureza, dict):
        natureza = natureza.get("descricao") or natureza.get("nome")

    logradouro = norm_text(row.get("endereco"))
    if logradouro and row.get("numero"):
        logradouro = f"{logradouro}, {row['numero']}"

    extras = {
        "endereco": logradouro or None,
        "bairro": row.get("bairro"),
        "cep": row.get("cep"),
        "telefone": row.get("telefone"),
        "email": row.get("email"),
        "responsavel": resp.get("nome") if isinstance(resp, dict) else resp,
        "latitude": row.get("latitude"),
        "longitude": row.get("longitude"),
        "website": row.get("website"),
        "situacao_juridica": (sit.get("descricao")
                              if isinstance(sit, dict) else sit),
    }

    rec = build_record(
        cns=row.get("cns"),
        denominacao=denom,
        status=row.get("status"),
        municipio=row.get("cidade"),
        uf=row.get("uf"),
        atribuicoes_raw=natureza,
        fonte="justica_aberta_api",
        extras=extras,
    )
    if rec is None:
        return None

    # id interno do CNJ — útil para rebater com as rotas /serventias/{id}
    if row.get("id") is not None:
        rec["id_cnj"] = row["id"]
    return rec


# --------------------------------------------------------------------------- #
# Varredura COMPLETA por UF (15/09/2026)
# --------------------------------------------------------------------------- #
# A paginação da API NÃO tem ordem estável: cada página é um recorte de uma ordenação que muda entre as
# requisições. Paginando de 100 em 100, a coleta de 06/08 trouxe as 16.268 linhas que a API declara — mas só
# 12.430 serventias distintas. 3.838 linhas eram REPETIÇÕES de serventias já vistas, e outras tantas nunca
# vieram: em SC, 689 declaradas e 429 coletadas; o Registro de Imóveis de Gaspar (10.424-0) ficou de fora, e o
# RCPN da mesma cidade (10.780-5) veio três vezes. O `vistos` descartava a repetição em silêncio.
#
# O remédio: pedir a UF INTEIRA numa página só (perPage ≥ total) e CONFERIR distintos == total. Se a API limitar
# o perPage, varre de novo, várias vezes, somando o que faltava, até fechar a conta ou desistir alto.
MAX_PASSADAS = 8


def varrer_completo(sess, out, vistos, *, uf: str, assignments: str, search: str,
                    desconhecidas: Counter) -> tuple[int, int, int, int]:
    """Coleta uma UF inteira e devolve (novos gravados, distintos da UF, total declarado, linhas da página única).

    `total` é o que a API CONTA; as linhas da página única são o que ela LISTA. Em SP (15/09) ela contou 1.680 e
    listou 1.679 mesmo com perPage=1.680 e oito passadas — a diferença é da API, não da paginação. Por isso a
    conferência que importa é distintos == linhas listadas (nada repetido, nada truncado)."""
    sonda = fetch_page(sess, 1, 1, assignments=assignments, search=search, uf=uf)
    time.sleep(RATE_SLEEP)
    if sonda is None:
        print(f"  [!] {uf}: sem resposta na sonda")
        return 0, 0, -1, 0
    _, meta = unwrap(sonda)
    total = int(meta.get("total") or 0)
    if total == 0:
        print(f"[{uf}] total=0")
        return 0, 0, 0, 0

    da_uf: set[str] = set()
    novos = 0

    def absorver(registros: list[dict]) -> None:
        nonlocal novos
        for row in registros:
            for k in row:
                if k not in CHAVES_CONHECIDAS:
                    desconhecidas[k] += 1
            rec = map_row(row)
            if rec is None:
                continue
            da_uf.add(rec["cns_digits"])
            if rec["cns_digits"] not in vistos:
                vistos.add(rec["cns_digits"])
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                novos += 1

    payload = fetch_page(sess, 1, total, assignments=assignments, search=search, uf=uf)
    time.sleep(RATE_SLEEP)
    if payload is None:
        print(f"  [!] {uf}: falha na página única")
        return novos, len(da_uf), total, 0
    (RAW_DIR / f"ja_{uf}_unica_pp{total}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    registros, meta = unwrap(payload)
    listadas = len(registros)
    absorver(registros)
    efetivo = int(meta.get("per_page") or len(registros) or 100)

    passada = 0
    while len(da_uf) < total and passada < MAX_PASSADAS:
        passada += 1
        ultima = max(1, -(-total // efetivo))
        antes = len(da_uf)
        for page in range(1, ultima + 1):
            p = fetch_page(sess, page, efetivo, assignments=assignments, search=search, uf=uf)
            time.sleep(RATE_SLEEP)
            if p is None:
                continue
            (RAW_DIR / f"ja_{uf}_passada{passada}_p{page}_pp{efetivo}.json").write_text(
                json.dumps(p, ensure_ascii=False), encoding="utf-8")
            absorver(unwrap(p)[0])
        print(f"  [{uf}] passada {passada}: {antes} → {len(da_uf)} de {total}")

    marca = "ok" if len(da_uf) == total else (
        f"ok · a API conta {total - len(da_uf)} a mais do que lista" if len(da_uf) >= listadas and listadas < efetivo else "INCOMPLETA")
    print(f"[{uf}] total={total} listadas={listadas} distintos={len(da_uf)} novos={novos} [{marca}]")
    return novos, len(da_uf), total, listadas


# --------------------------------------------------------------------------- #
# Varredura (paginada, a de antes — sujeita à ordem instável; ver varrer_completo)
# --------------------------------------------------------------------------- #
def varrer(sess, out, vistos, *, escopo: str, uf: str | None, per_page: int,
           assignments: str, search: str, max_pages: int | None,
           resume: bool, desconhecidas: Counter) -> int:
    novos_total = 0
    page = 1
    last_page = None

    while True:
        raw_path = RAW_DIR / f"ja_{escopo}_p{page}_pp{per_page}.json"
        if resume and raw_path.exists():
            payload = json.loads(raw_path.read_text(encoding="utf-8"))
        else:
            payload = fetch_page(sess, page, per_page, assignments=assignments,
                                 search=search, uf=uf)
            if payload is None:
                print(f"  [!] {escopo}: falha na página {page}; interrompendo escopo.")
                break
            raw_path.write_text(json.dumps(payload, ensure_ascii=False),
                                encoding="utf-8")
            time.sleep(RATE_SLEEP)

        registros, meta = unwrap(payload)
        if last_page is None:
            last_page = meta.get("last_page")
            total = meta.get("total")
            print(f"[{escopo}] total={total} last_page={last_page} "
                  f"per_page={meta.get('per_page')}")

        novos = 0
        for row in registros:
            for k in row:
                if k not in CHAVES_CONHECIDAS:
                    desconhecidas[k] += 1
            rec = map_row(row)
            if rec and rec["cns_digits"] not in vistos:
                vistos.add(rec["cns_digits"])
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                novos += 1
        novos_total += novos
        print(f"  [{escopo} p{page}/{last_page or '?'}] "
              f"{len(registros)} itens, {novos} novos (acum. {len(vistos)})")

        if not registros:
            break
        page += 1
        if last_page and page > last_page:
            break
        if max_pages and page > max_pages:
            print(f"  [stop] --max-pages {max_pages}")
            break

    return novos_total


def run_completo(*, search="", assignments="", ufs=None, aceitar_incompleto=False) -> None:
    """Todas as UFs, cada uma inteira e conferida contra o total da API. Grava a cobertura e FALHA se faltar."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    STAGING.parent.mkdir(parents=True, exist_ok=True)
    sess = requests.Session()
    sess.headers.update(HEADERS)

    vistos: set[str] = set()
    desconhecidas: Counter = Counter()
    cobertura: dict[str, dict] = {}
    with STAGING.open("w", encoding="utf-8") as out:
        for uf in (ufs or sorted(UFS)):
            _, distintos, total, listadas = varrer_completo(sess, out, vistos, uf=uf, assignments=assignments,
                                                           search=search, desconhecidas=desconhecidas)
            cobertura[uf] = {"total_api": total, "listadas": listadas, "distintos": distintos,
                             "divergencia_api": max(total - listadas, 0)}

    # FALTA de verdade: sem resposta, página truncada (listou tudo o que pedimos e ainda assim ficou abaixo do
    # total) ou repetição dentro da própria página única. A diferença entre o que a API CONTA e o que LISTA,
    # com a página vindo abaixo do perPage, é da API — vai para a cobertura, não derruba a coleta.
    faltam = {uf: c for uf, c in cobertura.items()
              if c["total_api"] < 0 or c["distintos"] < c["listadas"]
              or (c["listadas"] >= c["total_api"] and c["distintos"] < c["total_api"])}
    soma_api = sum(max(c["total_api"], 0) for c in cobertura.values())
    (STAGING.parent / "cobertura.json").write_text(json.dumps(
        {"total_api": soma_api, "distintos": len(vistos), "por_uf": cobertura, "incompletas": sorted(faltam)},
        ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[ok] {len(vistos)} CNS distintos; a API declara {soma_api} → {STAGING}")
    if desconhecidas:
        print("[dica] chaves da API ainda não mapeadas (top 10):")
        for k, c in desconhecidas.most_common(10):
            print(f"       {k} ({c}x)")
    if faltam:
        print(f"[ERRO] cobertura incompleta em {sorted(faltam)}: {faltam}")
        if not aceitar_incompleto:
            sys.exit(2)   # a Action NÃO pode publicar um catálogo com buraco
    print("Agora rode: python -m pipeline.build")


def run(*, search="", assignments="", per_page=100, max_pages=None,
        uf=None, por_uf=False, dump_first=False, resume=True) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    STAGING.parent.mkdir(parents=True, exist_ok=True)

    sess = requests.Session()
    sess.headers.update(HEADERS)

    # ---- sonda: valida rota, perPage e formato --------------------------
    payload = fetch_page(sess, 1, per_page, assignments=assignments,
                         search=search, uf=uf)
    if payload is None:
        sys.exit("Sem resposta da API. Rode sniff_spa.py para remapear o endpoint.")
    registros, meta = unwrap(payload)
    efetivo = meta.get("per_page") or len(registros)
    if efetivo and int(efetivo) < per_page:
        print(f"[info] a API limitou perPage {per_page} -> {efetivo}")
        per_page = int(efetivo)

    if dump_first:
        print("\n--- meta ---")
        print(json.dumps(meta, ensure_ascii=False, indent=2))
        print(f"\n--- registros nesta página: {len(registros)} ---")
        if registros:
            print("\n--- 1º registro bruto ---")
            print(json.dumps(registros[0], ensure_ascii=False, indent=2)[:3000])
            print("\n--- registro canônico ---")
            print(json.dumps(map_row(registros[0]), ensure_ascii=False, indent=2))
            extras = sorted(set(registros[0]) - CHAVES_CONHECIDAS)
            if extras:
                print(f"\n[atenção] chaves não mapeadas: {extras}")
        return

    # ---- varredura ------------------------------------------------------
    vistos: set[str] = set()
    modo = "a" if resume and STAGING.exists() else "w"
    if modo == "a":
        with STAGING.open(encoding="utf-8") as fh:
            vistos = {json.loads(l)["cns_digits"] for l in fh if l.strip()}
        print(f"[resume] {len(vistos)} CNS já em {STAGING.name}")

    escopos = sorted(UFS) if por_uf else [uf]
    desconhecidas: Counter = Counter()
    total_novos = 0

    with STAGING.open(modo, encoding="utf-8") as out:
        for escopo_uf in escopos:
            rotulo = escopo_uf or "BR"
            total_novos += varrer(
                sess, out, vistos, escopo=rotulo, uf=escopo_uf,
                per_page=per_page, assignments=assignments, search=search,
                max_pages=max_pages, resume=resume, desconhecidas=desconhecidas)

    print(f"\n[ok] {total_novos} registros novos → {STAGING}")
    print(f"[ok] {len(vistos)} CNS distintos no total")
    if desconhecidas:
        print("[dica] chaves da API ainda não mapeadas (top 10):")
        for k, c in desconhecidas.most_common(10):
            print(f"       {k} ({c}x)")
    print("Agora rode: python -m pipeline.build")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--search", default="", help="busca textual da API")
    ap.add_argument("--assignments", default="", help='filtro de atribuições, ex.: "[3]"')
    ap.add_argument("--per-page", type=int, default=100)
    ap.add_argument("--max-pages", type=int, default=None)
    ap.add_argument("--uf", default=None, help="restringe a uma UF (ex.: SC)")
    ap.add_argument("--por-uf", action="store_true",
                    help="varre UF a UF (evita paginação profunda; recomendado)")
    ap.add_argument("--dump-first", action="store_true",
                    help="mostra meta + 1º registro + mapeamento e sai")
    ap.add_argument("--no-resume", dest="resume", action="store_false")
    ap.add_argument("--paginado", action="store_true",
                    help="o modo ANTIGO, paginado — sujeito à ordem instável da API (perde serventias)")
    ap.add_argument("--aceitar-incompleto", action="store_true",
                    help="não falha quando alguma UF fica abaixo do total da API (só para diagnóstico)")
    a = ap.parse_args()
    if a.dump_first or a.paginado:
        run(search=a.search, assignments=a.assignments, per_page=a.per_page,
            max_pages=a.max_pages, uf=a.uf, por_uf=a.por_uf,
            dump_first=a.dump_first, resume=a.resume)
    else:
        # o padrão desde 15/09: UF a UF, inteira, conferida
        run_completo(search=a.search, assignments=a.assignments,
                     ufs=[a.uf.upper()] if a.uf else None, aceitar_incompleto=a.aceitar_incompleto)
