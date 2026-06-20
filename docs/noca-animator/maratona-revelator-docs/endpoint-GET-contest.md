# Endpoint: `GET /api/contest`

Retorna o estado atual da contest (scoreboard) filtrado para uma sede específica.

---

## Especificação

| Atributo | Valor |
|----------|-------|
| Método | `GET` |
| Caminho | `/api/contest` |
| Tipo | REST |
| Autenticação | Nenhuma |
| Fonte | `server/server-v2/src/api.rs` — `get_contest_fn` |

---

## Query parameters

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|-------------|-----------|
| `contest` | `string` | Não | Nome da sede/contest a ser retornada. Se omitido, usa `""` (string vazia), que corresponde à configuração padrão carregada no servidor. |

---

## Respostas

| Código HTTP | Situação | Corpo |
|-------------|----------|-------|
| `200 OK` | Contest iniciada e sede encontrada | JSON com `ContestFile` filtrado |
| `403 Forbidden` | Contest ainda não iniciada (`time_file < 0`) | Vazio |
| `404 Not Found` | Nome de sede não encontrado na configuração | Vazio |

---

## Lógica interna

```
1. Adquire lock do DB em memória (mutex assíncrono)
2. Verifica se time_file >= 0 → se não, retorna 403
3. Busca a configuração da sede pelo nome (parâmetro "contest")
   → se não encontrada, retorna 404
4. Clona contest_file_begin (estado atual com todos os times e scores)
5. Aplica filter_sede(sede.titulo):
   → mantém apenas times cujo login casa com os regex da sede
6. Retorna o ContestFile filtrado como JSON
```

O **filtro de sede** usa um `RegexSet` compilado a partir dos padrões `codes` do arquivo TOML de configuração. Por exemplo, `codes = ["teambr.*"]` mantém apenas times cujo login começa com `teambr`.

---

## Estrutura do JSON retornado (`ContestFile`)

```json
{
  "contest_name": "ACM ICPC Regional 2022",
  "current_time": 7320,
  "maximum_time": 18000,
  "score_freeze_time": 14400,
  "penalty_per_wrong_answer": 20,
  "number_problems": 11,
  "teams": {
    "teambrbr1": {
      "login": "teambrbr1",
      "escola": "USP",
      "name": "Nome do Time",
      "placement": 1,
      "placement_global": 3,
      "id": 42,
      "problems": {
        "A": {
          "solved": true,
          "solved_first": true,
          "submissions": 2,
          "penalty": 140,
          "time_solved": 100,
          "answers": [],
          "waits": [],
          "id": 7
        },
        "B": {
          "solved": false,
          "solved_first": false,
          "submissions": 1,
          "penalty": 20,
          "time_solved": 0,
          "answers": [],
          "waits": [375971416],
          "id": 8
        }
      }
    }
  }
}
```

### Campos de `ContestFile`

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `contest_name` | string | Nome da contest |
| `current_time` | i64 | Tempo atual em segundos |
| `maximum_time` | i64 | Duração total da contest em segundos |
| `score_freeze_time` | i64 | Momento do freeze em minutos (atenção: minutos, não segundos) |
| `penalty_per_wrong_answer` | i64 | Penalidade por resposta errada (normalmente 20) |
| `number_problems` | usize | Número de problemas |
| `teams` | map | Mapa `login → Team` com apenas os times da sede |

### Campos de `Team`

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `login` | string | Login BOCA do time |
| `escola` | string | Instituição |
| `name` | string | Nome do time |
| `placement` | usize | Colocação na sede |
| `placement_global` | usize | Colocação global (recalculada a cada atualização) |
| `id` | u64 | ID incremental; muda toda vez que o estado do time muda |
| `problems` | map | Mapa `letra → Problem` |

### Campos de `Problem`

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `solved` | bool | Se o problema foi resolvido |
| `solved_first` | bool | Se foi a primeira solução global do problema |
| `submissions` | usize | Número de submissões (excluindo Waits pendentes) |
| `penalty` | i64 | Penalidade acumulada (tempo + 20 por erro) |
| `time_solved` | i64 | Tempo em que foi resolvido (0 se não resolvido) |
| `answers` | array | Lista de respostas congeladas ainda não reveladas |
| `waits` | array | Set de run_ids aguardando julgamento |
| `id` | u64 | ID incremental; muda toda vez que o estado do problema muda |

---

## Observação sobre o freeze

Este endpoint retorna os dados **com o freeze aplicado**: submissões após `score_freeze_time` têm seu veredicto substituído por `Wait`. Isso significa que o scoreboard público não revela resultados durante o período de congelamento. Para ver os dados reais (incluindo congelados), use `GET /api/allruns_secret`.

---

## Exemplo de uso

```bash
# Retorna a sede padrão
curl http://localhost:8080/api/contest

# Retorna uma sede específica
curl "http://localhost:8080/api/contest?contest=Site-SP"
```
