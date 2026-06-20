# Visão Geral: binário `simples`

O binário `simples` (em `target/release/simples`) é um servidor HTTP escrito em Rust (Actix-web) que serve como ponte entre o sistema de juiz online **BOCA** e o frontend de animação da maratona. Ele busca dados periodicamente de uma URL (o webcast do BOCA), processa as submissões e as serve via API REST e WebSocket para os clientes de animação.

---

## Como é invocado (via `run_reveleitor.sh`)

```bash
./target/release/simples ${URL} --port ${PORT} --config config/Regional_2022.toml --secret ${SECRET}
```

| Argumento | Descrição |
|-----------|-----------|
| `${URL}` | URL do webcast BOCA — um arquivo ZIP com os dados da contest |
| `--port ${PORT}` | Porta TCP em que o servidor HTTP irá escutar |
| `--config FILE.toml` | Arquivo TOML com configuração da contest (sedes, regex de times, medalhas) |
| `--secret VALOR` | Chave de acesso para os endpoints protegidos do reveleitor |

> **Nota:** No script, o secret é gerado aleatoriamente a cada execução:
> ```bash
> SECRET=$(echo $RANDOM | md5sum | head -c 5)
> ```
> E a URL do servidor é exibida no terminal:
> ```
> SedeName => http://localhost:PORT/reveleitor.html?secret=SECRET
> ```

---

## Argumentos completos disponíveis

| Flag | Padrão | Descrição |
|------|--------|-----------|
| (posicional) | — | URL do webcast BOCA ou caminho de arquivo ZIP local |
| `-p, --port` | `8000` | Porta TCP do servidor HTTP |
| `-k` | — | API key para o endpoint `PUT /api/contests` |
| `-s, --sedes` | `config/basic.toml:default` | Arquivo TOML de config + nome da contest (`ARQUIVO:NOME`) |
| `-x, --secret` | — | Arquivo TOML de secrets (mapeia chaves para sedes) |
| `-y, --salt` | — | Salt prefixado a todos os secrets |
| `-v, --volume` | — | Mapeia pasta local para rota HTTP (`PASTA:CAMINHO`) |

---

## Estrutura do projeto Rust

O binário é construído a partir do crate `server/cli`, dentro de um workspace com os seguintes componentes:

```
server/
├── cli/           — Entry point do binário e parsing de argumentos
├── server-v2/     — Servidor HTTP Actix-web e definição dos endpoints
├── service/       — Lógica de negócio: fetch, parsing, atualização do DB
└── data/          — Estruturas de dados do domínio (Contest, Team, Run, etc.)
```

**Arquivos-chave:**

| Arquivo | Responsabilidade |
|---------|-----------------|
| `server/cli/src/bin/simples.rs` | Entry point: inicialização e orquestração |
| `server/cli/src/lib.rs` | Parsing de argumentos e carregamento de config |
| `server/server-v2/src/api.rs` | Definição dos endpoints REST e WebSocket |
| `server/server-v2/src/endpoints/update_contest.rs` | Endpoint `PUT /api/contests` |
| `server/service/src/webcast.rs` | Download da URL e parsing do ZIP do BOCA |
| `server/service/src/dataio.rs` | Parsing do formato BOCA e gerenciamento do DB |
| `server/data/src/lib.rs` | Estruturas: `RunTuple`, `Team`, `Problem`, `ContestFile` |
| `server/data/src/configdata.rs` | Estruturas de config, sede, secret e regex |

---

## Fluxo de execução

### 1. Inicialização

1. Parseia argumentos da linha de comando.
2. Carrega o arquivo TOML de configuração: sedes, padrões regex de times, posições de medalha.
3. Carrega arquivo(s) de secrets: mapeia chaves → sedes.
4. Aplica salt aos secrets, se fornecido.
5. Cria banco de dados em memória (`DB`) vazio.
6. Configura dois canais de broadcast assíncronos:
   - `runs_tx`: distribui runs novas para clientes WebSocket
   - `time_tx`: distribui atualizações de timer

### 2. Loop de atualização em background (a cada 1 segundo)

Ativo apenas se uma URL foi fornecida.

```
URL BOCA (ZIP)
    ↓ GET HTTP (reqwest) ou leitura de arquivo local
Arquivo ZIP
    ↓ extrai três entradas:
├── "time"    → inteiro i64 (segundos decorridos na contest)
├── "contest" → metadados (times, problemas, timing, freeze)
└── "runs"    → lista de submissões no formato BOCA
    ↓
filter_teams   → remove runs de times fora da configuração
filter_frozen  → submissões após o freeze viram Answer::Wait
    ↓
Atualiza DB em memória
    ↓
Broadcast de runs novas → clientes WebSocket /api/allruns_ws
Broadcast de timer      → clientes WebSocket /api/timer
```

O ZIP é procurado nos seguintes caminhos internos (em ordem de tentativa):
`name`, `./name`, `./sample/name`, `sample/name`, `./webcast/name`, `webcast/name`

### 3. Servidor HTTP

Escuta na porta configurada com CORS habilitado. Todos os endpoints vivem sob o prefixo `/api/`.

---

## Endpoints disponíveis

| Método | Caminho | Tipo | Descrição resumida |
|--------|---------|------|--------------------|
| `GET` | `/api/contest` | REST | Scoreboard atual filtrado por sede |
| `GET` | `/api/config` | REST | Configuração TOML da contest |
| `GET` | `/api/allruns_ws` | WebSocket | Stream em tempo real de runs (públicas) |
| `GET` | `/api/timer` | WebSocket | Stream em tempo real do timer |
| `GET` | `/api/allruns_secret` | REST | Todas as runs incluindo congeladas (requer secret) |
| `PUT` | `/api/contests` | REST | Injeta estado da contest externamente (requer api-key) |

