# Plano Unificado de Implementação: módulo `animator/` no NOCA

## Origem

Este plano consolida duas propostas anteriores:

- [ANIMATOR_IMPLEMENTATION_PLAN.md](ANIMATOR_IMPLEMENTATION_PLAN.md) — base principal, com foco em live scoreboard e reveal como runtime independente
- [revelator-noca.md](revelator-noca.md) — contribui o conceito de sedes com medalhas por sede e autenticação por secret

Documento de referência arquitetural:

- [ANIMATOR_MODULE_PROPOSAL.md](ANIMATOR_MODULE_PROPOSAL.md)

## Decisões de consolidação

| Aspecto | Decisão | Origem |
|---------|---------|--------|
| Nome do módulo | `animator/` | ANIMATOR_IMPLEMENTATION_PLAN |
| Entrypoint | `noca-animator` | ANIMATOR_IMPLEMENTATION_PLAN |
| Faseamento | live scoreboard primeiro, reveal depois | ANIMATOR_IMPLEMENTATION_PLAN |
| Extração de score para `shared/` | sim | ANIMATOR_IMPLEMENTATION_PLAN |
| Streaming | SSE (não WebSocket) | ANIMATOR_IMPLEMENTATION_PLAN |
| Sedes com regex e medalhas | sim | revelator-noca |
| Secrets por sede para operadores | sim | revelator-noca |
| Admin de sedes no `web/` | sim | revelator-noca |
| Reveal sede-aware | sim | revelator-noca |
| Estado de reveal em Valkey | sim (evolução, não MVP) | revelator-noca |

## Princípios de implementação

1. Não acoplar `animator/` a `web/services/`.
2. Extrair lógica pura reutilizável para `shared/`.
3. Entregar primeiro live scoreboard.
4. Adicionar sedes e reveal só depois da base estar estável.
5. Tratar dados ricos de times como evolução, não como bloqueio do MVP.
6. Sedes são conceito do domínio do NOCA, administradas no `web/`, consumidas pelo `animator/`.

## Resultado final esperado

Ao final das fases principais, o NOCA deve ter:

- novo runtime `noca-animator`
- frontend próprio de apresentação
- consumo de PostgreSQL e Valkey sem dependência direta do `web`
- placar animado em tempo real
- conceito de sedes com filtragem de times e medalhas por sede
- reveleitor pós-freeze com suporte a sedes
- autenticação de operador por secret por sede
- endpoints para dados de time e controle de sessão

## Visão geral por fases

| Fase | Objetivo | Dependência |
|------|----------|-------------|
| 0 | Preparação estrutural | nenhuma |
| 1 | Extração da lógica pura de placar | Fase 0 |
| 2 | MVP do runtime `animator/` | Fase 1 |
| 3 | Streaming e UI animada | Fase 2 |
| 4 | Sedes: modelo de dados e admin no `web/` | Fase 0 (independente de 1-3) |
| 5 | Reveal engine pós-freeze (sede-aware) | Fases 3 e 4 |
| 6 | Dados ricos de time | Fase 2 |
| 7 | Robustez operacional | Fase 5 |

Nota: a Fase 4 (Sedes) pode ser executada em paralelo com as Fases 1-3, já que o trabalho é no módulo `web/` e no `shared/db_schema.py`.

---

## Fase 0: Preparação estrutural

Objetivo: preparar o repositório para suportar um terceiro runtime e lógica compartilhada.

### 0.1. Atualizar empacotamento do projeto

Arquivo:

- [pyproject.toml](/home/dclobato/noca/pyproject.toml)

Mudanças:

- incluir `animator` em `tool.hatch.build.targets.wheel.packages`
- adicionar novo entrypoint:
  - `noca-animator = "animator.main:main"`

Resultado esperado:

- o projeto passa a reconhecer formalmente um terceiro runtime

### 0.2. Criar o pacote `animator/`

Arquivos novos mínimos:

- `/home/dclobato/noca/animator/__init__.py`
- `/home/dclobato/noca/animator/main.py`
- `/home/dclobato/noca/animator/config.py`
- `/home/dclobato/noca/animator/database.py`

Responsabilidade inicial:

- bootstrap do processo
- config
- DB session factory
- conexão com Valkey

