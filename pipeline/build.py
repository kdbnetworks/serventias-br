# -*- coding: utf-8 -*-
"""
build.py — consolida tudo que está em data/staging/*.jsonl (+ data/raw/har_records.jsonl)
num repositório consumível:

    data/dist/serventias.csv.gz      a fonte que viaja: o CSV inteiro, comprimido (versionado no git)
    data/dist/manifest.json          o farol: versão, hash, contagens, prazos de defasagem (versionado)
    data/dist/meta.json              estatísticas e proveniência (versionado)
    data/dist/serventias.csv         interoperabilidade geral
    data/dist/serventias.sqlite      banco com índices + FTS5 (busca textual)
    data/dist/serventias.min.json    array compacto p/ apps web (autofill/detector)
    data/dist/uf/{UF}.json           shards por estado (carregamento sob demanda)

Dedupe: chave = cns_digits; registros de múltiplas fontes são fundidos
(campo a campo, preferindo valores não nulos — ver normalize.merge_records).

Só o `.csv.gz`, o `manifest.json` e o `meta.json` entram no git; o resto é
derivado e vai para a release. `--de-csv` refaz sqlite/json/shards a partir
do `.csv.gz`, sem precisar do staging — é assim que a release é montada.
"""
from __future__ import annotations

import csv
import gzip
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import manifesto
from .normalize import make_search_text, merge_records

ROOT = Path(__file__).resolve().parents[1]
STAGING = ROOT / "data" / "staging"
HAR_RECORDS = ROOT / "data" / "raw" / "har_records.jsonl"
DIST = ROOT / "data" / "dist"

# O CSV carrega TUDO que o registro canônico tem, para que `--de-csv` seja
# sem perdas. Quem lê por cabeçalho ignora o que não conhece.
CSV_FIELDS = ["cns", "cns_digits", "denominacao", "status", "status_raw",
              "situacao_juridica", "situacao_juridica_raw", "tipo_registro",
              "municipio", "uf", "atribuicoes", "atribuicoes_raw",
              "endereco", "bairro", "cep", "telefone", "email", "website",
              "responsavel", "comarca", "circunscricao", "codigo_ibge",
              "latitude", "longitude", "id_cnj", "fonte", "coletado_em"]

CSV_GZ = DIST / "serventias.csv.gz"
MANIFESTO = DIST / "manifest.json"

# A amostra fictícia existe só para smoke test do pipeline; incluí-la num build
# real contaminaria a base com 6 CNS inventados. Só entra com --com-amostra.
PADRAO_AMOSTRA = "FICTICIO"


def _iter_inputs(com_amostra: bool = False):
    files = sorted(STAGING.glob("*.jsonl"))
    if not com_amostra:
        descartados = [f for f in files if PADRAO_AMOSTRA in f.name.upper()]
        for f in descartados:
            print(f"[info] ignorando amostra fictícia: {f.name} (use --com-amostra p/ incluir)")
        files = [f for f in files if f not in descartados]
    if HAR_RECORDS.exists():
        files.append(HAR_RECORDS)
    if not files:
        raise SystemExit("Nada em data/staging/. Rode antes ingest_tabular, extract_api ou parse_har.")
    for f in files:
        with f.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)


