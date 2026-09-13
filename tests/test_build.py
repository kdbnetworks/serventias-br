# -*- coding: utf-8 -*-
"""O build de ponta a ponta sobre um staging pequeno, e a volta pelo csv.gz."""
import gzip
import json

import pytest

from pipeline import build as b
from pipeline.normalize import build_record


def _staging(tmp_path, monkeypatch, quantos=5):
    dist = tmp_path / "dist"
    staging = tmp_path / "staging"
    staging.mkdir()
    monkeypatch.setattr(b, "DIST", dist)
    monkeypatch.setattr(b, "STAGING", staging)
    monkeypatch.setattr(b, "HAR_RECORDS", tmp_path / "nao-existe.jsonl")
    monkeypatch.setattr(b, "CSV_GZ", dist / "serventias.csv.gz")
    monkeypatch.setattr(b, "MANIFESTO", dist / "manifest.json")

    with (staging / "teste.jsonl").open("w", encoding="utf-8") as fh:
        for i in range(1, quantos + 1):
            rec = build_record(cns=str(i).zfill(6), denominacao=f"{i}º Registro de Imóveis de Itajaí",
                               status="Ativo", municipio="Itajaí", uf="SC",
                               atribuicoes_raw="Registro de Imóveis", fonte="teste",
                               extras={"latitude": -26.9, "longitude": -48.66})
            rec["id_cnj"] = 1000 + i
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return dist


def test_build_gera_csv_gz_manifesto_e_derivados(tmp_path, monkeypatch):
    dist = _staging(tmp_path, monkeypatch)
    b.build()

    assert (dist / "serventias.csv.gz").exists()
    assert (dist / "serventias.sqlite").exists()
    assert (dist / "uf" / "SC.json").exists()

    m = json.loads((dist / "manifest.json").read_text(encoding="utf-8"))
    assert m["contagens"]["serventias_unicas"] == 5
    assert m["contagens"]["registro_imoveis_ativos"] == 5
    assert set(m["arquivos"]) == {"serventias.csv.gz", "serventias.sqlite", "serventias.min.json", "meta.json"}

    with gzip.open(dist / "serventias.csv.gz", "rt", encoding="utf-8") as fh:
        cabecalho = fh.readline().strip().split(";")
    assert cabecalho == b.CSV_FIELDS
    assert "latitude" in cabecalho and "id_cnj" in cabecalho


def test_csv_gz_e_deterministico(tmp_path, monkeypatch):
    dist = _staging(tmp_path, monkeypatch)
    b.build()
    primeiro = (dist / "serventias.csv.gz").read_bytes()
    b.build()
    assert (dist / "serventias.csv.gz").read_bytes() == primeiro


def test_de_csv_reconstroi_sem_staging_e_preserva_o_manifesto(tmp_path, monkeypatch):
    dist = _staging(tmp_path, monkeypatch)
    b.build()
    manifesto_publicado = (dist / "manifest.json").read_text(encoding="utf-8")
    (dist / "serventias.sqlite").unlink()
    (dist / "serventias.min.json").unlink()

    b.build(de_csv=True)

    assert (dist / "serventias.sqlite").exists()
    recs = json.loads((dist / "serventias.min.json").read_text(encoding="utf-8"))
    assert len(recs) == 5
    assert recs[0]["atribuicoes"] == ["registro_imoveis"]
    assert "itajai" in recs[0]["search_text"]
    assert recs[0]["latitude"] == "-26.9"
    assert (dist / "manifest.json").read_text(encoding="utf-8") == manifesto_publicado


def test_encolhimento_aborta_antes_de_escrever(tmp_path, monkeypatch):
    dist = _staging(tmp_path, monkeypatch, quantos=10)
    b.build()
    sqlite_antes = (dist / "serventias.sqlite").stat().st_mtime_ns

    staging = tmp_path / "staging"
    linhas = (staging / "teste.jsonl").read_text(encoding="utf-8").splitlines()
    (staging / "teste.jsonl").write_text("\n".join(linhas[:5]) + "\n", encoding="utf-8")

    with pytest.raises(SystemExit) as ex:
        b.build()
    assert "queda" in str(ex.value)
    assert (dist / "serventias.sqlite").stat().st_mtime_ns == sqlite_antes

    b.build(force=True)
    m = json.loads((dist / "manifest.json").read_text(encoding="utf-8"))
    assert m["contagens"]["serventias_unicas"] == 5