Observação:

- a primeira versão pode espelhar a estrutura de `web/main.py`, mas sem templates e rotas complexas logo de início

### 0.3. Definir configuração do módulo

Arquivos:

- `/home/dclobato/noca/animator/config.py`
- [docs/CONFIG.md](/home/dclobato/noca/docs/CONFIG.md)

Variáveis iniciais sugeridas:

- `NOCA_ANIMATOR_HOST`
- `NOCA_ANIMATOR_PORT`
- `NOCA_ANIMATOR_POLL_FALLBACK_SECONDS`

Variáveis adicionais que serão necessárias na Fase 5:

- `NOCA_ANIMATOR_ENABLE_CONTROL` — habilita endpoints de controle do reveleitor

Resultado esperado:

- o módulo consegue subir com configuração própria sem reusar indevidamente a config do `web`

---

## Fase 1: Extração da lógica pura de placar

Objetivo: mover a semântica de score para `shared/`.

### 1.1. Criar DTOs de placar em `shared/`

Arquivo novo:

- `/home/dclobato/noca/shared/services/scoreboard_projection.py`

Conteúdo inicial:

- `ProblemResult`
- `TeamStanding`
- `ScoreboardSnapshot`
- helpers de serialização

Origem:

- extrair de [web/services/scoreboard.py](/home/dclobato/noca/web/services/scoreboard.py)

Resultado esperado:

- `web` e `animator` podem compartilhar o mesmo modelo de snapshot

### 1.2. Extrair o cálculo puro de score

Arquivo de destino:

- `/home/dclobato/noca/shared/services/scoreboard_projection.py`

Mover:

- `_compute_icpc`
- helpers puros associados
- regras de:
  - freeze visível
  - tentativas penalizantes
  - solved/pending
  - ordenação do placar

Resultado esperado:

- uma única implementação de score

### 1.3. Adaptar o `web` para consumir a lógica extraída

Arquivo:

- [web/services/scoreboard.py](/home/dclobato/noca/web/services/scoreboard.py)

Mudanças:

- manter apenas:
  - queries
  - integração com cache
  - adaptação de entrada/saída
- parar de ser dono da lógica de placar

Resultado esperado:

- `web` segue funcionando igual
- `animator` pode reutilizar score sem importar `web/services`

### 1.4. Cobrir com testes

Arquivos:

- `/home/dclobato/noca/tests/test_scoreboard_projection.py`
- adaptar [tests/test_scoreboard.py](/home/dclobato/noca/tests/test_scoreboard.py)

Cobertura mínima:

- cálculo sem freeze
- cálculo com freeze para visão pública
- ordenação do ranking
- pending cells
- casos com `accept_pe` e `ce_adds_penalty`

---

## Fase 2: MVP do runtime `animator/`

Objetivo: subir o novo processo com live scoreboard.

### 2.1. Criar aplicação FastAPI mínima do animator

Arquivos novos:

- `/home/dclobato/noca/animator/main.py`
- `/home/dclobato/noca/animator/dependencies.py`
- `/home/dclobato/noca/animator/routes/health.py`
- `/home/dclobato/noca/animator/routes/public.py`

Responsabilidades:

- subir app
- abrir DB pool
- abrir runtime Valkey
- expor healthcheck
- expor página inicial do animator

Resultado esperado:

- `noca-animator` sobe isoladamente

### 2.2. Implementar serviço de leitura de contest para animator

Arquivo novo:

- `/home/dclobato/noca/animator/services/contest_feed_service.py`

Responsabilidades:

- carregar contest
- carregar times
- carregar problemas
- carregar submissões e julgamentos
- produzir snapshot inicial

Dependências permitidas:

- `shared/db_schema.py`
- `shared/services/scoreboard_projection.py`
- models ou queries locais do próprio `animator`

Resultado esperado:

- bootstrap do placar animado sem depender do `web`

### 2.3. Expor endpoint de snapshot

Arquivo:

- `/home/dclobato/noca/animator/routes/public.py`

Endpoints iniciais:

- `GET /animator/c/{slug}/snapshot`
- `GET /animator/c/{slug}/meta`

Conteúdo mínimo:

