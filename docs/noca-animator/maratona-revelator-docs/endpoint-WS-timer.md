# Endpoint: `GET /api/timer`

WebSocket que transmite em tempo real as atualizações do timer da contest: tempo atual e tempo de freeze.

---

## Especificação

| Atributo | Valor |
|----------|-------|
| Método | `GET` (upgrade para WebSocket) |
| Caminho | `/api/timer` |
| Tipo | WebSocket |
| Autenticação | Nenhuma |
| Fonte | `server/server-v2/src/api.rs` — `get_timer_fn` |

---

## Query parameters

Nenhum. Este endpoint não aceita parâmetros — o timer é global para toda a instância do servidor.

---

## Respostas HTTP no handshake

| Código HTTP | Situação |
|-------------|----------|
| `101 Switching Protocols` | WebSocket estabelecido com sucesso |

Não há verificações de autenticação ou de contest iniciada: qualquer cliente pode conectar a qualquer momento.

---

## Lógica interna

```
1. Faz upgrade HTTP → WebSocket (actix_ws)
2. Subscreve no canal broadcast "time_tx"
3. Spawna task assíncrona em loop:
   └── aguarda próxima mensagem do canal time_tx
       ├── Se TimerData igual ao anterior → ignora (não envia)
       ├── Se TimerData diferente:
       │   → atualiza "previous"
       │   → serializa como JSON
       │   → envia como frame de texto WebSocket
       │   → se conexão fechada (Closed), encerra o loop
       └── Se erro no canal, encerra o loop
```

A deduplicação (`previous`) garante que mensagens idênticas consecutivas não sejam transmitidas, economizando largura de banda quando o tempo não muda entre ciclos.

---

## Formato das mensagens enviadas ao cliente

Cada mensagem é um JSON representando um `TimerData`:

```json
{
  "current_time": 7320,
  "score_freeze_time": 14400
}
```

### Campos de `TimerData`

| Campo | Tipo | Unidade | Descrição |
|-------|------|---------|-----------|
| `current_time` | i64 | **segundos** | Tempo decorrido desde o início da contest |
| `score_freeze_time` | i64 | **minutos** | Momento em que o placar é congelado |

> **Atenção à unidade:** `current_time` está em **segundos**, mas `score_freeze_time` está em **minutos**. Para comparar os dois, o frontend deve converter: a contest está congelada quando `current_time >= score_freeze_time * 60`.

---

## Quando uma mensagem é enviada

- Uma mensagem é enviada **a cada ciclo de atualização** (aproximadamente 1 segundo) em que o `TimerData` **mudou** em relação ao ciclo anterior.
- Se o servidor estiver parado ou sem dados novos, nenhuma mensagem será enviada.
- O cliente pode se conectar antes da contest iniciar e receberá as atualizações assim que o servidor começar a receber dados da URL BOCA.

---

## Canal `time_tx`

O canal `time_tx` é um `tokio::sync::broadcast` com capacidade para 1.000.000 mensagens. Diferente do canal de runs (`runs_tx`), este **não tem memória persistente** — um cliente que se conectar após mensagens já enviadas não receberá o histórico, apenas as próximas atualizações.

---

## Estados do timer

O frontend tipicamente usa `TimerData` para:

1. **Exibir o relógio da contest** — mostrando `current_time` formatado como `HH:MM:SS`
2. **Indicar o estado do freeze** — comparando `current_time` com `score_freeze_time * 60`:
   - `current_time < score_freeze_time * 60` → contest em andamento, placar visível
   - `current_time >= score_freeze_time * 60` → placar congelado
3. **Indicar fim da contest** — quando `current_time >= maximum_time` (obtido via `/api/contest`)

---

## Tratamento de desconexão

- Se o envio ao WebSocket retornar `Closed` (cliente desconectou), o loop termina.
- Se o canal broadcast retornar erro, o loop termina com log de warning.

---

## Exemplo de uso (JavaScript)

```javascript
const ws = new WebSocket('ws://localhost:8080/api/timer');

ws.onmessage = (event) => {
  const timer = JSON.parse(event.data);

  const current = timer.current_time;
  const freezeSeconds = timer.score_freeze_time * 60;

  const h = Math.floor(current / 3600);
  const m = Math.floor((current % 3600) / 60);
  const s = current % 60;

  console.log(`Tempo: ${h}h${m}m${s}s`);

  if (current >= freezeSeconds) {
    console.log('Placar CONGELADO');
  }
};

ws.onclose = () => console.log('Conexão encerrada');
```
