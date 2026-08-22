# Plano Unificado de Implementação: módulo `animator/` no NOCA

## Origem

Este plano consolida duas propostas anteriores:

- [ANIMATOR_IMPLEMENTATION_PLAN.md](ANIMATOR_IMPLEMENTATION_PLAN.md) — base principal, com foco em live scoreboard e reveal como runtime independente
- [revelator-noca.md](revelator-noca.md) — contribui o conceito de sedes com medalhas por sede e autenticação por secret

Documento de referência arquitetural:

- [ANIMATOR_MODULE_PROPOSAL.md](ANIMATOR_MODULE_PROPOSAL.md)

## Estratégia: módulo nativo, desacoplado do Maratona

A decisão fundacional deste plano é construir o `animator/` como um **módulo nativo do
NOCA, totalmente desacoplado do `maratona-animeitor`**. O animator lê **diretamente** o
PostgreSQL e o Valkey do NOCA (pela mesma fronteira de infraestrutura que `web`, `arena` e
`autojudge` já usam), reconstrói o placar com a lógica de score do próprio NOCA e desenha
seu próprio frontend e motor de revelação.

Consequências desta decisão:

- **`compatible-layer/` está fora de escopo.** Não haverá exportação de webcast compatível
  com o BOCA nem reuso do consumidor/reveleitor do `maratona-animeitor`. Todo o contrato
  legado — separador `0x1C`, `time` em segundos vs. tempos em minutos, penalidade fixa em
  `20`, arquivos `icpc`/`version` não usados — é **irrelevante** para o animator.
- **Os documentos de `maratona-revelator-docs/` são prior art, não um contrato de
  compatibilidade.** Servem apenas para descrever *como uma cerimônia de revelação deve se
  comportar* para a plateia (fila bottom-up, orientada pelo ranking corrente, uma run
  congelada por vez, re-ordenando a cada passo). O animator reimplementa esse
  *comportamento* com o modelo de domínio limpo do NOCA — **nunca** copia o *formato* nem a
  semântica legada.
- **NOCA usa sua própria semântica de score.** O reveal reaproveita `compute_icpc`
  (penalidade por-contest, `accept_pe`, `ce_adds_penalty`) via `shared/`, e não a
  penalidade fixa em `20` do consumidor legado.
- **Sem dependência de tempo de execução do Maratona.** Nenhum binário, TOML de config
  externo, nem ZIP de webcast participam do fluxo do animator.

## Decisões de consolidação

| Aspecto | Decisão | Origem |
|---------|---------|--------|
| Acoplamento ao Maratona | nenhum — módulo nativo, desacoplado | NOCA |
| Camada de webcast compatível (`compatible-layer/`) | fora de escopo | NOCA |
| Maratona docs (`maratona-revelator-docs/`) | prior art de UX de cerimônia, não contrato | NOCA |
| Nome do módulo | `animator/` | ANIMATOR_IMPLEMENTATION_PLAN |
| Entrypoint | `noca-animator` | ANIMATOR_IMPLEMENTATION_PLAN |
| Faseamento | live scoreboard primeiro, reveal depois | ANIMATOR_IMPLEMENTATION_PLAN |
| Extração de score para `shared/` | sim | ANIMATOR_IMPLEMENTATION_PLAN |
| Streaming | SSE (não WebSocket) | ANIMATOR_IMPLEMENTATION_PLAN |
| Sedes = Site já existente + medalhas | sim (Site do NOCA, sem regex) | NOCA + revelator-noca |
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
6. Sedes já são conceito do domínio do NOCA (o **Site**, tabela `sites`, com times
   associados por `users.site_id`), administradas no `web/` e consumidas pelo `animator/`.
   A Fase 4 apenas **estende** o Site existente; não recria o conceito nem introduz regex.

## Resultado final esperado

Ao final das fases principais, o NOCA deve ter:

- novo runtime `noca-animator`
- frontend próprio de apresentação
- consumo de PostgreSQL e Valkey sem dependência direta do `web`
- placar animado em tempo real
- conceito de sedes com filtragem de times e medalhas por sede
- cerimônia de revelação pós-freeze com suporte a sedes
- autenticação de operador por secret por sede
- endpoints para dados de time e controle de sessão

## Visão geral por fases

| Fase | Objetivo | Dependência |
|------|----------|-------------|
| 0 | Preparação estrutural | nenhuma |
| 1 | Extração da lógica pura de placar | Fase 0 |
| 2 | MVP do runtime `animator/` | Fase 1 |
| 3 | Streaming e UI animada | Fase 2 |
| 4 | Sedes (Site): estender modelo existente + admin no `web/` | Fase 0 (independente de 1-3) |
| 5 | Reveal engine pós-freeze (sede-aware) | Fases 3 e 4 |
| 6 | Dados ricos de time | Fase 2 |
| 7 | Robustez operacional | Fase 5 |

Nota: a Fase 4 (Sedes) pode ser executada em paralelo com as Fases 1-3, já que o trabalho é no módulo `web/` e no `shared/db_schema/`.

---

## Fase 0: Preparação estrutural

Objetivo: preparar o repositório para suportar um novo runtime e lógica compartilhada.

### 0.1. Registrar o novo membro no workspace uv

