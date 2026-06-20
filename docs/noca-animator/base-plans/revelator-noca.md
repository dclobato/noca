# Plano: Reveleitor nativo + Sedes no NOCA

## Contexto

O maratona-animator expõe um binário (`simples`) que lê dados do BOCA e serve uma API de animação de scoreboard com suporte a congelamento e cerimônia de revelação ("reveleitor"). O NOCA quer incorporar funcionalidade equivalente de forma nativa, eliminando a dependência do BOCA como fonte de dados intermediária.

A implementação tem dois componentes independentes:

1. **Sedes** — conceito de sites regionais dentro de um contest, com filtragem de times por padrões regex e configuração de posições de medalha (ouro/prata/bronze). Implementado dentro do módulo `web/` existente.
2. **Reveleitor** — novo módulo standalone (`revelation/`) com cerimônia interativa de revelação dos resultados congelados, arquitetura paralela à do `web/` e `autojudge/`.

---

## Arquivos críticos de referência

| Arquivo | Relevância |
|---------|-----------|
| `shared/db_schema.py` | Onde as novas tabelas serão adicionadas; padrões com `_id_column()`, `_created_at_column()` |
| `shared/enumerations.py` | `Verdict` enum (AC, WA, TLE, etc.) para mapeamento para formato de animação |
| `shared/services/valkey_service.py` | `ValkeyRuntime` — adicionar pub/sub para estado da revelação |
| `web/main.py` | Padrão de app FastAPI + lifespan para replicar em `revelation/main.py` |
| `web/config.py` | Padrão `BaseSettings` com `env_prefix` para replicar em `revelation/config.py` |
| `web/routes/contest_admin.py` | Padrão de router + context dependency para as rotas de admin de sedes |
| `web/services/scoreboard.py` | `ScoreboardSnapshot`, `TeamStanding`, `ProblemResult` — reutilizar no reveleitor |
| `pyproject.toml` | Adicionar `revelation/` aos packages e novo script `noca-revelation` |
| `migrations/versions/` | Padrão Alembic para a nova migration |

---

## Fase 1 — Banco de dados: tabelas de Sedes e Secrets

**Arquivo:** `shared/db_schema.py`

Adicionar duas novas tabelas usando os helpers existentes:

### Tabela `contest_sedes`

```
id             String(36) PK
contest_id     FK → contests.id  NOT NULL
name           String(200)       NOT NULL  # ex: "Regional SP"
codes          JSON              NOT NULL  # array de regex ex: ["teambr_sp_.*"]
gold_cutoff    Integer default 1
silver_cutoff  Integer default 2
bronze_cutoff  Integer default 3
style          String(100) nullable        # classe CSS opcional
created_at, updated_at
UniqueConstraint(contest_id, name)
```

### Tabela `contest_sede_secrets`

```
id         String(36) PK
sede_id    FK → contest_sedes.id  NOT NULL
secret     String(256)            NOT NULL  # valor em texto claro (ou hashed)
label      String(200)            NOT NULL  # ex: "Operador SP"
created_at
```

**Arquivo:** `migrations/versions/TIMESTAMP_add_sedes_and_sede_secrets.py`

Migration Alembic com `upgrade()` e `downgrade()` para as duas tabelas.

---

## Fase 2 — Módulo `web/`: Admin de Sedes

### `web/services/sede_service.py` (novo)

Funções:
- `list_sedes(db, contest_id) → list[SedeRow]`
- `get_sede(db, sede_id) → SedeRow | None`
- `create_sede(db, contest_id, data) → SedeRow`
- `update_sede(db, sede_id, data) → SedeRow`
- `delete_sede(db, sede_id) → None`
- `list_secrets(db, sede_id) → list[SecretRow]`
- `create_secret(db, sede_id, label) → str` — gera e persiste o secret, retorna o valor para exibição única
- `delete_secret(db, secret_id) → None`
- `get_sede_by_secret(db, contest_id, secret) → SedeRow | None` — usado pelo reveleitor

Reutilizar: padrão de SQLAlchemy Core já usado em `web/services/scoreboard.py`.