- snapshot do placar
- problemas
- balloon colors
- timer do contest

### 2.4. Implementar frontend simples do animator

Arquivos novos:

- `/home/dclobato/noca/animator/template/animator.html`
- `/home/dclobato/noca/animator/static/css/animator.css`
- `/home/dclobato/noca/animator/static/js/animator.js`

Escopo inicial:

- renderizar standings
- renderizar problemas por time
- renderizar timer

Sem escopo nesta etapa:

- reveal
- controle remoto
- UI sofisticada

---

## Fase 3: Streaming e UI animada

Objetivo: entregar atualização visual ao vivo.

### 3.1. Criar serviço de subscription a `VerdictEvent`

Arquivo novo:

- `/home/dclobato/noca/animator/services/event_stream_service.py`

Responsabilidades:

- assinar `judge:results`
- filtrar por contest
- emitir eventos internos do animator

Reuso:

- `shared.services.valkey_service.ValkeyRuntime`
- `shared.queue_schema.VerdictEvent`

### 3.2. Expor stream para o frontend

Escolha: SSE.

Arquivo:

- `/home/dclobato/noca/animator/routes/public.py`

Endpoint:

- `GET /animator/c/{slug}/events`

Eventos mínimos:

- `verdict`
- `scoreboard_refresh`
- `timer_tick`

Heartbeat de 15s para manter a conexão viva.

### 3.3. Atualizar o frontend com base em eventos

Arquivo:

- `/home/dclobato/noca/animator/static/js/animator.js`

Responsabilidades:

- abrir EventSource
- buscar novo snapshot quando necessário
- animar mudanças de rank e célula

Recomendação:

- começar com refresh incremental simples
- só depois otimizar para diffs finos

---

## Fase 4: Sedes — modelo de dados e admin no `web/`

Objetivo: introduzir o conceito de sedes regionais com filtragem de times e configuração de medalhas. Este trabalho é inteiramente no módulo `web/` e em `shared/`, e pode ser executado em paralelo com as Fases 1-3.

### 4.1. Criar tabelas de sedes e secrets no banco

Arquivo:

- [shared/db_schema.py](/home/dclobato/noca/shared/db_schema.py)

#### Tabela `contest_sedes`

```
id             String(36) PK
contest_id     FK → contests.id  NOT NULL
name           String(200)       NOT NULL   # ex: "Regional SP"
codes          JSON              NOT NULL   # array de regex, ex: ["teambr_sp_.*"]
gold_cutoff    Integer default 1
silver_cutoff  Integer default 2
bronze_cutoff  Integer default 3
style          String(100) nullable         # classe CSS opcional
created_at, updated_at
UniqueConstraint(contest_id, name)
```

#### Tabela `contest_sede_secrets`

```
id         String(36) PK
sede_id    FK → contest_sedes.id  NOT NULL
secret     String(256)            NOT NULL
label      String(200)            NOT NULL   # ex: "Operador SP"
created_at
```

Resultado esperado:

- o banco suporta sedes com filtragem regex e medalhas

### 4.2. Criar migration Alembic

Arquivo novo:

- `/home/dclobato/noca/migrations/versions/TIMESTAMP_add_contest_sedes.py`

Migration com `upgrade()` e `downgrade()` para as duas tabelas.

### 4.3. Criar serviço de sedes no `web/`

Arquivo novo:

- `/home/dclobato/noca/web/services/sede_service.py`

Funções:

- `list_sedes(db, contest_id) → list[SedeRow]`
- `get_sede(db, sede_id) → SedeRow | None`
- `create_sede(db, contest_id, data) → SedeRow`
- `update_sede(db, sede_id, data) → SedeRow`
- `delete_sede(db, sede_id) → None`
- `list_secrets(db, sede_id) → list[SecretRow]`
- `create_secret(db, sede_id, label) → str` — gera e persiste o secret, retorna o valor para exibição única
- `delete_secret(db, secret_id) → None`
- `get_sede_by_secret(db, contest_id, secret) → SedeRow | None` — usado pelo animator na Fase 5

Padrão: SQLAlchemy Core, como o restante do `web/services/`.

### 4.4. Criar rotas de admin de sedes no `web/`

Arquivo novo:

