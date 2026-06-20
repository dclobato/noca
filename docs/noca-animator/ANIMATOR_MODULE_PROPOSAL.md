# Proposta Inicial de Arquitetura: módulo `animator/`

## Objetivo

Adicionar ao NOCA um terceiro módulo de runtime, além de `web/` e `autojudge/`, responsável por:

- placar animado em tempo real
- revelação progressiva de resultados após o freeze
- exposição de dados dos times para apresentação visual

O objetivo não é reproduzir o protocolo legado de webcast do BOCA internamente. O objetivo é implementar essa capacidade como parte nativa da arquitetura do NOCA, usando os dados autoritativos já existentes no sistema.

## Conclusão

Sim, é tecnicamente viável introduzir um módulo `animator/` no NOCA.

A arquitetura atual do NOCA já oferece quase todos os blocos necessários:

- dados autoritativos em PostgreSQL
- eventos de veredicto em Valkey
- lógica de placar já implementada
- estados de contest já modelados
- dados básicos de times já disponíveis
- frontend web já validado para UI em tempo real

O que falta hoje não é infraestrutura de base. O que falta é:

- separar a lógica de placar puro do módulo `web/`
- criar um runtime dedicado para projeções de apresentação e revelação
- definir um modelo de dados complementar para mídia/perfil de time, se quisermos algo além de nome + foto/avatar

## Por que um módulo separado faz sentido

O NOCA atual tem uma fronteira arquitetural clara:

- `web/` cuida de fluxos de negócio, administração, autenticação e UI principal
- `autojudge/` cuida de execução não confiável e produção de veredictos
- `shared/` concentra schema, enums, payloads e serviços compartilhados

Um `animator/` se encaixa naturalmente nessa mesma filosofia:

- lê estado autoritativo de PostgreSQL
- consome eventos de Valkey
- não chama `web/` nem `autojudge/` diretamente por API interna
- pode escalar e ser implantado separadamente
- pode evoluir sem contaminar a aplicação principal com lógica de apresentação

Essa separação é especialmente desejável porque o animator tem características próprias:

- alto volume de leitura e atualização visual
- sessões de apresentação com estado temporário
- necessidade de streaming para navegador
- lógica de revelação que não deve interferir na semântica do placar oficial

## Evidências da arquitetura atual que sustentam essa proposta

### 1. O NOCA já é modular por runtime

Hoje o pacote expõe dois entrypoints:

- `noca-web`
- `noca-autojudge`

Isso aparece em `pyproject.toml`.

Logo, acrescentar um terceiro entrypoint como `noca-animator` é coerente com a forma atual de empacotamento e operação.

### 2. O placar já existe como serviço separado no `web/`

O placar do NOCA já está encapsulado em `web/services/scoreboard.py`.

Esse serviço já faz:

- leitura de times, problemas, submissões e julgamentos
- cálculo do placar ICPC
- aplicação de freeze para visualização pública
- cache em Valkey

Portanto, o problema não é inventar a regra de placar do zero. O problema é reposicionar essa lógica para que ela também possa ser consumida por um módulo `animator/`.

### 3. O NOCA já publica eventos de veredicto

O sistema já publica `VerdictEvent` em Valkey no canal `judge:results`.

Isso é suficiente para um runtime `animator/` reagir a:

- novas submissões julgadas
- mudanças de veredicto
- atualizações ao vivo do placar

Ou seja, já existe um barramento mínimo de eventos para suportar atualização live.

### 4. O modelo atual de contest já suporta freeze e lifecycle

O `Contest` do NOCA já tem:

- `start_time`
- `duration_minutes`
- `stop_updating_scoreboard`
- `release_scoreboard_after_end`
- helpers como `is_running`, `is_past`, `is_scoreboard_frozen`

Isso já fornece a base de estado para:

- placar ao vivo
- freeze
- revelação pós-freeze

### 5. O modelo atual de times já suporta dados básicos de apresentação

O `User` com role `TEAM` já tem:

- `username`
- `fullname`
- `foto_base64`
- `avatar_base64`
- `foto_mime`

Isso é suficiente para uma primeira versão do animator com:

- identificador do time
- nome de exibição
- avatar/foto

O que não existe ainda, de forma estruturada, é:

- instituição
- nome curto específico para broadcast
- mídia extra do time
- trilha sonora do time
- branding específico de apresentação

Esses itens podem ser tratados como evolução, não como pré-requisito para a primeira versão.

