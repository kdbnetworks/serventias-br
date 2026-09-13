# -*- coding: utf-8 -*-
"""O manifesto é o contrato com quem consome; o que se testa aqui é o que ele promete."""
import gzip
import hashlib
import json

from pipeline import manifesto


def _rec(cns, status="ATIVADA", uf="SC", atrib=("registro_imoveis",), coletado="2026-08-06T02:17:50+00:00"):
    return {"cns_digits": cns, "cns": f"{cns[:2]}.{cns[2:5]}-{cns[5]}", "status": status,
            "uf": uf, "atribuicoes": list(atrib), "tipo_registro": "serventia",
            "coletado_em": coletado}


def test_manifesto_assina_o_arquivo_e_data_a_versao_pela_coleta(tmp_path):
    gz = tmp_path / "serventias.csv.gz"
    with gzip.GzipFile(gz, "wb", mtime=0) as fh:
        fh.write(b"cns;cns_digits\n12.345-6;123456\n")

    recs = [_rec("123456"), _rec("654321", status="DESATIVATA", uf="PR", atrib=("notas",),
                                  coletado="2026-08-05T23:00:00+00:00")]
    m = manifesto.montar(recs, {"serventias.csv.gz": gz}, {"linhas_lidas": 3})

    assert m["esquema"] == "serventias-br/manifesto@1"
    assert m["versao"] == "2026-08-06"
    assert m["coletado_entre"] == ["2026-08-05T23:00:00+00:00", "2026-08-06T02:17:50+00:00"]
    assert m["arquivos"]["serventias.csv.gz"]["sha256"] == hashlib.sha256(gz.read_bytes()).hexdigest()
    assert m["arquivos"]["serventias.csv.gz"]["bytes"] == gz.stat().st_size
    assert m["arquivos"]["serventias.csv.gz"]["url"].endswith("/releases/download/v2026-08-06/serventias.csv.gz")
    assert m["contagens"] == {
        "serventias_unicas": 2, "linhas_lidas": 3,
        "registro_imoveis": 1, "registro_imoveis_ativos": 1,
        "por_status": {"ATIVADA": 1, "DESATIVATA": 1},
        "por_tipo_registro": {"serventia": 2},
        "por_uf": {"PR": 1, "SC": 1},
    }
    assert m["defasagem"] == {"aviso_dias": 60, "alerta_dias": 120}
    json.dumps(m)  # serializável de ponta a ponta


def test_trava_de_encolhimento():
    anterior = {"contagens": {"serventias_unicas": 12430}}
    assert manifesto.conferir_encolhimento({"contagens": {"serventias_unicas": 12500}}, anterior) is None
    assert manifesto.conferir_encolhimento({"contagens": {"serventias_unicas": 10000}}, anterior) is None
    motivo = manifesto.conferir_encolhimento({"contagens": {"serventias_unicas": 9000}}, anterior)
    assert motivo and "9000" in motivo and "12430" in motivo
    assert manifesto.conferir_encolhimento({"contagens": {"serventias_unicas": 40}}, None) is None


def test_ler_tolera_ausencia_e_json_quebrado(tmp_path):
    assert manifesto.ler(tmp_path / "nao-existe.json") is None
    quebrado = tmp_path / "manifest.json"
    quebrado.write_text("{", encoding="utf-8")
    assert manifesto.ler(quebrado) is None
