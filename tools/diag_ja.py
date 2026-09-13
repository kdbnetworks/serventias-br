# -*- coding: utf-8 -*-
"""
diag_ja.py — diagnostica o endpoint do Justica Aberta apos um 404.

Uso:  python tools/diag_ja.py
Nao grava nada; so imprime status + inicio do corpo de cada variante.
"""
import json
import socket
import sys

import requests

SPA = "https://justicaaberta.cnj.jus.br"
UA_BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
UA_REPO = "serventias-br/0.1 (dataset civico)"

BASES = [
    "https://justicaabertaapi.cnj.jus.br/v1/api/serventias",
    "https://justicaabertaapi.cnj.jus.br/api/v1/serventias",
    "https://justicaabertaapi.cnj.jus.br/v1/api/serventias/",
    "https://justicaabertaapi.cnj.jus.br/v1/api/serventia",
    "https://justicaabertaapi.cnj.jus.br/v1/api/serventias-extrajudiciais",
    "https://justicaaberta.cnj.jus.br/api/v1/serventias",
]

PARAM_SETS = [
    {"assignments": "", "page": 1, "perPage": 20, "search": ""},
    {"page": 1, "perPage": 20},
    {"page": 0, "perPage": 20},
    {},
]


def hdrs(ua, with_origin=True):
    h = {"Accept": "application/json", "User-Agent": ua}
    if with_origin:
        h["Origin"] = SPA
        h["Referer"] = SPA + "/"
    return h


def show(label, resp):
    body = (resp.text or "")[:220].replace("\n", " ")
    ct = resp.headers.get("Content-Type", "?")
    print(f"  {resp.status_code:>3} | {ct[:30]:<30} | {label}")
    if resp.status_code == 200:
        print(f"      >> {body}")
    elif body.strip():
        print(f"      .. {body}")


def main():
    print("== DNS ==")
    for host in ("justicaabertaapi.cnj.jus.br", "justicaaberta.cnj.jus.br"):
        try:
            print(f"  {host} -> {socket.gethostbyname(host)}")
        except Exception as e:              # noqa: BLE001
            print(f"  {host} -> FALHOU: {e}")

    print("\n== A SPA responde? ==")
    try:
        r = requests.get(SPA, headers=hdrs(UA_BROWSER, False), timeout=30)
        print(f"  {r.status_code} | {len(r.content)} bytes | {SPA}")
    except Exception as e:                  # noqa: BLE001
        print(f"  FALHOU: {e}  <- provavel bloqueio de rede/proxy/TLS")
        sys.exit(1)

    print("\n== Matriz de variantes ==")
    ok = []
    for base in BASES:
        for params in PARAM_SETS:
            for ua_name, ua in (("browser", UA_BROWSER), ("repo", UA_REPO)):
                for origin in (True, False):
                    label = (f"{base.split('.br')[-1]} params={list(params) or '-'} "
                             f"ua={ua_name} origin={'sim' if origin else 'nao'}")
                    try:
                        r = requests.get(base, params=params,
                                         headers=hdrs(ua, origin), timeout=30)
                    except Exception as e:  # noqa: BLE001
                        print(f"  ERR | {label}: {e}")
                        continue
                    if r.status_code == 200 or r.status_code not in (403, 404):
                        show(label, r)
                    if r.status_code == 200:
                        ok.append((base, params, ua_name, origin, r))
                # UA repo sem origin raramente muda algo; segue

    if not ok:
        print("\n[resultado] Nenhuma variante devolveu 200.")
        print("            O endpoint mudou de forma -> caminho HAR (ver instrucoes).")
        return

    base, params, ua_name, origin, r = ok[0]
    print("\n[resultado] FUNCIONOU:")
    print(f"  URL     : {base}")
    print(f"  params  : {params}")
    print(f"  UA      : {ua_name} | Origin/Referer: {'sim' if origin else 'nao'}")
    try:
        payload = r.json()
    except Exception:                       # noqa: BLE001
        print("  (corpo nao e JSON)")
        return
    if isinstance(payload, dict):
        print(f"  chaves  : {list(payload)[:15]}")
    print("\n--- inicio do payload ---")
    print(json.dumps(payload, ensure_ascii=False, indent=2)[:2500])


if __name__ == "__main__":
    main()