### `web/routes/contest_admin_sede.py` (novo)

Router `APIRouter(prefix="/c/{slug}/admin/sedes", tags=["contest_admin_sede"])`.

Rotas:
- `GET /` — listar sedes
- `GET /new` — formulário de criação
- `POST /new` — persistir nova sede
- `GET /{sede_id}` — editar sede
- `POST /{sede_id}` — salvar edição
- `POST /{sede_id}/delete` — remover sede
- `POST /{sede_id}/secrets/new` — gerar novo secret (exibe valor uma única vez)
- `POST /{sede_id}/secrets/{secret_id}/delete` — revogar secret

Dependency: reutilizar `ContestAdminContext` (já existente).

### Templates `web/templates/contest_admin_sede/`

- `list.html` — tabela de sedes com botão novo
- `form.html` — formulário criação/edição (nome, codes/regex, cutoffs, style)
- `secrets.html` — lista de secrets com botão "gerar novo"
- `secret_reveal.html` — modal/página exibindo o secret gerado (única vez)

### `web/main.py`

Registrar o novo router com `app.include_router(contest_admin_sede.router)`.

---

## Fase 3 — Módulo `revelation/` (novo módulo standalone)

Estrutura:

```
revelation/
├── __init__.py
├── config.py          # BaseSettings env_prefix="NOCA_"
├── main.py            # FastAPI app + lifespan + entry point main()
├── database.py        # async engine + session factory (mesmo padrão de web/)
├── routes/
│   └── ceremony.py    # todas as rotas do reveleitor
├── services/
│   ├── revelation_service.py   # lógica central da cerimônia
│   └── scoreboard_builder.py  # reconstrói scoreboard a partir das runs
├── models.py          # Pydantic: RevelationState, TeamReveal, RunPublic
└── templates/
    ├── viewer.html    # página dos espectadores (projeção)
    └── control.html   # painel do operador
```

### `revelation/config.py`

Herda de `BaseSettings` com `env_prefix="NOCA_"`. Campos:
- Reutiliza: `DB_USER/PASSWORD/SERVER/PORT/NAME`, `VALKEY_*` (mesmo que web/config.py)
- Novo: `REVELATION_PORT` (default 8002), `REVELATION_HOST`

### `revelation/models.py`

```python
class RunPublic(BaseModel):      # run com veredito real
    id: str
    team_id: str
    team_login: str
    problem_label: str
    timestamp_minutes: int
    verdict: str                 # "Y" | "N" | "W" (mapeia de Verdict enum)

class TeamRevealState(BaseModel):
    team_id: str
    team_name: str
    sede_name: str
    frozen_rank: int
    final_rank: int
    problems: dict[str, ProblemReveal]

class RevelationState(BaseModel):
    contest_id: str
    sede_id: str
    phase: Literal["idle", "revealing", "done"]
    current_team_index: int      # índice da lista ordenada (bottom-up)
    teams: list[TeamRevealState] # ordenados: menor rank final → maior
```

Estado persistido em Valkey com chave `revelation:{contest_id}:{sede_id}` e TTL = fim do contest.

### `revelation/services/revelation_service.py`

Funções principais:
- `load_frozen_scoreboard(db, contest_id, sede_id) → ScoreboardSnapshot` — reutiliza `web/services/scoreboard.py`; filtra times pelo regex da sede
- `load_real_runs(db, contest_id, sede_id) → list[RunPublic]` — busca todas as submissões com `final_verdict IS NOT NULL`; inclui as pós-freeze; filtra por sede
- `build_final_scoreboard(frozen, real_runs) → ScoreboardSnapshot` — aplica os vereditos reais sobre o frozen para calcular ranking final
- `initialize_revelation(state, contest_id, sede_id) → RevelationState` — persiste estado inicial no Valkey
- `advance(state, contest_id, sede_id) → RevelationState | None` — avança para próximo time/problema
- `back(state, contest_id, sede_id) → RevelationState | None` — volta um passo
- `publish_revelation_event(valkey, event: dict) → None` — pub/sub no canal `revelation:events:{contest_id}:{sede_id}`

### Mapeamento de vereditos NOCA → Revelação