Cada endpoint é detalhado em seu próprio documento na pasta `docs/`.

---

## Estruturas de dados centrais

### `RunTuple` — uma submissão

```json
{
  "id": 375971416,
  "order": 42,
  "time": 299,
  "team_login": "teambrbr3",
  "prob": "B",
  "answer": { "No": { "run_id": 375971416 } }
}
```

O campo `answer` pode ser:
- `{ "Yes": { "time": 299, "is_first": false, "run_id": 123 } }` — Aceito
- `{ "No": { "run_id": 123 } }` — Rejeitado
- `{ "Wait": { "run_id": 123 } }` — Aguardando julgamento (ou congelado)
- `{ "Unk": { "run_id": 123 } }` — Erro de compilação / desconhecido

O formato BOCA de entrada é: `ID\tTEMPO\tTEAM_LOGIN\tPROBLEMA\tRESPOSTA`
onde `RESPOSTA` é `Y`, `N`, `X` ou `?`.

### `ContestFile` — estado completo da contest

Inclui: nome, times (com seus problemas e scores), `current_time`, `maximum_time`, `score_freeze_time`, `penalty_per_wrong_answer`, `number_problems`.

### `TimerData` — dados do timer

```json
{ "current_time": 7200, "score_freeze_time": 14400 }
```

Ambos em segundos. `is_frozen()` retorna `true` se `current_time >= score_freeze_time * 60`.

### `DB` — banco de dados em memória

| Campo | Conteúdo |
|-------|----------|
| `run_file` | Runs públicas (com freeze aplicado — verdade para clientes normais) |
| `run_file_secret` | Todas as runs sem freeze (verdade absoluta, usada pelo reveleitor) |
| `contest_file_begin` | Estado atual da contest com scores de todos os times |
| `time_file` | Tempo atual da contest em segundos (`-1` = não iniciada) |

---

## Sistema de configuração (TOML)

```toml
[titulo]
name = "ACM ICPC Regional 2022"
codes = ["teambr.*"]   # regex: times do contest principal
ouro   = 3             # posição máxima para ouro
prata  = 6             # posição máxima para prata
bronze = 9             # posição máxima para bronze

[[sedes]]
name  = "Site-SP"
codes = ["teambr_sp_.*"]
ouro   = 1
prata  = 2
bronze = 3
```

## Sistema de secrets (TOML)

```toml
[[secrets]]
name   = "Site-SP"
secret = "chave-para-site-sp"
```

Com `--salt "base-"`, o cliente deve enviar `base-chave-para-site-sp` para autenticar.

---

## WebSocket vs SSE — decisão de design

Os dois endpoints de streaming (`/api/allruns_ws` e `/api/timer`) usam WebSocket. Uma evidência importante no código revela que essa pode não ter sido a escolha mais idiomática:

```rust
// server-v2/src/api.rs — ambos os handlers WebSocket
let (response, mut session, _msg_stream) = actix_ws::handle(&req, body)?;
```

O `_msg_stream` (prefixo `_` = intencionalmente ignorado) significa que **o servidor nunca lê nada enviado pelo cliente**. A comunicação é estritamente unidirecional: servidor → cliente. Esse é exatamente o caso de uso para o qual **Server-Sent Events (SSE)** foi projetado.

### Comparação direta para este caso

| Critério | WebSocket (atual) | SSE (alternativa) |
|----------|-------------------|-------------------|
| Direção | Bidirecional (mas só server→client é usado) | Unidirecional (server→client) — suficiente aqui |
| Protocolo | Upgrade HTTP → WS (framing binário próprio) | HTTP puro (`text/event-stream`) |
| Reconexão automática | Não — o cliente precisa implementar | Sim — o browser reconecta sozinho |
| Suporte a HTTP/2 | Problemático (WS não roda sobre H2) | Nativo |
| Proxies reversos | Alguns proxies bloqueiam ou não fazem buffer de WS | Funciona como qualquer HTTP |
| Multiplexing | Uma conexão TCP por stream | Múltiplos streams na mesma conexão HTTP/2 |
| Implementação no servidor | Mais complexa (framing, ping/pong, estados) | Trivial (`text/event-stream` + `\n\n`) |

### Por que provavelmente foi escolhido WebSocket assim mesmo

1. **Inércia de decisão**: WebSocket é a escolha "padrão" quando se pensa em "tempo real" no browser, mesmo quando SSE bastaria.
2. **Ecossistema Actix**: `actix_ws` é a solução natural no Actix-web. SSE com Actix exige mais código manual (`HttpResponse::Ok().streaming(...)` com um stream assíncrono).
3. **Antecipação de bidirecionalidade**: pode ter sido pensado que o cliente precisaria enviar algo futuramente (ex: controle da revelação), o que tornaria WebSocket necessário — mas isso não existe no código atual.
4. **Suporte a browsers legados**: na época do início do projeto, SSE tinha limitações no IE/Edge legado. Hoje isso não é mais relevante.

### Conclusão

A escolha não é incorreta, mas é over-engineered para o uso atual. Se fosse redesenhado, SSE seria mais simples e idiomático para esses dois endpoints — especialmente com HTTP/2, onde SSE escala melhor que múltiplas conexões WebSocket independentes.

---

## Score e ranking

Critérios de desempate (em ordem):
1. Número de problemas resolvidos (maior primeiro)
2. Penalidade total (menor primeiro): tempo de resolução + 20 pontos por tentativa errada
3. Tempo da última solução (menor primeiro)
4. Login do time (alfabético)
