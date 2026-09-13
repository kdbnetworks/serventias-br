# serventias-br

Todas as serventias extrajudiciais do Brasil num arquivo que a sua aplicação
consegue ler: cartórios de Registro de Imóveis, Notas, Protesto, Registro Civil
e Títulos e Documentos, cada um com o seu CNS, denominação, situação, município,
atribuições e contato. Junto vem o pipeline que regenera a base a partir do
Justiça Aberta, do CNJ, e um manifesto que diz de quando é a coleta.

Nasceu dentro de um software de regularização fundiária, onde o formulário
precisava de um localizador de cartório que funcionasse sem que a pessoa
soubesse o código de cor. Depois percebemos que o mesmo problema aparece em
muita coisa: sistemas de cartório, de imobiliária, de engenharia, de prefeitura.
Então o dataset virou este repositório.

Se você só quer os dados, pule para [Baixar](#baixar). Se quer entender como a
base é feita, ou ajudar a mantê-la, o resto do texto é para você.

## O que tem aqui

| | |
|---|---|
| Serventias (CNS distintos) | **12.430** |
| Coleta | 06/08/2026, varredura nacional UF a UF |
| Cobertura | 27 UFs, mais um registro sem UF na fonte |
| Situação operacional | 8.912 ativadas · 3.362 desativadas · 156 extintas |
| Situação jurídica | 6.223 providas · 3.960 vagas · 422 em diligência · 50 sob intervenção · 1.774 sem informação |
| Registro de Imóveis | 3.012, sendo 2.575 ativos, em 2.446 municípios |
| Preenchimento | endereço 95% · telefone 95% · e-mail 93% · responsável 82% |

Os números vêm do [`manifest.json`](data/dist/manifest.json) e do
[`meta.json`](data/dist/meta.json), que o build regera a cada coleta.

Fonte primária: Sistema Justiça Aberta, da Corregedoria Nacional de Justiça
(<https://justicaaberta.cnj.jus.br/produtividade-e-localizacao-de-serventias-extrajudiciais>).
Os dados são públicos por força do art. 136 do Código Nacional de Normas do
Foro Extrajudicial (Provimento CNJ n. 149/2023).

## Baixar

Cada coleta vira uma release. A última está sempre em:

```
https://github.com/kdbnetworks/serventias-br/releases/latest/download/manifest.json
https://github.com/kdbnetworks/serventias-br/releases/latest/download/serventias.csv.gz
```

A release traz também `serventias.sqlite` (com índices e busca textual FTS5),
`serventias.min.json` (um array compacto para o navegador), `uf.zip` (um JSON
por estado, para carregar sob demanda) e `meta.json`.

O `serventias.csv.gz` também fica versionado aqui no repositório, em
`data/dist/`. Ele tem cerca de 850 KB e é o arquivo que recomendamos guardar
dentro do seu projeto: assim o seu deploy sobe sem depender de rede, e você
atualiza quando quiser.

### Para quem usa Laravel

Existe um pacote pronto, com migration, importador, busca e um farol de
sanidade para o painel administrativo:
[kdbnetworks/serventias-laravel](https://github.com/kdbnetworks/serventias-laravel).

## O manifesto

O `manifest.json` é o jeito de a sua aplicação saber se a cópia local
envelheceu. Ele traz:

- `versao`: a data da coleta, no formato `AAAA-MM-DD`;
- `coletado_em`: o instante da última página lida na API do CNJ;
- `arquivos`: tamanho, `sha256` e URL de cada arquivo da release;
- `contagens`: total de serventias, quantas são Registro de Imóveis, quantas
  estão ativas, e a distribuição por status, tipo e UF;
- `defasagem`: a sugestão de prazos. Aos 60 dias vale avisar quem administra
  o sistema; aos 120, vale alertar.

Uma aplicação compara o `sha256` e o `coletado_em` do manifesto remoto com o
que tem gravado e decide sozinha se está em dia. Nenhuma chamada ao CNJ
precisa acontecer em produção.

## O que cada registro contém

| campo | descrição |
|---|---|
| `cns` / `cns_digits` | Código Nacional da Serventia, em `NN.NNN-N` e só dígitos. É o identificador único e estável de cada delegação, ativa ou não (art. 136-G do CNN). Chave primária. |
| `denominacao` | Nome oficial (`denominacao_fantasia`, com fallback para `denominacao_padrao`) |
| `status` | Situação **operacional**: `ATIVADA · DESATIVADA · EXTINTA` (o valor original fica em `status_raw`) |
| `situacao_juridica` | Situação **da delegação**: `PROVIDA · VAGA · VAGA_SUB_JUDICE · SOB_INTERVENCAO · CONVERSAO_EM_DILIGENCIA` (original em `situacao_juridica_raw`) |
| `tipo_registro` | `serventia · corregedoria · suspeito · sem_atribuicao · indefinido`. Veja [Ruído cadastral](#ruído-cadastral). |
| `municipio`, `uf` | Localidade |
| `atribuicoes` | Flags derivadas da natureza e da denominação: `registro_imoveis`, `notas`, `protesto`, `registro_civil_pn`, `registro_civil_pj`, `titulos_documentos`, `contratos_maritimos`, `distribuicao` (texto original em `atribuicoes_raw`) |
| extras | `endereco, bairro, cep, telefone, email, website, responsavel, comarca, circunscricao, codigo_ibge, latitude, longitude, id_cnj`, quando a fonte fornece |

O schema formal está em [`schema/serventia.schema.json`](schema/serventia.schema.json).

Uma coisa que confunde no começo: `status` e `situacao_juridica` são campos
diferentes e respondem a perguntas diferentes. Uma serventia pode estar
`ATIVADA` e `VAGA` ao mesmo tempo, ou seja, em funcionamento, sob interinidade,
sem delegatário titular. Para a maioria dos formulários o que importa é
`status = ATIVADA`.

Serventias desativadas e extintas ficam na base de propósito. Uma matrícula
antiga cita o cartório que existia na época, e o seu sistema precisa conseguir
citá-lo também.

## Como a base é feita

```
API do Justiça Aberta ──► extract_justica_aberta ──► data/staging/*.jsonl
                                (normalize)                  │
                                                             ▼
                          data/dist/  ◄────────────────── build
                          csv.gz · manifest · sqlite · json · uf/
```

1. **Coleta.** O extrator percorre a API pública do Justiça Aberta estado por
   estado, cem registros por página, guardando cada página bruta em
   `data/raw/api/`. Dá para interromper e retomar de onde parou.
2. **Normalização.** Cada registro vira um objeto canônico: CNS limpo, status
   e situação jurídica em enums, atribuições em flags, texto de busca sem
   acento. Está tudo em [`pipeline/normalize.py`](pipeline/normalize.py).
3. **Build.** Os registros são deduplicados por CNS, fundidos quando vêm de
   mais de uma fonte, e gravados nos formatos de saída. O build assina o
   `csv.gz`, escreve o manifesto e se recusa a publicar um dataset que
   encolheu mais de 20% em relação ao anterior, porque isso quase sempre
   significa uma varredura que morreu no meio.

Para rodar em casa:

```bash
make install                    # requests, pyyaml, pytest
make dump-first                 # confere o formato da API e sai
make extract-ja && make build   # varredura nacional, uns 4 minutos
make test
```

Sem internet? `make sample && python -m pipeline.build --com-amostra` usa uma
amostra fictícia, boa para testar o pipeline e ruim para qualquer outra coisa.

### A API do Justiça Aberta, e suas três pegadinhas

O endpoint foi confirmado em 06/08/2026 lendo o bundle da SPA
(`assets/registry-*.js`, módulo `registry.search`). Ele existe, funciona e
está sem documentação oficial, então pode mudar sem aviso.

```
POST https://justicaabertaapi.cnj.jus.br/v1/api/serventias
     ?assignments=&page=1&perPage=100&search=
Content-Type: application/json

{"cidade_id": 0, "uf": "SC", "cns": null}
```

Três detalhes que já custaram horas e que o extrator já trata:

- **É POST.** A rota responde 404 a GET, e um 404 se disfarça muito bem de
  "o endpoint sumiu". A paginação vai na query string, os filtros vão no corpo.
- **O host da SPA devolve `index.html` com HTTP 200 para qualquer rota.** Uma
  sondagem que só olha o código de status acusa falso positivo. Olhe o
  `Content-Type`.
- **`meta.total` conta linhas, e linhas se repetem entre páginas.** Na Bahia
  vieram 1.804 linhas para 1.489 CNS. Deduplicar por `cns_digits` é
  obrigatório, e a diferença entre o total e o número de CNS distintos é
  normal.

Outras rotas públicas do mesmo host, úteis para enriquecer um registro:

```
GET  /v1/api/serventias/{cns}                    dados do cartório
GET  /v1/api/serventias/{cns}/localizacao        geolocalização
GET  /v1/api/serventias/{cns}/dados-complementares
GET  /v1/api/serventias/{cns}/horarios-funcionamento
GET  /v1/api/serventias/{cns}/responsaveis
GET  /v1/api/estados/listar-uf
GET  /v1/api/atribuicoes/tipos                   valores para o filtro assignments
```

### Quando a API mudar

Vai acontecer. Quando acontecer, `python tools/sniff_spa.py` baixa a página
do portal, encontra os bundles JS, extrai as URLs absolutas, as declarações de
`baseURL` e os caminhos `/api/...`, e grava tudo em `diag_spa.txt`. Foi assim
que o endpoint atual foi encontrado. `python tools/fetch_bundles.py` baixa os
chunks para inspeção offline, e `python tools/diag_ja.py` testa variantes de
rota e de parâmetros depois de um 404.

Ao final de cada varredura o extrator lista as chaves da API que ainda não
conhece. É por aí que o mapeamento evolui: quando aparecer uma chave nova,
ela entra em `CHAVES_CONHECIDAS` e, se valer a pena, em `map_row`.

Caminhos alternativos, para o dia em que a API estiver fora do ar:

- **HAR do navegador.** `F12` → Network → filtro Fetch/XHR → faça uma busca
  no portal → "Save all as HAR with content" → `make har HAR=arquivo.har`.
- **Painel Qlik do CNJ.** Exporta `.csv`/`.xlsx` com os campos cadastrais:
  `python -m pipeline.ingest_tabular arquivo.xlsx --fonte painel_cnj`
  (precisa de `pandas` e `openpyxl`).
- **Planilhas das Corregedorias estaduais.** Mesmo comando; o mapeamento de
  cabeçalhos é heurístico e vive em `COLUMN_ALIASES`.

### Atualização mensal

Uma GitHub Action roda no dia 1 de cada mês, refaz a varredura, roda o build e
abre um pull request com o `csv.gz`, o `manifest.json` e o `meta.json` novos.
O diff do `meta.json` mostra o que mudou. Quando o PR é aprovado, outra Action
monta os derivados e publica a release. Nada chega ao `main` sem passar por
alguém.

## Consumir sem Laravel

**API local em Python** (`pip install fastapi uvicorn`, depois `make api`):

```
GET /serventias?uf=SC&municipio=Itajaí&atribuicao=registro_imoveis&status=ATIVADA
GET /serventias/12.345-6
GET /detect?q=registro imoveis itajai          # autocomplete
GET /detect?q=<texto colado de uma certidão>   # acha o CNS no texto
GET /detect/registro-imoveis?municipio=Itajaí&uf=SC
```

**No navegador**, com [`js/serventia-detect.js`](js/serventia-detect.js), sem
dependências:

```html
<script src="js/serventia-detect.js"></script>
<script>
  const { ServentiaIndex, attachAutofill } = ServentiasBR;
  const idx = await ServentiaIndex.fromURL('/data/uf/SC.json');
  attachAutofill(document.querySelector('#cartorio'), idx, {
    uf: 'SC', atribuicao: 'registro_imoveis',
    onSelect: s => {
      form.cns.value = s.cns;
      form.serventia.value = s.denominacao;
    }
  });
</script>
```

Se a pessoa colar o cabeçalho de uma certidão no campo, `findCNSInText()`
acha o `NN.NNN-N` no meio do texto e preenche o resto.

**Em qualquer outra linguagem:** leia o `serventias.csv.gz` pelo cabeçalho,
separador `;`, UTF-8, uma linha por CNS. Colunas novas podem aparecer no fim;
as existentes não mudam de nome.

## Avisos que valem a leitura

### Ruído cadastral

A base do CNJ inclui registros que não são serventias: as 29 corregedorias
estaduais (CNS `15.3xx`), 4 lançamentos de teste deixados pelos tribunais
(`Serventia_Teste`, `SEJ00055AL`) e 384 registros sem atribuição declarada.
Nada foi removido, porque a base é fiel à fonte, mas o campo `tipo_registro`
marca cada caso. Em formulário, filtre por `tipo_registro = 'serventia'`.

### Formato do CNS, e só o formato

Validamos que o CNS tem seis dígitos. O último parece ser um dígito
verificador, mas o algoritmo não é público, e inventar um mod-11 aqui faria o
seu sistema rejeitar cartório legítimo. Para fé pública, a fonte oficial é
sempre o CNJ.

### Circunscrição imobiliária

Dos 2.446 municípios com Registro de Imóveis, 423 têm mais de um. Nesses
casos a competência territorial depende da circunscrição, que o Justiça
Aberta ainda não expõe de forma estruturada. O endpoint
`/detect/registro-imoveis` sinaliza `atencao_circunscricao: true`, e o campo
`circunscricao` existe no schema para quem quiser curar à mão.

### Uso responsável

Dado público e carga ilimitada são coisas diferentes. O extrator faz cerca de
uma requisição por segundo, com retry exponencial e User-Agent identificado;
por favor mantenha isso se for adaptar. Os campos de contato e de responsável
se referem à serventia e ao delegatário como agente público. Não colete nem
derive dados pessoais além disso.

## Contribuir

Issues e pull requests são bem-vindos. Coisas que ajudam muito:

- uma chave nova da API que apareceu no relatório do extrator;
- uma planilha de Corregedoria com campos que a API não tem (circunscrição,
  por exemplo);
- um consumidor em outra linguagem, com um exemplo curto para o README.

Antes de abrir o PR, `make test`. A suíte roda em segundos.

## Licença

O código é MIT. Os dados são públicos e vêm do CNJ/Justiça Aberta; ao
redistribuir, cite a fonte e a data de coleta, que está no `manifest.json`.
