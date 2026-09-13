# -*- coding: utf-8 -*-
"""
API de consumo do dataset — pensada para apps de regularização fundiária:
autopreenchimento de formulários, detecção de serventia competente e
validação de CNS informado pelo usuário.

    uvicorn api.main:app --reload

Endpoints:
    GET /serventias                 filtros: uf, municipio, atribuicao, status, q, limit
    GET /serventias/{cns}           lookup direto (aceita '12.345-6' ou '123456')
    GET /detect?q=...               autocomplete/detector p/ formulários
    GET /detect/registro-imoveis    atalho REURB: RIs ativos de um município
    GET /meta                       estatísticas do build
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from pipeline.normalize import clean_cns, find_cns_in_text, make_search_text  # noqa: E402

DB = Path(__file__).resolve().parents[1] / "data" / "dist" / "serventias.sqlite"
app = FastAPI(title="serventias-br", version="0.1.0",
              description="Serventias extrajudiciais do Brasil (fonte primária: CNJ/Justiça Aberta)")


def _con() -> sqlite3.Connection:
    if not DB.exists():
        raise HTTPException(503, "Dataset ainda não construído. Rode `make build`.")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["atribuicoes"] = [a for a in (d.get("atribuicoes") or "").split("|") if a]
    d.pop("search_text", None)
    return d


@app.get("/serventias")
def listar(uf: str | None = None,
           municipio: str | None = None,
           atribuicao: str | None = Query(None, description="ex.: registro_imoveis, notas, protesto"),
           status: str | None = Query(None, description="ex.: ATIVADA"),
           q: str | None = Query(None, description="busca textual livre"),
           limit: int = Query(50, le=500)):
    sql = "SELECT * FROM serventias WHERE 1=1"
    args: list = []
    if uf:
        sql += " AND uf = ?"; args.append(uf.upper())
    if municipio:
        sql += " AND lower(municipio) LIKE ?"; args.append(f"%{municipio.lower()}%")
    if atribuicao:
        sql += " AND '|'||atribuicoes||'|' LIKE ?"; args.append(f"%|{atribuicao.lower()}|%")
    if status:
        sql += " AND status = ?"; args.append(status.upper())
    if q:
        sql += " AND search_text LIKE ?"; args.append(f"%{make_search_text(q)}%")
    sql += " ORDER BY uf, municipio, cns_digits LIMIT ?"; args.append(limit)
    with _con() as con:
        return [_row(r) for r in con.execute(sql, args)]


@app.get("/serventias/{cns}")
def por_cns(cns: str):
    digits = clean_cns(cns)
    if digits is None:
        raise HTTPException(400, "CNS deve conter 6 dígitos (ex.: 12.345-6).")
    with _con() as con:
        r = con.execute("SELECT * FROM serventias WHERE cns_digits = ?", (digits,)).fetchone()
    if not r:
        raise HTTPException(404, f"CNS {digits} não encontrado no dataset.")
    return _row(r)


@app.get("/detect")
def detect(q: str, uf: str | None = None, limit: int = Query(10, le=50)):
    """Detector p/ autofill: aceita CNS colado, trecho de certidão ou nome parcial."""
    # 1) há um CNS embutido no texto? (ex.: usuário colou cabeçalho de certidão)
    for digits in find_cns_in_text(q) or ([clean_cns(q)] if clean_cns(q) else []):
        with _con() as con:
            r = con.execute("SELECT * FROM serventias WHERE cns_digits = ?", (digits,)).fetchone()
        if r:
            return {"match": "cns", "resultados": [_row(r)]}
    # 2) busca textual (FTS5 se disponível, senão LIKE)
    needle = make_search_text(q)
    with _con() as con:
        try:
            rows = con.execute(
                """SELECT s.* FROM serventias_fts f
                   JOIN serventias s ON s.rowid = f.rowid
                   WHERE serventias_fts MATCH ? {} LIMIT ?"""
                .format("AND s.uf = ?" if uf else ""),
                ([" ".join(t + "*" for t in re.findall(r"\w+", needle))]
                 + ([uf.upper()] if uf else []) + [limit])).fetchall()
        except sqlite3.OperationalError:
            rows = con.execute(
                "SELECT * FROM serventias WHERE search_text LIKE ? {} LIMIT ?"
                .format("AND uf = ?" if uf else ""),
                ([f"%{needle}%"] + ([uf.upper()] if uf else []) + [limit])).fetchall()
    return {"match": "texto", "resultados": [_row(r) for r in rows]}


@app.get("/detect/registro-imoveis")
def registro_imoveis(municipio: str, uf: str):
    """Atalho REURB: quais Registros de Imóveis (ativos) atendem o município.

    Nota: quando houver mais de um RI na mesma cidade, a competência depende
    da circunscrição imobiliária — o app deve pedir confirmação ao usuário.
    """
    res = listar(uf=uf, municipio=municipio, atribuicao="registro_imoveis",
                 status="ATIVADA", q=None, limit=50)
    return {"municipio": municipio, "uf": uf.upper(), "quantidade": len(res),
            "atencao_circunscricao": len(res) > 1, "resultados": res}


@app.get("/meta")
def meta():
    p = DB.parent / "meta.json"
    if not p.exists():
        raise HTTPException(503, "meta.json ausente — rode `make build`.")
    return json.loads(p.read_text(encoding="utf-8"))
