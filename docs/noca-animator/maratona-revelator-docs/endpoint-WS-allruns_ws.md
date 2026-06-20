# Endpoint: `GET /api/allruns_ws`

WebSocket que transmite em tempo real cada nova run (submissão) da contest, filtrada para a sede solicitada.

---

## Especificação

| Atributo | Valor |
|----------|-------|
| Método | `GET` (upgrade para WebSocket) |
| Caminho | `/api/allruns_ws` |
| Tipo | WebSocket |
| Autenticação | Nenhuma |
| Fonte | `server/server-v2/src/api.rs` — `get_allruns_ws_fn` |

---

## Query parameters

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|-------------|-----------|
| `contest` | `string` | Não | Nome da sede. Se omitido, usa `""` (string vazia), correspondendo à configuração padrão. |

---

## Respostas HTTP no handshake

| Código HTTP | Situação |
|-------------|----------|
| `101 Switching Protocols` | Sede encontrada; WebSocket estabelecido com sucesso |
| `403 Forbidden` | Nome de sede não encontrado na configuração |

> Nota: ao contrário dos endpoints REST, **não há verificação de `time_file < 0`** no WebSocket. O cliente pode conectar antes da contest começar e aguardar as primeiras runs chegarem.

---

## Lógica interna

```
1. Resolve o nome da sede (parâmetro "contest")
   → se não encontrada, retorna 403 antes do upgrade
2. Faz upgrade HTTP → WebSocket (actix_ws)
3. Subscreve no canal broadcast "runs_tx"
4. Spawna task assíncrona em loop:
   └── aguarda próxima mensagem do canal runs_tx
       ├── Se run.team_login pertence à sede (regex match):
       │   → serializa run como JSON
       │   → envia como frame de texto WebSocket
       │   → se conexão fechada (Closed), encerra o loop
       └── Se erro no canal (lagged/closed), encerra o loop
```

O canal `runs_tx` é um **broadcast com memória** (`membroadcast`), que retém até 1.000.000 de mensagens. Isso permite que clientes que se conectam após algumas runs já terem sido processadas recebam o histórico recente. Cada mensagem no canal corresponde a uma `RunTuple` que foi alterada ou inserida no ciclo de atualização mais recente.

---

## Filtragem por sede

Cada run recebida do canal é verificada individualmente:

```rust
if sede.team_belongs_str(&r.team_login) {
    // envia ao cliente
}
```

O método `team_belongs_str` usa um `RegexSet` compilado a partir dos `codes` da sede. Se o login do time não casar com nenhum padrão da sede, a run é silenciosamente ignorada — o cliente não recebe nada para aquele time.

---

## Formato das mensagens enviadas ao cliente

Cada mensagem é um JSON representando uma `RunTuple`:

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

### Variantes de `answer`

| Variante | Formato JSON | Significado |
|----------|-------------|-------------|
| Aceito | `{ "Yes": { "time": 299, "is_first": false, "run_id": 123 } }` | Solução correta |
| Rejeitado | `{ "No": { "run_id": 123 } }` | Solução incorreta |
| Aguardando | `{ "Wait": { "run_id": 123 } }` | Pendente de julgamento ou congelado |
| Desconhecido | `{ "Unk": { "run_id": 123 } }` | Erro de compilação ou status desconhecido |

### Campos de `RunTuple`

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `id` | i64 | ID único da submissão no BOCA |
| `order` | u64 | Posição na lista de runs (ordem de entrada no arquivo) |
| `time` | i64 | Tempo da submissão em segundos desde o início da contest |
| `team_login` | string | Login BOCA do time |
| `prob` | string | Letra do problema (ex: `"A"`, `"B"`, `"AA"`) |
| `answer` | objeto | Veredito do juiz (ver tabela acima) |

---

## Comportamento com o freeze

Este endpoint transmite apenas as runs do `run_file` **com o freeze aplicado**: runs submetidas após `score_freeze_time` têm seu veredito substituído por `{ "Wait": { ... } }`. O veredicto real dessas runs só é acessível via `/api/allruns_secret`.

Esse comportamento é aplicado durante `DB::refresh_db` antes do broadcast, portanto o WebSocket nunca envia vereditos reais de runs congeladas.

---

## Quando uma mensagem é enviada

Uma run é enviada ao cliente **apenas quando seu estado muda** em relação ao ciclo de atualização anterior. O método `RunsFile::refresh` compara o estado atual com o anterior e retorna somente as runs novas ou alteradas. Runs sem mudança não geram tráfego.

---

## Tratamento de desconexão

- Se o envio ao WebSocket retornar `Closed` (cliente desconectou), o loop termina e a task é encerrada limpa.
- Se o canal broadcast retornar erro (ex: lag excessivo), o loop também termina com um log de warning.

---

## Exemplo de uso (JavaScript)

```javascript
const ws = new WebSocket('ws://localhost:8080/api/allruns_ws?contest=Site-SP');

ws.onmessage = (event) => {
  const run = JSON.parse(event.data);
  console.log(`Run ${run.id}: time=${run.time}s team=${run.team_login} prob=${run.prob}`);

  if ('Yes' in run.answer) {
    console.log(`  → ACEITO (primeiro: ${run.answer.Yes.is_first})`);
  } else if ('No' in run.answer) {
    console.log(`  → ERRADO`);
  } else if ('Wait' in run.answer) {
    console.log(`  → AGUARDANDO`);
  }
};

ws.onclose = () => console.log('Conexão encerrada');
```