- `/home/dclobato/noca/web/routes/contest_admin_sede.py`

Router: `APIRouter(prefix="/c/{slug}/admin/sedes", tags=["contest_admin_sede"])`

Rotas:

- `GET /` — listar sedes
- `GET /new` — formulário de criação
- `POST /new` — persistir nova sede
- `GET /{sede_id}` — editar sede
- `POST /{sede_id}` — salvar edição
- `POST /{sede_id}/delete` — remover sede
- `POST /{sede_id}/secrets/new` — gerar novo secret (exibe valor uma única vez)
- `POST /{sede_id}/secrets/{secret_id}/delete` — revogar secret

Dependency: reutilizar `ContestAdminContext` existente.

### 4.5. Criar templates de admin de sedes

Arquivos novos:

- `/home/dclobato/noca/web/templates/contest_admin_sede/list.html`
- `/home/dclobato/noca/web/templates/contest_admin_sede/form.html`
- `/home/dclobato/noca/web/templates/contest_admin_sede/secrets.html`
- `/home/dclobato/noca/web/templates/contest_admin_sede/secret_reveal.html`

### 4.6. Registrar o novo router no `web/main.py`

Arquivo:

- [web/main.py](/home/dclobato/noca/web/main.py)

Mudança:

- `app.include_router(contest_admin_sede.router)`

---

## Fase 5: Reveal engine pós-freeze (sede-aware)

Objetivo: implementar revelação progressiva com suporte a sedes e medalhas.

### 5.1. Criar modelo de estado de revelação

Arquivo novo:

- `/home/dclobato/noca/animator/models/reveal_session.py`

Tipos:

```python
class RevealSessionState(BaseModel):
    contest_id: str
    sede_id: str | None              # None = reveal global
    sede_name: str | None
    phase: Literal["idle", "revealing", "done"]
    current_team_index: int          # cursor na fila (bottom-up)
    teams: list[TeamRevealState]     # ordenados por rank final
    medal_cutoffs: MedalCutoffs | None

class MedalCutoffs(BaseModel):
    gold: int       # posição máxima para ouro
    silver: int     # posição máxima para prata
    bronze: int     # posição máxima para bronze

class TeamRevealState(BaseModel):
    team_id: str
    team_name: str
    sede_name: str | None
    frozen_rank: int
    current_rank: int
    problems: dict[str, ProblemRevealState]

class ProblemRevealState(BaseModel):
    frozen_attempts: int
    frozen_solved: bool
    frozen_pending: bool
    revealed: bool
    final_verdict: str | None        # "Y" | "N" | None
    final_attempts: int
    final_time: int | None

class RevealQueueEntry(BaseModel):
    team_id: str
    problem_label: str
    runs_to_reveal: list[str]        # run ids
```

Estado mínimo:

- contest id
- sede id (se reveal filtrado por sede)
- snapshot congelado inicial
- runs pós-freeze ainda não reveladas
- fila de revelação
- cursor atual
- status da sessão
- cutoffs de medalha da sede

### 5.2. Criar motor de revelação

Arquivo novo:

- `/home/dclobato/noca/animator/services/reveal_engine.py`

Responsabilidades:

- reconstruir snapshot congelado
- filtrar times por sede (usando regex de `contest_sedes.codes`)
- identificar runs pós-freeze
- aplicar passos de revelação
- recalcular ranking após cada passo
- aplicar cutoffs de medalha da sede ao ranking

#### Mapeamento de veredictos NOCA para revelação

| NOCA `Verdict` | Formato revelação |
|----------------|-------------------|
| `AC` (ou `PE` se `accept_pe`) | `"Y"` — Aceito |
| `WA`, `RE`, `TLE`, `MLE`, `OLE`, `OE` | `"N"` — Errado |
| `CE` | `"N"` ou ignorado, conforme `ce_adds_penalty` |
| `final_verdict IS NULL` | Pendente/congelado |

#### Filtragem de times por sede

A lógica de filtragem usa os padrões regex definidos em `contest_sedes.codes`:

1. carregar os patterns da sede
2. para cada time do contest, testar `username` contra cada pattern
3. incluir no reveal apenas os times que casam com pelo menos um pattern

Esta é a mesma semântica do `maratona-animeitor` com os arquivos TOML de config.

