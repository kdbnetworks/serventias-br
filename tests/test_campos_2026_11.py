# -*- coding: utf-8 -*-
"""Os campos que a API do Justiça Aberta passou a mandar em 11/2026, e os que deixou de mandar.

O registro abaixo é o formato real conferido em 25/11/2026 (SC), com os dados de uma
serventia de Itajaí: sem `id`, sem coordenadas, com `tipo_cartorio`, `substitutos` em
texto e a comarca vazia."""
import csv
import gzip
import json

from pipeline import build as b
from pipeline import extract_justica_aberta as ja
from pipeline.normalize import build_record, merge_records, normalize_tipo_cartorio, parse_substitutos

LINHA_11_2026 = {
    "denominacao_padrao": "Notas, Protesto de Títulos",
    "cns": "104372",
    "denominacao_fantasia": "1º TABELIONATO DE NOTAS E PROTESTOS DE ITAJAÍ",
    "tipo_cartorio": "PRIVATIZADO",
    "telefone": "4733442445",
    "website": "www.tabelionatoitajai.com.br",
    "email": "contato@cartorioitajai.com.br",
    "cep": "88301420",
    "endereco": "Rua XV de Novembro",
    "bairro": "Centro",
    "numero": "173",
    "natureza": "Notas | Protesto de Títulos",
    "cidade": "Itajaí",
    "uf": "SC",
    "cidade_comarca": "",
    "cidade_comarca_uf": "",
    "situacao_juridica_cartorio": {"descricao": "PROVIDO", "data_decisao": "2010-07-25"},
    "responsavel": {"nome": "Gilmara Vanderlinde Medeiros D Avila", "data_ingresso": "2010-02-08",
                    "situacao_juridica_responsavel": "Titular"},
    "substitutos": "Gustavo Vanderlinde Medeiros | Ana Souza | Gustavo Vanderlinde Medeiros",
    "status": "Ativo",
}


def test_tipo_cartorio():
    assert normalize_tipo_cartorio("PRIVATIZADO") == "PRIVATIZADO"
    assert normalize_tipo_cartorio(" oficializado ") == "OFICIALIZADO"
    assert normalize_tipo_cartorio("Público") == "PUBLICO"
    assert normalize_tipo_cartorio("misto") == "OUTRO"
    assert normalize_tipo_cartorio("") is None and normalize_tipo_cartorio(None) is None


def test_substitutos_separa_apara_e_tira_repeticao():
    assert parse_substitutos("A Silva | B  Souza | a silva") == ["A Silva", "B Souza"]
    assert parse_substitutos("") == [] and parse_substitutos(None) == []
    assert parse_substitutos(["X", "Y", "X"]) == ["X", "Y"]


def test_mapeamento_do_formato_de_11_2026():
    rec = ja.map_row(LINHA_11_2026)
    assert rec["cns"] == "10.437-2"
    assert rec["tipo_cartorio"] == "PRIVATIZADO"
    assert rec["substitutos"] == ["Gustavo Vanderlinde Medeiros", "Ana Souza"]
    # a comarca vazia não cria campo; quando vier, vai para o `comarca` canônico
    assert "comarca" not in rec and "comarca_uf" not in rec
    assert "id_cnj" not in rec and "latitude" not in rec


def test_a_comarca_quando_vem():
    rec = ja.map_row({**LINHA_11_2026, "cidade_comarca": "Itajaí", "cidade_comarca_uf": "sc"})
    assert rec["comarca"] == "Itajaí" and rec["comarca_uf"] == "SC"


def test_os_quatro_estao_entre_as_conhecidas_e_o_que_sumiu_e_dito():
    assert set(LINHA_11_2026) <= ja.CHAVES_CONHECIDAS
    ja.CHAVES_VISTAS.clear()
    ja.CHAVES_VISTAS.update(LINHA_11_2026.keys())
    try:
        assert {"id", "latitude", "longitude"} <= set(ja.chaves_que_sumiram())
    finally:
        ja.CHAVES_VISTAS.clear()


def test_merge_junta_os_substitutos_sem_repetir():
    a = build_record(cns="104372", denominacao="X", extras={"substitutos": "A | B"})
    c = build_record(cns="104372", denominacao="X", extras={"substitutos": "B | C"})
    assert merge_records(a, c)["substitutos"] == ["A", "B", "C"]


def _preparar(tmp_path, monkeypatch, recs):
    dist = tmp_path / "dist"
    staging = tmp_path / "staging"
    staging.mkdir(exist_ok=True)
    for nome, valor in (("DIST", dist), ("STAGING", staging), ("HAR_RECORDS", tmp_path / "nao.jsonl"),
                        ("CSV_GZ", dist / "serventias.csv.gz"), ("MANIFESTO", dist / "manifest.json")):
        monkeypatch.setattr(b, nome, valor)
    with (staging / "ja.jsonl").open("w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return dist


def _linhas(dist):
    with gzip.open(dist / "serventias.csv.gz", "rt", encoding="utf-8", newline="") as fh:
        return {r["cns_digits"]: r for r in csv.DictReader(fh, delimiter=";")}


def test_csv_leva_os_campos_novos_e_volta_sem_perda(tmp_path, monkeypatch):
    dist = _preparar(tmp_path, monkeypatch, [ja.map_row(LINHA_11_2026)])
    b.build(force=True)
    linha = _linhas(dist)["104372"]
    assert linha["tipo_cartorio"] == "PRIVATIZADO"
    assert linha["substitutos"] == "Gustavo Vanderlinde Medeiros|Ana Souza"
    assert "comarca_uf" in linha

    volta = {r["cns_digits"]: r for r in b._iter_csv_gz(dist / "serventias.csv.gz")}
    assert volta["104372"]["substitutos"] == ["Gustavo Vanderlinde Medeiros", "Ana Souza"]

    meta = json.loads((dist / "meta.json").read_text(encoding="utf-8"))
    assert meta["por_tipo_cartorio"] == {"PRIVATIZADO": 1}


def test_a_coleta_sem_id_e_coordenadas_herda_da_anterior(tmp_path, monkeypatch):
    # a coleta antiga: com id_cnj e coordenadas
    antiga = ja.map_row(LINHA_11_2026)
    antiga.update({"id_cnj": "3170", "latitude": "-26.9", "longitude": "-48.66"})
    dist = _preparar(tmp_path, monkeypatch, [antiga])
    b.build(force=True)

    # a coleta nova: a API não manda mais nenhum dos três, e mudou o telefone
    nova = ja.map_row({**LINHA_11_2026, "telefone": "4730000000"})
    _preparar(tmp_path, monkeypatch, [nova])
    b.build(force=True)

    linha = _linhas(dist)["104372"]
    assert (linha["id_cnj"], linha["latitude"], linha["longitude"]) == ("3170", "-26.9", "-48.66")
    assert linha["telefone"] == "4730000000"      # o que a fonte manda vence
    meta = json.loads((dist / "meta.json").read_text(encoding="utf-8"))
    assert meta["herdados_da_coleta_anterior"] == 3


def test_valor_novo_da_fonte_vence_a_heranca(tmp_path, monkeypatch):
    antiga = ja.map_row(LINHA_11_2026)
    antiga["latitude"] = "-26.9"
    dist = _preparar(tmp_path, monkeypatch, [antiga])
    b.build(force=True)

    nova = ja.map_row(LINHA_11_2026)
    nova["latitude"] = "-27.0"
    _preparar(tmp_path, monkeypatch, [nova])
    b.build(force=True)
    assert _linhas(dist)["104372"]["latitude"] == "-27.0"