O repositório é um **workspace uv** de 7 membros, cada um com seu próprio
`pyproject.toml` (com `[project.scripts]` e build Hatchling `packages = ["."]` /
`dev-mode-dirs = [".."]`). O root `pyproject.toml` **não** tem
`tool.hatch.build.targets.wheel.packages` nem `[project.scripts]` — ele apenas declara
os membros e as sources do workspace.

Arquivos:

- [pyproject.toml](/home/dclobato/noca/pyproject.toml) (root)
- `/home/dclobato/noca/animator/pyproject.toml` (novo membro)

Mudanças:

- criar `animator/pyproject.toml` espelhando um membro existente (ex: `web/pyproject.toml`),
  incluindo:
  - `[project.scripts]` → `noca-animator = "animator.main:main"`
  - build Hatchling com `packages = ["."]` e `dev-mode-dirs = [".."]`
  - dependência de `noca-shared`
- no root `pyproject.toml`:
  - adicionar `"animator"` em `[tool.uv.workspace] members`
  - adicionar `noca-animator = { workspace = true }` em `[tool.uv.sources]`

Resultado esperado:

- o projeto passa a reconhecer formalmente um novo runtime como membro do workspace

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

- `NOCA_ANIMATOR_ENABLE_CONTROL` — habilita endpoints de controle da cerimônia

Resultado esperado:

- o módulo consegue subir com configuração própria sem reusar indevidamente a config do `web`

---

## Fase 1: Extração da lógica pura de placar

Objetivo: mover a semântica de score para `shared/`.

### 1.1. Mover os DTOs de placar para `shared/`

Arquivo novo:

- `/home/dclobato/noca/shared/services/scoreboard_projection.py`

Os DTOs **já existem** como `@dataclass` em
[web/services/scoreboard/models.py](/home/dclobato/noca/web/services/scoreboard/models.py)
(o `web/services/scoreboard` é um **pacote**, não um módulo único). Esta etapa **move**
esses tipos para o `shared/`, não os cria do zero:

- `ProblemResult`
- `TeamStanding`
- `ScoreboardSnapshot`
- helpers de serialização

Origem:

- [web/services/scoreboard/models.py](/home/dclobato/noca/web/services/scoreboard/models.py)

Resultado esperado:

- `web` e `animator` podem compartilhar o mesmo modelo de snapshot

### 1.2. Extrair o cálculo puro de score

Arquivo de destino:

- `/home/dclobato/noca/shared/services/scoreboard_projection.py`

Mover de
[web/services/scoreboard/computation.py](/home/dclobato/noca/web/services/scoreboard/computation.py):

- `compute_icpc` (função pública; usa `contest.accept_pe` e `contest.ce_adds_penalty`)
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

- [web/services/scoreboard/](/home/dclobato/noca/web/services/scoreboard/) (pacote:
  `models.py`, `computation.py`, `service.py`)

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
- adaptar [tests/web/test_scoreboard.py](/home/dclobato/noca/tests/web/test_scoreboard.py)

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

- `shared/db_schema/`
- `shared/services/scoreboard_projection.py`
- models ou queries locais do próprio `animator`

Resultado esperado:

- bootstrap do placar animado sem depender do `web`

### 2.3. Expor endpoint de snapshot

Arquivo:

- `/home/dclobato/noca/animator/routes/public.py`

Endpoints iniciais:

- `GET /c/{slug}/snapshot`
- `GET /c/{slug}/meta`

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

- `GET /c/{slug}/events`

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

## Fase 4: Sedes (Sites) — estender o conceito existente e admin no `web/`

Objetivo: reaproveitar o conceito de **Site** que já é cidadão de primeira classe do
domínio do NOCA (a "Sede" deste plano **é** o Site existente) e estendê-lo com o que
falta para o reveal por sede: cutoffs de medalha e secrets de operador.
Este trabalho é inteiramente no módulo `web/` e em `shared/`, e pode ser executado em
paralelo com as Fases 1-3.

> **O que já existe no NOCA (não recriar):**
>
> - Tabela `sites` (`shared/db_schema/contest.py`): `id`, `sitename`,
>   `sitename_normalized`, `contest_id`, timestamps, com unicidade
>   `(contest_id, sitename_normalized)`.
> - Associação time → sede por **FK explícita** `users.site_id` (mais o campo
>   `users.location` para sala/laboratório dentro da sede). **Não há regex de codes** —
>   a filtragem de times por sede é uma simples query por `site_id`, não um casamento de
>   padrões contra o `username`.
> - CRUD completo de sites via admin de metadados do contest
>   (`web/services/site_service.py`: `list_contest_sites`, `create_site`, `remove_site`,
>   `sync_contest_sites`, etc.) e relatório "usuários por sede"
>   (`web/services/users_per_site_report_service.py`).
>
> Por isso a Fase 4 **não cria** a tabela de sedes nem o CRUD básico — apenas **estende**
> o que já existe. A abordagem de regex do `revelator-noca.md` fica descartada em favor da
> associação por FK que o NOCA já modela.

### 4.1. Estender a tabela `sites` com medalhas

Arquivo:

- [shared/db_schema/contest.py](/home/dclobato/noca/shared/db_schema/contest.py)

