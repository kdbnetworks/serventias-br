# -*- coding: utf-8 -*-
"""
ingest_tabular.py — ingere CSV/XLSX exportados manualmente.

Fontes típicas:
  * "Painel de Dados Estatísticos das Serventias Extrajudiciais" (CNJ/Qlik)
    → exporta .csv/.xlsx com CNS, denominação, situação, município, UF,
      atribuições — funciona HOJE, sem depender de API.
  * Planilhas das Corregedorias estaduais (CGJ-SC, CGJ-SP etc.).

Uso:
    python -m pipeline.ingest_tabular data/raw/painel_cnj.xlsx
    python -m pipeline.ingest_tabular arquivo.csv --fonte cgj_sc

O mapeamento de colunas é heurístico (aliases abaixo); ajuste COLUMN_ALIASES
se sua planilha usar cabeçalhos diferentes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .normalize import build_record, fold

ROOT = Path(__file__).resolve().parents[1]
STAGING = ROOT / "data" / "staging"

COLUMN_ALIASES = {
    "cns": ["cns", "codigo cns", "cod cns", "n cns", "numero cns", "codigo da serventia"],
    "denominacao": ["denominacao", "nome da serventia", "serventia", "nome",
                    "denominacao da serventia", "descricao da serventia", "descricao"],
    "status": ["situacao", "status", "situacao da serventia"],
    "municipio": ["municipio", "cidade", "localidade"],
    "uf": ["uf", "estado", "sigla uf"],
    "atribuicoes": ["atribuicoes", "atribuicao", "atribuicoes da serventia",
                    "tipo de servico", "tipo de serventia", "competencias", "especialidade"],
    "endereco": ["endereco", "logradouro"],
    "bairro": ["bairro"],
    "cep": ["cep"],
    "telefone": ["telefone", "fone", "telefones"],
    "email": ["email", "e mail", "e-mail"],
    "responsavel": ["responsavel", "delegatario", "titular", "nome do responsavel"],
    "comarca": ["comarca"],
    "codigo_ibge": ["codigo ibge", "cod ibge", "ibge", "codigo municipio ibge"],
}


def _norm_header(h: str) -> str:
    return fold(h).replace("_", " ").replace("-", " ").strip()


def _map_columns(df: pd.DataFrame) -> dict[str, str]:
    headers = {_norm_header(c): c for c in df.columns}
    mapping = {}
    for canon, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in headers:
                mapping[canon] = headers[alias]
                break
    return mapping


def _read(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(path, dtype=str)
    # CSVs do CNJ costumam vir em ';' + latin-1; tenta combinações comuns
    for sep in (";", ","):
        for enc in ("utf-8-sig", "latin-1"):
            try:
                df = pd.read_csv(path, dtype=str, sep=sep, encoding=enc)
                if df.shape[1] > 1:
                    return df
            except Exception:
                continue
    raise SystemExit(f"Não consegui ler {path} como CSV (tentei ; e , em utf-8/latin-1).")


def ingest(path: str | Path, fonte: str | None = None) -> Path:
    path = Path(path)
    df = _read(path).fillna("")
    mapping = _map_columns(df)
    if "cns" not in mapping:
        raise SystemExit(
            f"Coluna de CNS não identificada em {path.name}.\n"
            f"Cabeçalhos encontrados: {list(df.columns)}\n"
            f"Adicione o alias correto em COLUMN_ALIASES['cns']."
        )
    print(f"[map] {path.name}: {mapping}")

    fonte = fonte or f"tabular:{path.stem}"
    out_path = STAGING / f"{path.stem}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ok = descartados = 0
    with out_path.open("w", encoding="utf-8") as out:
        for _, row in df.iterrows():
            get = lambda k: row[mapping[k]] if k in mapping else None
            rec = build_record(
                cns=get("cns"),
                denominacao=get("denominacao"),
                status=get("status"),
                municipio=get("municipio"),
                uf=get("uf"),
                atribuicoes_raw=get("atribuicoes"),
                fonte=fonte,
                extras={k: get(k) for k in
                        ("endereco", "bairro", "cep", "telefone", "email",
                         "responsavel", "comarca", "codigo_ibge")},
            )
            if rec:
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                ok += 1
            else:
                descartados += 1

    print(f"[ok] {ok} registros ({descartados} sem CNS válido) → {out_path}")
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("arquivo", help="CSV ou XLSX exportado da fonte")
    ap.add_argument("--fonte", default=None, help="rótulo da origem (ex.: painel_cnj)")
    args = ap.parse_args()
    ingest(args.arquivo, args.fonte)
