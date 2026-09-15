# -*- coding: utf-8 -*-
"""
A varredura UF a UF (15/09/2026): a paginação da API do Justiça Aberta não tem ordem estável — paginando de 100 em
100, a coleta de 06/08 repetiu 3.838 linhas e perdeu outras tantas (em SC, o Registro de Imóveis de Gaspar). Aqui a
API é falsa e DELIBERADAMENTE instável: cada página sorteia a ordem. A varredura completa tem de fechar a conta.
"""
from __future__ import annotations

import io
import random
from collections import Counter

import pipeline.extract_justica_aberta as ja


def _linha(i: int, uf: str) -> dict:
    return {"id": i, "cns": f"{10000 + i:05d}{i % 10}", "denominacao_padrao": f"Serventia {i}",
            "cidade": "Gaspar", "uf": uf, "status": "Ativo", "natureza": "Registro de Imóveis"}


class ApiInstavel:
    """Conta `total`, lista `listaveis` (a API real contou 1.680 em SP e listou 1.679) e sorteia a ordem a cada página."""

    def __init__(self, total: int, listaveis: int | None = None, teto_per_page: int | None = None):
        self.total, self.teto = total, teto_per_page
        self.linhas = [_linha(i, "SC") for i in range(listaveis if listaveis is not None else total)]
        self.rng = random.Random(42)

    def __call__(self, sess, page, per_page, **kw):
        pp = min(per_page, self.teto) if self.teto else per_page
        embaralhadas = self.linhas[:]
        self.rng.shuffle(embaralhadas)            # a ordem muda a cada requisição
        ini = (page - 1) * pp
        return {"data": embaralhadas[ini:ini + pp],
                "meta": {"total": self.total, "per_page": pp, "last_page": -(-self.total // pp), "current_page": page}}


def _varrer(monkeypatch, tmp_path, api):
    monkeypatch.setattr(ja, "fetch_page", api)
    monkeypatch.setattr(ja, "RATE_SLEEP", 0)
    monkeypatch.setattr(ja, "RAW_DIR", tmp_path)
    out, vistos = io.StringIO(), set()
    return ja.varrer_completo(None, out, vistos, uf="SC", assignments="", search="", desconhecidas=Counter())


def test_pagina_unica_traz_a_uf_inteira_mesmo_com_ordem_instavel(monkeypatch, tmp_path):
    novos, distintos, total, listadas = _varrer(monkeypatch, tmp_path, ApiInstavel(689))
    assert (novos, distintos, total, listadas) == (689, 689, 689, 689)


def test_api_que_limita_o_per_page_e_fechada_por_passadas(monkeypatch, tmp_path):
    # com teto de 100 por página e ordem sorteada, UMA passada perde serventias; as passadas somam até fechar
    _, distintos, total, _ = _varrer(monkeypatch, tmp_path, ApiInstavel(689, teto_per_page=100))
    assert distintos == total == 689


def test_a_api_que_conta_um_a_mais_do_que_lista_nao_e_falta(monkeypatch, tmp_path):
    _, distintos, total, listadas = _varrer(monkeypatch, tmp_path, ApiInstavel(1680, listaveis=1679))
    assert (distintos, total, listadas) == (1679, 1680, 1679)