| NOCA `Verdict` | Formato revelação |
|----------------|-------------------|
| `AC` (ou `PE` se accept_pe) | `"Y"` — Aceito |
| `WA`, `RE`, `TLE`, `MLE`, `OLE`, `OE` | `"N"` — Errado |
| `CE` | `"N"` ou `"X"` dependendo de `ce_adds_penalty` |
| `final_verdict IS NULL` | `"W"` — Pendente/congelado |

### `revelation/routes/ceremony.py`

Router `APIRouter(prefix="/c/{slug}")`. Rotas:

| Método | Caminho | Descrição |
|--------|---------|-----------|
| `GET` | `/` | HTML: página de espectadores (viewer.html) |
| `GET` | `/control` | HTML: painel do operador (control.html) — requer secret via query |
| `GET` | `/state` | JSON: `RevelationState` atual (ou 404 se não iniciada) |
| `GET` | `/events` | SSE: stream de eventos da cerimônia |
| `POST` | `/start?sede={id}&secret={key}` | Inicializa cerimônia para uma sede |
| `POST` | `/advance?secret={key}` | Avança um passo |
| `POST` | `/back?secret={key}` | Volta um passo |
| `GET` | `/runs?sede={id}&secret={key}` | JSON: todas as runs com vereditos reais |

**SSE `/events`:** emite JSON em cada mudança de estado. Evento: `data: {type, payload}\n\n`. Heartbeat de 15s como em `contest_runs.py`.

**Auth:** secret validado via `sede_service.get_sede_by_secret()`. Retorna 403 se inválido.

### `revelation/main.py`

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # inicializa engine DB, sessão, valkey
    yield
    # cleanup

app = FastAPI(lifespan=lifespan)
app.include_router(ceremony.router)

def main() -> None:
    configure_logging()
    uvicorn.run(app, host=settings.REVELATION_HOST, port=settings.REVELATION_PORT)
```

---

## Fase 4 — `pyproject.toml`

```toml
[tool.hatch.build.targets.wheel]
packages = ["web", "shared", "autojudge", "revelation"]  # adicionar "revelation"

[project.scripts]
noca-web        = "web.main:main"
noca-autojudge  = "autojudge.worker:main"
noca-revelation = "revelation.main:main"  # novo
```

---

## Fase 5 — Valkey: canal de eventos da revelação

**Arquivo:** `shared/services/valkey_service.py`

Adicionar métodos a `ValkeyRuntime`:
- `async def publish_revelation(self, channel: str, event_json: str) -> None`
- `async def iter_revelation_events(self, channel: str) -> AsyncIterator[str]` — pub/sub listener (mesmo padrão de `iter_verdict_events()`)
- `async def get_revelation_state(self, key: str) -> str | None`
- `async def set_revelation_state(self, key: str, value: str, ex: int) -> None`

---

## Sequência de implementação

1. `shared/db_schema.py` — novas tabelas
2. `migrations/` — nova migration Alembic
3. `web/services/sede_service.py` + `web/routes/contest_admin_sede.py` + templates
4. `web/main.py` — registrar novo router
5. `shared/services/valkey_service.py` — novos métodos
6. `revelation/` — módulo completo
7. `pyproject.toml` — packages + script entry point

---

## Verificação

```bash
# 1. Aplicar migration
cd /home/dclobato/noca
alembic upgrade head

# 2. Verificar tabelas criadas
psql -c "\d contest_sedes" && psql -c "\d contest_sede_secrets"

# 3. Verificar tipos e lint
uv run mypy web shared revelation
uv run ruff check .

# 4. Testes existentes ainda passam
uv run pytest

# 5. Subir o servidor web e navegar para admin de sedes
uv run noca-web
# Acessar: /c/{slug}/admin/sedes/

# 6. Subir o reveleitor e testar endpoints
uv run noca-revelation
# Acessar: /c/{slug}/          ← viewer
# Acessar: /c/{slug}/control   ← painel
# Curl: /c/{slug}/runs?sede=X&secret=KEY
# Curl: /c/{slug}/state
# Curl (SSE): /c/{slug}/events
```