## Proposta de posicionamento arquitetural

## Novo runtime

Criar um novo pacote de topo:

```text
animator/
```

com entrypoint:

```text
noca-animator
```

Esse módulo deve ser um processo independente, análogo ao `web/` e ao `autojudge/`.

## Responsabilidades do `animator/`

O módulo deve ser dono de:

- projeção de placar para apresentação visual
- estado de revelação pós-freeze
- streaming de updates para navegador
- endpoints e páginas específicas de apresentação
- dados visuais de times consumidos pela experiência do animator

O módulo não deve ser dono de:

- autenticação geral do sistema
- CRUD de contest
- submissões
- julgamentos
- regras de negócio do contest fora do escopo de apresentação

## Fronteiras entre módulos

### `web/`

Continua dono de:

- administração do contest
- CRUD de times/problemas
- operações de judge/admin
- placar oficial ao usuário comum

### `autojudge/`

Continua dono de:

- fila de julgamento
- execução em container
- persistência de resultados
- publicação de eventos de veredicto

### `animator/`

Passa a ser dono de:

- visualização de apresentação
- timeline de runs para exibição
- reveal sessions
- controle remoto da revelação

### `shared/`

Deve absorver a lógica puramente compartilhável necessária para isso.

## Refatorações necessárias antes ou durante a criação do módulo

## 1. Extrair a lógica pura de placar do `web/` para `shared/`

Hoje `web/services/scoreboard.py` mistura:

- acesso a banco
- DTOs de snapshot
- lógica pura de score
- cache

Para suportar um `animator/` limpo, a recomendação é separar isso em duas camadas:

### `shared/services/scoreboard_projection.py`

Responsável por:

- DTOs do placar
- lógica pura de cálculo de standings
- ordenação e semântica do placar

### `web/services/scoreboard.py`

Passaria a ficar responsável por:

- queries do `web`
- cache em Valkey
- adaptação da camada pura para o contexto do `web`

### `animator/services/live_scoreboard.py`

Usaria:

- queries próprias
- a lógica pura compartilhada em `shared/`

Isso é importante para manter a disciplina arquitetural do NOCA:

- não fazer `animator/` importar serviço de `web/`

## 2. Definir um modelo explícito de projeção de apresentação

O placar oficial do NOCA e o placar do animator não são a mesma coisa.

O placar oficial:

- é o snapshot atual conforme a regra do contest

O placar do animator:

- precisa carregar estado de apresentação
- precisa distinguir placar oficial de placar revelado
- pode precisar armazenar fila, cursor e sessão

A recomendação é criar tipos próprios de projeção em `animator/`:

- `AnimatorContestSnapshot`
- `AnimatorTeamView`
- `AnimatorProblemView`
- `RevealSessionState`
- `RevealQueueEntry`

## Proposta funcional do módulo

## Modo 1: placar animado ao vivo

Objetivo:

- exibir o placar em tempo real durante o contest
- destacar mudanças visuais de runs, balões e movimentações

Fonte de dados:

- PostgreSQL para bootstrap
- `VerdictEvent` em Valkey para atualização incremental

Semântica:

- usar a mesma regra de score oficial do NOCA
- respeitar `stop_updating_scoreboard`
- manter uma timeline curta de eventos recentes para a UI

## Modo 2: revelação progressiva pós-freeze

Objetivo:

- reconstruir o placar congelado
- avançar manualmente ou automaticamente pelas runs pós-freeze
- recalcular o ranking a cada revelação

Semântica recomendada:

- usar o placar oficial do NOCA como base sem reinventar regra de score
- tratar a revelação como camada de apresentação sobre submissões e julgamentos já persistidos

Essa lógica não precisa reproduzir fielmente o `maratona-animeitor`, mas deve entregar a mesma funcionalidade:

- estado congelado inicial
- passos de revelação
- reordenação do ranking a cada passo
- foco visual no time em revelação

## Modo 3: dados dos times

Objetivo:

- disponibilizar informações de apresentação do time para UI do animator

Na primeira fase, o módulo pode usar apenas os campos já existentes:

- `username`
- `fullname`
- `avatar_base64`
- `foto_base64`
- `foto_mime`

Para igualar experiências mais ricas de animeitor/reveleitor, será útil uma fase posterior com metadata específica de apresentação.

## Proposta de dados adicionais

