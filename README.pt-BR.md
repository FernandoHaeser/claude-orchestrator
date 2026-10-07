# claude-orchestrator

Orquestrador que roda **5 agentes Claude em paralelo** numa feature, cada um isolado, com um painel ao vivo no terminal.

## A ideia em 30 segundos

Um agente só faz tudo em sequência e o contexto dele enche. Aqui, um **lead** planeja e 5 **especialistas** trabalham ao mesmo tempo, cada um na sua própria cópia do projeto. No fim, o trabalho é juntado numa branch para você revisar.

```
brief (sua feature)
   │
   ▼
CONTEXT.md   brief + arquivos do projeto + standards.md   (todos leem o mesmo)
   │
   ▼
LEAD (opus, só leitura) escreve:
   • CONTRACT  = stack, estrutura de arquivos, assinaturas, comandos
   • 1 tarefa por especialista
   │
   ▼  ONDA 1: em paralelo, cada um num git worktree próprio
┌─────────────┬─────────┬──────┬───────────┐
│ implementer │ tester  │ docs │ architect │
│ src/        │ tests/  │ docs │ docs/adr/ │
└──────┬──────┴────┬────┴──┬───┴─────┬─────┘
       └───────────┴───────┴─────────┘
                   │ merge → branch orch/<run>/integration
                   ▼
            ONDA 2: reviewer (lê o código já junto, escreve REVIEW.md)
                   │
                   ▼
            runs/<run>/SUMMARY.md  +  branch para você revisar
```

## Por que assim

| Decisão | Motivo |
|---|---|
| **Contrato primeiro** | Agentes paralelos não veem o trabalho um do outro. O contrato (assinaturas, arquivos) é o que os mantém compatíveis. |
| **Um worktree por agente** | Cada um tem pasta e branch próprias. Ninguém sobrescreve ninguém. |
| **Dono de arquivos por papel** | implementer=`src/`, tester=`tests/`, docs=`README.md`+`docs/`, architect=`docs/adr/`. Sem conflito no merge. |
| **Reviewer na onda 2** | Revisão precisa do código pronto. Não dá para paralelizar. |
| **Ferramentas mínimas por papel** | Ex.: architect e docs sem `Bash`. Menos risco, menos custo. |
| **Orçamento por agente** | `--max-budget-usd` (padrão US$ 2). |

Base: padrão *orchestrator-worker* da Anthropic. Multi-agente só compensa quando a tarefa se divide em partes independentes.

## Uso dentro de qualquer projeto: `/orch`

Abra o Claude no projeto e digite `/orch` (skill em `skill/orch/`, ligada a `~/.claude/skills/orch` por symlink).
1. Claude lê `CLAUDE.md`/README do projeto e faz as perguntas de pré-contexto.
2. Grava `<projeto>/.orch/brief.md`.
3. `orch/open-window.sh` abre uma **janela nova do Terminal** (ou split do tmux) com o mapa ao vivo.
4. Claude monitora e, ao terminar, lê `SUMMARY.md` e `REVIEW.md` e te reporta.

Dados da execução ficam em `<projeto>/.orch/` (escondido do git via `.git/info/exclude`). O projeto precisa estar com a árvore limpa: agentes partem do HEAD.

## Como usar (CLI direto)

```bash
cd <repo>

# 1. Simular (não gasta nada): vê o painel funcionando
./orch.sh "qualquer coisa" --dry-run

# 2. Rodar de verdade
./orch.sh "CLI que encurta URLs, com persistência em arquivo" \
  --project ~/my-project

# brief longo via arquivo
./orch.sh - < brief.md
```

O `brief` é **obrigatório**: é a descrição da feature. Sem ele o comando mostra o erro `the following arguments are required: brief`.

Opções: `--model` (workers, padrão `sonnet`), `--lead-model` (padrão `opus`), `--budget` (US$ por agente), `--dry-run`, `--speed`.

## O painel

Dois modos, escolha com `--view map|table|both` (padrão `both`): **mapa** do fluxo (lead → 4 agentes paralelos → merge → reviewer, setas ficam verdes quando a etapa termina) e **tabela** com detalhes.

```
 ✔  lead         done      finished                5   1.4s  $0.065
 ●  implementer  running   Write src/shortener.py  3   9.2s  $0.210
 ·  reviewer     queued    -                       0   0.0s  $0.000
```

Colunas: status, atividade atual (tool em uso), nº de tool calls, tempo, custo. Vem do `--output-format stream-json` de cada `claude -p`.

## O que sai

```
runs/<run-id>/
├── CONTEXT.md        contexto compartilhado
├── plan.json         contrato + tarefas do lead
├── agents/*.jsonl    log bruto de cada agente (debug)
├── SUMMARY.md        relatório de cada agente + custo
└── wt/<papel>/       worktree de cada agente
```

O resultado final está na branch `orch/<run-id>/integration` do projeto alvo, em `runs/<run-id>/wt/integration`. **`main` nunca é alterada.** Para aceitar: revise, depois faça o merge você mesmo.

## Arquivos

| Arquivo | Para quê |
|---|---|
| `orch.sh` | launcher (usa o venv com `rich`) |
| `orch/orchestrator.py` | fases, subprocessos, worktrees, painel |
| `orch/standards.md` | padrões de engenharia (clean code, escala, segurança, testes, docs) injetados em todos os agentes. **Edite aqui para mudar o comportamento de todos.** |
| `orch/roles/*.md` | prompt, ferramentas e arquivos de cada papel. **Edite aqui para ajustar um agente.** |

## Notas e limites atuais

- O modo real (`claude -p`) ainda **não foi testado**; só o `--dry-run`.
- O orquestrador faz commits **apenas** nas branches efêmeras `orch/*` do projeto alvo (autor `orchestrator`), só para viabilizar o merge.
- Hooks/plugins do seu `~/.claude` (ex.: caveman) podem rodar dentro dos workers. A verificar.
- Conflito de merge: o agente é pulado e listado no `SUMMARY.md` para resolução manual.
- Limpeza: `git worktree prune` e apagar `runs/<id>` quando não precisar mais.

## Próximos passos possíveis

1. Teste real com feature pequena.
2. Perguntas interativas de pré-contexto (stack, restrições, nível de qualidade).
3. Papéis configuráveis por tipo de feature (front, API, infra, dados).
4. Ajustar o `CLAUDE.md` global com as regras de orquestração.
