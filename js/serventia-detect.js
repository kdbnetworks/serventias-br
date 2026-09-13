/**
 * serventia-detect.js — helpers de front-end para formulários de
 * regularização fundiária (REURB) e afins.
 *
 * Sem dependências. Funciona com os shards gerados pelo pipeline
 * (data/dist/uf/SC.json etc.) ou com o serventias.min.json completo.
 *
 * Recursos:
 *   cleanCNS / formatCNS / isValidCNSFormat  — máscara e validação de formato
 *   findCNSInText(texto)                     — detecta CNS colado (certidões)
 *   ServentiaIndex                           — busca/autocomplete em memória
 *   attachAutofill(input, index, opts)       — liga um <input> ao detector
 *
 * Uso típico:
 *   const idx = await ServentiaIndex.fromURL('/data/uf/SC.json');
 *   attachAutofill(document.querySelector('#cartorio'), idx, {
 *     uf: 'SC',
 *     atribuicao: 'registro_imoveis',            // foco REURB
 *     onSelect: s => {                           // autopreenchimento
 *       form.cns.value        = s.cns;
 *       form.serventia.value  = s.denominacao;
 *       form.municipio.value  = s.municipio;
 *       form.uf.value         = s.uf;
 *     }
 *   });
 */
(function (global) {
  'use strict';

  const CNS_RE = /\b(\d{2})\.?(\d{3})-?(\d)\b/g;

  function cleanCNS(raw) {
    if (raw == null) return null;
    let d = String(raw).replace(/\D/g, '');
    if (!d) return null;
    if (d.length < 6) d = d.padStart(6, '0');
    return d.length === 6 ? d : null;
  }

  function formatCNS(raw) {
    const d = cleanCNS(raw);
    return d ? `${d.slice(0, 2)}.${d.slice(2, 5)}-${d.slice(5)}` : null;
  }

  /** Valida apenas o FORMATO (6 dígitos). O algoritmo do dígito verificador
   *  do CNS não é documentado publicamente — não inventamos um aqui. */
  function isValidCNSFormat(raw) {
    return cleanCNS(raw) !== null;
  }

  /** Extrai CNSs de texto livre (ex.: cabeçalho de certidão/matrícula colado). */
  function findCNSInText(text) {
    const out = [], seen = new Set();
    let m;
    CNS_RE.lastIndex = 0;
    while ((m = CNS_RE.exec(String(text || ''))) !== null) {
      const d = m[1] + m[2] + m[3];
      if (!seen.has(d)) { seen.add(d); out.push(d); }
    }
    return out;
  }

  function fold(s) { // remove acentos, minúsculas
    return String(s || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  }

  class ServentiaIndex {
    constructor(records) {
      this.records = (records || []).map(r => ({
        ...r,
        _search: r.search_text || fold([r.cns, r.denominacao, r.municipio, r.uf,
                                        r.atribuicoes_raw].filter(Boolean).join(' ')),
      }));
      this.byCNS = new Map(this.records.map(r => [r.cns_digits, r]));
    }

    static async fromURL(url) {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`Falha ao carregar ${url}: HTTP ${res.status}`);
      return new ServentiaIndex(await res.json());
    }

    getByCNS(raw) {
      const d = cleanCNS(raw);
      return d ? this.byCNS.get(d) || null : null;
    }

    /**
     * Detector principal: recebe o que o usuário digitou/colou e devolve
     * candidatos ranqueados. Prioriza CNS exato > prefixo > substring,
     * com filtros opcionais {uf, municipio, atribuicao, status}.
     */
    suggest(query, opts = {}, limit = 8) {
      const { uf, municipio, atribuicao, status } = opts;

      // 1) CNS embutido no texto
      for (const d of findCNSInText(query)) {
        const hit = this.byCNS.get(d);
        if (hit) return [{ ...hit, _match: 'cns' }];
      }
      const exact = this.getByCNS(query);
      if (exact) return [{ ...exact, _match: 'cns' }];

      // 2) busca textual por tokens
      const tokens = fold(query).split(/\s+/).filter(t => t.length >= 2);
      if (!tokens.length) return [];
      const scored = [];
      for (const r of this.records) {
        if (uf && r.uf !== uf.toUpperCase()) continue;
        if (status && r.status !== status.toUpperCase()) continue;
        if (municipio && !fold(r.municipio).includes(fold(municipio))) continue;
        if (atribuicao && !(r.atribuicoes || []).includes(atribuicao)) continue;
        let score = 0;
        for (const t of tokens) {
          const i = r._search.indexOf(t);
          if (i === -1) { score = -1; break; }
          score += (i === 0 || r._search[i - 1] === ' ') ? 3 : 1; // prefixo pesa mais
        }
        if (score > 0) {
          if (r.status === 'ATIVADA') score += 1;               // ativas primeiro
          scored.push([score, r]);
        }
      }
      scored.sort((a, b) => b[0] - a[0]);
      return scored.slice(0, limit).map(([, r]) => ({ ...r, _match: 'texto' }));
    }
  }

  /**
   * Liga um <input> ao índice: datalist nativa (zero CSS) + callback de
   * seleção para autopreencher os demais campos do formulário.
   */
  function attachAutofill(input, index, opts = {}) {
    const listId = opts.datalistId || `serventias-${Math.random().toString(36).slice(2, 8)}`;
    let dl = document.getElementById(listId);
    if (!dl) {
      dl = document.createElement('datalist');
      dl.id = listId;
      input.insertAdjacentElement('afterend', dl);
    }
    input.setAttribute('list', listId);
    input.setAttribute('autocomplete', 'off');

    let last = [];
    const render = () => {
      last = index.suggest(input.value, opts, opts.limit || 8);
      dl.innerHTML = last.map(s =>
        `<option value="${s.cns} — ${s.denominacao || ''}">` +
        `${s.municipio || ''}/${s.uf || ''}</option>`).join('');
    };
    const pick = () => {
      const d = findCNSInText(input.value)[0];
      const sel = d ? index.byCNS.get(d) : (last.length === 1 ? last[0] : null);
      if (sel && typeof opts.onSelect === 'function') opts.onSelect(sel);
    };
    input.addEventListener('input', render);
    input.addEventListener('change', pick);
    input.addEventListener('blur', pick);
    return { destroy() { input.removeEventListener('input', render); dl.remove(); } };
  }

  const api = { cleanCNS, formatCNS, isValidCNSFormat, findCNSInText, ServentiaIndex, attachAutofill };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.ServentiasBR = api;
})(typeof window !== 'undefined' ? window : globalThis);