#### Medalhas

Após cada passo de revelação, o ranking atualizado é comparado com os cutoffs da sede:

- rank <= `gold_cutoff` → medalha de ouro
- rank <= `silver_cutoff` → medalha de prata
- rank <= `bronze_cutoff` → medalha de bronze

A UI deve destacar visualmente as faixas de medalha.

Resultado esperado:

- reveal coerente com o placar do próprio NOCA
- suporte a reveal global ou por sede

### 5.3. Criar endpoints de controle da sessão

Arquivo novo:

- `/home/dclobato/noca/animator/routes/control.py`

Endpoints:

- `POST /animator/c/{slug}/control/start-reveal` — inicia sessão de reveal
  - parâmetros: `sede_id` (opcional), `secret`
- `POST /animator/c/{slug}/control/step` — avança um passo
  - parâmetro: `secret`
- `POST /animator/c/{slug}/control/back` — volta um passo
  - parâmetro: `secret`
- `POST /animator/c/{slug}/control/reset` — reseta sessão
  - parâmetro: `secret`
- `POST /animator/c/{slug}/control/jump-team` — pula para time específico
  - parâmetros: `team_id`, `secret`
- `GET /animator/c/{slug}/control/state` — estado atual da sessão
  - parâmetro: `secret`

#### Autenticação por secret

O controle da revelação é protegido por secrets vinculados a sedes:

1. o operador envia o `secret` como parâmetro
2. o animator valida via `sede_service.get_sede_by_secret()`
3. se o secret for válido, o operador controla o reveal daquela sede
4. se inválido, retorna 403

Um secret autoriza controle apenas da sede à qual pertence. O reveal global (sem sede) requer um mecanismo próprio de autorização (a definir: pode ser o secret de admin do contest ou uma variável de ambiente).

### 5.4. Publicar eventos de revelação via Valkey

Arquivo:

- [shared/services/valkey_service.py](/home/dclobato/noca/shared/services/valkey_service.py)

Adicionar a `ValkeyRuntime`:

- `async def publish_revelation(self, channel: str, event_json: str) → None`
- `async def iter_revelation_events(self, channel: str) → AsyncIterator[str]`

Canal: `revelation:events:{contest_id}:{sede_id}`

O animator publica eventos a cada mudança de estado do reveal, e o SSE os repassa para os clientes conectados.

### 5.5. Criar UI de reveleitor

Arquivos novos:

- `/home/dclobato/noca/animator/template/reveleitor.html`
- `/home/dclobato/noca/animator/template/control.html`
- `/home/dclobato/noca/animator/static/js/reveleitor.js`
- `/home/dclobato/noca/animator/static/css/reveleitor.css`

Funcionalidades mínimas:

- renderizar placar congelado
- avançar um passo
- voltar
- resetar
- destacar time em foco
- exibir faixas de medalha (ouro/prata/bronze) conforme cutoffs da sede
- exibir nome da sede, se aplicável

Rotas de acesso:

- `GET /animator/c/{slug}/reveleitor` — página dos espectadores (projeção)
- `GET /animator/c/{slug}/control?secret={key}` — painel do operador

---

## Fase 6: Dados ricos de time

Objetivo: suportar instituição, mídia e metadados de apresentação.

### 6.1. Entregar MVP com dados existentes

Sem migração inicial.

Usar:

- `username`
- `fullname`
- `foto_base64`
- `avatar_base64`
- `foto_mime`

Rotas:

- `GET /animator/c/{slug}/teams`
- `GET /animator/c/{slug}/teams/{team_id}/photo`
- `GET /animator/c/{slug}/teams/{team_id}/avatar`

### 6.2. Definir modelo complementar de perfil de apresentação

Arquivos a criar:

- migration nova
- extensão em `shared/db_schema.py`
- model ou query layer no `animator`

Tabela sugerida:

- `contest_team_profiles`

Campos:

- `contest_id`
- `team_id`
- `display_name`
- `institution_name`
- `institution_short_name`
- `theme_color`
- `media_json`
- `soundtrack_path`

### 6.3. Serviço de perfil de time

Arquivo novo:

- `/home/dclobato/noca/animator/services/team_profile_service.py`

