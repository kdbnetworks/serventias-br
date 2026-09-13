# -*- coding: utf-8 -*-
"""
manifesto.py — o farol do dataset.

O `manifest.json` é o arquivo que uma aplicação lê para saber em que pé está
o catálogo publicado: de quando é a coleta, qual o hash do CSV, quantas
serventias vieram, e a partir de quantos dias vale a pena avisar o usuário
de que a cópia local envelheceu. Quem consome o dataset (o pacote
`serventias-laravel`, por exemplo) compara o manifesto remoto com o que tem
gravado e decide sozinho se está em dia.

O manifesto é gerado pelo `build` e publicado junto com os arquivos de
`data/dist/` em cada release. A URL estável é

    https://github.com/kdbnetworks/serventias-br/releases/latest/download/manifest.json
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ESQUEMA = "serventias-br/manifesto@1"
REPO = "https://github.com/kdbnetworks/serventias-br"
URL_MANIFESTO = f"{REPO}/releases/latest/download/manifest.json"

FONTE = {
    "nome": "justica_aberta_api",
    "orgao": "Conselho Nacional de Justiça — Corregedoria Nacional de Justiça",
    "portal": "https://justicaaberta.cnj.jus.br/produtividade-e-localizacao-de-serventias-extrajudiciais",
    "endpoint": "https://justicaabertaapi.cnj.jus.br/v1/api/serventias",
    "metodo": "POST",
}

# Sugestão para quem consome: aos 60 dias vale avisar, aos 120 vale alertar.
# O CNJ atualiza o cadastro continuamente, mas o que muda de um mês para o
# outro é pequeno (provimentos, vacâncias, desativações). Cada aplicação pode
# apertar ou afrouxar esses prazos.
DEFASAGEM = {"aviso_dias": 60, "alerta_dias": 120}


def sha256_de(caminho: Path) -> str:
    h = hashlib.sha256()
    with Path(caminho).open("rb") as fh:
        for bloco in iter(lambda: fh.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _datas_de_coleta(recs: list[dict]) -> tuple[str | None, str | None]:
    datas = sorted(str(r["coletado_em"]) for r in recs if r.get("coletado_em"))
    if not datas:
        return None, None
    return datas[0], datas[-1]


def montar(recs: list[dict], arquivos: dict[str, Path], meta: dict) -> dict:
    """Monta o manifesto a partir dos registros consolidados e dos arquivos gerados.

    `arquivos` mapeia o nome publicado (ex.: "serventias.csv.gz") para o
    caminho em disco; cada um entra com tamanho e sha256.
    """
    primeira, ultima = _datas_de_coleta(recs)
    versao = (ultima or datetime.now(timezone.utc).isoformat())[:10]

    ri = [r for r in recs if "registro_imoveis" in (r.get("atribuicoes") or [])]

    return {
        "esquema": ESQUEMA,
        "dataset": "serventias-br",
        "versao": versao,
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "coletado_em": ultima,
        "coletado_entre": [primeira, ultima],
        "fonte": dict(FONTE),
        "repositorio": REPO,
        "url": URL_MANIFESTO,
        "defasagem": dict(DEFASAGEM),
        "arquivos": {
            nome: {
                "bytes": Path(caminho).stat().st_size,
                "sha256": sha256_de(caminho),
                "url": f"{REPO}/releases/download/v{versao}/{nome}",
            }
            for nome, caminho in arquivos.items()
        },
        "contagens": {
            "serventias_unicas": len(recs),
            "linhas_lidas": meta.get("linhas_lidas", len(recs)),
            "registro_imoveis": len(ri),
            "registro_imoveis_ativos": sum(1 for r in ri if r.get("status") == "ATIVADA"),
            "por_status": dict(Counter(r.get("status") for r in recs).most_common()),
            "por_tipo_registro": dict(Counter(r.get("tipo_registro") or "indefinido"
                                              for r in recs).most_common()),
            "por_uf": dict(sorted(Counter(r.get("uf") or "XX" for r in recs).items())),
        },
    }


def conferir_encolhimento(novo: dict, anterior: dict | None, tolerancia: float = 0.20) -> str | None:
    """Devolve um motivo para abortar quando o dataset novo é bem menor que o anterior.

    Um cadastro nacional cresce e se corrige; ele não perde um quinto das
    serventias de um mês para o outro. Quando isso aparece, o mais provável é
    uma varredura interrompida no meio, e publicar esse arquivo faria toda
    aplicação que o consome encolher junto.
    """
    if not anterior:
        return None
    de = int((anterior.get("contagens") or {}).get("serventias_unicas") or 0)
    para = int((novo.get("contagens") or {}).get("serventias_unicas") or 0)
    if de > 0 and para < de * (1 - tolerancia):
        return (f"O build trouxe {para} serventias contra {de} do manifesto anterior "
                f"(queda acima de {int(tolerancia * 100)}%). Nada foi publicado. "
                "Se o encolhimento for real, repita com --force.")
    return None


def ler(caminho: Path) -> dict | None:
    caminho = Path(caminho)
    if not caminho.exists():
        return None
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
