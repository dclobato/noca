# Endpoint: `GET /api/config`

Retorna a configuração TOML da contest para uma sede específica, conforme carregada na inicialização do servidor.

---

## Especificação

| Atributo | Valor |
|----------|-------|
| Método | `GET` |
| Caminho | `/api/config` |
| Tipo | REST |
| Autenticação | Nenhuma |
| Fonte | `server/server-v2/src/api.rs` — `get_config_fn` |

---

## Query parameters

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|-------------|-----------|
| `contest` | `string` | Não | Nome da sede/contest. Se omitido, usa `""` (string vazia), que corresponde à configuração padrão. |

---

## Respostas

| Código HTTP | Situação | Corpo |
|-------------|----------|-------|
| `200 OK` | Contest iniciada e sede encontrada | JSON com `ConfigContest` |
| `403 Forbidden` | Contest ainda não iniciada (`time_file < 0`) | Vazio |
| `404 Not Found` | Nome de sede não encontrado na configuração | Vazio |

---

## Lógica interna

```
1. Adquire lock do DB em memória (mutex assíncrono)
2. Verifica se time_file >= 0 → se não, retorna 403
3. Busca a tupla (ConfigContest, Contest, Secret) pelo nome de sede
   → se não encontrada, retorna 404
4. Retorna o ConfigContest (a estrutura bruta desserializada do TOML) como JSON
```

A diferença em relação a `/api/contest` é que este endpoint retorna a **configuração estática** carregada do arquivo TOML, não o estado dinâmico da contest. O estado dinâmico (scores, times, submissões) vem de `/api/contest`.

---

## Estrutura do JSON retornado (`ConfigContest`)

```json
{
  "titulo": {
    "name": "ACM ICPC Regional 2022",
    "codes": ["teambr.*"],
    "style": null,
    "ouro": 3,
    "prata": 6,
    "bronze": 9
  },
  "sedes": [
    {
      "name": "Site-SP",
      "codes": ["teambr_sp_.*"],
      "style": "color-sp",
      "ouro": 1,
      "prata": 2,
      "bronze": 3
    },
    {
      "name": "Site-RJ",
      "codes": ["teambr_rj_.*"],
      "style": null,
      "ouro": 1,
      "prata": 2,
      "bronze": 3
    }
  ]
}
```

### Campos de `ConfigContest`

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `titulo` | `SedeEntry` | Configuração do contest principal (engloba todas as sedes) |
| `sedes` | `SedeEntry[]` ou `null` | Lista de sedes individuais. `null` se não houver sedes configuradas. |

### Campos de `SedeEntry`

| Campo | Tipo | Padrão | Descrição |
|-------|------|--------|-----------|
| `name` | string | — | Nome da sede. Usado como chave no parâmetro `?contest=` |
| `codes` | string[] | `[]` | Lista de padrões regex. Um time pertence à sede se seu login casar com **qualquer** padrão |
| `style` | string ou `null` | `null` | Identificador de estilo CSS para o frontend |
| `ouro` | usize | `1` | Colocação máxima que recebe medalha de ouro |
| `prata` | usize | `2` | Colocação máxima que recebe medalha de prata |
| `bronze` | usize | `3` | Colocação máxima que recebe medalha de bronze |

---

## Como os `codes` (regex) funcionam

Os padrões em `codes` são compilados em um `RegexSet` (biblioteca `regex`). Um time é considerado parte da sede se o seu login **contiver** qualquer um dos padrões (busca de substring, não ancoragem implícita).

Exemplos:
- `"teambr.*"` → casa `teambrbr1`, `teambr_sp_01`, etc.
- `"teambr_sp_"` → casa `teambr_sp_01`, `teambr_sp_02`, etc.
- `"^teambr_sp_\\d+$"` → casa exatamente times SP com sufixo numérico

---

## Uso típico pelo frontend

O frontend usa `/api/config` para obter os metadados da sede:
- Nome da contest para exibição
- Posições de medalha (ouro/prata/bronze) para colorir o scoreboard
- Padrões de times (para lógica de filtragem local, se necessário)

---

## Exemplo de uso

```bash
# Retorna configuração da sede padrão
curl http://localhost:8080/api/config

# Retorna configuração de uma sede específica
curl "http://localhost:8080/api/config?contest=Site-SP"
```

---

## Arquivo TOML correspondente

O JSON retornado é a serialização direta do arquivo TOML passado via `--config` (ou `--sedes`). Exemplo de arquivo TOML fonte:

```toml
[titulo]
name   = "ACM ICPC Regional 2022"
codes  = ["teambr.*"]
ouro   = 3
prata  = 6
bronze = 9

[[sedes]]
name   = "Site-SP"
codes  = ["teambr_sp_.*"]
ouro   = 1
prata  = 2
bronze = 3

[[sedes]]
name   = "Site-RJ"
codes  = ["teambr_rj_.*"]
ouro   = 1
prata  = 2
bronze = 3
```
