# Endpoint: `PUT /api/contests`

Permite que sistemas externos injetem o estado completo da contest diretamente no servidor, sem depender da URL BOCA. Útil para integrações via push ao invés de polling.

---

## Especificação

| Atributo | Valor |
|----------|-------|
| Método | `PUT` |
| Caminho | `/api/contests` |
| Tipo | REST |
| Autenticação | API key via header HTTP |
| Fonte | `server/server-v2/src/endpoints/update_contest.rs` |

---

## Autenticação

A requisição deve incluir o header `apikey` com o valor configurado no servidor via flag `-k`:

```
apikey: minha-api-key-secreta
```

O servidor é iniciado com:
```bash
./target/release/simples ... -k minha-api-key-secreta
```

Se o servidor não foi iniciado com `-k`, **todas** as requisições a este endpoint retornam `401 Unauthorized` (a chave é obrigatória para habilitar o endpoint).

---

## Corpo da requisição

Content-Type: `application/json`

O corpo deve ser um `ContestState`:

```json
{
  "time": 7320,
  "contest": {
    "contest_name": "ACM ICPC Regional 2022",
    "current_time": 7320,
    "maximum_time": 18000,
    "score_freeze_time": 240,
    "penalty_per_wrong_answer": 20,
    "number_problems": 11,
    "teams": {
      "teambrbr1": {
        "login": "teambrbr1",
        "escola": "USP",
        "name": "Nome do Time",
        "placement": 0,
        "placement_global": 0,
        "id": 0,
        "problems": {}
      }
    }
  },
  "runs": [
    {
      "id": 375971416,
      "order": 0,
      "time": 299,
      "team_login": "teambrbr1",
      "prob": "A",
      "answer": { "Yes": { "time": 299, "is_first": false, "run_id": 375971416 } }
    }
  ]
}
```

### Campos de `ContestState`

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `time` | i64 | Tempo atual da contest em segundos |
| `contest` | `ContestFile` | Estado completo da contest com times |
| `runs` | `RunTuple[]` | Lista de todas as submissões |

---

## Respostas

| Código HTTP | Situação |
|-------------|----------|
| `201 Created` | Estado atualizado com sucesso |
| `401 Unauthorized` | Header `apikey` ausente, incorreto, ou servidor sem `-k` configurado |
| `500 Internal Server Error` | Falha ao processar e salvar o estado |

---

## Lógica interna

```
1. Verifica se server_api_key está configurado
   → se não, retorna 401
2. Verifica header "apikey" da requisição
   → se ausente ou diferente da chave configurada, retorna 401
3. Extrai ContestState do corpo JSON
4. Chama update_runs_from_data(contest_state, db, runs_tx, time_tx)
   → Atualiza o DB em memória com os novos dados
   → Faz broadcast das runs novas para clientes WebSocket
   → Faz broadcast do novo timer para clientes WebSocket
5. Retorna 201 em sucesso, 500 em falha (logando o erro)
```

---

## Comparação com o loop de polling

| Característica | Loop de polling (URL BOCA) | `PUT /api/contests` |
|----------------|---------------------------|---------------------|
| Direção | Pull (servidor busca) | Push (cliente envia) |
| Frequência | A cada ~1 segundo | A critério do chamador |
| Configuração | Flag posicional (URL) | Flag `-k` + chamadas HTTP |
| Uso típico | Integração com BOCA via webcast | Integrações customizadas ou replay |

Ambos os mecanismos atualizam o mesmo DB e disparam os mesmos broadcasts para os WebSockets — do ponto de vista dos clientes conectados, o comportamento é idêntico.

---

## Efeito nos clientes WebSocket

Após uma chamada bem-sucedida a `PUT /api/contests`, todos os clientes conectados em `/api/allruns_ws` e `/api/timer` recebem imediatamente as atualizações correspondentes:
- Runs novas ou modificadas → broadcast em `runs_tx`
- Novo `TimerData` → broadcast em `time_tx`

---

## Exemplo de uso

```bash
curl -X PUT http://localhost:8080/api/contests \
  -H "Content-Type: application/json" \
  -H "apikey: minha-api-key-secreta" \
  -d '{
    "time": 3600,
    "contest": {
      "contest_name": "Minha Contest",
      "current_time": 3600,
      "maximum_time": 18000,
      "score_freeze_time": 240,
      "penalty_per_wrong_answer": 20,
      "number_problems": 5,
      "teams": {}
    },
    "runs": []
  }'
```

---

## Segurança

- A comparação do `apikey` é feita byte a byte (`as_bytes() != contest_key.as_bytes()`), sem timing-safe comparison. Em ambientes de produção expostos à internet, recomenda-se adicionar TLS e/ou usar um proxy reverso.
- O servidor deve ser iniciado com `-k` para habilitar o endpoint. Sem essa flag, o endpoint retorna `401` para qualquer requisição.