Adicionar colunas à tabela `sites` já existente (todas com default para não quebrar linhas
antigas):

```
gold_cutoff    Integer default 1     # posição máxima para ouro
silver_cutoff  Integer default 2     # posição máxima para prata
bronze_cutoff  Integer default 3     # posição máxima para bronze
```

#### Flag de habilitação no contest

Adicionar à tabela `contests`:

```
animator_enabled  Boolean  default false, server_default false, NOT NULL
```

Gate consumido pela Fase 5: o animator só expõe snapshot/eventos/reveal de contests com a
flag ligada (senão `404`).

#### Nova tabela `site_secrets`

Segue o naming existente (`sites`), não `contest_sede_secrets`. O `site_id` é **nullable**:
uma linha com `site_id` preenchido é secret de sede; com `site_id = NULL` é um **secret de
controle contest-global** (usado pelo reveal global na Fase 5). O `contest_id` é sempre
obrigatório, então mesmo um secret global fica atrelado ao contest:

```
id          String(36) PK
contest_id  FK → contests.id  ON DELETE CASCADE  NOT NULL
site_id     FK → sites.id     ON DELETE CASCADE  NULL   # NULL = controle global do contest
secret      String(256)                          NOT NULL
label       String(200)                          NOT NULL   # ex: "Operador SP" / "Global"
created_at
UniqueConstraint(contest_id, secret)
```

Registrar a tabela em `shared/db_schema/__init__.py` (import e `__all__`), como já é feito
para `sites`.

Resultado esperado:

- os sites passam a carregar cutoffs de medalha e secrets de operador
- o contest tem gate de habilitação e um secret de controle global opcional

### 4.2. Criar migration Alembic

Arquivo novo:

- `/home/dclobato/noca/migrations/versions/TIMESTAMP_extend_sites_for_animator.py`

Migration com `upgrade()` e `downgrade()` para: (a) `ALTER TABLE sites ADD COLUMN` das
três colunas de medalha; (b) `ALTER TABLE contests ADD COLUMN animator_enabled`;
(c) `CREATE TABLE site_secrets` (com `site_id` nullable e `contest_id` NOT NULL). O
`downgrade()` derruba `site_secrets` e remove todas as colunas adicionadas.

### 4.3. Estender o serviço de sites no `web/`

Arquivo:

- [web/services/site_service.py](/home/dclobato/noca/web/services/site_service.py)

O CRUD de sites (`list_contest_sites`, `create_site`, `remove_site`, `sync_contest_sites`,
`get_site_in_contest`) **já existe** — reaproveitar. Adicionar apenas as funções novas:

- `update_site_medals(session, site, gold, silver, bronze) → Site` — atualiza
  cutoffs de um site existente
- `list_site_secrets(session, site_id) → list[SiteSecret]`
- `create_site_secret(session, site, label) → str` — gera e persiste o secret de sede,
  retorna o valor em claro para exibição única
- `create_global_secret(session, contest, label) → str` — idem, mas com `site_id=NULL`
  (secret de controle contest-global)
- `delete_site_secret(session, secret_id) → None`
- `get_site_by_secret(session, contest_id, secret) → Site | None` — reveal por sede
  (Fase 5); casa apenas secrets com `site_id` preenchido
- `get_contest_by_global_secret(session, contest_id, secret) → Contest | None` — reveal
  global (Fase 5); casa apenas secrets com `site_id=NULL`

Padrão: mesmo estilo do `site_service.py` atual (ORM/`select`, como o restante do
`web/services/`).

### 4.4. Expor a admin no `web/`

As sedes já são administradas na página de metadados do contest
(`web/routes/contest_admin_metadata.py`, via `list_contest_site_entries` /
`parse_site_names_payload` / `sync_contest_sites`). A extensão desta fase acrescenta a
essa admin existente:

- toggle `animator_enabled` do contest
- campos de cutoff (ouro/prata/bronze) por sede
- subseção de secrets: gerar novo secret de sede **ou** global (exibindo o valor **uma
  única vez**) e revogar secret

Se a admin de metadados ficar sobrecarregada, extrair uma página dedicada por sede
reutilizando o `ContestAdminContext` existente; caso contrário, estender os templates de
metadados atuais. Decidir na implementação conforme o tamanho do template resultante (ver
regras de tamanho de arquivo no CLAUDE.md).

Rotas novas (sob o admin de contest já existente), quando a subseção de secrets for
adicionada:

- `POST .../sites/{site_id}/secrets/new` — gerar novo secret de sede (valor uma única vez)
- `POST .../secrets/global/new` — gerar novo secret de controle global (valor uma única vez)
- `POST .../secrets/{secret_id}/delete` — revogar secret (sede ou global)

Lembrar de atualizar `web/docs/ROUTES.md`, `web/docs/URL_FOR_REFERENCE.md`,
`web/docs/SERVICES.md` e registrar qualquer router novo em `web/main.py` (conforme o
CLAUDE.md).

---

## Fase 5: Reveal engine pós-freeze (sede-aware)

Objetivo: implementar revelação progressiva com suporte a sedes e medalhas.

### 5.0. Pré-requisitos de schema (adições à Fase 4)

