# -*- coding: utf-8 -*-
"""O build de ponta a ponta sobre um staging pequeno, e a volta pelo csv.gz."""
import gzip
import hashlib
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
    # o git guarda LF; um sha calculado sobre CRLF (Windows) não confere com o checkout da Action
    assert b"\r" not in (dist / "meta.json").read_bytes()
    assert b"\r" not in (dist / "manifest.json").read_bytes()

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
    antes, depois = json.loads(manifesto_publicado), json.loads((dist / "manifest.json").read_text(encoding="utf-8"))
    for chave in ("versao", "gerado_em", "contagens"):
        assert depois[chave] == antes[chave]
    for nome in ("serventias.csv.gz", "meta.json"):
        assert depois["arquivos"][nome] == antes["arquivos"][nome]


def test_de_csv_publica_o_csv_gz_versionado_e_o_manifesto_confere_com_o_disco(tmp_path, monkeypatch):
    # A release roda noutra máquina, com outra zlib: o mesmo CSV comprime para outros bytes. Aqui isso é simulado
    # recomprimindo o .csv.gz num nível diferente, como se fosse o que o git trouxe de um build feito em outro lugar.
    dist = _staging(tmp_path, monkeypatch)
    b.build()
    conteudo = gzip.decompress((dist / "serventias.csv.gz").read_bytes())
    with gzip.GzipFile(dist / "serventias.csv.gz", "wb", compresslevel=1, mtime=0) as fh:
        fh.write(conteudo)
    versionado = (dist / "serventias.csv.gz").read_bytes()
    m = json.loads((dist / "manifest.json").read_text(encoding="utf-8"))
    m["arquivos"]["serventias.csv.gz"]["sha256"] = hashlib.sha256(versionado).hexdigest()
    (dist / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    b.build(de_csv=True)

    assert (dist / "serventias.csv.gz").read_bytes() == versionado
    final = json.loads((dist / "manifest.json").read_text(encoding="utf-8"))
    for nome, entrada in final["arquivos"].items():
        assert entrada["sha256"] == hashlib.sha256((dist / nome).read_bytes()).hexdigest(), nome


def test_de_csv_recusa_manifesto_que_nao_descreve_o_csv_gz(tmp_path, monkeypatch):
    dist = _staging(tmp_path, monkeypatch)
    b.build()
    m = json.loads((dist / "manifest.json").read_text(encoding="utf-8"))
    m["arquivos"]["serventias.csv.gz"]["sha256"] = "0" * 64
    (dist / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(SystemExit) as ex:
        b.build(de_csv=True)
    assert "serventias.csv.gz" in str(ex.value)


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
