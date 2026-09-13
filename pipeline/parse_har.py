# -*- coding: utf-8 -*-
"""
parse_har.py — descobre o endpoint da API do Justiça Aberta a partir de um HAR.

O portal https://justicaaberta.cnj.jus.br/produtividade-e-localizacao-de-serventias-extrajudiciais
é uma SPA: os dados chegam por XHR/fetch. Como a API pública ainda não tem
documentação, este script automatiza a engenharia reversa:

1. No navegador: F12 → aba Network → filtro "Fetch/XHR" → faça uma busca de
   serventia no site (ex.: escolha UF e município) → clique com o botão
   direito em qualquer requisição → "Save all as HAR with content".
2. Rode:  python -m pipeline.parse_har caminho/arquivo.har
3. O script:
   - identifica as respostas JSON que "parecem" listas de serventias
     (chaves como cns, denominacao, municipio, situacao...);
   - grava os registros já capturados em data/raw/har_records.jsonl;
   - grava o(s) endpoint(s) descoberto(s) em sources.discovered.yaml,
     pronto para virar seu sources.yaml (usado por extract_api.py).
"""
from __future__ import annotations

import base64
import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from .normalize import build_record, fold

RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"
OUT_RECORDS = RAW_DIR / "har_records.jsonl"
OUT_SOURCES = Path(__file__).resolve().parents[1] / "sources.discovered.yaml"

# aliases de campo vistos em sistemas do CNJ/PDPJ (snake, camel, PT/EN)
FIELD_ALIASES = {
    "cns": {"cns", "codigocns", "codigo_cns", "nrcns", "numerocns", "codigoserventia", "cod_cns",
            "cnsnumber", "numcns", "numerodocns"},
    "denominacao": {"denominacao", "nome", "nomeserventia", "descricao", "descricaoserventia",
                    "denominacaoserventia", "nomefantasia", "titulo", "name", "razaosocial"},
    "municipio": {"municipio", "cidade", "localidade", "nomemunicipio", "municipionome",
                  "city", "cityname", "nomecidade", "municipality"},
    "uf": {"uf", "estado", "siglauf", "ufsigla", "sgluf", "state", "stateacronym",
           "stateabbreviation", "siglaestado"},
    "status": {"situacao", "status", "situacaoserventia", "descricaosituacao", "statusserventia",
               "situation", "statusdescription"},
    "atribuicoes": {"atribuicao", "atribuicoes", "atribuicaoserventia", "tiposervico",
                    "tipoatribuicao", "descricaoatribuicao", "competencias",
                    "assignments", "assignment", "assignmentsdescription"},
    "endereco": {"endereco", "logradouro", "enderecocompleto", "address", "street"},
    "bairro": {"bairro", "neighborhood"},
    "cep": {"cep", "zipcode", "postalcode"},
    "telefone": {"telefone", "fone", "telefones", "telefonecontato", "phone", "phonenumber"},
    "email": {"email", "emailcontato", "e_mail", "mail"},
    "responsavel": {"responsavel", "nomeresponsavel", "delegatario", "titular", "nomedelegatario",
                    "responsible", "holder", "responsiblename"},
    "comarca": {"comarca", "nomecomarca"},
    "latitude": {"latitude", "lat"},
    "longitude": {"longitude", "lng", "lon", "long"},
}
_ALIAS_LOOKUP = {alias: canon for canon, aliases in FIELD_ALIASES.items() for alias in aliases}

# chaves usadas para achatar valores aninhados (dict/list) em texto útil
_NESTED_TEXT_KEYS = ("sigla", "acronym", "descricao", "description", "nome", "name",
                     "label", "title", "value")


def _flatten_value(v):
    """Achata dict/list em string legível (ex.: assignments=[{description:...}])."""
    if isinstance(v, dict):
        for k in _NESTED_TEXT_KEYS:
            if isinstance(v.get(k), (str, int, float)):
                return str(v[k])
        return None
    if isinstance(v, list):
        parts = []
        for item in v:
            if isinstance(item, (str, int, float)):
                parts.append(str(item))
            elif isinstance(item, dict):
                flat = _flatten_value(item)
                if flat:
                    parts.append(flat)
        return ", ".join(parts) if parts else None
    return v


def _canon_key(k: str) -> str | None:
    return _ALIAS_LOOKUP.get(fold(k).replace(" ", "").replace("-", "").replace("_", ""))