Duas pequenas adições ao schema/admin (que vivem na Fase 4, no `web/` + `shared/`) são
pré-condições da Fase 5. Estão listadas aqui porque só a Fase 5 as consome:

- **Gate de habilitação por contest.** Adicionar `contests.animator_enabled`
  (`Boolean`, default `false`). O animator só expõe snapshot/eventos/reveal de contests
  com a flag ligada; caso contrário responde `404`. Isso evita expor um contest arbitrário
  apenas por adivinhar o `slug`. A admin do contest ganha o toggle.
- **Secret de controle global.** Para autorizar o reveal *global* (sem sede), generalizar
  a tabela `site_secrets` da Fase 4: tornar `site_id` **nullable** e acrescentar
  `contest_id` (FK, NOT NULL). Uma linha com `site_id = NULL` é um **secret de controle
  contest-global**; uma linha com `site_id` preenchido continua sendo um secret de sede.
  `UniqueConstraint(contest_id, secret)`. Sem isso, o reveal global ficaria sem mecanismo
  de autorização definido.

### 5.1. Criar modelo de estado de revelação

Arquivo novo:

- `/home/dclobato/noca/animator/models/reveal_session.py`

**Princípio central:** o motor **não** mantém um placar paralelo escrito à mão. O estado
mutável da sessão é apenas **o conjunto de submissões pós-freeze já reveladas** (um log
ordenado e reversível). Todo o resto — ranking, células resolvidas, penalidades, medalhas —
é **função pura** desse conjunto, calculada reaproveitando `compute_icpc` do `shared/` (ver
Fase 1). Isso elimina a duplicação de lógica de score e garante que o reveal e o placar ao
vivo concordem sempre.

```python
class RevealSessionState(BaseModel):
    contest_id: str
    sede_id: str | None                  # None = reveal global
    sede_name: str | None
    phase: Literal["idle", "revealing", "done"]
    medal_cutoffs: MedalCutoffs | None

    # Universo imutável desta sessão: ids de submissões pós-freeze
    # (timestamp_seconds > freeze_at_seconds), dos times da sede escolhida,
    # em ordem de submissão (timestamp_seconds, created_at, id).
    frozen_submission_ids: list[str]

    # Estado mutável: log ordenado de reveals já aplicados (subconjunto de
    # frozen_submission_ids). step() faz append; back() faz pop.
    reveal_log: list[str]

    # Time atualmente em foco (mais baixo no ranking corrente que ainda tem
    # submissão congelada relevante). Derivado, mas persistido para a UI.
    focused_team_id: str | None

class MedalCutoffs(BaseModel):
    gold: int       # posição máxima para ouro
    silver: int     # posição máxima para prata
    bronze: int     # posição máxima para bronze
```

Views derivadas (não são estado; calculadas a cada passo para o payload da UI):

```python
class TeamRevealView(BaseModel):
    team_id: str
    team_name: str
    sede_name: str | None
    current_rank: int
    solved: int
    penalty: int
    medal: Literal["gold", "silver", "bronze"] | None
    problems: dict[str, ProblemRevealView]   # chave = label do problema

class ProblemRevealView(BaseModel):
    solved: bool
    attempts: int                    # tentativas penalizantes já reveladas
    solved_at_minutes: int | None
    pending_frozen: bool             # ainda há submissão congelada não revelada
    is_first_solver: bool
```

- **`solved` / `attempts` / `solved_at_minutes`** vêm de `compute_icpc` rodando sobre
  (submissões pré-freeze ∪ `reveal_log`), com `viewer_sees_frozen=False` — porque as
  congeladas ainda não reveladas simplesmente **não entram no conjunto de entrada**.
- **`pending_frozen`** é `True` sse aquele (time, problema) ainda tem algum id em
  `frozen_submission_ids` fora do `reveal_log` — é o que a UI pinta como célula "?".

### 5.2. Criar motor de revelação

Arquivo novo:

- `/home/dclobato/noca/animator/services/reveal_engine.py`

Responsabilidades:

- montar o `frozen_submission_ids` (filtrado por sede)
- calcular o standings corrente **reaproveitando `compute_icpc`** (nunca reimplementar)
- executar os passos de revelação (bottom-up, orientado pelo ranking corrente)
- derivar as views de time/problema e as faixas de medalha

#### Reuso obrigatório do score do NOCA

O standings a cada passo é obtido chamando `compute_icpc` (extraído para
`shared/services/scoreboard_projection.py` na Fase 1) sobre o conjunto
`pré-freeze ∪ reveal_log`. **Não** há um segundo caminho de cálculo de ranking. Isso
mantém penalidade (`contest.wa_penalty`), `accept_pe` e `ce_adds_penalty` idênticos ao
placar oficial — e sem a penalidade fixa em `20` do consumidor legado.

#### Definição de "pós-freeze" (alinhada ao placar)

Uma submissão é congelada quando `timestamp_seconds > freeze_at_seconds`, exatamente o
critério que `compute_icpc` usa (ordenação por `(timestamp_seconds, created_at, id)`).
Usar `timestamp_seconds` (segundos desde o início do contest), **não** o relógio de parede
`created_at`. Linhas pré-migração com `timestamp_seconds` nulo seguem a mesma convenção do
placar.

#### Algoritmo de um passo (`step`)

