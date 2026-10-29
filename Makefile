# Atalhos de conveniencia para o ambiente de dev local (Docker Compose +
# LocalStack). Requer `make` (Git Bash/WSL no Windows) e Docker Desktop.
# Alternativa equivalente para PowerShell puro: scripts/dev.ps1
# (mesmos alvos, sem depender de `make` estar instalado).
#
# Ver docs/local-dev.md para o guia completo.

.PHONY: up down logs db-shell db-reset seed intervals-sync test test-unit test-integration test-frontend preview-prod smoke-export

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

db-shell:
	docker compose exec mysql mysql -u$${MYSQL_USER:-musclefit} -p$${MYSQL_PASSWORD:-musclefit} $${MYSQL_DATABASE:-fitness}

# Apaga o volume de dados do MySQL e recria os containers do zero - as
# migrations em api/db/migrations/ rodam de novo, banco volta vazio.
db-reset:
	docker compose down -v
	docker compose up -d

# Aplica o dump real de producao (PII) contra o MySQL local. Ver aviso em
# api/scripts/bootstrap_local_db.py e docs/local-dev.md antes de usar.
seed:
	docker compose exec -e SEED_WITH_PROD_DUMP=1 api python scripts/bootstrap_local_db.py

# Equivalente local do agendamento do Intervals.icu (melhoria M2). Em producao
# quem dispara e o EventBridge -> Lambda musclefit-intervals-sync
# (infra/lambda_intervals_sync.tf); aqui e o mesmo `sync_users`, chamado a mao.
# Ex.: make intervals-sync ARGS="--days 7 --dry-run"
intervals-sync:
	docker compose exec api python scripts/run_intervals_sync.py $(ARGS)

# Testes automatizados (ver docs/local-dev.md, "Testes automatizados").
# Rodam no HOST (Linux: pip install --require-hashes -r api/requirements-dev.txt;
# host Windows: pip install -r api/requirements-dev.in; npm ci em frontend/).
test: test-unit test-integration test-frontend

# Sem banco.
test-unit:
	cd api && python -m pytest tests/unit

# Exige MySQL 8 acessivel. Default: o MySQL do compose (127.0.0.1:3306,
# root/musclefit-root); sobrescreva com TEST_MYSQL_HOST/PORT/USER/PASSWORD.
# Cria SO schemas descartaveis `musclefit_run<token>[_<tag>]_test` (nunca toca
# `fitness`), derruba todos eles no fim da execucao e varre os orfaos de
# execucoes anteriores no inicio (api/tests/support/schema_lifecycle.py).
test-integration:
	cd api && python -m pytest tests/integration

test-frontend:
	cd frontend && npm test

# Frontend como em PRODUCAO: `next build` (output export) + servidor local de
# out/ com a CloudFront Function real (http://127.0.0.1:4000). Ver
# docs/local-dev.md, "Validando o comportamento de producao".
preview-prod:
	cd frontend && npm run preview:prod

# Smoke HTTP das rotas dinamicas/redirects sobre o out/ ja buildado.
smoke-export:
	cd frontend && npm run build && npm run smoke:export