def _iter_dicts(node):
    """Percorre o JSON e produz todos os dicts (em qualquer profundidade)."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _iter_dicts(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_dicts(v)


def _score_dict(d: dict) -> tuple[int, dict]:
    """Pontua um dict pelo nº de campos de serventia reconhecidos; mapeia-os.

    Também devolve as chaves não reconhecidas em mapped['_desconhecidos']
    (útil p/ evoluir FIELD_ALIASES quando a API mudar).
    """
    mapped, unknown = {}, []
    for k, v in d.items():
        canon = _canon_key(str(k))
        if canon and canon not in mapped:
            flat = _flatten_value(v)
            if flat not in (None, ""):
                mapped[canon] = flat
                continue
        if canon is None and not isinstance(v, (dict, list)):
            unknown.append(str(k))
    # segundo passe: chaves canônicas dentro de dicts aninhados
    # (ex.: {"city": {"name": ..., "state": {"acronym": "SC"}}} -> uf)
    for v in d.values():
        if isinstance(v, dict):
            for nk, nv in v.items():
                canon = _canon_key(str(nk))
                if canon and canon not in mapped:
                    flat = _flatten_value(nv)
                    if flat not in (None, ""):
                        mapped[canon] = flat
    if unknown:
        mapped["_desconhecidos"] = unknown
    score = len([k for k in mapped if k != "_desconhecidos"]) + (3 if "cns" in mapped else 0)
    return score, mapped


def _decode_body(content: dict) -> str | None:
    text = content.get("text")
    if text is None:
        return None
    if content.get("encoding") == "base64":
        try:
            return base64.b64decode(text).decode("utf-8", errors="replace")
        except Exception:
            return None
    return text


def parse_har(har_path: str | Path, min_score: int = 3) -> None:
    har = json.loads(Path(har_path).read_text(encoding="utf-8", errors="replace"))
    entries = har.get("log", {}).get("entries", [])
    print(f"[har] {len(entries)} requisições no arquivo")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    endpoints: list[dict] = []
    n_records = 0

    with OUT_RECORDS.open("w", encoding="utf-8") as fh:
        for e in entries:
            resp = e.get("response", {})
            mime = (resp.get("content", {}).get("mimeType") or "").lower()
            if "json" not in mime:
                continue
            body = _decode_body(resp.get("content", {}))
            if not body:
                continue
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                continue

            hits, samples = 0, Counter()
            for d in _iter_dicts(payload):
                score, mapped = _score_dict(d)
                if score >= min_score and "cns" in mapped:
                    rec = build_record(
                        cns=mapped.get("cns"),
                        denominacao=mapped.get("denominacao"),
                        status=mapped.get("status"),
                        municipio=mapped.get("municipio"),
                        uf=mapped.get("uf"),
                        atribuicoes_raw=mapped.get("atribuicoes"),
                        fonte="justica_aberta_har",
                        extras=mapped,
                    )
                    if rec:
                        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        hits += 1
                        samples.update(k for k in mapped if k != "_desconhecidos")

            if hits:
                n_records += hits
                req = e.get("request", {})
                u = urlsplit(req.get("url", ""))
                endpoints.append({
                    "url": f"{u.scheme}://{u.netloc}{u.path}",
                    "method": req.get("method", "GET"),
                    "query": {q["name"]: q["value"] for q in req.get("queryString", [])},
                    "post_data": (req.get("postData") or {}).get("text"),
                    "registros_no_har": hits,
                    "campos_vistos": dict(samples),
                })
                print(f"[hit] {hits:>5} registros ← {req.get('method','GET')} {u.path}")

    if not endpoints:
        print("[!] Nenhuma resposta JSON com cara de serventia encontrada.")
        print("    Refaça a captura garantindo que a busca no site retornou resultados")
        print("    e que o HAR foi salvo COM conteúdo ('Save all as HAR with content').")
        return

    # agrega por (método, url) e sugere um sources.yaml
    agg: dict[tuple, dict] = {}
    for ep in endpoints:
        key = (ep["method"], ep["url"])
        cur = agg.setdefault(key, {**ep, "exemplos_query": [], "registros_no_har": 0})
        cur["registros_no_har"] = cur.get("registros_no_har", 0) + ep["registros_no_har"]
        if ep["query"] and ep["query"] not in cur["exemplos_query"]:
            cur["exemplos_query"].append(ep["query"])

    doc = {
        "descoberto_de": str(har_path),
        "endpoints": [
            {
                "name": f"justica_aberta_{i}",
                "method": m,
                "url": u,
                "exemplos_query": v["exemplos_query"][:3],
                "post_data_exemplo": v.get("post_data"),
                "registros_no_har": v["registros_no_har"],
                "campos_vistos": v["campos_vistos"],
                # preencha após inspecionar: parâmetros de paginação/UF
                "pagination": {"page_param": "TODO", "size_param": "TODO", "start_page": 0},
                "records_path": "TODO  # ex.: content / resultado.lista / data.items",
            }
            for i, ((m, u), v) in enumerate(sorted(agg.items(), key=lambda kv: -kv[1]["registros_no_har"]))
        ],
    }
    OUT_SOURCES.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"\n[ok] {n_records} registros → {OUT_RECORDS}")
    print(f"[ok] endpoints descobertos → {OUT_SOURCES}")
    print("     Revise pagination/records_path e copie para sources.yaml para coleta completa.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("uso: python -m pipeline.parse_har <arquivo.har>")
    parse_har(sys.argv[1])
