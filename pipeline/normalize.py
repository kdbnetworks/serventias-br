# -*- coding: utf-8 -*-
"""
Normalização canônica dos registros de serventias extrajudiciais.

Converte registros heterogêneos (API Justiça Aberta, CSV do Painel Qlik,
planilhas de corregedorias) para o schema canônico definido em
schema/serventia.schema.json.

Campos canônicos:
    cns              str  "NN.NNN-N" (6 dígitos formatados)
    cns_digits       str  "NNNNNN"  (só dígitos — chave primária)
    denominacao      str  nome oficial da serventia
    status           str  enum normalizado (ATIVADA, DESATIVADA, VAGA, ...)
    status_raw       str  valor original da fonte
    municipio        str  localidade
    uf               str  sigla (2 letras)
    atribuicoes      list flags canônicas (registro_imoveis, notas, ...)
    atribuicoes_raw  str  texto original ("descrição da serventia")
    search_text      str  texto sem acentos/minúsculo p/ busca e autofill
    fonte            str  origem do registro
    coletado_em      str  ISO-8601
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone

# --------------------------------------------------------------------------- #
# CNS — Código Nacional da Serventia
# --------------------------------------------------------------------------- #
# O CNS é um identificador único e estável de 6 dígitos, exibido no formato
# "NN.NNN-N" (art. 136-G do Código Nacional de Normas / Prov. 149/2023-CNJ).
# ATENÇÃO: o último dígito aparenta ser verificador, mas o algoritmo de DV
# não é documentado publicamente — por isso validamos apenas o FORMATO.
# Não invente um mod-11 aqui sem confirmação oficial.

CNS_PATTERN = re.compile(r"\b(\d{2})\.?(\d{3})-?(\d)\b")


def clean_cns(raw) -> str | None:
    """Extrai os 6 dígitos do CNS. Aceita '12.345-6', '123456', ' 12345-6 '.

    Retorna string de 6 dígitos (zero-padded à esquerda quando a fonte
    suprime zeros) ou None se irrecuperável.
    """
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if not digits:
        return None
    if len(digits) < 6:
        digits = digits.zfill(6)  # fontes antigas às vezes cortam zeros à esq.
    if len(digits) != 6:
        return None
    return digits


def format_cns(digits: str) -> str:
    """'123456' -> '12.345-6'."""
    d = clean_cns(digits)
    if d is None:
        raise ValueError(f"CNS inválido: {digits!r}")
    return f"{d[:2]}.{d[2:5]}-{d[5]}"


def find_cns_in_text(text: str) -> list[str]:
    """Detector: localiza CNSs em texto livre (certidões, matrículas, ofícios).

    Útil para autopreenchimento: o usuário cola o cabeçalho de uma certidão
    e o sistema identifica a serventia. Retorna lista de CNS (6 dígitos).
    """
    if not text:
        return []
    out, seen = [], set()
    for m in CNS_PATTERN.finditer(text):
        d = "".join(m.groups())
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


# --------------------------------------------------------------------------- #
# Status / situação da serventia
# --------------------------------------------------------------------------- #
# A API do Justiça Aberta usa a forma MASCULINA ("Ativo"/"Inativo"/"Extinto");
# planilhas de corregedorias usam a feminina ("Ativada"/"Desativada"). Aceitamos
# as duas — omitir o masculino fazia 100% dos registros virarem "OUTRO".
STATUS_CANONICO = {
    "ativada": "ATIVADA",
    "ativa": "ATIVADA",
    "ativo": "ATIVADA",
    "em atividade": "ATIVADA",
    "instalada": "ATIVADA",
    "instalado": "ATIVADA",
    "desativada": "DESATIVADA",
    "desativado": "DESATIVADA",
    "inativa": "DESATIVADA",
    "inativo": "DESATIVADA",
    "desinstalada": "DESATIVADA",
    "desinstalado": "DESATIVADA",
    "extinta": "EXTINTA",
    "extinto": "EXTINTA",
    "vaga": "VAGA",
    "vago": "VAGA",
    "anexada": "ANEXADA",
    "anexado": "ANEXADA",
    "acumulada": "ACUMULADA",
    "acumulado": "ACUMULADA",
}

# ATENÇÃO — no Justiça Aberta, `status` (Ativo/Inativo/Extinto) e a situação
# jurídica da delegação (PROVIDO/VAGO/SOB INTERVENÇÃO/...) são coisas
# DIFERENTES e vêm em campos distintos. VAGA/ANEXADA/ACUMULADA acima existem
# para fontes estaduais que misturam os dois num campo só; da API, "vago"
# chega em `situacao_juridica`, não em `status`.
SITUACAO_JURIDICA_CANONICA = {
    "provido": "PROVIDA",
    "provida": "PROVIDA",
    "vago": "VAGA",
    "vaga": "VAGA",
    "vago - sub judice": "VAGA_SUB_JUDICE",
    "sob intervencao": "SOB_INTERVENCAO",
    "conversao em diligencia": "CONVERSAO_EM_DILIGENCIA",
}


def normalize_situacao_juridica(raw) -> tuple[str | None, str | None]:
    """Retorna (situacao_canonica, situacao_raw). None quando a fonte não informa."""
    raw = ("" if raw is None else str(raw)).strip()
    if not raw:
        return None, None
    key = strip_accents(raw).lower()
    return SITUACAO_JURIDICA_CANONICA.get(key, "OUTRA"), raw


# --------------------------------------------------------------------------- #
# Classificação do registro (a base do CNJ contém não-serventias)
# --------------------------------------------------------------------------- #
# O cadastro traz, além das serventias, as próprias corregedorias estaduais
# (CNS 15.3xx) e alguns registros de teste deixados pelos tribunais
# ("Serventia_Teste", "SEJ00055AL"). Não removemos nada — a base é fiel à
# fonte —, mas marcamos, para que aplicações de REURB filtrem com segurança.
RE_CORREGEDORIA = re.compile(
    r"\bcorregedoria\b|\btribunal de justica\b|\bdiretoria do foro\b")
RE_SUSPEITO = re.compile(
    r"\bteste\b|^sej\d{3,}|\bhomologacao\b|\bxxx+\b|^sem denominacao$")


def classificar_registro(denominacao, atribuicoes=None) -> str:
    """'serventia' | 'corregedoria' | 'suspeito' | 'sem_atribuicao' | 'indefinido'."""
    # separadores viram espaço: "Serventia_Teste" precisa casar com \bteste\b
    d = re.sub(r"[^a-z0-9]+", " ", fold(denominacao)).strip()
    if not d:
        return "indefinido"
    if RE_CORREGEDORIA.search(d):
        return "corregedoria"
    if RE_SUSPEITO.search(d):
        return "suspeito"
    if not atribuicoes:
        return "sem_atribuicao"
    return "serventia"


def normalize_status(raw) -> tuple[str, str]:
    """Retorna (status_canonico, status_raw)."""
    raw = ("" if raw is None else str(raw)).strip()
    key = strip_accents(raw).lower()
    return STATUS_CANONICO.get(key, "OUTRO" if raw else "DESCONHECIDO"), raw


# --------------------------------------------------------------------------- #
# Atribuições (a "descrição da serventia")
# --------------------------------------------------------------------------- #
# Flags canônicas -> padrões (aplicados sobre texto sem acento, minúsculo).
# Para regularização fundiária (REURB), a flag decisiva é `registro_imoveis`.
ATRIBUICAO_PATTERNS: dict[str, list[str]] = {
    "registro_imoveis":     [r"registro\s+de\s+imove", r"registrador?\s+imobiliari", r"\bimoveis\b"],
    "notas":                [r"tabelionato\s+de\s+notas", r"tabeliao\s+de\s+notas", r"\bnotas\b", r"notarial"],
    "protesto":             [r"protesto"],
    "registro_civil_pn":    [r"pessoas\s+naturais", r"registro\s+civil(?!\s+d?e?\s*pessoas\s+jur)", r"\brcpn\b"],
    "registro_civil_pj":    [r"pessoas\s+juridicas", r"\brcpj\b", r"\brpj\b"],
    "titulos_documentos":   [r"titulos\s+e\s+documentos", r"\brtd\b"],
    "contratos_maritimos":  [r"maritimos"],
    "distribuicao":         [r"distribui[cç]?[aã]?o|distribuidor"],
}
_COMPILED = {k: [re.compile(p) for p in v] for k, v in ATRIBUICAO_PATTERNS.items()}


def parse_atribuicoes(text) -> list[str]:
    """Extrai flags canônicas de um texto de atribuições/denominação."""
    base = strip_accents("" if text is None else str(text)).lower()
    flags = [flag for flag, pats in _COMPILED.items() if any(p.search(base) for p in pats)]
    return flags


# --------------------------------------------------------------------------- #
# Texto / UF / utilidades
# --------------------------------------------------------------------------- #
UFS = {
    "AC","AL","AP","AM","BA","CE","DF","ES","GO","MA","MT","MS","MG","PA",
    "PB","PR","PE","PI","RJ","RN","RS","RO","RR","SC","SP","SE","TO",
}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_text(s) -> str:
    """Colapsa espaços PRESERVANDO acentos (denominações oficiais)."""
    s = "" if s is None else str(s)
    return re.sub(r"\s+", " ", s).strip()


def fold(s) -> str:
    """Minúsculo + sem acentos — só p/ busca e mapeamento de cabeçalhos."""
    return norm_text(strip_accents("" if s is None else str(s))).lower()


def normalize_uf(raw) -> str | None:
    uf = norm_text(raw).upper()
    return uf if uf in UFS else None


def make_search_text(*parts) -> str:
    return fold(" ".join(str(p) for p in parts if p))


# --------------------------------------------------------------------------- #
# Registro canônico
# --------------------------------------------------------------------------- #
def build_record(
    *,
    cns,
    denominacao,
    status=None,
    municipio=None,
    uf=None,
    atribuicoes_raw=None,
    fonte="desconhecida",
    extras: dict | None = None,
) -> dict | None:
    """Monta um registro canônico. Retorna None se o CNS for irrecuperável."""
    digits = clean_cns(cns)
    if digits is None:
        return None
    status_norm, status_raw = normalize_status(status)
    denominacao = norm_text(denominacao) or None
    municipio = norm_text(municipio) or None
    atr_raw = None if atribuicoes_raw is None else re.sub(r"\s+", " ", str(atribuicoes_raw)).strip()
    # muitas fontes embutem as atribuições na própria denominação
    flags = parse_atribuicoes(" ".join(x for x in (atr_raw, denominacao) if x))
    rec = {
        "cns": format_cns(digits),
        "cns_digits": digits,
        "denominacao": denominacao,
        "status": status_norm,
        "status_raw": status_raw or None,
        "municipio": municipio,
        "uf": normalize_uf(uf),
        "atribuicoes": flags,
        "atribuicoes_raw": atr_raw,
        "search_text": make_search_text(digits, format_cns(digits), denominacao, municipio, uf, atr_raw),
        "fonte": fonte,
        "coletado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    rec["tipo_registro"] = classificar_registro(denominacao, flags)
    if extras:
        # campos adicionais úteis p/ REURB e autofill (endereço, contato etc.)
        for k in ("endereco", "bairro", "cep", "telefone", "email",
                  "responsavel", "comarca", "circunscricao", "codigo_ibge",
                  "latitude", "longitude", "website"):
            v = extras.get(k)
            if v not in (None, ""):
                rec[k] = norm_text(v) if isinstance(v, str) else v
        sj, sj_raw = normalize_situacao_juridica(extras.get("situacao_juridica"))
        if sj:
            rec["situacao_juridica"] = sj
            rec["situacao_juridica_raw"] = sj_raw
    return rec


def merge_records(a: dict, b: dict) -> dict:
    """Funde dois registros do mesmo CNS: campos não nulos de `b` complementam `a`."""
    out = dict(a)
    for k, v in b.items():
        cur = out.get(k)
        if cur in (None, "", [], "DESCONHECIDO", "OUTRO") and v not in (None, "", []):
            out[k] = v
        elif k == "atribuicoes":
            out[k] = sorted(set(cur or []) | set(v or []))
    srcs = {s for s in re.split(r"\s*\+\s*", str(a.get("fonte", ""))) if s}
    srcs |= {s for s in re.split(r"\s*\+\s*", str(b.get("fonte", ""))) if s}
    out["fonte"] = " + ".join(sorted(srcs))
    return out