O reveal **não** é um replay linear de submissões; é orientado pelo ranking corrente
(comportamento herdado como *prior art* de `maratona-revelator-docs/`, reimplementado com o
modelo limpo do NOCA):

1. calcular o standings corrente sobre `pré-freeze ∪ reveal_log`;
2. escolher o **time de menor rank** que ainda tenha submissão congelada *relevante*
   (i.e., um problema ainda não resolvido, na visão revelada, com id em
   `frozen_submission_ids` fora do `reveal_log`) → esse vira `focused_team_id`;
3. dentro do time, percorrer os problemas em **ordem de label**; no primeiro problema não
   resolvido com congelada pendente, revelar **uma** submissão — a mais antiga por
   `(timestamp_seconds, created_at, id)` — dando `append` no `reveal_log`;
4. recalcular o standings (o time pode subir de posição);
5. o time permanece em foco enquanto tiver congelada relevante; quando não tiver mais, o
   cursor desce para o próximo time de menor rank;
6. `phase → "done"` quando nenhum time tiver congelada relevante.

Uma vez que um problema é resolvido, submissões congeladas posteriores dele tornam-se
irrelevantes (o `compute_icpc` já as ignora para o placar); elas não precisam ser reveladas
individualmente.

#### Reversibilidade e saltos

- **`back`**: `pop` no `reveal_log` (o estado é puramente o log, então voltar é exato).
- **`jump-team`**: aplicar `step` repetidamente até que `focused_team_id` seja o time alvo
  (i.e., resolver todos os times abaixo dele), reusando a mesma máquina de passos.
- **`reset`**: esvaziar `reveal_log` e voltar a `phase="idle"`.

#### Filtragem de times por sede

A associação time → sede já é explícita no NOCA via `users.site_id`, então a filtragem é
uma query direta (não há regex nem casamento contra `username`):

1. selecionar os times (`users` com `role=TEAM`) cujo `site_id` é o da sede escolhida;
2. incluir no reveal apenas submissões desses times.

Reveal global (`sede_id=None`) inclui todos os times do contest.

#### Medalhas

Após cada passo, o ranking (dentro do escopo da sede) é comparado com os cutoffs:

- rank <= `gold_cutoff` → ouro
- rank <= `silver_cutoff` → prata
- rank <= `bronze_cutoff` → bronze

A view de cada time carrega `medal`, e a UI destaca as faixas.

#### Mapeamento de veredictos (apenas para exibição)

O motor não converte veredicto em score (isso é do `compute_icpc`). O mapa abaixo é só para
a **cor da célula** na UI:

| NOCA `Verdict` | Cor / rótulo |
|----------------|--------------|
| `AC` (ou `PE` se `accept_pe`) | verde — Aceito |
| `WA`, `RE`, `TLE`, `MLE`, `OLE`, `CE` | vermelho — Erro |
| `final_verdict IS NULL` | congelado/pendente |

(O enum de veredictos do NOCA é `AC, PE, WA, TLE, MLE, OLE, RE, CE` — não existe `OE`.)

Resultado esperado:

- reveal coerente com o placar oficial do NOCA (mesmo `compute_icpc`)
- suporte a reveal global ou por sede, com step/back/jump exatos

### 5.3. Criar endpoints de controle da sessão

Arquivo novo:

- `/home/dclobato/noca/animator/routes/control.py`

Todos os endpoints exigem que o contest tenha `animator_enabled=True` (senão `404`) e um
`secret` válido (senão `403`).

- `POST /c/{slug}/control/start-reveal` — inicia sessão de reveal
  - parâmetros: `sede_id` (opcional), `secret`
- `POST /c/{slug}/control/step` — avança um passo
  - parâmetro: `secret`
- `POST /c/{slug}/control/back` — volta um passo
  - parâmetro: `secret`
- `POST /c/{slug}/control/reset` — reseta sessão
  - parâmetro: `secret`
- `POST /c/{slug}/control/jump-team` — pula para time específico
  - parâmetros: `team_id`, `secret`
- `GET /c/{slug}/control/state` — estado atual da sessão
  - parâmetro: `secret`

#### Autenticação por secret

1. o operador envia o `secret` como parâmetro;
2. **reveal por sede** (`sede_id` presente): validar via
   `site_service.get_site_by_secret(session, contest_id, secret)`; o secret autoriza
   controle apenas daquela sede;
3. **reveal global** (`sede_id=None`): validar via
   `site_service.get_contest_by_global_secret(session, contest_id, secret)` — o secret
   contest-global definido em 5.0 (`site_secrets` com `site_id=NULL`);
4. secret inválido para o escopo pedido → `403`.

O flag `NOCA_ANIMATOR_ENABLE_CONTROL` (Fase 0.3) desliga globalmente todos os endpoints de
controle no deploy (kill-switch operacional), independentemente de secrets.

### 5.4. Persistir e publicar o estado de reveal via Valkey

A sessão de reveal é **persistida no Valkey desde já** — não em memória. Uma cerimônia é um
evento ao vivo de alta exposição; estado em memória não sobrevive a um restart no meio da
revelação nem a mais de uma réplica do animator. A Fase 7 apenas **endurece** isto (TTL,
recuperação, testes de múltiplas réplicas), não o introduz.