Responsabilidades:

- mesclar `users` com `contest_team_profiles`
- construir view model para a UI

---

## Fase 7: Robustez operacional

Objetivo: estabilizar deploy, observabilidade e testes ponta a ponta.

### 7.1. Persistência da sessão de reveal em Valkey

Primeira versão (Fase 5): estado em memória.

Segunda versão (esta fase): persistência em Valkey.

Chave: `revelation:{contest_id}:{sede_id}`
TTL: duração do contest + margem.

Arquivo novo:

- `/home/dclobato/noca/animator/services/reveal_session_store.py`

Motivação:

- sobrevive a reinício do processo
- permite controle remoto mais robusto
- prepara para múltiplas réplicas do animator

### 7.2. Observabilidade

Adicionar:

- logs estruturados do animator
- métricas de contest carregado
- métricas de clientes SSE conectados
- métricas de eventos recebidos
- métricas de sessões de reveal ativas
- métricas de acesso ao controle (válidos e inválidos)

### 7.3. Testes

Arquivos novos:

- `/home/dclobato/noca/tests/test_animator_snapshot.py`
- `/home/dclobato/noca/tests/test_animator_events.py`
- `/home/dclobato/noca/tests/test_reveal_engine.py`
- `/home/dclobato/noca/tests/test_sede_service.py`
- `/home/dclobato/noca/tests/test_team_profile_service.py`

Cobertura mínima:

- snapshot inicial
- atualização por evento
- reveal step / back / reset
- ranking após reveal
- filtragem de times por sede (regex)
- aplicação de cutoffs de medalha
- validação de secret
- reveal global vs reveal por sede

---

## Estrutura final do módulo `animator/`

```
animator/
├── __init__.py
├── main.py
├── config.py
├── database.py
├── dependencies.py
├── routes/
│   ├── public.py
│   ├── control.py
│   └── health.py
├── services/
│   ├── contest_feed_service.py
│   ├── event_stream_service.py
│   ├── reveal_engine.py
│   ├── reveal_session_store.py     # Fase 7
│   └── team_profile_service.py     # Fase 6
├── models/
│   └── reveal_session.py
├── template/
│   ├── animator.html
│   ├── reveleitor.html
│   └── control.html
└── static/
    ├── css/
    │   ├── animator.css
    │   └── reveleitor.css
    └── js/
        ├── animator.js
        └── reveleitor.js
```

## Ordem concreta de execução

### Etapa 1 (Fase 0)

- atualizar `pyproject.toml`
- criar `animator/` mínimo
- subir `noca-animator`

### Etapa 2 (Fase 1)

- extrair lógica pura de placar para `shared/`
- adaptar `web/services/scoreboard.py`
- validar que o `web` continua estável

### Etapa 3 (Fases 2 + 3)

- implementar `contest_feed_service.py`
- expor `snapshot` e `events`
- renderizar página básica do animator com atualização ao vivo

### Etapa 4 (Fase 4 — pode ser paralela a Etapa 2 e 3)

- criar tabelas `contest_sedes` e `contest_sede_secrets`
- criar migration Alembic
- implementar `sede_service.py` no `web/`
- criar rotas e templates de admin de sedes
- registrar router no `web/main.py`

### Etapa 5 (Fase 5)

- implementar `reveal_engine.py` com suporte a sedes
- implementar rotas de controle com auth por secret
- implementar pub/sub de revelação no Valkey
- implementar `reveleitor.html` e `control.html` com medalhas

### Etapa 6 (Fases 6 + 7)

- adicionar dados ricos de time, se necessário
- persistir sessão de reveal em Valkey
- endurecer operação e testes

## Arquivos existentes com maior chance de mudança

### Mudança certa

- [pyproject.toml](/home/dclobato/noca/pyproject.toml)
- [web/services/scoreboard.py](/home/dclobato/noca/web/services/scoreboard.py)
- [web/main.py](/home/dclobato/noca/web/main.py) — registrar router de sedes
- [docs/CONFIG.md](/home/dclobato/noca/docs/CONFIG.md)
- [docs/ARCHITECTURE.md](/home/dclobato/noca/docs/ARCHITECTURE.md)

### Mudança provável

