# Automacoes do Claude Code (hooks, skills, agentes, MCP)

Arquivos versionados que valem para qualquer sessao do Claude Code aberta neste repo.
Nada disso entra em imagem Docker nem em producao (os `api/Dockerfile*.dockerignore` sao
allowlist de `requirements*.txt`, `app`, `vendor/...` e `db/migrations`; o contexto e `api/`).
`.claude/settings.local.json` continua pessoal e fora deste conjunto.

## Hooks (`.claude/settings.json` -> `.claude/hooks/*.py`)

Python puro (stdlib), JSON do hook no stdin. Bloqueio = exit 2 + mensagem em portugues
no stderr dizendo a forma correta. Qualquer erro interno do script = exit 0 (bug do hook
nunca trava o trabalho). Comando: `python "$CLAUDE_PROJECT_DIR/.claude/hooks/<script>.py"`
(roda no Git Bash; funciona com `CLAUDE_PROJECT_DIR` em barras invertidas).

| Hook | Quando | Bloqueia | Incidente |
|---|---|---|---|
| `guarda_comandos.py` | PreToolUse `Bash\|PowerShell` | `git worktree remove <wt>` com `<wt>/frontend/node_modules` existente (junction, link ou pasta) | junction apagou o node_modules do usuario em 22/09 e 04/10 |
| | | `terraform apply\|destroy\|import`, `terraform state rm\|mv` (init/plan/validate/fmt passam) | regra: infra so via PR, quem aplica e o pipeline |
| | | `aws <servico> <op>` mutavel (`create-`, `delete-`, `put-`, `update-`, `attach-`, `detach-`, `modify-`, `remove-`, `add-`, `tag-`, `untag-`, `invoke`, `start-`, `stop-`, `terminate-`, `run-instances`, `publish`, `set-`, `reboot-`, `restore-`); `s3 rm\|mb\|rb`; `s3 cp\|sync` com destino `s3://`; `s3 mv` envolvendo `s3://` | regra: escrita na AWS so via PR/pipeline (excecao: o usuario roda `! <comando>`) |
| | | `gh pr merge` no mesmo comando que `gh pr checks ... \|` | #430 mesclado com plan vermelho em 08/10 |
| `guarda_arquivos.py` | PreToolUse `Edit\|Write\|MultiEdit\|NotebookEdit` | `api/requirements*.txt` (lock do pip-compile: editar o `.in` e rodar `sh api/scripts/lock-requirements.sh`) | lock com hashes (M47) |
| | | `frontend/package-lock.json` (vem de `npm install` + revisao) | npm do Windows remove `@emnapi` e quebra o `npm ci` do CI |
| | | qualquer caminho em `C:\openclaw-dev-agent` | projeto de origem congelado |
| `pos_edicao_python.py` | PostToolUse `Edit\|Write\|MultiEdit` | `.py` sob `api/` (fora de `api/vendor`): `ruff check` com `api/ruff.toml`; se o arquivo e citado no mapa `docs/motores/FONTES_DA_VERDADE.md` (ou e o teste da guarda), roda so `tests/unit/test_guarda_fontes_da_verdade.py` (~25 s) | regra "Motores: fonte unica da verdade" |

Como o `guarda_comandos.py` casa: o comando vira segmentos (`;`, `&&`, `\|\|`, `\|`, `&`,
nova linha, parenteses, chaves, `$(`), respeitando aspas e pulando corpo de heredoc/here-string;
so a primeira palavra de cada segmento conta como comando. `git commit -m "nao rodar terraform
apply"` e o heredoc de commit passam; `bash -c`, `sh -c`, `powershell -Command` e `cmd /c`
sao reanalisados.

O `pos_edicao_python.py` le o mapa em tempo de execucao (todo token entre crases terminado em
`.py`, comparado como sufixo do caminho): dono novo registrado no mapa ja e coberto. A raiz do
checkout vem do caminho do arquivo editado (funciona em worktree). Sem `ruff` no PATH ou sem
`pytest`: pula com aviso. Timeout de 60 s por subprocesso.

Limitacoes conhecidas (o hook erra para "deixar passar"):
- comando montado em variavel (`$CMD`, `eval`, `xargs`), alias, script `.sh` chamado pelo nome;
- `git worktree remove "$WT"` (caminho em variavel);
- `aws` via MCP (`aws-mcp run_script`) nao passa por Bash/PowerShell;
- o hook pos-edicao so olha `.py` de `api/`: copia de regra em `frontend/src` ou `api/privado/rag/markdown`
  so e pega pela guarda no CI (job backend unit);
- a guarda roda so quando o arquivo editado esta no mapa; copia nova em arquivo que o mapa nao
  cita so aparece no CI.

### Testar os hooks

```bash
python -m pytest .claude/hooks/tests -q -p no:cacheprovider   # ~8 s, sem banco/docker
ruff check .claude/hooks
# simulacao manual (como o Claude Code chama):
echo '{"tool_name":"Bash","tool_input":{"command":"terraform apply"}}' \
  | python .claude/hooks/guarda_comandos.py; echo "exit=$?"     # exit=2 + mensagem
```

O CI (`ci-develop.yml`) so coleta `api/tests/unit`; estes testes rodam localmente.

## Skills (`.claude/skills/`, so por invocacao explicita)

| Skill | Uso |
|---|---|
| `/atualizar-dev` | atualizar o DEV local depois de merges na develop, antes de pedir teste (merge da origin/develop, containers, migracao, frontend com `.next` limpo, verificacao). Script auxiliar: `.claude/skills/atualizar-dev/scripts/atualizar-backend.sh` |
| `/promover-producao` | ciclo develop -> main -> `deploy-producao.yml`. Comeca com a trava: so com permissao de producao dada na conversa para aquela promocao |

## Subagentes revisores (`.claude/agents/`, so leitura)

| Agente | Revisa |
|---|---|
| `guardiao-motores` | diff contra "Motores: fonte unica da verdade" (dono no mapa, numero a mao em prompt/RAG, regra no frontend, allowlist que cresceu, mapa desatualizado, semantica sem impacto medido) |
| `revisor-impacto-producao` | vazamento de config de dev (pymysql, LocalStack, hosts locais) em infra/workflows/Dockerfiles/Lambdas, bifurcacao dev/prod, migracao com FK para `exercise_catalog` ou nao idempotente, PR sem "Impacto em produção" provado |

`tools: Read, Grep, Glob, Bash` (Bash so para `git diff`/`git log`, dito no prompt). Para
revisar um PR, passe a branch e cole a descricao do PR no pedido.

## MCP (`.mcp.json`)

`playwright` -> `@playwright/mcp@0.0.83` (versao fixada, publicada em 2026-09-28), via
`cmd /c npx -y` (no Windows nativo o `npx` e um `.cmd` e precisa do `cmd /c`). Na primeira
sessao o Claude Code pede aprovacao do servidor do projeto. Para atualizar: escolher versao
com mais de 2 semanas (`npm view @playwright/mcp time --json`) e trocar o numero.