Chave de estado: `animator:reveal:{contest_id}:{scope}` onde `scope = sede_id` ou
`"global"`. O valor é o `RevealSessionState` serializado (o `reveal_log` é pequeno e
reconstitui todo o resto). Escrita serializada por um lock do `shared` (o mesmo padrão de
locks já usado no NOCA) para manter um único escritor por sessão.

Pub/sub para a projeção (espectadores):

- Arquivo:
  [shared/services/valkey_service/runtime.py](/home/dclobato/noca/shared/services/valkey_service/runtime.py)
  (`valkey_service` é um **pacote**; `ValkeyRuntime` vive em `runtime.py`)
- Adicionar a `ValkeyRuntime`, seguindo a convenção existente
  (`publish_verdict` / `iter_verdict_events`):
  - `async def publish_revelation(self, channel: str, event_json: str) -> None`
  - `async def iter_revelation_events(self, channel: str) -> AsyncIterator[str]`
- Canal: `revelation:events:{contest_id}:{scope}`

A cada `step`/`back`/`jump`/`reset` o animator: (a) grava o novo estado na chave, e (b)
publica o novo payload de views no canal; o SSE repassa aos espectadores conectados.

### 5.5. Criar UI da cerimônia

Arquivos novos:

- `/home/dclobato/noca/animator/template/ceremony.html`
- `/home/dclobato/noca/animator/template/control.html`
- `/home/dclobato/noca/animator/static/js/ceremony.js`
- `/home/dclobato/noca/animator/static/css/ceremony.css`

Funcionalidades mínimas:

- renderizar placar congelado
- avançar um passo
- voltar
- resetar
- destacar time em foco
- exibir faixas de medalha (ouro/prata/bronze) conforme cutoffs da sede
- exibir nome da sede, se aplicável
- **abrir um modal com a foto do time ao clicar no nome do time e reproduzir
  automaticamente seu clipe de áudio opcional** (ver 5.5.1)

#### 5.5.1. Modal de foto e áudio do time

Durante a cerimônia, clicar no **nome de um time** (na projeção da cerimônia)
abre um modal com a foto daquele time e tenta reproduzir seu clipe de áudio
opcional.

- O nome do time no placar vira um elemento clicável (`data-team-id`); o handler
  vive em `ceremony.js` (sem JS inline), e o markup do modal é um modal
  **Bootstrap** reutilizando os estilos já disponíveis — estilos próprios da
  cerimônia ficam em `ceremony.css`, sem estilos inline (conforme CLAUDE.md).
- O modal exibe a foto via uma tag `<img>` apontando para o endpoint de foto
  abaixo; título do modal = nome do time (e sede, se aplicável).
- **Fallback:** times sem foto (`users_media.com_foto = false` ou sem linha em
  `users_media`) mostram `users_media.avatar_base64` ou um placeholder — o
  clique nunca resulta em imagem quebrada.
- A imagem é grande o suficiente para projeção; usar `max-width:100%` e deixar o
  modal rolar se necessário (nada de overflow horizontal na página).
- Após o clique abrir o modal, o handler tenta reproduzir automaticamente
  `users_media.audio_base64` em um `<audio>` nativo. Se o navegador bloquear
  autoplay, controles acessíveis permanecem disponíveis para reprodução
  manual; áudio ausente ou inválido não quebra o modal.
- Ao fechar o modal, o handler pausa o áudio, volta `currentTime` para zero e
  remove a origem para interromper também qualquer download em andamento.

Endpoints de mídia (dependência — ver nota abaixo):

- `GET /c/{slug}/teams/{team_id}/photo` — serve
  `users_media.foto_base64` decodificado com `users_media.foto_mime`; honra o
  gate `animator_enabled` (404 se desligado) e escopo de sede; usa
  `users_media.dta_foto` para `Cache-Control`/`ETag` (mesmo padrão de cache que
  o `web/` já aplica às fotos de usuário).
- `GET /c/{slug}/teams/{team_id}/audio` — serve o clipe opcional com
  MIME validado; honra o mesmo gate e escopo da foto, usa
  `users_media.dta_audio` para `Cache-Control`/`ETag` e retorna `404` quando o
  time não tem áudio utilizável.

> **Dependência de fase:** estes endpoints são os mesmos listados na Fase 6.1.
> Como foto e áudio são requisitos do modal da cerimônia (Fase 5), as versões
> **mínimas e somente-leitura** são antecipadas para cá; a Fase 6.1 apenas os
> mantém junto das demais rotas de dados de time (`/teams`, `/avatar`). Não
> duplicar: uma única implementação de cada operação, consumida por ambas as
> fases.

Rotas de acesso:

- `GET /c/{slug}/ceremony` — página dos espectadores (projeção)
- `GET /c/{slug}/control?secret={key}` — painel do operador

---

## Fase 6: Dados ricos de time

Objetivo: suportar instituição, mídia e metadados de apresentação.

