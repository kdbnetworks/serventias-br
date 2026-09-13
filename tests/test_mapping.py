# -*- coding: utf-8 -*-
"""Mapeamento heurístico contra o formato real da API (chaves EN + aninhadas)."""
from pipeline.parse_har import _flatten_value, _score_dict


def test_mapeamento_registro_estilo_api_real():
    row = {
        "id": 7,
        "cns": "12.345-6",
        "name": "1º Ofício de Registro de Imóveis de Itajaí",
        "situation": "Ativada",
        "city": {"name": "Itajaí", "state": {"acronym": "SC"}},
        "assignments": [{"id": 1, "description": "Registro de Imóveis"},
                        {"id": 5, "description": "Títulos e Documentos"}],
        "zipCode": "88300-000",
        "phone": "(47) 3000-0000",
        "responsible": "Fulana de Tal",
    }
    score, m = _score_dict(row)
    assert m["cns"] == "12.345-6"
    assert m["denominacao"].startswith("1º Ofício")
    assert m["status"] == "Ativada"
    assert m["municipio"] == "Itajaí"
    assert m["uf"] == "SC"                       # veio de city.state.acronym
    assert "Registro de Imóveis" in m["atribuicoes"]
    assert m["cep"] == "88300-000" and m["telefone"].startswith("(47)")
    assert m["responsavel"] == "Fulana de Tal"
    assert m["_desconhecidos"] == ["id"]
    assert score >= 8


def test_flatten():
    assert _flatten_value({"acronym": "SC"}) == "SC"
    assert _flatten_value([{"description": "A"}, {"description": "B"}]) == "A, B"
    assert _flatten_value(["x", "y"]) == "x, y"
    assert _flatten_value("plain") == "plain"
    assert _flatten_value({"foo": 1}) is None
