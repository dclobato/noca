# Endpoint: `GET /api/allruns_secret`

Retorna **todas** as runs de uma sede — incluindo as que estão com veredito congelado — mediante apresentação de um secret válido. É o endpoint central do **reveleitor**.

---

## Especificação

| Atributo | Valor |
|----------|-------|
| Método | `GET` |
| Caminho | `/api/allruns_secret` |
| Tipo | REST |
| Autenticação | Secret por query string |
| Fonte | `server/server-v2/src/api.rs` — `get_allruns_secret_fn` |

---

## Query parameters

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|-------------|-----------|
| `secret` | `string` | **Sim** | Chave de acesso que identifica a sede |
| `contest` | `string` | Não | Nome da contest/configuração. Se omitido, usa `""` (padrão). |

---

## Respostas

| Código HTTP | Situação | Corpo |
|-------------|----------|-------|
| `200 OK` | Secret válido e contest iniciada | JSON com `RunsFile` filtrado |
| `403 Forbidden` | Secret inválido, sede não encontrada, ou contest não iniciada | Vazio |

Não há distinção de código entre "secret errado" e "contest não iniciada" — ambos retornam `403` por segurança (não vazar informação sobre o estado do servidor).

---

## Lógica interna

```
1. Busca a tupla de configuração pelo nome de "contest"
2. Dentro da configuração, busca a sede pelo secret fornecido:
   secret.get_sede_by_secret(&query.secret)
   → Procura no HashMap { secret_string → Sede }
   → Se não encontrar, retorna 403
3. Adquire lock do DB em memória
4. Verifica se time_file >= 0 → se não, retorna 403
5. Retorna run_file_secret.filter_sede(&sede) como JSON
```

A resolução do secret acontece **antes** de adquirir o lock do DB, o que significa que a verificação é feita sem bloquear outros leitores.

---

## O que diferencia este endpoint dos outros

| Endpoint | Fonte das runs | Freeze aplicado? |
|----------|---------------|-----------------|
| `GET /api/contest` | `DB.run_file` + estado dos times | Sim — vereditos congelados são `Wait` |
| `GET /api/allruns_ws` | `DB.run_file` via broadcast | Sim — vereditos congelados são `Wait` |
| **`GET /api/allruns_secret`** | **`DB.run_file_secret`** | **Não — vereditos reais** |

`run_file_secret` é atualizado a partir da fonte bruta do BOCA, sem aplicação de `filter_frozen`. Ele contém o veredito real de cada submissão, inclusive as que ocorreram após o horário de congelamento.

---

## Sistema de secrets

Os secrets são configurados em um arquivo TOML (passado via `--secret` na linha de comando):

```toml
[[secrets]]
name   = "Site-SP"
secret = "minha-chave-secreta"

[[secrets]]
name   = "Site-RJ"
secret = "outra-chave"
```

Internamente, isso cria um `HashMap<String, Sede>` onde a chave é o secret e o valor é a sede resolvida (com seu `RegexSet` de times).

### Com salt

Se o servidor foi iniciado com `--salt "prefixo-"`, o cliente deve enviar:
```
?secret=prefixo-minha-chave-secreta
```

O salt é prefixado a todos os secrets durante a inicialização:
```rust
let complete = format!("{}{}", salt, &sede_secret.secret);
```

### No `run_reveleitor.sh`

No script, o secret é passado diretamente como string (não como arquivo):
```bash
SECRET=$(echo $RANDOM | md5sum | head -c 5)
./target/release/simples ${URL} --port ${PORT} --config ... --secret ${SECRET}
echo "$SEDE => http://localhost:${PORT}/reveleitor.html?secret=${SECRET}"
```

> **Nota:** Neste uso do script, `--secret` está sendo usado como o **valor da chave** (não como caminho de arquivo). Verifique a versão atual do CLI para confirmar o comportamento exato.

---

## Formato do JSON retornado (`RunsFile`)

```json
{
  "runs": {
    "375971416": {
      "id": 375971416,
      "order": 42,
      "time": 299,
      "team_login": "teambrbr3",
      "prob": "B",
      "answer": { "No": { "run_id": 375971416 } }
    },
    "375971500": {
      "id": 375971500,
      "order": 43,
      "time": 14500,
      "team_login": "teambrbr3",
      "prob": "B",
      "answer": { "Yes": { "time": 14500, "is_first": false, "run_id": 375971500 } }
    }
  }
}
```

O JSON é um `BTreeMap<i64, RunTuple>` onde a chave é o `id` da run. As runs são **todas** as submissões da sede, ordenadas por `id`, com seus vereditos reais.

### Variantes de `answer` possíveis (sem freeze)

| Variante | Formato | Significado |
|----------|---------|-------------|
| Aceito | `{ "Yes": { "time": 299, "is_first": false, "run_id": 123 } }` | Solução aceita |
| Rejeitado | `{ "No": { "run_id": 123 } }` | Solução rejeitada |
| Aguardando | `{ "Wait": { "run_id": 123 } }` | Pendente de julgamento (run ainda em avaliação) |
| Desconhecido | `{ "Unk": { "run_id": 123 } }` | Erro de compilação ou status não reconhecido |

Diferente de `/api/allruns_ws`, aqui `Wait` significa genuinamente que a submissão ainda está sendo julgada — **não** que foi congelada.

---

## Uso pelo reveleitor

O frontend do reveleitor (`reveleitor.html`) usa este endpoint para:

1. Buscar todas as runs com vereditos reais (pré e pós-freeze)
2. Construir o estado final do scoreboard (como ficará após a revelação)
3. Realizar a revelação animada: revelar time a time, de baixo para cima, comparando o estado congelado (visível ao público) com o estado real (conhecido apenas pelo reveleitor)

O URL completo de acesso é exibido no terminal ao iniciar o servidor:
```
Site-SP => http://localhost:10001/reveleitor.html?secret=a3f9c
```

---

## Exemplo de uso

```bash
# Acessa runs secretas da sede padrão
curl "http://localhost:8080/api/allruns_secret?secret=minha-chave"

# Acessa runs secretas de uma sede específica
curl "http://localhost:8080/api/allruns_secret?secret=minha-chave&contest=Site-SP"
```