def _iter_csv_gz(caminho: Path):
    """Reidrata registros canônicos a partir do `.csv.gz` publicado."""
    with gzip.open(caminho, "rt", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            rec = {k: (v if v not in ("", None) else None) for k, v in row.items()}
            rec["atribuicoes"] = [a for a in (row.get("atribuicoes") or "").split("|") if a]
            rec["search_text"] = make_search_text(
                rec["cns_digits"], rec["cns"], rec.get("denominacao"),
                rec.get("municipio"), rec.get("uf"), rec.get("atribuicoes_raw"))
            yield rec


def _escrever_csv(recs: list[dict], regravar_gz: bool = True) -> None:
    linhas = []
    for r in recs:
        row = {k: r.get(k) for k in CSV_FIELDS}
        row["atribuicoes"] = "|".join(r.get("atribuicoes") or [])
        linhas.append(row)

    with (DIST / "serventias.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore", delimiter=";")
        w.writeheader()
        w.writerows(linhas)

    # Na montagem da release o .csv.gz versionado JÁ É o arquivo publicado.
    # Recomprimi-lo noutra máquina (outra zlib) dá outro sha256 para o mesmo
    # conteúdo, e o manifesto passa a mentir sobre o arquivo que vai junto —
    # foi o que as releases de 06/08 e 15/09/2026 publicaram.
    if not regravar_gz:
        return

    # mtime=0 deixa o gzip determinístico: o mesmo dado dá o mesmo sha256,
    # e o manifesto só muda quando o conteúdo muda.
    with (DIST / "serventias.csv").open("rb") as origem, \
            gzip.GzipFile(CSV_GZ, "wb", compresslevel=9, mtime=0) as destino:
        destino.write(origem.read())


def build(com_amostra: bool = False, de_csv: bool = False, force: bool = False) -> None:
    registros: dict[str, dict] = {}
    lidos = 0
    fontes = _iter_csv_gz(CSV_GZ) if de_csv else _iter_inputs(com_amostra)
    for rec in fontes:
        lidos += 1
        key = rec["cns_digits"]
        registros[key] = merge_records(registros[key], rec) if key in registros else rec

    recs = sorted(registros.values(), key=lambda r: (r.get("uf") or "ZZ", r.get("municipio") or "", r["cns_digits"]))
    DIST.mkdir(parents=True, exist_ok=True)
    (DIST / "uf").mkdir(exist_ok=True)

    # A trava vem ANTES de tocar em qualquer arquivo: um build que encolheu
    # demais não deixa nem o sqlite pela metade.
    anterior = manifesto.ler(MANIFESTO)
    if not de_csv and not force:
        provisorio = {"contagens": {"serventias_unicas": len(recs)}}
        motivo = manifesto.conferir_encolhimento(provisorio, anterior)
        if motivo:
            raise SystemExit(motivo)

    # ---------- JSON completo ----------
    (DIST / "serventias.min.json").write_text(
        json.dumps(recs, ensure_ascii=False, separators=(",", ":")), encoding="utf-8", newline="\n")

    # ---------- shards por UF ----------
    por_uf: dict[str, list] = {}
    for r in recs:
        por_uf.setdefault(r.get("uf") or "XX", []).append(r)
    for uf, lst in por_uf.items():
        (DIST / "uf" / f"{uf}.json").write_text(
            json.dumps(lst, ensure_ascii=False, separators=(",", ":")), encoding="utf-8", newline="\n")

    # ---------- CSV (+ .gz, a fonte que viaja) ----------
    _escrever_csv(recs, regravar_gz=not de_csv)

    # ---------- SQLite ----------
    db_path = DIST / "serventias.sqlite"
    db_path.unlink(missing_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE serventias (
            cns_digits TEXT PRIMARY KEY,
            cns TEXT NOT NULL,
            denominacao TEXT,
            status TEXT,
            status_raw TEXT,
            situacao_juridica TEXT,
            situacao_juridica_raw TEXT,
            tipo_registro TEXT,
            municipio TEXT,
            uf TEXT,
            atribuicoes TEXT,          -- flags separadas por |
            atribuicoes_raw TEXT,
            endereco TEXT, bairro TEXT, cep TEXT,
            telefone TEXT, email TEXT, website TEXT, responsavel TEXT,
            comarca TEXT, codigo_ibge TEXT,
            circunscricao TEXT, latitude TEXT, longitude TEXT, id_cnj TEXT,
            search_text TEXT,
            fonte TEXT, coletado_em TEXT
        )""")
    con.executemany(
        """INSERT INTO serventias VALUES (:cns_digits,:cns,:denominacao,:status,:status_raw,
           :situacao_juridica,:situacao_juridica_raw,:tipo_registro,
           :municipio,:uf,:atribuicoes,:atribuicoes_raw,:endereco,:bairro,:cep,:telefone,
           :email,:website,:responsavel,:comarca,:codigo_ibge,
           :circunscricao,:latitude,:longitude,:id_cnj,:search_text,:fonte,:coletado_em)""",
        [{**{k: None for k in CSV_FIELDS + ["search_text"]},
          **{k: (None if v is None else v if isinstance(v, (str, int, float)) else str(v))
             for k, v in r.items()},
          "atribuicoes": "|".join(r.get("atribuicoes") or [])} for r in recs])
    con.execute("CREATE INDEX ix_uf_mun ON serventias(uf, municipio)")
    con.execute("CREATE INDEX ix_status ON serventias(status)")
    con.execute("CREATE INDEX ix_tipo ON serventias(tipo_registro)")
    try:  # busca textual p/ detector/autofill (FTS5 pode não existir em builds mínimos)
        con.execute("""CREATE VIRTUAL TABLE serventias_fts USING fts5(
                         cns_digits UNINDEXED, search_text,
                         content='serventias', content_rowid='rowid')""")
        con.execute("INSERT INTO serventias_fts(rowid, cns_digits, search_text) "
                    "SELECT rowid, cns_digits, search_text FROM serventias")
    except sqlite3.OperationalError:
        print("[aviso] FTS5 indisponível; busca cairá em LIKE.")
    con.commit()
    con.close()

    # ---------- meta ----------
    meta = {
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "linhas_lidas": lidos,
        "serventias_unicas": len(recs),
        "por_uf": dict(sorted(Counter(r.get("uf") or "XX" for r in recs).items())),
        "por_status": dict(Counter(r.get("status") for r in recs).most_common()),
        "por_situacao_juridica": dict(Counter(r.get("situacao_juridica") or "NAO_INFORMADA"
                                              for r in recs).most_common()),
        "por_tipo_registro": dict(Counter(r.get("tipo_registro") or "indefinido"
                                          for r in recs).most_common()),
        "por_atribuicao": dict(Counter(a for r in recs for a in r.get("atribuicoes") or []).most_common()),
        "fontes": dict(Counter(r.get("fonte") for r in recs).most_common()),
    }
    # O meta.json versionado viaja como o .csv.gz: a release não o reescreve.
    # Os JSON saem com LF em qualquer máquina — o git guarda LF, e um sha
    # calculado sobre CRLF no Windows não confere com o checkout da Action.
    if not de_csv or anterior is None or not (DIST / "meta.json").exists():
        (DIST / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")

    # ---------- manifesto ----------
    publicados = {
        "serventias.csv.gz": CSV_GZ,
        "serventias.sqlite": db_path,
        "serventias.min.json": DIST / "serventias.min.json",
        "meta.json": DIST / "meta.json",
    }
    if de_csv and anterior is not None:
        # Montagem da release: o manifesto versionado é a verdade sobre o que
        # veio do git (versão, contagens, csv.gz, meta.json). O sqlite e o
        # min.json nascem aqui e entram com o sha do arquivo que vai subir.
        for nome in ("serventias.sqlite", "serventias.min.json"):
            entrada = (anterior.get("arquivos") or {}).get(nome)
            if entrada is not None:
                entrada["bytes"] = publicados[nome].stat().st_size
                entrada["sha256"] = manifesto.sha256_de(publicados[nome])
        MANIFESTO.write_text(json.dumps(anterior, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    else:
        novo = manifesto.montar(recs, publicados, meta)
        MANIFESTO.write_text(json.dumps(novo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"[ok] manifesto v{novo['versao']} · sha256 do csv.gz {novo['arquivos']['serventias.csv.gz']['sha256'][:12]}…")

    # A trava da release: todo arquivo que o manifesto descreve tem de ser,
    # byte a byte, o que está em disco para subir.
    divergentes = [nome for nome, entrada in ((manifesto.ler(MANIFESTO) or {}).get("arquivos") or {}).items()
                   if nome in publicados and entrada.get("sha256") != manifesto.sha256_de(publicados[nome])]
    if divergentes:
        raise SystemExit(f"O manifesto não descreve o que está em disco: {', '.join(divergentes)}. Nada foi publicado.")

    print(f"[ok] {len(recs)} serventias únicas (de {lidos} linhas) → {DIST}/")
    for k, v in meta["por_atribuicao"].items():
        print(f"     {k:>22}: {v}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Consolida data/staging -> data/dist")
    ap.add_argument("--com-amostra", action="store_true",
                    help="inclui data/staging/*FICTICIO*.jsonl (só p/ teste do pipeline)")
    ap.add_argument("--de-csv", action="store_true",
                    help="parte de data/dist/serventias.csv.gz em vez do staging (montagem da release)")
    ap.add_argument("--force", action="store_true",
                    help="ignora a trava de encolhimento contra o manifesto anterior")
    build(**vars(ap.parse_args()))