> **Status: backlog opcional.** Toda a Fase 6 (fases de implementação
> 15, 16 e 17 -- issues #59, #60 e #61) saiu do caminho
> obrigatório. As rotas de foto e áudio do time — a única parte com consumidor
> real — **já foram entregues** nas fases de implementação 13 e 14, e o modal da
> cerimônia monta suas próprias URLs de mídia a partir da projeção do reveal.
> O que resta aqui (`/teams`, `/avatar`, `contest_team_profiles` e sua admin) não
> tem cliente hoje. O caminho obrigatório vai da fase 14 direto para a
> 18 (issue #62); veja cada issue de fase para o que justificaria
> retomá-las.

### 6.1. Entregar MVP com dados existentes

Reutilizar a migração e a tabela `users_media` já fornecidas pelo módulo `web`.

Usar:

- `username`
- `fullname`
- `users_media.foto_base64`
- `users_media.avatar_base64`
- `users_media.foto_mime`
- `users_media.audio_base64`
- `users_media.audio_mime`

Rotas:

- `GET /c/{slug}/teams`
- `GET /c/{slug}/teams/{team_id}/photo` — **já antecipado na Fase
  5.5.1** (modal de foto da cerimônia); aqui apenas fica junto das demais rotas
  de time
- `GET /c/{slug}/teams/{team_id}/avatar`
- `GET /c/{slug}/teams/{team_id}/audio` — **já antecipado na Fase
  5.5.1** (modal da cerimônia); aqui apenas fica junto das demais rotas de time

### 6.2. Definir modelo complementar de perfil de apresentação

Arquivos a criar:

- migration nova
- extensão em `shared/db_schema/`
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

Foto, avatar e clipe de áudio não são duplicados em `contest_team_profiles`; esses
campos permanecem em `users_media`.

### 6.3. Serviço de perfil de time

Arquivo novo:

- `/home/dclobato/noca/animator/services/team_profile_service.py`

Responsabilidades:

- mesclar `users` e `users_media` com `contest_team_profiles`
- construir view model para a UI

---

## Fase 7: Robustez operacional

Objetivo: estabilizar deploy, observabilidade e testes ponta a ponta.

### 7.1. Endurecer a persistência da sessão de reveal

A persistência em Valkey **já é feita na Fase 5.4** (chave
`animator:reveal:{contest_id}:{scope}`, escrita sob lock) — não é introduzida aqui. Esta
fase endurece esse mecanismo, extraindo o acesso para um store dedicado e cobrindo os casos
operacionais:

Arquivo novo:

- `/home/dclobato/noca/animator/services/reveal_session_store.py`

Escopo desta fase:

- TTL da chave: duração do contest + margem
- recuperação limpa após reinício do processo no meio de uma cerimônia
- comportamento correto com múltiplas réplicas do animator (um único escritor por sessão)
- testes de crash/restart e de concorrência

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
- `/home/dclobato/noca/tests/test_site_service.py`
- `/home/dclobato/noca/tests/test_team_profile_service.py`

Cobertura mínima:

- snapshot inicial
- atualização por evento
- reveal step / back / jump-team / reset
- **back(step(s)) == s** (reversibilidade exata do `reveal_log`)
- ranking após reveal **idêntico** ao `compute_icpc` sobre o mesmo conjunto revelado
  (golden test de uma cerimônia conhecida)
- ordem bottom-up e reinserção do time em foco até esgotar congeladas relevantes
- `pending_frozen` correto por (time, problema)
- filtragem de times por sede (via `users.site_id`)
- aplicação de cutoffs de medalha
- validação de secret de sede vs. global; escopo cruzado rejeitado
- contest sem `animator_enabled` → 404
- persistência: estado sobrevive a restart do processo (round-trip Valkey)
- reveal global vs reveal por sede

---

## Estrutura final do módulo `animator/`

```
animator/
├── pyproject.toml                   # membro do workspace uv (noca-animator)
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
│   ├── ceremony.html
│   └── control.html
└── static/
    ├── css/
    │   ├── animator.css
    │   └── ceremony.css
    └── js/
        ├── animator.js
        └── ceremony.js
```

## Ordem concreta de execução

### Etapa 1 (Fase 0)

- atualizar `pyproject.toml`
- criar `animator/` mínimo
- subir `noca-animator`

### Etapa 2 (Fase 1)

- extrair lógica pura de placar para `shared/`
- adaptar o pacote `web/services/scoreboard/`
- validar que o `web` continua estável

### Etapa 3 (Fases 2 + 3)

- implementar `contest_feed_service.py`
- expor `snapshot` e `events`
- renderizar página básica do animator com atualização ao vivo

### Etapa 4 (Fase 4 — pode ser paralela a Etapa 2 e 3)

- estender a tabela `sites` existente com cutoffs de medalha
- criar a tabela `site_secrets`
- criar migration Alembic
- estender `web/services/site_service.py` (medalhas + secrets + `get_site_by_secret`)
- estender a admin de sedes existente (metadados do contest) com medalhas e secrets

### Etapa 5 (Fase 5)

- implementar `reveal_engine.py` com suporte a sedes
- implementar rotas de controle com auth por secret
- implementar pub/sub de revelação no Valkey
- implementar `ceremony.html` e `control.html` com medalhas
- implementar o modal de foto e áudio do time (clique no nome) + endpoints de
  foto e áudio do time

### Etapa 6 (Fases 6 + 7)

- persistir sessão de reveal em Valkey
- endurecer operação e testes

Backlog opcional (fora do caminho obrigatório, ver o aviso da Fase 6):

- integrar os endpoints de foto e áudio com um feed de times e adicionar avatar
- adicionar metadados ricos de time, se necessário

## Arquivos existentes com maior chance de mudança

### Mudança certa

- [pyproject.toml](/home/dclobato/noca/pyproject.toml)
- [web/services/scoreboard/](/home/dclobato/noca/web/services/scoreboard/) — pacote (`models.py`, `computation.py`, `service.py`)
- [web/services/site_service.py](/home/dclobato/noca/web/services/site_service.py) — medalhas + secrets
- [shared/db_schema/contest.py](/home/dclobato/noca/shared/db_schema/contest.py) — estender `sites` + nova `site_secrets`
- [docs/CONFIG.md](/home/dclobato/noca/docs/CONFIG.md)
- [docs/ARCHITECTURE.md](/home/dclobato/noca/docs/ARCHITECTURE.md)

### Mudança provável

- [shared/services/valkey_service/runtime.py](/home/dclobato/noca/shared/services/valkey_service/runtime.py) — pub/sub de revelação
- [shared/queue_schema.py](/home/dclobato/noca/shared/queue_schema.py)
- [web/routes/contest_admin_metadata.py](/home/dclobato/noca/web/routes/contest_admin_metadata.py) — admin de sedes (medalhas/secrets)
- [web/main.py](/home/dclobato/noca/web/main.py) — registrar router de secrets, se extraído

### Criação certa no `animator/`

- `animator/pyproject.toml` — novo membro do workspace uv
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

### Mudança/criação no `web/` (sedes)

- estender `web/services/site_service.py` (medalhas + secrets + `get_site_by_secret`)
- estender a admin de sedes existente em `web/routes/contest_admin_metadata.py` (e seus
  templates de metadados) com cutoffs e subseção de secrets
- opcional: extrair `web/routes/contest_admin_site_secrets.py` + templates se a admin de
  metadados crescer demais

### Criação certa em `shared/`

- `shared/services/scoreboard_projection.py`

### Criação em migration

- `migrations/versions/TIMESTAMP_extend_sites_for_animator.py` — colunas de medalha
  em `sites` + tabela `site_secrets`

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

- sedes (Sites) já criáveis/editáveis via admin passam a ter cutoffs de medalha
- o contest tem o toggle `animator_enabled`
- secrets de sede e global podem ser gerados e revogados
- admin exibe valor do secret uma única vez ao gerar

### Fase 5 concluída

- existe sessão de reveal com step/back/jump-team/reset, reversível e persistida no Valkey
- o ranking a cada passo vem de `compute_icpc` (mesmo score do placar oficial)
- reveal pode ser global ou filtrado por sede
- operador se autentica por secret (de sede ou global); contest sem `animator_enabled` dá 404
- UI exibe faixas de medalha conforme cutoffs da sede
- clicar no nome de um time abre um modal com a foto do time (fallback para
  avatar/placeholder), tenta reproduzir o clipe de áudio opcional e interrompe
  o áudio ao fechar

### Fase 6 concluída

- a UI consegue exibir metadados visuais úteis de times
- a UI consegue reproduzir o clipe de áudio opcional de `users_media`

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
| `scoreboard_builder.py` separado | A lógica de score fica em `shared/services/scoreboard_projection.py` |
| Sedes com regex `codes` casando `username` | O NOCA já associa time → sede por FK (`users.site_id`); filtragem por query, sem regex |
| Tabelas novas `contest_sedes` / `contest_sede_secrets` | Reusa a tabela `sites` existente (estendida) + nova `site_secrets` |

### Mantido do revelator-noca.md

| Item | Onde entra |
|------|-----------|
| Conceito de sede | Já existe como Site (`sites`) — Fase 4 só estende |
| Secrets de operador por sede (`site_secrets`) | Fase 4 |
| Filtragem de times por sede | Fase 5 (via `users.site_id`, **sem regex**) |
| Cutoffs de medalha por sede (ouro/prata/bronze) | Fase 4 (colunas em `sites`), aplicados na Fase 5 |
| Autenticação de operador por secret | Fase 5 |
| Persistência de estado de reveal em Valkey | Fase 7 |
| Mapeamento de veredictos NOCA → Y/N | Fase 5 |
| Rotas sob `/c/{slug}/` diretamente | O animator roda em processo, porta e vhost próprios (`:8003`), então não há conflito de namespace com o `web/`. O prefixo `/animator/` foi usado até a Fase 22 e removido em seguida; `/health`, `/assets/` e `/static/` já estavam na raiz. |

## Recomendação final

O melhor caminho é começar com uma implementação enxuta, respeitando as fronteiras do NOCA:

1. extrair score para `shared/`
2. criar `animator/` como runtime independente
3. entregar live scoreboard primeiro
4. adicionar sedes como conceito do domínio, administrado no `web/`
5. entregar reveal engine sede-aware depois
6. evoluir os dados de time por migração específica, apenas se necessário

Sedes e medalhas por sede são um diferencial claro em relação ao `maratona-animeitor`, que depende de configuração TOML externa. No NOCA, sedes são cidadãs de primeira classe do domínio, gerenciadas pela interface de admin e consumidas nativamente pelo animator.