Para suportar "dados dos times" de forma mais rica, proponho uma tabela nova, de escopo de contest, por exemplo:

```text
contest_team_profiles
```

Campos sugeridos:

- `id`
- `contest_id`
- `team_id`
- `display_name`
- `institution_name`
- `institution_short_name`
- `photo_asset_path` ou `photo_blob_ref`
- `avatar_asset_path` ou `avatar_blob_ref`
- `theme_color`
- `soundtrack_asset_path`
- `extra_metadata_json`
- `created_at`
- `updated_at`

Motivação:

- não poluir `users`
- permitir dados específicos de broadcast
- permitir múltiplos contests com perfis distintos para o mesmo time lógico

## Proposta de arquitetura interna do módulo `animator/`

## Estrutura inicial sugerida

```text
animator/
├── __init__.py
├── main.py
├── config.py
├── database.py
├── dependencies.py
├── routes/
│   ├── public.py
│   ├── control.py
│   ├── media.py
│   └── health.py
├── services/
│   ├── live_scoreboard.py
│   ├── reveal_engine.py
│   ├── team_profile_service.py
│   ├── event_stream_service.py
│   └── contest_feed_service.py
├── models/
│   ├── projection.py
│   └── reveal_session.py
├── template/
│   ├── animator.html
│   ├── reveleitor.html
│   └── control.html
└── static/
    ├── css/
    ├── js/
    └── img/
```

## Componentes principais

### `live_scoreboard.py`

Responsável por:

- carregar bootstrap do placar a partir do banco
- aplicar updates incrementais
- produzir snapshots para a UI

### `reveal_engine.py`

Responsável por:

- montar o estado congelado inicial
- localizar runs ocultas pelo freeze
- manter fila de revelação
- aplicar passos de revelação
- recalcular o ranking a cada passo

### `team_profile_service.py`

Responsável por:

- ler dados básicos de times
- combinar dados de `users` com perfil opcional de apresentação

### `event_stream_service.py`

Responsável por:

- assinar eventos de Valkey
- multiplexar updates para SSE ou WebSocket
- publicar eventos internos do módulo

### `contest_feed_service.py`

Responsável por:

- alimentar a UI com contest metadata, teams, problems, clock e standings

## Modelo de execução recomendado

## Backend do animator

Sugestão:

- FastAPI independente, como o `web/`

Motivos:

- já existe padrão operacional no projeto
- fácil servir HTML + SSE/WebSocket
- fácil compartilhar infraestrutura de config, logging e DB session factory

## Frontend do animator

Sugestão inicial:

- HTML + CSS + JavaScript, no mesmo estilo pragmático do NOCA

Motivos:

- reduz custo de introdução
- aproveita experiência já existente no `web/`
- suficiente para MVP de placar animado e revelação

Observação:

- nada impede uma evolução futura para frontend separado, mas isso não é necessário agora

## Persistência do estado de revelação

Há duas opções realistas.

### Opção A: estado efêmero em memória

Vantagens:

- mais simples
- menor custo de implementação

Desvantagens:

- perde estado ao reiniciar processo
- complica múltiplas réplicas

### Opção B: estado persistido em Valkey ou PostgreSQL

Vantagens:

- sobrevive a reinício
- permite controle remoto mais robusto

Desvantagens:

- aumenta complexidade

Recomendação inicial:

- usar estado efêmero em memória no MVP
- modelar interfaces de serviço já preparadas para persistência futura

## Fluxo de dados proposto

## Live scoreboard

1. `animator/` carrega bootstrap do contest a partir do PostgreSQL
2. `animator/` calcula o snapshot inicial
3. `animator/` assina `VerdictEvent` em Valkey
4. a cada evento relevante:
   - invalida ou atualiza projeção do placar
   - publica delta ou snapshot para clientes conectados

## Reveal

1. operador inicia uma `RevealSession` para um contest
2. `animator/` reconstrói o placar congelado a partir dos dados autoritativos
3. `animator/` identifica submissões pós-freeze elegíveis para revelação
4. `animator/` mantém fila ordenada de revelação
5. comandos do operador avançam o estado
6. a UI recebe snapshots ou diffs a cada passo

## Forma de integração com a infraestrutura existente

## PostgreSQL

O módulo deve usar PostgreSQL como fonte autoritativa para:

- contests
- teams
- problems
- submissions
- judgments
- team profile data complementar, se criada

## Valkey

O módulo deve usar Valkey para:

- assinatura de `VerdictEvent`
- eventual persistência de sessões efêmeras
- fan-out de eventos do próprio animator, se necessário

## Shared filesystem

No MVP, o módulo não precisa depender do filesystem para dados dos times, porque foto/avatar já estão no banco.

No futuro, se houver:

- trilhas sonoras
- fotos em alta resolução
- assets adicionais

então faz sentido usar diretório compartilhado ou object storage.

## Mudanças concretas sugeridas no NOCA

## 1. Empacotamento

Adicionar `animator` ao wheel:

- incluir `animator` em `tool.hatch.build.targets.wheel.packages`

Adicionar script:

- `noca-animator = "animator.main:main"`

## 2. Configuração

Adicionar novas variáveis `NOCA_` para o módulo, por exemplo:

- `NOCA_ANIMATOR_HOST`
- `NOCA_ANIMATOR_PORT`
- `NOCA_ANIMATOR_SECRET`
- `NOCA_ANIMATOR_ENABLE_CONTROL`
- `NOCA_ANIMATOR_DEFAULT_THEME`
- `NOCA_ANIMATOR_POLL_FALLBACK_SECONDS`

## 3. Shared

Extrair para `shared/`:

- DTOs de placar
- lógica pura de score
- helpers de projeção

Opcionalmente adicionar:

- esquemas Pydantic de eventos do animator

## 4. Banco de dados

Fase 1:

- sem mudanças obrigatórias para MVP, usando apenas dados atuais

Fase 2:

- adicionar tabela de perfil de apresentação dos times
- opcionalmente adicionar tabela de sessão de revelação persistida

## 5. Rotas

Rotas públicas sugeridas:

- `/animator/c/{slug}/`
- `/animator/c/{slug}/reveleitor`
- `/animator/c/{slug}/feed`
- `/animator/c/{slug}/events`
- `/animator/c/{slug}/teams/{team_id}/photo`

Rotas de controle sugeridas:

- `POST /animator/c/{slug}/control/start-reveal`
- `POST /animator/c/{slug}/control/step`
- `POST /animator/c/{slug}/control/back`
- `POST /animator/c/{slug}/control/reset`
- `POST /animator/c/{slug}/control/jump-team`

## Estratégia de implementação em fases

## Fase 1: MVP funcional

Entregar:

- módulo `animator/` separado
- live scoreboard
- feed de teams com nome e avatar/foto
- placar animado simples

Sem escopo nesta fase:

- revelação pós-freeze
- dados ricos de time
- persistência de sessão

## Fase 2: reveal engine

Entregar:

- freeze-aware snapshot
- sessão de revelação em memória
- controle manual de passo
- UI de reveleitor

## Fase 3: dados ricos de times

Entregar:

- perfil de apresentação por time
- instituição
- imagem dedicada
- mídia opcional

## Fase 4: robustez operacional

Entregar:

- persistência de sessão de revelação
- controle remoto multioperador
- observabilidade própria
- deploy separado

## Riscos e pontos de atenção

## 1. Acoplamento indevido ao `web/`

Risco:

- `animator/` passar a importar `web/services/scoreboard.py` ou modelos ORM do `web/`

Mitigação:

- mover lógica pura e DTOs para `shared/`
- manter queries específicas dentro do próprio módulo

## 2. Divergência entre placar oficial e placar do animator

Risco:

- o animator recalcular score com semântica diferente do placar oficial

Mitigação:

- uma única implementação pura de score compartilhada

## 3. Dados insuficientes de time para experiência visual

Risco:

- `username`, `fullname` e avatar não bastarem para a apresentação desejada

Mitigação:

- começar com o que já existe
- adicionar tabela de perfil de apresentação em fase posterior

## 4. Complexidade do reveal

Risco:

- tentar reproduzir uma experiência de reveleitor completa já na primeira iteração

Mitigação:

- separar claramente live scoreboard e reveal
- entregar reveal só na segunda fase

## Recomendação final

A melhor direção para o NOCA é:

1. criar um terceiro runtime `animator/`
2. extrair a lógica pura de placar para `shared/`
3. implementar primeiro o live scoreboard animado
4. depois adicionar o motor de revelação progressiva
5. só então enriquecer os dados de time para apresentação

Essa abordagem respeita a arquitetura já existente do NOCA, reduz acoplamento e evita introduzir o protocolo legado do BOCA como dependência interna do sistema.