- [shared/services/valkey_service.py](/home/dclobato/noca/shared/services/valkey_service.py) — pub/sub de revelação
- [shared/queue_schema.py](/home/dclobato/noca/shared/queue_schema.py)
- [shared/db_schema.py](/home/dclobato/noca/shared/db_schema.py) — tabelas de sedes

### Criação certa no `animator/`

- `animator/__init__.py`
- `animator/main.py`
- `animator/config.py`
- `animator/database.py`
- `animator/dependencies.py`
- `animator/routes/public.py`
- `animator/routes/control.py`
- `animator/routes/health.py`
- `animator/services/contest_feed_service.py`
- `animator/services/event_stream_service.py`
- `animator/services/reveal_engine.py`
- `animator/models/reveal_session.py`

### Criação certa no `web/` (sedes)

- `web/services/sede_service.py`
- `web/routes/contest_admin_sede.py`
- `web/templates/contest_admin_sede/list.html`
- `web/templates/contest_admin_sede/form.html`
- `web/templates/contest_admin_sede/secrets.html`
- `web/templates/contest_admin_sede/secret_reveal.html`

### Criação certa em `shared/`

- `shared/services/scoreboard_projection.py`

### Criação em migration

- `migrations/versions/TIMESTAMP_add_contest_sedes.py`

## Critério de sucesso por fase

### Fase 0 concluída

- `noca-animator` sobe e responde healthcheck

### Fase 1 concluída

- o `web` usa score compartilhado em `shared/`
- testes de score passam

### Fase 2 concluída

- `noca-animator` exibe snapshot inicial do placar

### Fase 3 concluída

- o animator reage a novos veredictos e atualiza a UI ao vivo

### Fase 4 concluída

- sedes podem ser criadas e editadas via admin do `web/`
- secrets podem ser gerados e revogados por sede
- admin exibe valor do secret uma única vez ao gerar

### Fase 5 concluída

- existe sessão de reveal com step/back/reset
- reveal pode ser filtrado por sede
- operador se autentica por secret
- UI exibe faixas de medalha conforme cutoffs da sede

### Fase 6 concluída

- a UI consegue exibir metadados visuais úteis de times

### Fase 7 concluída

- sessão de reveal persiste em Valkey
- módulo está testado e operacionalizável em produção

## O que este plano descarta dos planos originais

### Descartado do revelator-noca.md

| Item | Motivo |
|------|--------|
| Nome `revelation/` para o módulo | Consolidado como `animator/` para cobrir live scoreboard + reveal num módulo só |
| Entrypoint `noca-revelation` | Usa `noca-animator` |
| Config com `env_prefix="NOCA_"` genérico | Usa prefixo `NOCA_ANIMATOR_` para isolamento |
| Rotas sob `/c/{slug}/` diretamente | Usa prefixo `/animator/c/{slug}/` para evitar conflito com o `web/` |
| `scoreboard_builder.py` separado | A lógica de score fica em `shared/services/scoreboard_projection.py` |

### Mantido do revelator-noca.md

| Item | Onde entra |
|------|-----------|
| Tabelas `contest_sedes` e `contest_sede_secrets` | Fase 4 |
| Admin CRUD de sedes no `web/` | Fase 4 |
| Filtragem de times por regex da sede | Fase 5 |
| Cutoffs de medalha por sede (ouro/prata/bronze) | Fase 5 |
| Autenticação de operador por secret | Fase 5 |
| Persistência de estado de reveal em Valkey | Fase 7 |
| Mapeamento de veredictos NOCA → Y/N | Fase 5 |

## Recomendação final

O melhor caminho é começar com uma implementação enxuta, respeitando as fronteiras do NOCA:

1. extrair score para `shared/`
2. criar `animator/` como runtime independente
3. entregar live scoreboard primeiro
4. adicionar sedes como conceito do domínio, administrado no `web/`
5. entregar reveal engine sede-aware depois
6. evoluir os dados de time por migração específica, apenas se necessário

Sedes e medalhas por sede são um diferencial claro em relação ao `maratona-animeitor`, que depende de configuração TOML externa. No NOCA, sedes são cidadãs de primeira classe do domínio, gerenciadas pela interface de admin e consumidas nativamente pelo animator.
