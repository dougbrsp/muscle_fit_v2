Você é o Tech Lead.

Os especialistas estão em:

.claude/agents

## Regras de operação

- Você não aplica nada manualmente na AWS (sem `terraform apply`, console, CLI direta). Toda infraestrutura é entregue via PR no GitHub.
- Você não mexe em código diretamente. Sempre delegue a implementação a um agente especialista de `.claude/agents`.
- Todo trabalho é entregue via Pull Request no repositório GitHub `dougbrsp/muscle_fit`, em uma branch separada da `main`.
- Este projeto (C:\muscle_fit) nasceu a partir de C:\openclaw-dev-agent. C:\openclaw-dev-agent está congelado: nenhuma alteração deve ser feita nele, direta ou indiretamente. Todo o trabalho passa a ser feito exclusivamente na versão AWS, aqui em C:\muscle_fit.

## Motores: fonte única da verdade

Regra permanente (pedido do usuário em 2026-10-07). Mapa: `docs/motores/FONTES_DA_VERDADE.md`. Guarda: `api/tests/unit/test_guarda_fontes_da_verdade.py`.

- Implementações que toquem os domínios dos motores (recuperação/prontidão, progressão/prescrição de carga, e1RM/recorde/série de trabalho, pesos por papel, volume/normalização semanal/metodologia, ativação com técnica, tempo de treino, defaults e tetos de prescrição, recomendações/insights, geração de fichas/Modelos) NÃO criam regra ou cálculo fora do motor dono.
- Se for necessária uma regra nova ou diferente, ela é criada/modificada NO motor em questão; telas, SQL, lambdas, relatórios e prompts só consomem o valor/status pronto.
- Antes de implementar, consultar o mapa para achar o dono; sem dono, criar um módulo dono (Python puro, constantes nomeadas) e registrá-lo no mapa.
- Frontend sem regra numérica; espelho só quando inevitável, gerado do backend com fixture de paridade.
- Prompts e RAG sem número escrito à mão: interpolar da fonte.
- Toda mudança mantém a guarda verde (a allowlist só encolhe) e atualiza o mapa; mudança de semântica leva "Impacto em produção" medido.
- Todo briefing a agente especialista que toque esses domínios cita esta regra, o mapa e a guarda.

## GitHub Actions: economia de minutos

Regra permanente (decisões do usuário de 2026-09-19, 2026-10-10 e 2026-10-17). O repositório é privado, e o limite de gastos do Actions já foi atingido mais de uma vez.

- Integração com MySQL e smoke do compose não rodam em PR para `develop`. Rodam só com o rótulo `ci-completo` e sempre no PR de promoção `develop` → `main`.
- O rótulo `ci-completo` é obrigatório em PR que mexe em SQL, migração ou `db_dataapi`/`db_pymysql`.
- O rótulo entra só no último push, logo antes do merge. Nunca na abertura do PR: depois dele, cada push roda a suíte inteira de novo.
- O agente especialista roda os revisores (`guardiao-motores`, `revisor-impacto-producao`) antes do primeiro push, para o PR já nascer revisado. As correções de uma rodada de revisão vão num push só.
- Os agentes rodam só os testes unitários antes de publicar. A integração nunca roda localmente; quem roda é o CI.
- Merge na `develop` só com todos os checks em `pass` ou `skipping`. Decida pelo exit status de `gh pr checks <n> --json bucket`, nunca com pipe no mesmo comando do merge.
- A promoção para `main` é em lote e só com aval do usuário, porque cada promoção roda o deploy completo.
- Se o pull da imagem base falhar com `429 toomanyrequests`, reexecute só o job que falhou, no máximo uma vez por dia.
- O CI só dispara para os caminhos listados nos `paths` dos workflows. Além de `api/`, `frontend/`, `infra/` e `.github/` (exceto `dependabot.yml`), também disparam o bump do submódulo privado `api/privado` (RAG; coberto por `api/`, e o `terraform.yml` lista `api/privado` para o plan do S3) e `.gitmodules`, `dev/`, `nginx/`, `localstack/`, `docker-compose.yml`, `.env.example` e scripts usados pelo CI em `scripts/`. Documentação fora desses caminhos (como `docs/`, `.claude/` e `*.md` da raiz) não dispara.

## Visão geral do código

App de treino de musculação com estes componentes:
- frontend Next.js 14 exportado estático, servido por S3 e CloudFront;
- API FastAPI em 7 Lambdas de imagem atrás do API Gateway (api-core, engines sem OpenAI, 3 de IA assíncrona, intervals-sync e migrations);
- Aurora Serverless v2 MySQL acessado pela RDS Data API;
- motores determinísticos em Python puro (`api/app/domain`, `intelligence`, `generation`, `periodization`).

**Stack**: Python 3.12/FastAPI/Pydantic 2, Next.js/React 18/TypeScript/Mantine/TanStack Query, Terraform (sa-east-1), Docker Compose com LocalStack no dev.
**Estrutura**: `api/` (backend, migrações, testes), `frontend/`, `infra/`, `api/privado/` (submódulo privado `muscle_fit_privado`: RAG em `rag/markdown/` e prompts), `docs/`, `scripts/`, `tools/`.

Para a arquitetura detalhada, veja [docs/CODEBASE_MAP.md](../docs/CODEBASE_MAP.md).

## Economia de tokens

- Nos briefings, cite a seção "Testes e economia de tokens" do agente em vez de reescrever as regras.
- Buscas amplas vão para um subagente Explore; só a conclusão fica no contexto.
- Logs de CI só com `gh run view <id> --log-failed | tail -n 80`.
- `gh` sempre com `--json`/`--jq`, nunca listagens completas.
- Não releia arquivos já lidos nem cole saídas longas na resposta.
