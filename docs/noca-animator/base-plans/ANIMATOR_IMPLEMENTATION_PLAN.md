# Plano de Implementação: módulo `animator/` no NOCA

## Objetivo

Implementar no NOCA um terceiro módulo de runtime, `animator/`, responsável por:

- placar animado em tempo real
- revelação progressiva pós-freeze
- exposição de dados de times para apresentação

Este documento converte a proposta arquitetural em uma sequência prática de implementação.

Documento de referência:

- [ANIMATOR_MODULE_PROPOSAL.md](ANIMATOR_MODULE_PROPOSAL.md)

## Princípios de implementação

1. Não acoplar `animator/` a `web/services/`.
2. Extrair lógica pura reutilizável para `shared/`.
3. Entregar primeiro live scoreboard.
4. Adicionar reveal engine só depois da base estar estável.
5. Tratar dados ricos de times como evolução, não como bloqueio do MVP.

## Resultado final esperado

Ao final das fases principais, o NOCA deve ter:

- novo runtime `noca-animator`
- frontend próprio de apresentação
- consumo de PostgreSQL e Valkey sem dependência direta do `web`
- placar animado em tempo real
- reveleitor pós-freeze
- endpoints para dados de time e controle de sessão

## Visão geral por fases

### Fase 0: Preparação estrutural

Objetivo:

- preparar o repositório para suportar um terceiro runtime e lógica compartilhada

### Fase 1: Extração da lógica pura de placar

Objetivo:

- mover a semântica de score para `shared/`

### Fase 2: MVP do runtime `animator/`

Objetivo:

- subir o novo processo com live scoreboard

### Fase 3: Streaming e UI animada

Objetivo:

- entregar atualização visual ao vivo

### Fase 4: Reveal engine pós-freeze

Objetivo:

- implementar revelação progressiva

### Fase 5: Dados ricos de time

Objetivo:

- suportar instituição, mídia e metadados de apresentação

### Fase 6: Robustez operacional

Objetivo:

- estabilizar deploy, observabilidade e testes ponta a ponta

## Fase 0: Preparação estrutural

## 0.1. Atualizar empacotamento do projeto

Arquivo:

- [pyproject.toml](/home/dclobato/noca/pyproject.toml)

Mudanças:

- incluir `animator` em `tool.hatch.build.targets.wheel.packages`
- adicionar novo entrypoint:
  - `noca-animator = "animator.main:main"`

Resultado esperado:

- o projeto passa a reconhecer formalmente um terceiro runtime

## 0.2. Criar o pacote `animator/`

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

## 0.3. Definir configuração do módulo

Arquivos:

- `/home/dclobato/noca/animator/config.py`
- [docs/CONFIG.md](/home/dclobato/noca/docs/CONFIG.md)

Variáveis iniciais sugeridas:

- `NOCA_ANIMATOR_HOST`
- `NOCA_ANIMATOR_PORT`
- `NOCA_ANIMATOR_SECRET`
- `NOCA_ANIMATOR_ENABLE_CONTROL`
- `NOCA_ANIMATOR_POLL_FALLBACK_SECONDS`

Resultado esperado:

- o módulo consegue subir com configuração própria sem reusar indevidamente a config do `web`

## Fase 1: Extração da lógica pura de placar

## 1.1. Criar DTOs de placar em `shared/`

Arquivos novos sugeridos:

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

## 1.2. Extrair o cálculo puro de score

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

## 1.3. Adaptar o `web` para consumir a lógica extraída

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

## 1.4. Cobrir com testes

Arquivos:

- `/home/dclobato/noca/tests/test_scoreboard_projection.py`
- adaptar [tests/test_scoreboard.py](/home/dclobato/noca/tests/test_scoreboard.py)

Cobertura mínima:

- cálculo sem freeze
- cálculo com freeze para visão pública
- ordenação do ranking
- pending cells
- casos com `accept_pe` e `ce_adds_penalty`

## Fase 2: MVP do runtime `animator/`

## 2.1. Criar aplicação FastAPI mínima do animator

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

## 2.2. Implementar serviço de leitura de contest para animator

Arquivos novos:

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

## 2.3. Expor endpoint de snapshot

Arquivos:

- `/home/dclobato/noca/animator/routes/public.py`

Endpoints iniciais sugeridos:

- `GET /animator/c/{slug}/snapshot`
- `GET /animator/c/{slug}/meta`

Conteúdo mínimo:

- snapshot do placar
- problemas
- balloon colors
- timer do contest

## 2.4. Implementar frontend simples do animator

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

## Fase 3: Streaming e UI animada

## 3.1. Criar serviço de subscription a `VerdictEvent`

Arquivos novos:

- `/home/dclobato/noca/animator/services/event_stream_service.py`

Responsabilidades:

- assinar `judge:results`
- filtrar por contest
- emitir eventos internos do animator

Reuso:

- `shared.services.valkey_service.ValkeyRuntime`
- `shared.queue_schema.VerdictEvent`

## 3.2. Expor stream para o frontend

Escolha recomendada:

- SSE no MVP

Arquivos:

- `/home/dclobato/noca/animator/routes/public.py`

Endpoint sugerido:

- `GET /animator/c/{slug}/events`

Eventos mínimos:

- `verdict`
- `scoreboard_refresh`
- `timer_tick`

## 3.3. Atualizar o frontend com base em eventos

Arquivos:

- `/home/dclobato/noca/animator/static/js/animator.js`

Responsabilidades:

- abrir EventSource
- buscar novo snapshot quando necessário
- animar mudanças de rank e célula

Recomendação:

- começar com refresh incremental simples
- só depois otimizar para diffs finos

## Fase 4: Reveal engine pós-freeze

## 4.1. Criar modelo de estado de revelação

Arquivos novos:

- `/home/dclobato/noca/animator/models/reveal_session.py`

Tipos sugeridos:

- `RevealSessionState`
- `RevealQueueEntry`
- `RevealStepResult`

Estado mínimo:

- contest id
- snapshot congelado inicial
- runs pós-freeze ainda não reveladas
- fila de revelação
- cursor atual
- status da sessão

## 4.2. Criar motor de revelação

Arquivos novos:

- `/home/dclobato/noca/animator/services/reveal_engine.py`

Responsabilidades:

- reconstruir snapshot congelado
- identificar runs pós-freeze
- aplicar passos de revelação
- recalcular ranking após cada passo

Decisão importante:

- a primeira implementação deve usar a semântica oficial do NOCA, não a do BOCA legado

Resultado esperado:

- reveal coerente com o placar do próprio NOCA

## 4.3. Criar endpoints de controle da sessão

Arquivos:

- `/home/dclobato/noca/animator/routes/control.py`

Endpoints sugeridos:

- `POST /animator/c/{slug}/control/start-reveal`
- `POST /animator/c/{slug}/control/step`
- `POST /animator/c/{slug}/control/back`
- `POST /animator/c/{slug}/control/reset`
- `POST /animator/c/{slug}/control/jump-team`

Controle inicial:

- segredo simples de operador
- sem RBAC completo na primeira iteração

## 4.4. Criar UI de reveleitor

Arquivos novos:

- `/home/dclobato/noca/animator/template/reveleitor.html`
- `/home/dclobato/noca/animator/static/js/reveleitor.js`
- `/home/dclobato/noca/animator/static/css/reveleitor.css`

Funcionalidades mínimas:

- renderizar placar congelado
- avançar um passo
- voltar
- resetar
- destacar time em foco

## Fase 5: Dados ricos de time

## 5.1. Entregar MVP com dados existentes

Sem migração inicial.

Usar:

- `username`
- `fullname`
- `foto_base64`
- `avatar_base64`
- `foto_mime`

Rotas sugeridas:

- `GET /animator/c/{slug}/teams`
- `GET /animator/c/{slug}/teams/{team_id}/photo`
- `GET /animator/c/{slug}/teams/{team_id}/avatar`

## 5.2. Definir modelo complementar de perfil de apresentação

Arquivos a criar:

- migration nova
- extensão em `shared/db_schema.py`
- model ou query layer no `animator`

Tabela sugerida:

- `contest_team_profiles`

Campos sugeridos:

- `contest_id`
- `team_id`
- `display_name`
- `institution_name`
- `institution_short_name`
- `theme_color`
- `media_json`
- `soundtrack_path`

## 5.3. Serviço de perfil de time

Arquivos novos:

- `/home/dclobato/noca/animator/services/team_profile_service.py`

Responsabilidades:

- mesclar `users` com `contest_team_profiles`
- construir view model para a UI

## Fase 6: Robustez operacional

## 6.1. Persistência opcional da sessão de reveal

Primeira versão:

- estado em memória

Segunda versão:

- persistência em Valkey ou PostgreSQL

Arquivos futuros:

- `/home/dclobato/noca/animator/services/reveal_session_store.py`

## 6.2. Observabilidade

Adicionar:

- logs estruturados do animator
- métricas de contest carregado
- métricas de clientes conectados
- métricas de eventos recebidos
- métricas de sessões de reveal

Arquivos prováveis:

- `/home/dclobato/noca/animator/main.py`
- serviços do animator

## 6.3. Testes

Arquivos novos sugeridos:

- `/home/dclobato/noca/tests/test_animator_snapshot.py`
- `/home/dclobato/noca/tests/test_animator_events.py`
- `/home/dclobato/noca/tests/test_reveal_engine.py`
- `/home/dclobato/noca/tests/test_team_profile_service.py`

Cobertura mínima:

- snapshot inicial
- atualização por evento
- reveal step
- reveal reset
- ranking após reveal

## Ordem concreta de execução

## Etapa 1

- atualizar `pyproject.toml`
- criar `animator/` mínimo
- subir `noca-animator`

## Etapa 2

- extrair lógica pura de placar para `shared/`
- adaptar `web/services/scoreboard.py`
- validar que o `web` continua estável

## Etapa 3

- implementar `contest_feed_service.py`
- expor `snapshot`
- renderizar página básica do animator

## Etapa 4

- implementar assinatura de `VerdictEvent`
- expor SSE
- atualizar UI ao vivo

## Etapa 5

- implementar `reveal_engine.py`
- implementar rotas de controle
- implementar `reveleitor.html`

## Etapa 6

- adicionar dados ricos de time, se necessário
- endurecer operação e testes

## Arquivos existentes com maior chance de mudança

### Mudança certa

- [pyproject.toml](/home/dclobato/noca/pyproject.toml)
- [web/services/scoreboard.py](/home/dclobato/noca/web/services/scoreboard.py)
- [docs/CONFIG.md](/home/dclobato/noca/docs/CONFIG.md)
- [docs/ARCHITECTURE.md](/home/dclobato/noca/docs/ARCHITECTURE.md)

### Mudança provável

- [shared/services/valkey_service.py](/home/dclobato/noca/shared/services/valkey_service.py)
- [shared/queue_schema.py](/home/dclobato/noca/shared/queue_schema.py)
- [shared/db_schema.py](/home/dclobato/noca/shared/db_schema.py)

### Criação certa

- `/home/dclobato/noca/animator/main.py`
- `/home/dclobato/noca/animator/config.py`
- `/home/dclobato/noca/animator/database.py`
- `/home/dclobato/noca/animator/routes/public.py`
- `/home/dclobato/noca/animator/routes/control.py`
- `/home/dclobato/noca/animator/routes/health.py`
- `/home/dclobato/noca/animator/services/contest_feed_service.py`
- `/home/dclobato/noca/animator/services/event_stream_service.py`
- `/home/dclobato/noca/animator/services/reveal_engine.py`
- `/home/dclobato/noca/animator/services/team_profile_service.py`

### Criação recomendada em `shared/`

- `/home/dclobato/noca/shared/services/scoreboard_projection.py`

## Critério de sucesso por fase

### Fase 1 concluída

- o `web` usa score compartilhado em `shared/`

### Fase 2 concluída

- `noca-animator` sobe e exibe snapshot inicial do placar

### Fase 3 concluída

- o animator reage a novos veredictos e atualiza a UI

### Fase 4 concluída

- existe sessão de reveal com step/reset/back

### Fase 5 concluída

- a UI consegue exibir metadados visuais úteis de times

### Fase 6 concluída

- o módulo está testado e operacionalizável em produção

## Recomendação final

O melhor caminho é começar com uma implementação enxuta, respeitando as fronteiras do NOCA:

1. extrair score para `shared/`
2. criar `animator/` como runtime independente
3. entregar live scoreboard primeiro
4. adicionar reveal engine depois
5. evoluir os dados de time por migração específica, apenas se necessário

Isso entrega valor cedo, reduz risco arquitetural e evita misturar lógica de apresentação com o core do `web`.
