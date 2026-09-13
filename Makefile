.PHONY: install install-tudo sample build release-local api test clean
install:        ## dependências do pipeline
	pip install -r requirements.txt
install-tudo:   ## + planilhas (pandas/openpyxl) + API local (fastapi/uvicorn)
	pip install -r requirements.txt pandas openpyxl fastapi uvicorn
sample:         ## ingere a amostra fictícia (smoke test do pipeline)
	python -m pipeline.ingest_tabular data/sample/serventias_sample_FICTICIO.csv --fonte amostra_ficticia
extract-ja:     ## varredura nacional da API do Justiça Aberta, UF a UF (retomável)
	python -m pipeline.extract_justica_aberta --por-uf
dump-first:     ## inspeciona página 1 da API (formato/meta) e sai
	python -m pipeline.extract_justica_aberta --dump-first
har:            ## fallback: descobre endpoint de um HAR: make har HAR=arquivo.har
	python -m pipeline.parse_har $(HAR)
extract:        ## coleta completa via sources.yaml (após revisar o descoberto)
	python -m pipeline.extract_api
build:          ## consolida staging -> data/dist (csv.gz + manifesto + sqlite/json)
	python -m pipeline.build
release-local:  ## refaz sqlite/json/shards a partir do csv.gz versionado (sem staging)
	python -m pipeline.build --de-csv
api:            ## sobe a API local de consumo
	uvicorn api.main:app --reload
test:
	python -m pytest -q
clean:
	rm -rf data/staging/* data/dist/serventias.csv data/dist/serventias.sqlite data/dist/serventias.min.json data/dist/uf data/raw/api/* data/raw/har_records.jsonl
