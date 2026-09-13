# -*- coding: utf-8 -*-
"""Pipeline do serventias-br: coleta, normalização e build do dataset.

As mensagens do pipeline têm acento e setas. Num terminal do Windows com
página de código legada isso derrubava o `build` no último print, depois de
todo o trabalho feito. Reconfigurar a saída para UTF-8 (trocando o que não
couber) evita que um caractere de log mate uma coleta inteira.
"""
import sys

for _fluxo in (sys.stdout, sys.stderr):
    if hasattr(_fluxo, "reconfigure"):
        try:
            _fluxo.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
