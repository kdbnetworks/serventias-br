# -*- coding: utf-8 -*-
from pipeline.normalize import (build_record, clean_cns, find_cns_in_text,
                                format_cns, merge_records, normalize_status,
                                parse_atribuicoes)


def test_cns_limpeza_e_formato():
    assert clean_cns("12.345-6") == "123456"
    assert clean_cns(" 123456 ") == "123456"
    assert clean_cns("9876-1") == "098761"      # zero à esquerda restaurado
    assert clean_cns("1234567") is None          # 7 dígitos = inválido
    assert clean_cns("") is None and clean_cns(None) is None
    assert format_cns("123456") == "12.345-6"


def test_detector_de_cns_em_texto():
    txt = ("CERTIDÃO DE MATRÍCULA — Ofício de Registro de Imóveis, "
           "CNS: 12.345-6, Livro 2. Ver também CNS 04.567-8.")
    assert find_cns_in_text(txt) == ["123456", "045678"]


def test_status():
    assert normalize_status("Ativada") == ("ATIVADA", "Ativada")
    assert normalize_status("VAGA")[0] == "VAGA"
    assert normalize_status("provisória")[0] == "OUTRO"
    assert normalize_status(None)[0] == "DESCONHECIDO"


def test_atribuicoes():
    flags = parse_atribuicoes("Registro de Imóveis, Títulos e Documentos e "
                              "Civil das Pessoas Jurídicas")
    assert "registro_imoveis" in flags
    assert "titulos_documentos" in flags
    assert "registro_civil_pj" in flags
    assert "protesto" not in flags
    assert "registro_imoveis" in parse_atribuicoes("Registrador Imobiliário")


def test_build_e_merge():
    a = build_record(cns="12.345-6", denominacao="1º RI de Itajaí",
                     status="Ativada", municipio="Itajaí", uf="sc",
                     atribuicoes_raw="Registro de Imóveis", fonte="painel")
    assert a["uf"] == "SC" and a["atribuicoes"] == ["registro_imoveis"]
    assert "itajai" in a["search_text"]          # sem acento p/ busca
    b = build_record(cns="123456", denominacao=None, status=None,
                     municipio=None, uf=None, fonte="har",
                     extras={"telefone": "(47) 3333-0000"})
    m = merge_records(b, a)
    assert m["denominacao"] == "1º RI de Itajaí" and m["telefone"] == "(47) 3333-0000"
    assert set(m["fonte"].split(" + ")) == {"painel", "har"}
    assert build_record(cns="abc", denominacao="x", fonte="y") is None
