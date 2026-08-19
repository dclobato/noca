# Padrões de Interface do Usuário

Este documento descreve padrões de UI e boilerplates reutilizáveis usados no projeto.

## Índice

- [Admin List Page](#admin-list-page)
- [Paginação](#paginação)
- [Linhas de tabela clicáveis](#linhas-de-tabela-clicáveis)
- [Coluna de Tabela Ordenável (sort_by)](#coluna-de-tabela-ordenável-sort_by)
- [Autocomplete com Estado Pendente](#autocomplete-com-estado-pendente)
- [Destaque de Linha após CRUD](#destaque-de-linha-após-crud)
- [Upload de Imagem com Cropper](#upload-de-imagem-com-cropper)

---

## Admin List Page

### Visão Geral

Padrão para páginas de listagem do painel admin da Arena (ex: `/admin/problems`, `/admin/categories`,
`/admin/affiliations`, `/admin/users`). Garante layout consistente do cabeçalho, barra de filtro e
controles de paginação.

**Referência canônica:** `arena/template/admin/problem_list.html`

### Estrutura do Cabeçalho

```jinja2
<div class="d-flex flex-wrap gap-2 align-items-center justify-content-between mb-3">
    <h1 class="arena-card-title mb-0">
        {{ render_icon(icon="<icon>") }}
        <Entity> Management
    </h1>
    {# Add button — only when the page has a create flow #}
    <a href="{{ request.url_for("arena_admin_<entity>_new") }}" class="btn btn-primary btn-sm">
        {{ render_icon(icon="add") }}
        Add new <entity>
    </a>
</div>
```

**Regras:**
- `d-flex flex-wrap gap-2 align-items-center justify-content-between mb-3` no div container
- `mb-0` no `<h1>` para alinhar verticalmente com o botão
- Botão "Add new \<entity\>": `btn btn-primary btn-sm` + ícone `add` + label "Add new \<entity\>"
- Se a página não tem fluxo de criação (ex: users), omitir o botão — mas manter o div wrapper para consistência de alinhamento

### Estrutura da Barra de Filtros

```jinja2
<form method="get" class="d-flex flex-wrap gap-2 align-items-end mb-3" id="<entity>-filter-form">
    {% with label="Search", value=search, placeholder="…" %}
        {% include "_partials/filter_search_field.html" %}
    {% endwith %}

    {# Outros filtros opcionais (selects, dropdowns, etc.) #}

    <div>
        <label class="form-label form-label-sm mb-1" for="per_page">Per page</label>
        <select class="form-select form-select-sm" id="per_page" name="per_page">
            {% for n in [10, 25, 50, 100] %}
                <option value="{{ n }}" {% if per_page == n %}selected{% endif %}>{{ n }}</option>
            {% endfor %}
        </select>
    </div>

    {% include "_partials/filter_submit_button.html" %}

    {% set _filters_active = search or <other_filters> or per_page != 25 %}
    {% with clear_url=request.url_for("arena_admin_<entity>_list"), filters_active=_filters_active %}
        {% include "_partials/filter_clear_link.html" %}
    {% endwith %}
</form>
```

**Regras:**
- Campo de busca: wrapper `<div class="flex-grow-1 arena-filter-search">` — usa a classe CSS
  `.arena-filter-search` (min 180px / max 320px) definida em `arena/static/css/arena.css`
- O campo de busca deve usar `_partials/filter_search_field.html` para manter label, classes,
  largura e atributos consistentes.
- Botão de filtrar: `btn btn-secondary btn-sm` + ícone `filter_list` + label "Filter"
- O botão de filtrar deve usar `_partials/filter_submit_button.html`.
- **Não usar** `btn-primary`, `btn-outline-secondary` ou ícone `search` no botão de filtrar
- Link "Clear filters": conditional — `btn-outline-secondary` quando filtros ativos, `btn-link text-muted` quando inativo
- O link "Clear filters" deve usar `_partials/filter_clear_link.html`.
- `_filters_active` deve incluir **todos** os filtros da página (search, selects, per_page se != 25, etc.)
- O link "Clear filters" deve estar sempre presente (habilitado ou desabilitado)

### Exemplos Reais

| Template | Referência |
|---|---|
| `arena/template/admin/problem_list.html` | Referência canônica completa |
| `arena/template/admin/category_list.html` | Com botão "Add new category" |
| `arena/template/admin/affiliation_list.html` | Com filtros de country/subdivision |
| `arena/template/admin/user_list.html` | Sem botão de criação |
| `arena/template/problems/problem_list.html` | Versão pública (sem painel admin) |

### Checklist de Implementação

- [ ] Header em `d-flex flex-wrap gap-2 align-items-center justify-content-between mb-3`
- [ ] `mb-0` no `<h1>` dentro do header
- [ ] Botão "Add new \<entity\>" com `btn btn-primary btn-sm` + ícone `add` (quando aplicável)
- [ ] Campo de busca em wrapper `flex-grow-1 arena-filter-search` (sem inline style)
- [ ] Botão de filtro com `btn btn-secondary btn-sm` + ícone `filter_list` + label "Filter"
- [ ] Link "Clear filters" com lógica `_filters_active` cobrindo todos os filtros
- [ ] `_filters_active` inclui todos os campos de filtro relevantes da página

---

## Paginação

### Visão Geral

Controles de paginação reutilizáveis para listagens da Arena, renderizados **acima e abaixo**
da tabela para que o usuário possa trocar de página sem rolar até o fim de listas longas.

**Partial canônico:** `arena/template/_partials/pagination.html`

### Uso

Inclua o partial via `{% with %}` — uma vez antes da tabela (`nav_margin="mb-2"`) e uma vez
depois (margem padrão `mt-2`):

```jinja2
{# acima da tabela #}
{% with pagination=pagination, page_param="page", label="User list pagination", nav_margin="mb-2" %}
    {% include "_partials/pagination.html" %}
{% endwith %}
<div class="table-responsive">
    <table class="arena-table">…</table>
</div>
{# abaixo da tabela #}
{% with pagination=pagination, page_param="page", label="User list pagination" %}
    {% include "_partials/pagination.html" %}
{% endwith %}
```

**Parâmetros:**
- `pagination` (obrigatório): objeto `Pagination` (`arena.services.pagination_service`) com
  `pages`, `page`, `has_prev`/`has_next`, `prev_num`/`next_num` e `iter_pages()`.
- `page_param` (default `"page"`): nome do parâmetro de query da página. Permite múltiplas
  paginações na mesma página (ex: as abas de `class_list.html`: `registered_page`, `open_page`,
  `manage_page`).
- `label`: texto do `aria-label` do `<nav>`.
- `nav_margin` (default `"mt-2"`): classe utilitária de margem. Use `"mb-2"` na instância de
  cima e o default na de baixo.

**Regras:**
- O partial se auto-protege com `{% if pagination and pagination.pages > 1 %}` — nada é
  renderizado quando há só uma página.
- Os links usam `request.url.include_query_params(**{page_param: …})`, preservando os demais
  filtros da URL atual.
- A página selecionada usa Arena Green (`--arena-primary`) no fundo e na borda,
  com `--arena-on-primary` no texto. Não use o azul padrão do Bootstrap.
- Renderizar sempre a mesma paginação acima e abaixo da tabela (somente `nav_margin` muda).

### Casos especiais (perfil)

As abas do perfil que combinam `tab=` + filtros usam partials dedicados que codificam esse
estado nos links — mantenha-os, apenas incluindo-os **também acima** da listagem (com
`nav_margin="mb-2"`):
- `arena/template/users/_pagination.html` (solved/attempted, notifications, credits)
- `arena/template/users/_submissions_pagination.html` (submissions, com search/verdict)

Ambos aceitam `nav_margin` (default vazio).

### Exemplos Reais

| Template | Observação |
|---|---|
| `arena/template/admin/user_list.html` | Inline genérico (`page`) |
| `arena/template/ranking/users.html` | Ranking |
| `arena/template/classes/class_list.html` | Três abas, três `page_param` |
| `arena/template/users/_submissions_list.html` | Partial dedicado com filtros |

---

## Linhas de tabela clicáveis

Use este padrão quando cada linha de uma tabela representa um recurso que tem
uma página de detalhes. A linha inteira funciona como link, sem precisar de uma
coluna de ações com um botão **Open**.

### Uso

Defina a URL de destino no atributo `data-href` da linha:

```jinja2
<tbody>
    {% for item in pagination.items %}
        <tr data-href="{{ request.url_for('item_detail', item_id=item.id) }}">
            <td>{{ item.name }}</td>
            <td>{{ item.status }}</td>
        </tr>
    {% endfor %}
</tbody>
```

Carregue o script compartilhado no bloco `extra_script`:

```jinja2
{% block extra_script %}
    <script src="{{ request.url_for('static_shared_js', path='row-href.js') }}?v={{ app_version }}"
            defer></script>
{% endblock %}
```

O script adiciona a classe `noca-clickable-row`, `tabindex="0"` e
`role="link"`. Ele abre o destino com clique, **Enter** ou **Espaço**, e mantém
o comportamento de abrir em uma nova aba com um clique modificado.

### Regras

Siga estas regras para manter a navegação previsível e acessível:

- Use `data-href` apenas quando a linha tiver um destino principal inequívoco.
- Não adicione uma coluna de ações apenas para repetir o mesmo destino com um
  botão **Open**.
- Mantenha links, botões, campos de formulário e outros controles dentro da
  linha quando eles executarem ações diferentes. O script não intercepta esses
  elementos.
- Adicione `data-no-row-link` a qualquer outro elemento interno que não deva
  acionar a navegação da linha.
- Inclua `row-href.js` uma única vez na página, mesmo que ela tenha várias
  tabelas clicáveis.

**Referências reais:**

- `web/template/contest/solution_tests.html`
- `arena/template/problems/problem_list.html`
- `arena/template/classes/registered.html`
- `arena/template/ranking/users.html`

---

## Coluna de Tabela Ordenável (sort_by)

### Visão Geral

Padrão para tornar colunas de uma tabela ordenáveis clicando no cabeçalho. A
ordenação é **sempre server-side** (query param na URL + `ORDER BY` no SQL) —
**não** reordenar linhas no cliente via JavaScript. Isso mantém uma única fonte
de verdade (a URL), funciona sem JS, e sobrevive a paginação/HTMX sem estado
duplicado.

Duas variações já convivem no projeto; para telas novas, **prefira a variação A**
(mais simples quando não há paginação/filtros complexos para preservar):

**A) Parâmetro único combinado `sort_by="<campo>_<asc|desc>"`**
Usado por `arena/template/admin/problem_list.html` e pelos exemplos novos abaixo
(`web/template/contest/clarifications_list.html`, `web/template/contest/runs_list.html`).

**B) Dois parâmetros separados `sort` + `direction`**
Usado por `arena/template/classes/problem_set_list.html`,
`arena/template/classes/*` e `arena/template/admin/_user_login_history.html`
(este último com nomes de campo prefixados, ex. `login_sort_dir`, para não colidir
com outros `sort`/`direction` da mesma página com múltiplas tabelas).

Ambas seguem a mesma UX: clique no cabeçalho ordena crescente na primeira vez;
clique de novo alterna para decrescente; trocar de coluna sempre volta para
crescente. Um ícone (`arrow_upward` / `arrow_downward` / `unfold_more`) indica o
estado.

### Referência canônica (variação A, com macro reutilizável)

**Macro:** `web/template/_macros.html` → `sort_link(label, field, sort_by, base_url)`

```jinja2
{% macro sort_link(label, field, sort_by, base_url) %}
    {% set is_active = sort_by in (field ~ '_asc', field ~ '_desc') %}
    {% set next_dir = 'desc' if sort_by == field ~ '_asc' else 'asc' %}
    <a href="{{ base_url.include_query_params(sort_by=field ~ '_' ~ next_dir) }}"
       class="text-decoration-none text-reset d-inline-flex align-items-center gap-1">
        {{ label }}
        {% if is_active and sort_by == field ~ '_asc' %}
            {{ render_icon(icon='arrow_upward', classes='noca-sort-icon noca-sort-icon--active') }}
        {% elif is_active %}
            {{ render_icon(icon='arrow_downward', classes='noca-sort-icon noca-sort-icon--active') }}
        {% else %}
            {{ render_icon(icon='unfold_more', classes='noca-sort-icon noca-sort-icon--idle') }}
        {% endif %}
    </a>
{% endmacro %}
```

**Uso no template da tabela** (`web/template/contest/clarifications_list.html`,
`web/template/contest/runs_list.html`):

```jinja2
{% from "_macros.html" import render_icon, sort_link %}
{% set my_base_url = request.url_for('my_full_page_route', slug=contest.login_slug) %}
...
<th class="text-end noca-col-8">{{ sort_link('Time', 'time', sort_by, my_base_url) }}</th>
<th class="text-start">{{ sort_link('Problem', 'problem', sort_by, my_base_url) }}</th>
```

**IMPORTANTE:** `base_url` deve ser sempre a URL da **página completa**
(`request.url_for(...)` da rota principal), nunca a rota de um partial HTMX
(`/list`). O clique no link faz uma navegação normal (full page load); o partial
HTMX só existe para o auto-refresh, não para a ordenação em si.

### Backend

**Normalização e ORDER BY** (arquivo do serviço/query, não da rota):

```python
Sort = Literal["time_asc", "time_desc", "problem_asc", "problem_desc"]
_ALLOWED_SORTS: frozenset[str] = frozenset({"time_asc", "time_desc", "problem_asc", "problem_desc"})


def normalize_my_sort(value: str | None) -> Sort:
    """Normalize a list ``sort_by`` query parameter."""
    if value in _ALLOWED_SORTS:
        return cast(Sort, value)
    return "time_desc"


def _order_for_sort(sort_by: Sort) -> tuple[Any, ...]:
    """Return the ORDER BY clauses for *sort_by*; secondary-column sorts tie-break on newest first."""
    if sort_by == "time_asc":
        return (MyModel.created_at.asc(),)
    if sort_by == "problem_asc":
        return (Problem.ordinal.asc(), MyModel.created_at.desc())
    if sort_by == "problem_desc":
        return (Problem.ordinal.desc(), MyModel.created_at.desc())
    return (MyModel.created_at.desc(),)
```

Exemplos reais: `web/services/clarification_service/queries.py`
(`normalize_clarification_sort`, `_apply_clarification_sort`) e
`web/services/submission_service.py` (`normalize_submission_sort`,
`_order_for_submission_sort`).

**Regra de tie-break:** quando o campo ordenado não é o tempo (ex: `problem_asc`),
sempre desempate por `created_at.desc()` (mais recente primeiro) — nunca deixe a
ordem de empate indefinida, pois o resultado ficaria instável entre requisições.

**Rota** (ambas as rotas — página completa e partial `/list` — devem aceitar e
repassar o mesmo parâmetro):

```python
@router.get("/", response_class=HTMLResponse, name="my_full_page_route")
async def view(
    request: Request,
    ctx: ContestContext = Depends(get_contest_context),
    sort_by: str = Query("time_desc"),
) -> HTMLResponse:
    normalized_sort = normalize_my_sort(sort_by)
    rows = await list_my_rows(ctx.session, ctx.contest, sort_by=normalized_sort)
    return _html(
        templates.TemplateResponse(
            request,
            "my_template.html",
            {..., "sort_by": normalized_sort},
        )
    )
```

**IMPORTANTE:**
- Valide sempre com uma função `normalize_*` (`Literal` + `frozenset`) — nunca
  interpole `sort_by` bruto no SQL.
- `sort_by` deve ir para o contexto do template em **todo** branch de retorno da
  rota (inclusive os de acesso bloqueado / lista vazia), para o macro não
  quebrar por variável ausente.
- A rota do partial (`/list`, usada por HTMX) também precisa aceitar `sort_by` e
  repassá-lo à query — senão o auto-refresh perde a ordenação escolhida.

### Preservando o sort_by durante auto-refresh HTMX

Quando a tabela tem `hx-get` de polling (`hx-trigger="every 60s"` etc.), o
`sort_by` atual precisa ser embutido na própria URL do `hx-get` — a página não
navega, então não há outra forma de o partial saber qual ordenação está ativa:

```jinja2
<div id="my-list-wrapper"
     hx-get="{{ request.url_for('my_list_partial', slug=contest.login_slug).include_query_params(sort_by=sort_by) }}"
     hx-trigger="every 60s"
     hx-swap="outerHTML">
```

Se a tabela já tem um `<form>` de filtros com `hx-include`, **não** duplique
`sort_by` como hidden input dentro do form — mantenha-o só na URL do `hx-get`,
já que o form só está presente para alguns papéis/condições e a URL cobre todos
os casos uniformemente.

### CSS

**Arquivo:** `web/static/css/contest/_layout.css` (web) /
`arena/static/css/arena/_tables.css` (arena) — cada módulo mantém sua própria
cópia das classes (não compartilhadas via `shared/static/css/common.css`, pois
cada módulo tem tokens de cor próprios):

```css
.noca-sort-icon {
    font-size: 1rem;
    vertical-align: middle;
    flex-shrink: 0;
}

.noca-sort-icon--idle {
    opacity: 0.28;
}

.noca-sort-icon--active {
    color: var(--bs-primary);
    opacity: 1;
}
```

(Arena usa os equivalentes `.arena-sort-icon`, `.arena-sort-icon--idle`,
`.arena-sort-icon--active` com `var(--arena-primary)`.)

### Exemplos Reais no Projeto

| Template | Variação | Observação |
|---|---|---|
| `arena/template/admin/problem_list.html` | A (`sort_by`) | Referência original da variação A |
| `web/template/contest/clarifications_list.html` | A (`sort_by`), via macro `sort_link` | Ordena por Time / Problem, tie-break por tempo |
| `web/template/contest/runs_list.html` | A (`sort_by`), via macro `sort_link` | Idem, preserva `sort_by` no `hx-get` de polling |
| `arena/template/classes/problem_set_list.html` | B (`sort`/`direction`) | Macro `sort_link` local ao template (não compartilhado) |
| `arena/template/admin/_user_login_history.html` | B, prefixado (`login_sort_dir`) | Evita colisão com outra tabela/aba na mesma página |

### Checklist de Implementação

- [ ] Query param validado por função `normalize_*` (`Literal` + `frozenset`), nunca interpolado direto no SQL
- [ ] `ORDER BY` com tie-break explícito por tempo quando o campo ordenado não é o tempo
- [ ] Rota da página completa **e** rota do partial HTMX aceitam e repassam `sort_by`
- [ ] `sort_by` presente em **todos** os branches de retorno da rota (inclusive bloqueado/vazio)
- [ ] Links do cabeçalho apontam para a URL da **página completa**, nunca para a rota do partial
- [ ] `hx-get` de auto-refresh embute `sort_by` via `.include_query_params(sort_by=sort_by)`
- [ ] Ícone de estado (`arrow_upward` / `arrow_downward` / `unfold_more`) com classes `--active`/`--idle`

---

## Autocomplete com Estado Pendente

### Visão Geral

Padrão para formulários que permitem adicionar múltiplos itens relacionados (membros de equipe, tags, etc.) com:
- Busca via autocomplete
- Estado pendente em memória (não salvo)
- Submissão única de todos os itens via JSON

**Vantagens:**
- ✅ UX melhor: usuário vê todas as mudanças antes de salvar
- ✅ Menos requisições ao servidor
- ✅ Permite validação e rollback completo
- ✅ Transação atômica no backend

### Quando Usar

Use este padrão quando:
- Adicionar múltiplos itens relacionados a uma entidade principal
- Precisar de confirmação antes de salvar
- As mudanças devem ser atômicas (tudo ou nada)

**Exemplos no projeto:**
- Adicionar categoria a um problema

### Arquitetura

```
┌──────────────────────────────────────────┐
│ Template (Jinja2)                       │
│ - Chips visuais + input de categoria    │
│ - Dropdown de sugestões                 │
│ - Hidden input `category_names`         │
└────────────┬─────────────────────────────┘
             │
             │ Uses
             ▼
┌──────────────────────────────────────────┐
│ JavaScript (admin-problems-edit.js)     │
│ - Debounce + abort de requests antigas  │
│ - Estado pendente em memória (chips)    │
│ - Serializa nomes em string CSV         │
└────────────┬─────────────────────────────┘
             │
             │ GET autocomplete + POST form
             ▼
┌──────────────────────────────────────────┐
│ Backend (web/router.py + service)       │
│ - Busca categorias por `q`               │
│ - Cria categorias inexistentes            │
│ - Substitui categorias do problema        │
└──────────────────────────────────────────┘
```

---

## Boilerplate: Autocomplete com Estado Pendente

### 1. Template (Jinja2)

**Arquivo real:** `web/templates/admin_problems_edit.html`

```jinja2
<input type="hidden" name="category_names" id="category_names_input"
       value="{{ problem.categories | map(attribute='name') | join(',') }}">

<div id="category-chips" class="d-flex flex-wrap gap-2 mb-2 min-height-1">
  {% for cat in problem.categories %}
  <span class="category-chip" data-name="{{ cat.name }}">
    {{ cat.name }}
    <button type="button" class="remove-chip" onclick="removeChip(this)">&times;</button>
  </span>
  {% endfor %}
</div>

<div class="position-relative">
  <div class="input-group input-group-sm">
    <input type="text" id="category-input" class="form-control"
           placeholder="Add categories (comma-separated or press Enter)">
    <button type="button" class="btn btn-outline-secondary" onclick="addCategories()">Add</button>
  </div>
  <div id="category-suggestions" class="category-suggestions d-none"></div>
</div>

<div class="form-text">New category names will be created automatically.</div>
<div class="form-text">Category changes are saved only after clicking <strong>Save Changes</strong>.</div>
```

**IMPORTANTE:**
- Container do autocomplete precisa de `position-relative`
- Hidden input deve estar **dentro do form principal**
- O estado pendente fica nos chips e só é persistido no submit

---

### 2. JavaScript

**Arquivo:** `web/static/js/admin-problems-edit.js`

```javascript
function updateCategoryInput() {
  const chips = document.querySelectorAll("#category-chips .category-chip");
  const names = Array.from(chips).map((c) => c.dataset.name).filter(Boolean);
  document.getElementById("category_names_input").value = names.join(",");
}

function scheduleCategorySuggestions() {
  const query = currentCategoryToken();
  if (!query) {
    hideCategorySuggestions();
    return;
  }
  if (categorySuggestDebounce) clearTimeout(categorySuggestDebounce);
  categorySuggestDebounce = window.setTimeout(() => {
    fetchCategorySuggestions(query);
  }, 180);
}

async function fetchCategorySuggestions(query) {
  if (categorySuggestAbort) categorySuggestAbort.abort();
  categorySuggestAbort = new AbortController();

  const resp = await fetch(`/admin/problem-categories/autocomplete?q=${encodeURIComponent(query)}`, {
    signal: categorySuggestAbort.signal,
  });
  const data = await resp.json();
  renderCategorySuggestions(Array.isArray(data.categories) ? data.categories : []);
}

document.getElementById("edit-form")?.addEventListener("submit", function () {
  updateCategoryInput();
});
```

---

### 3. Backend (FastAPI)

**Arquivo:** `web/router.py`

```python
@router.get("/admin/problem-categories/autocomplete")
async def admin_problem_categories_autocomplete(..., q: str = "") -> Response:
    ...
    categories = await svc.list_categories(db, actor, query=q, limit=10)
    return Response(
        content=json.dumps({
            "ok": True,
            "categories": [{"id": c.id, "name": c.name} for c in categories],
        }),
        media_type="application/json",
    )

@router.post("/admin/problems/{problem_id}/edit")
async def admin_problems_edit_submit(..., category_names: Annotated[str, Form()] = "") -> Response:
    ...
    if category_names.strip():
        names = [n.strip() for n in category_names.split(",") if n.strip()]
        cats = await svc.get_or_create_categories(db, actor, names)
        cat_ids = [c.id for c in cats]
    else:
        cat_ids = []
    await svc.replace_problem_categories(db, actor, problem_id, category_ids=cat_ids)
```

**Arquivo:** `api/services/problem.py`

```python
async def list_categories(..., query: str | None = None, limit: int | None = None) -> list[ProblemCategory]:
    stmt = select(ProblemCategory)
    if query and query.strip():
        stmt = stmt.where(ProblemCategory.name.ilike(f"%{query.strip()}%"))
    stmt = stmt.order_by(ProblemCategory.name.asc())
    if limit is not None and limit > 0:
        stmt = stmt.limit(limit)
    return list(await db.scalars(stmt))
```

**IMPORTANTE:**
- Autocomplete deve retornar lista limitada (hoje: `limit=10`)
- Submissão deve aceitar categorias novas e existentes
- Persistência acontece uma vez, no submit principal do formulário

---

### 4. Inicialização no Template

**No projeto atual:** o JS é carregado no final do template de edição.

```jinja2
<script src="/static/js/admin-problems-edit.js" defer></script>
```

---

## Troubleshooting

### Dropdown não aparece ou aparece em posição errada

**Problema:** Autocomplete renderiza mas itens ficam sobrepostos ou fora de posição.

**Solução:** Container do input precisa de `position-relative`:
```html
<div class="position-relative">
    <input type="text" id="busca-input">
    <div id="autocomplete-results" class="dropdown-menu w-100">
    </div>
</div>
```

### Nenhuma categoria chega no backend

**Problema:** `category_names` chega vazio no `POST /admin/problems/{problem_id}/edit`.

**Causas possíveis:**
1. Hidden input fora do `<form>`
2. JavaScript não está serializando antes do submit
3. Nome do campo não coincide (`category_names`)

**Solução:** Verificar que:
- Hidden input está dentro do form
- `updateCategoryInput()` roda no `submit`
- Nome do campo no template = nome no backend (`category_names`)

### Categorias novas não são criadas

**Problema:** só categorias já existentes são associadas.

**Causa:** fluxo do submit não chamou `svc.get_or_create_categories(...)` antes de `replace_problem_categories(...)`.

**Solução:** manter a ordem no backend:

```python
cats = await svc.get_or_create_categories(db, actor, names)
await svc.replace_problem_categories(db, actor, problem_id, category_ids=[c.id for c in cats])
```

---

## Exemplos Reais no Projeto

- **Template:** `web/templates/admin_problems_edit.html` (chips + input + hidden `category_names`)
- **JavaScript:** `web/static/js/admin-problems-edit.js` (debounce, fetch, estado pendente e serialização)
- **Route web autocomplete:** `web/router.py` (`admin_problem_categories_autocomplete`)
- **Route web submit:** `web/router.py` (`admin_problems_edit_submit`)
- **Service query:** `api/services/problem.py` (`list_categories(query, limit)`)

## Editor de problema: duas portas

O problema é editado por **dois** editores, alcançados a partir da lista de problemas:

- **Definição** (ação de lápis) — metadados, enunciado, ilustração, categorias e (só no
  Contest) limites. Um único formulário, um único **Salvar**, painéis client-side: são
  campos de uma mesma transação, então trocar de painel não pode perder o que foi digitado.
- **Dados de julgamento** (ação `rule`, "Judgment data") — casos de teste, validador
  interativo e interações de exemplo. Cada aba é uma **página** própria, porque um problema
  pode ter muitos casos e um caso pode ser grande.

Regras de UX das páginas de julgamento:

- Toda ação sobre dado **existente** faz POST e é aplicada na hora (substituir, alternar
  amostra/secreto, remover, reordenar, enviar ZIP). Não há estado pendente no cliente.
- O **único** estado não visto pelo servidor são as linhas digitadas inline, salvas pelo
  botão "Salvar novos casos de teste" daquela página. Sair da página sem salvar avisa
  (`problem-edit-unsaved-guard.js`).
- Uma rejeição dessas linhas retorna **422 na mesma página**, preserva todos os valores e
  marca/focaliza a linha exata; nunca redireciona para um GET vazio.
- Um upload que descartaria linhas digitadas pede confirmação nomeando quantas
  (`data-clears-typed-rows`); a substituição em massa confirma quantos casos serão
  substituídos (`data-confirm`).
- Cada ação volta ancorada na própria linha (`#tc-{id}` / `#si-{id}`), destacada por
  `highlight-row.js`. Remoção e substituição em massa voltam sem âncora.
- A navegação entre páginas usa `<a>` reais com `aria-current="page"`. Um problema
  standard mostra apenas "Casos de teste"; um interativo mostra as três páginas.
- Antes das páginas, um resumo único informa se os dados estão prontos, incompletos ou
  aguardando compilação. Quando há submissões no Arena, o mesmo resumo oferece **Rejudge
  all** com confirmação de senha e retorna à página que iniciou a ação.
- Em cada caso de teste, **Edit** permanece visível. Download, substituição, troca entre
  amostra e secreto e remoção ficam no menu nomeado **More**; a remoção fica separada das
  ações não destrutivas.
- Menus **dentro** de `.table-responsive` usam a estratégia `fixed` do Popper
  (`data-bs-config='{"popperConfig":{"strategy":"fixed"}}'`). O wrapper é um container de
  rolagem, então um menu posicionado de forma absoluta não escapa dele: com um único caso
  de teste, abrir **More** gerava rolagem em vez de mostrar o menu. Posicionado contra a
  viewport, ele não é recortado por `overflow` de ancestral.
- `.table-responsive` rola **apenas na horizontal** (`overflow-y: hidden` em `common.css`).
  O Bootstrap define só `overflow-x: auto` e o CSS deriva o outro eixo para `auto`; uma
  tabela com altura fracionária ganhava uma barra de rolagem vertical de 1 px de curso.
- A página de um caso usa breadcrumb explícito e Back/Cancel retorna à lista de casos na
  âncora `#tc-{id}`.
- Alças de reordenação aceitam arrastar **e** setas Cima/Baixo quando focadas, mantêm o
  foco depois da resposta e anunciam o resultado em `aria-live`. Estados assíncronos do
  validador também ficam em uma região de status.
- No editor de definição, validação nativa ou do servidor abre o painel que contém o
  primeiro erro, associa texto persistente ao campo e leva o foco até ele.
- No Arena, Metadata divide campos em **Identity and attribution**, **Execution limits** e
  **Publication details**. Campos raros de notas e licença ficam em disclosure explícito,
  aberto automaticamente quando já possui algum valor.

Regras de **cromo compartilhado** entre as duas portas (um autor alterna entre elas o
tempo todo, então a página não pode mudar de forma):

- As duas portas renderizam o mesmo cabeçalho (`_partials/problem_editor_header.html`):
  título com ícone e subtítulo com o problema, seguido da barra fixa
  (`.noca-problem-save-bar`) com os submitters de publicação, **Back**, e — encostado à
  direita — o link para a outra porta, extras do módulo, o download do pacote e o badge
  read-only da estratégia. Todos os botões usam o mesmo tamanho (sem `btn-sm`).
- No Arena, **Save and enable** / **Save and disable** aparecem nas duas portas. Nas
  páginas de julgamento não há formulário pendente: os botões postam
  `POST /admin/problems/{id}/set-enabled` com o estado *alvo* e voltam para a mesma
  página. O Contest não tem estado de publicação e simplesmente não passa submitters.
- Avisos de página (erros, contest travado, "não pode ser removido") ficam em **um único
  slot**, abaixo da barra e acima da faixa de abas, nas duas portas. Nada é renderizado
  acima do título, senão o cromo de uma porta desce em relação ao da outra.
- Nas duas portas a faixa de abas fica colada a um painel com borda
  (`border border-top-0 rounded-bottom p-3 mb-3`); o `judgment_shell.html` renderiza o
  corpo da página ativa dentro desse painel.
- Cada módulo envolve as duas portas no mesmo container: Arena usa
  `container-fluid px-3 py-3`, Contest usa `container py-5`.

**Arquivos:**
- **Templates compartilhados:** `shared/template/_partials/problem_editor_header.html`,
  `problem_editor_shell.html`, `judgment_shell.html`,
  `judgment_testcases_page.html`, `judgment_validator_page.html`,
  `judgment_interactions_page.html`, `testcase_list_table.html`,
  `sample_interaction_list_table.html`
- **JavaScript:** `shared/static/js/judgment-actions.js` (confirmações, aviso de descarte,
  gatilho de substituição por linha), `tc-add-row.js`, `si-add-row.js`,
  `tc-reorder-sortable.js`, `problem-edit-unsaved-guard.js`
- **Rotas:** `web/routes/contest_admin_problem_judgment_tc.py` e
  `contest_admin_problem_judgment_pages.py`; `arena/routes/admin_problem_judgment.py`
- **View models:** `shared/services/problem_definition_view.py` (definição),
  `shared/services/judgment_page_view.py` (julgamento) e
  `shared/services/problem_editor_header.py` (cromo comum às duas portas)

---
## Checklist de Implementação

Ao implementar este padrão, verifique:

- [ ] Template tem container `position-relative` para dropdown
- [ ] Hidden input está **dentro** do form principal
- [ ] JS faz debounce e cancela request anterior (AbortController)
- [ ] Endpoint de autocomplete aceita `q` e aplica `limit`
- [ ] Form tem `submit` que serializa `category_names`
- [ ] Backend chama `get_or_create_categories` e depois `replace_problem_categories`
- [ ] UI informa que alterações só persistem ao clicar em **Save Changes**

---

## Destaque de Linha após CRUD

### Visão Geral

Este padrão destaca visualmente itens de listagem após operações de Create, Update e Delete (quando soft delete), proporcionando feedback visual imediato ao usuário sobre qual registro foi afetado pela operação.

### Objetivo

Melhorar a experiência do usuário (UX) ao:
- **Indicar visualmente** qual linha ou item foi criado, atualizado ou inativado
- **Preservar o contexto** de navegação (página, filtros, busca)
- **Fornecer feedback não intrusivo** através de animação suave

### Como Funciona

#### Fluxo Completo

1. **Usuário realiza operação** (criar/editar/inativar registro)
2. **Backend processa** e salva no banco de dados
3. **Redirect com hash fragment** (`#id-do-registro`) na URL
4. **Navegador carrega lista** e rola até o elemento com o ID correspondente
5. **JavaScript detecta hash** e aplica destaque visual amarelo
6. **Animação de fade** remove o destaque gradualmente após 500ms
7. **Estado normal** é restaurado após 2.6 segundos

#### Exemplo Visual

```
Antes: /gerencia/campeonatos?page=2
Depois: /admin/problems?page=2&page_size=50&q=graph#abc-123-def-456
                                                            ^^^^^^^^^^^^^^^^
                                                            Hash fragment (anchor)
```

---

## Componentes do Padrão

### 1. Script JavaScript

**Arquivo:** `shared/static/js/highlight-row.js`

**Funcionalidade:**
- Detecta hash fragment na URL (`window.location.hash`)
- Escapa caracteres especiais no ID alvo para uso no seletor CSS
- Localiza elemento com `id="..."` correspondente ao hash
- **Verifica se o elemento está dentro de um accordion colapsado (Bootstrap) e, se sim, expande o accordion automaticamente**
- Se o alvo estiver em uma tabela, aplica background amarelo (#ffeb3b) nas células `<td>`
- Se o alvo estiver em uma lista, aplica o destaque diretamente no `<li>`
- Remove destaque gradualmente com transição CSS

**Timing:**
- 0ms: Início da detecção do hash
- ~300ms (após carregamento DOM): Executa a lógica de destaque
- **Se o elemento estiver em accordion colapsado:** Aguarda conclusão da animação de expansão (`shown.bs.collapse` event)
- 500ms (após destaque): Inicia fade para transparente (duração: 2s)
- 2600ms (após início do destaque): Remove estilos inline completamente

### 2. Template de Lista (Jinja2)

**Requisitos:**
1. Cada registro precisa ter atributo `id` com o ID do registro
2. A página precisa incluir `shared/static/js/highlight-row.js`

**Exemplo com tabela (`web/templates/admin_problems.html`):**
```jinja2
<tbody>
  {% for problem in problems %}
  <tr id="{{ problem.id }}">
    <td class="text-muted small">{{ loop.index + (page - 1) * page_size }}</td>
    <td><div class="fw-semibold">{{ problem.title }}</div></td>
    <!-- outras colunas -->
  </tr>
  {% endfor %}
</tbody>

<script src="/static/shared-js/highlight-row.js" defer></script>
```

**Exemplo com lista (`web/template/user/enrolled_users.html`):**
```jinja2
<ul class="list-group list-group-flush px-3">
  {% for u in users %}
  <li class="list-group-item px-0 py-2" id="{{ u.id }}">
    <div class="d-flex align-items-center gap-2">
      <img src="/user/{{ u.id }}/avatar" width="32" height="32" class="rounded-circle" alt="">
      <div class="flex-grow-1 min-width-0">
        <div class="fw-semibold text-truncate">{{ u.fullname }}</div>
        <div class="text-muted small"><code>{{ u.username }}</code></div>
      </div>
      <a href="/c/{{ contest.login_slug }}/admin/users/{{ u.id }}/edit"
         class="btn btn-outline-secondary btn-sm">Edit</a>
    </div>
  </li>
  {% endfor %}
</ul>

<script src="/static/shared-js/highlight-row.js" defer></script>
```

### 3. Rotas FastAPI

#### Padrão para Operação UPDATE com retorno contextual

No projeto, a tela de edição recebe contexto da listagem (`q`, `category`, `page`, `page_size`) e, ao salvar, redireciona para a listagem com hash do registro atualizado.

**Exemplo real (`web/router.py`):**
```python
@router.post("/admin/problems/{problem_id}/edit")
async def admin_problems_edit_submit(
    ...,
    return_q: Annotated[str, Form()] = "",
    return_category: Annotated[str, Form()] = "",
    return_page: Annotated[str, Form()] = "1",
    return_page_size: Annotated[str, Form()] = "25",
) -> Response:
    ...
    return RedirectResponse(
        url=_admin_problem_list_url(
            q=normalized_return_q,
            category=normalized_return_category,
            page=normalized_return_page,
            page_size=normalized_return_page_size,
            anchor=problem_id,
        ),
        status_code=303,
    )
```

**Resultado da URL:**

```text
/admin/problems?page=2&page_size=50&q=graph&category=cat-uuid#problem-uuid
```

O `#problem-uuid` é o que ativa o destaque da linha ou item no carregamento da listagem.

---

## Parâmetros de URL Preservados

O padrão preserva os seguintes parâmetros de paginação e filtro:

- `q`: Termo de busca
- `page`: Página atual
- `page_size`: Itens por página
- Outros filtros específicos da rota

**Benefício:** O usuário retorna **exatamente** para onde estava, mantendo o contexto de navegação.

---

## Resumo de Decisões

| Operação | Registro Existe? | Usa hash (`#id`)? | Preserva filtros? |
|----------|------------------|-------------------|-------------------|
| CREATE   | ✅ Sim (novo)    | ✅ Sim            | ✅ Sim |
| UPDATE   | ✅ Sim           | ✅ Sim            | ✅ Sim |
| DELETE (hard) | ❌ Não (removido) | ❌ Não       | ✅ Sim |

---

## Checklist de Implementação

Ao implementar uma nova listagem/edição, siga este checklist:

### 0. Helpers da rota
- [ ] Criar helper para montar URL de retorno com filtros/paginação preservados

### 1. Template de Lista
- [ ] Adicionar `id="{{ registro.id }}"` em cada `<tr>` ou `<li>`
- [ ] Incluir script da listagem:
  ```jinja2
  <script src="/static/shared-js/highlight-row.js" defer></script>
  ```

### 2. Rota de Edição
- [ ] Receber contexto de retorno no GET de edição (`q`, `category`, `page`, `page_size`)
- [ ] Preencher hidden inputs no formulário para carregar esse contexto no POST
- [ ] No sucesso do POST, redirecionar para listagem com hash `#id-do-registro`

### 3. Rota de Remoção
- [ ] Preservar parâmetros da listagem no redirect
- [ ] Não usar hash quando o registro deixa de existir na tabela

---

## Benefícios UX

1. **Feedback Visual Claro**: Usuário vê exatamente qual registro foi afetado
2. **Navegação Inteligente**: Volta para mesma página, filtros e posição de scroll
3. **Não Intrusivo**: Efeito sutil que desaparece automaticamente
4. **Acessível**: Funciona sem JavaScript (apenas não tem destaque visual)
5. **Consistente**: Mesmo comportamento em todas as telas de gerenciamento

---

## Troubleshooting

### Problema: Destaque não aparece

**Causas possíveis:**
1. `<tr>` não tem atributo `id="{{ registro.id }}"`
2. Script `highlight-row.js` não foi incluído no template
3. O redirect não incluiu hash na URL (`#id-do-registro`)
4. ID usado no hash não existe na tabela atual
5. **O elemento alvo está dentro de um accordion colapsado e o script não conseguiu expandi-lo ou aguardar a expansão**
6. O template usa outro tipo de container sem suporte no script compartilhado (hoje: `<tr>` e `<li>`)

### Problema: Página não mantém contexto

**Causas possíveis:**
1. O GET de edição não recebeu `q`, `category`, `page`, `page_size`
2. O formulário de edição não reenviou os campos `return_*`
3. O redirect final ignorou os parâmetros ao montar URL de retorno

### Problema: Registro inativado não aparece na lista

**Causas possíveis:**
1. Filtro de status não foi ajustado para 'inativas' ou 'todas'
2. Query na rota `lista()` está filtrando apenas ativos por padrão

### Problema: navegador continua executando JS antigo

**Sintoma comum:** você já corrigiu `highlight-row.js`, mas o console ainda mostra erro em linha antiga (cache do browser/CDN).

**Solução (cache busting):** versionar o `src` do script com query string.

```jinja2
<script src="/static/shared-js/highlight-row.js?v={{ app_version }}-r1" defer></script>
```

**Notas práticas:**
1. Incremente o sufixo (`-r2`, `-r3`, etc.) quando precisar forçar atualização imediata.
2. Em ambiente de desenvolvimento, faça hard refresh (`Ctrl+F5`) após mudanças em arquivos estáticos.

---

## Manutenção

### Atualizando o Script

O arquivo `shared/static/js/highlight-row.js` é compartilhado por todas as listagens que usam destaque por hash. Qualquer alteração afetará todas essas telas.

**Atenção:** Testes em múltiplas telas são necessários após alterações.

### Adicionando Nova Tela

Ao criar nova listagem com edição:
1. Copie estrutura de uma tela existente (ex: gestão de problemas)
2. Siga checklist de implementação acima
3. Teste todas as operações CRUD
4. Verifique preservação de contexto de paginação

---

## Exemplos Reais no Projeto

- **Listagem:** `web/templates/admin_problems.html`
- **Script de destaque:** `shared/static/js/highlight-row.js`
- **Edição com retorno contextual:** `web/router.py` (`admin_problems_edit_page` e `admin_problems_edit_submit`)
- **Template de edição com campos de retorno:** `web/templates/admin_problems_edit.html`

---

## Upload de Imagem com Cropper

### Visão Geral

Padrão para upload de fotos de perfil com crop interativo usando Cropper.js.
O crop é aplicado tanto no cliente (UX imediato) quanto reforçado no servidor
(`ImageProcessingService.process_upload_image`).

Dois contextos suportados:

| Contexto | Input `name` | Aspecto | Max resolução |
|---|---|---|---|
| Usuário / Admin / Staff / Judge | `foto` | 2:3 (retrato) | 600×900 px |
| Time | `team` | 16:10 (paisagem) | 1600×1000 px |

**Vantagens:**
- ✅ Crop interativo antes do upload
- ✅ Preview em tempo real (proporcional ao aspecto do role)
- ✅ Aspecto reforçado pelo servidor (`crop_aspect_ratio=True`)
- ✅ Qualidade otimizada (85% para JPEG/WebP)
- ✅ Detecção automática de contexto via `name` do input
- ✅ Preserva formato original (JPEG, PNG, WebP)
- ✅ Cache-busting via `?v=<photo_version>` (timestamp de `dta_foto`)

### Quando Usar

Use este padrão quando:
- Precisar que o usuário selecione área específica da imagem
- Garantir proporções específicas determinadas pelo papel (role) do usuário
- Reforçar crop tanto client-side (UX) quanto server-side (integridade)
- Exibir avatar em múltiplos locais sem re-processar

### Arquitetura

```
┌─────────────────────────────────────────┐
│ Template (Jinja2)                       │
│ - user_profile.html                     │
│ - input[name="foto"] ou [name="team"]   │
│ - Modal Bootstrap com cropImage         │
│ - div#cropPreview (dims ∝ aspecto)      │
│ - preview src="/user/{id}/photo?v=..."  │
└────────────┬────────────────────────────┘
             │ FileReader → blob → DataTransfer
             ▼
┌─────────────────────────────────────────┐
│ JavaScript (image-cropper.js)           │
│ - detectContext(): foto → 2/3, team → 16/10│
│ - Cropper.js: aspectRatio, viewMode=1   │
│ - Gera input[name="foto_cropada"]       │
│ - Remove input original no submit       │
└────────────┬────────────────────────────┘
             │ POST /user/{user_id}/photo (multipart/form-data)
             ▼
┌─────────────────────────────────────────┐
│ Backend (web/routes/profile.py)         │
│ POST /user/{user_id}/photo              │
│ - target user resolvido por dependency  │
│ - upload permitido só para self         │
│ - role=="t" → aspect 16:10             │
│ - outros   → aspect 2:3                │
│ - image_service.process_upload_image()  │
│ - update_photo(session, user, result)   │
│ - redirect /profile?success=photo       │
└─────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────┐
│ GET /user/{user_id}/avatar?v=<...>      │
│ - Serve user.avatar (thumbnail)         │
│ - Fallback: identicon (com_foto=False)  │
│ - com_foto=True  → public               │
│ - com_foto=False → private              │
│                                         │
│ GET /user/{user_id}/photo?v=<...>       │
│ - Serve user.foto (full-size)           │
│ - Mesma política de cache acima         │
└─────────────────────────────────────────┘
```

---

## Boilerplate: Upload de Imagem com Cropper

### 1. Cache-busting da imagem

**Arquivos reais:**
- `web/template/_base.html`
- `web/template/user/user_profile.html`

O projeto atual usa `current_user.dta_foto` diretamente nos templates para gerar
o `?v=` de cache-busting.

```jinja2
<img src="/user/{{ current_user.id }}/avatar?v={{ current_user.dta_foto or '0' }}">
<img src="/user/{{ current_user.id }}/photo?v={{ current_user.dta_foto or '0' }}">
```

Use esse padrão em todo lugar que renderiza avatar/foto de usuário para que a
URL mude automaticamente após upload ou remoção.

---

### 2. Template (Jinja2)

**Arquivo real:** `web/template/user/user_profile.html`

#### CSS no `<head>` — via `{% block extra_head %}`

```jinja2
{% block extra_head %}
{% if current_user.role != "ua" %}
<link rel="stylesheet"
      href="{{ request.url_for('static_vendor', path='cropper.min.css') }}">
{% endif %}
{% endblock %}
```

#### Card de Foto (dentro de `{% block content %}`)

```jinja2
{% if current_user.role != "ua" %}
<div class="card shadow-sm mb-4">
  <div class="card-header fw-semibold">Profile Photo</div>
  <div class="card-body">

    {% if success == "photo" %}
    <div class="alert alert-success py-2 small" role="alert">Photo updated successfully.</div>
    {% elif success == "photo_removed" %}
    <div class="alert alert-success py-2 small" role="alert">Photo removed successfully.</div>
    {% endif %}
    {% if photo_error %}
    <div class="alert alert-danger py-2 small" role="alert">{{ photo_error }}</div>
    {% endif %}

    {% if current_user.role == "t" %}
    <img src="/user/{{ current_user.id }}/photo?v={{ current_user.dta_foto or '0' }}"
         width="128" height="80" class="rounded object-fit-cover border mb-3" alt="Current photo">
    {% else %}
    <img src="/user/{{ current_user.id }}/photo?v={{ current_user.dta_foto or '0' }}"
         width="60" height="90" class="rounded object-fit-cover border mb-3" alt="Current photo">
    {% endif %}

    <form method="post" action="/user/{{ current_user.id }}/photo" enctype="multipart/form-data" novalidate>
      <div class="mb-3">
        <label for="foto_input" class="form-label">New photo</label>
        {% if current_user.role == "t" %}
        <input type="file" class="form-control" id="foto_input" name="team"
               accept="image/jpeg,image/png,image/webp">
        {% else %}
        <input type="file" class="form-control" id="foto_input" name="foto"
               accept="image/jpeg,image/png,image/webp">
        {% endif %}
      </div>
      <div class="d-flex align-items-center gap-2">
        <button type="submit" class="btn btn-primary btn-sm">Save photo</button>
        {% if current_user.com_foto %}
        <button type="button" class="btn btn-outline-danger btn-sm"
                data-bs-toggle="modal" data-bs-target="#removePhotoModal">
          Remove photo
        </button>
        {% endif %}
      </div>
    </form>

  </div>
</div>

<div class="modal fade" id="cropModal" tabindex="-1"
     aria-labelledby="cropModalLabel" aria-hidden="true">
  <div class="modal-dialog modal-lg">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title" id="cropModalLabel">Adjust Photo</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
      </div>
      <div class="modal-body">
        <div class="row g-3">
          <div class="col-md-8">
            <img id="cropImage" style="max-width: 100%; display: block;" alt="Image to crop">
          </div>
          <div class="col-md-4">
            <p class="fw-semibold small mb-1">Preview</p>
            {% if current_user.role == "t" %}
            <div id="cropPreview"
                 style="width: 224px; height: 140px; overflow: hidden;
                        border: 1px solid #dee2e6;"></div>
            {% else %}
            <div id="cropPreview"
                 style="width: 140px; height: 210px; overflow: hidden;
                        border: 1px solid #dee2e6;"></div>
            {% endif %}
          </div>
        </div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-secondary btn-sm"
                data-bs-dismiss="modal">Cancel</button>
        <button type="button" class="btn btn-primary btn-sm"
                id="cropConfirm">Confirm crop</button>
      </div>
    </div>
  </div>
</div>

{% if current_user.com_foto %}
<div class="modal fade" id="removePhotoModal" tabindex="-1"
     aria-labelledby="removePhotoModalLabel" aria-hidden="true">
  <div class="modal-dialog modal-sm">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title" id="removePhotoModalLabel">Remove photo</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
      </div>
      <div class="modal-body small">
        Are you sure you want to remove your photo? Your avatar will revert to the
        default identicon.
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-secondary btn-sm" data-bs-dismiss="modal">Cancel</button>
        <form method="post" action="/user/{{ current_user.id }}/photo/remove" class="d-inline">
          <button type="submit" class="btn btn-danger btn-sm">Remove</button>
        </form>
      </div>
    </div>
  </div>
</div>
{% endif %}
{% endif %}{# end role != ua #}
```

#### Scripts — no final de `{% block content %}` (antes de `{% endblock %}`)

```jinja2
{% if current_user.role != "ua" %}
<script src="{{ request.url_for('static_vendor', path='cropper.min.js') }}"></script>
<script src="/static/js/image-cropper.js?v={{ app_version }}-r2"></script>
{% endif %}
```

**IMPORTANTE:**
- O CSS do Cropper.js vai em `{% block extra_head %}` para evitar FOUC.
- O JS do Cropper.js e o `image-cropper.js` vão no final do body (antes de `{% endblock %}`),
  não em `{% block extra_head %}` — o Bootstrap precisa estar disponível para a inicialização do modal.
- **Não usar `defer` em nenhum dos dois scripts.** Com `defer`, o navegador pode executar
  `image-cropper.js` antes que o script CDN do Cropper.js termine de carregar, resultando em
  `Uncaught ReferenceError: Cropper is not defined` no evento `shown.bs.modal`.
  Scripts no final do body já executam após o DOM estar pronto — `defer` é desnecessário e perigoso aqui.
- O sufixo `-r2` deve ser incrementado (`-r3`, `-r4`, …) quando `image-cropper.js` for alterado.

---

### 3. JavaScript

**Arquivo:** `web/static/js/image-cropper.js`

O script detecta automaticamente o contexto via `name` do input de arquivo:

| Input `name` | Contexto | `aspectRatio` | `maxWidth` | `maxHeight` |
|---|---|---|---|---|
| `foto` | usuário/admin/staff/judge | `2/3` (retrato) | 600 | 900 |
| `team` | time | `16/10` (paisagem) | 1600 | 1000 |

**Fluxo:**
1. `change` no input → FileReader lê o arquivo → Bootstrap Modal abre com `<img id="cropImage">`
2. `shown.bs.modal` → Cropper.js inicializa com `aspectRatio`, `viewMode: 1`, `autoCropArea: 1`
3. Preview em tempo real via `cropperConfig.preview = '#cropPreview'`
4. Click em `#cropConfirm` → `getCroppedCanvas({maxWidth, maxHeight})` → `toBlob()`
5. Blob → `new File(...)` → `DataTransfer` → `input[name="foto_cropada"].files`
6. `submit` → remove input original se `foto_cropada` existir

**IDs obrigatórios no DOM** (todos usados pelo script):
- `cropModal` — o elemento `.modal`
- `cropImage` — o `<img>` dentro do modal
- `cropPreview` — div de preview (opcional; se ausente, preview é ignorado)
- `cropConfirm` — botão de confirmação

---

### 4. Backend (FastAPI)

**Arquivo:** `web/routes/profile.py`

#### `GET /user/{user_id}/avatar` — serve o avatar do usuário

Serve `user.avatar`. O acesso é permitido para:
- o próprio usuário autenticado
- qualquer usuário do mesmo contest
- qualquer UberAdmin

```python
@router.get("/user/{user_id}/avatar")
async def user_avatar_by_id(
    request: Request,
    user: User = Depends(get_visible_user),
) -> Response:
    data, mime = user.avatar
    directive = "public" if user.com_foto else "private"
    return _image_response(
        image_service.build_image_response(data, mime, cache_directive=directive)
    )
```

#### `GET /user/{user_id}/photo` — serve a foto em tamanho real

Serve `user.foto`. Mesmas regras de visibilidade do avatar.

```python
@router.get("/user/{user_id}/photo")
async def user_photo_by_id(
    request: Request,
    user: User = Depends(get_visible_user),
) -> Response:
    data, mime = user.foto
    directive = "public" if user.com_foto else "private"
    return _image_response(
        image_service.build_image_response(data, mime, cache_directive=directive)
    )
```

**Cache (ambas as rotas):**
- Fotos reais (`com_foto=True`): `public`
- Identicons (`com_foto=False`): `private`
- O `?v={{ current_user.dta_foto or '0' }}` continua sendo o mecanismo de cache-busting no template.

#### `POST /user/{user_id}/photo` — recebe e salva a foto cropada

```python
@router.post("/user/{user_id}/photo")
async def user_photo_submit(
    request: Request,
    user_id: str,
    photo_ctx: UserPhotoContext = Depends(get_user_photo_context),
    foto_cropada: UploadFile | None = File(None),
) -> Response:
    # self-only
    ensure_user_photo_upload_allowed(photo_ctx.actor, user)
    result = await image_service.process_upload_image(
        upload=foto_cropada,
        crop_aspect_ratio=True,
        aspect_width=aspect_width,
        aspect_height=aspect_height,
    )
    await update_photo(session, user, result)
    return RedirectResponse(url="/profile?success=photo", status_code=303)
```

**IMPORTANTE:**
- O campo recebido é **sempre** `foto_cropada` (criado pelo JS no cliente).
- `process_upload_image(..., crop_aspect_ratio=True)` faz o crop server-side como
  salvaguarda — mesmo que o JS seja bypassado.
- `update_photo(...)` chama `user.apply_processed_photo(...)`, o que atualiza `dta_foto`
  e invalida automaticamente a URL com `?v=...` na próxima renderização.

#### `POST /user/{user_id}/photo/remove` — remoção unificada

Esta é a rota única de remoção de foto.

- self-service: remove a foto do usuário autenticado e redireciona para `/profile?success=photo_removed`
- tela admin: usa o mesmo endpoint com `return_to=admin_edit` e redireciona para a tela de edição
- upload continua sendo self-only; remoção aceita self, admin do mesmo contest, ou UberAdmin

```jinja2
<form method="post" action="/user/{{ edit_user.id }}/photo/remove">
  <input type="hidden" name="return_to" value="admin_edit">
  <button type="submit" class="btn btn-outline-danger btn-sm">Remove photo</button>
</form>
```

---

### 5. Exibir avatar em outras páginas (navbar)

**Arquivo:** `web/template/_base.html`

```jinja2
{% if current_user.role != "ua" %}
<a href="/profile" class="d-flex align-items-center" title="{{ current_user.username }}">
  <img src="/user/{{ current_user.id }}/avatar?v={{ current_user.dta_foto or '0' }}"
       width="40" height="40"
       class="rounded-circle object-fit-cover"
       style="border: 1px solid rgba(255,255,255,.25);"
       alt="{{ current_user.username }}">
</a>
{% endif %}
```

Esse é o ponto mais importante da migração: a navbar deve sempre apontar para
`/user/{current_user.id}/avatar`.

---

### Checklist de Implementação

- [ ] CSS do Cropper.js em `{% block extra_head %}`, condicionado ao role
- [ ] Input de arquivo com `name="foto"` (não-team) ou `name="team"` (team)
- [ ] Form com `enctype="multipart/form-data"` e `action="/user/{{ current_user.id }}/photo"`
- [ ] Modal com IDs exatos: `cropModal`, `cropImage`, `cropPreview`, `cropConfirm`
- [ ] `div#cropPreview` com dimensões proporcionais ao aspecto do role
- [ ] `image-cropper.js` e `cropper.min.js` carregados no final do body, **sem `defer`**, após o Bootstrap Bundle
- [ ] `GET /user/{user_id}/avatar` (navbar) e `GET /user/{user_id}/photo` (profile page) com `Cache-Control: private` para identicons e `public` para fotos reais
- [ ] `POST /user/{user_id}/photo` com `crop_aspect_ratio=True` e `aspect_width/height` por role
- [ ] `POST /user/{user_id}/photo/remove` para self-service e remoção via tela admin
- [ ] `?v={{ current_user.dta_foto or '0' }}` em `<img src="/user/.../avatar">` e `<img src="/user/.../photo">`
- [ ] Card de foto oculto para `role == "ua"` (UberAdmin não tem foto)

---

### Troubleshooting

#### Modal não abre ao selecionar imagem

**Causas possíveis:**
1. `image-cropper.js` carregado antes do Bootstrap Bundle (modal não encontrado)
2. ID do input não é `"foto"` nem `"team"` — `detectContext()` retorna `null`
3. Algum dos IDs `cropModal`, `cropImage`, `cropConfirm` está errado no HTML

**Solução:** verificar ordem dos scripts e que os IDs batem exatamente com os esperados.

#### Backend não recebe `foto_cropada`

**Causas possíveis:**
1. Form sem `enctype="multipart/form-data"`
2. Usuário não confirmou o crop (fechou o modal sem clicar "Confirm crop") — o submit
   envia o input original (`foto` ou `team`) que o JS remove, resultando em campo vazio

**Solução:** garantir `enctype` no form; o botão "Save photo" deve ser clicado apenas
após confirmar o crop no modal.

#### Avatar não atualiza após upload

**Causa:** o `?v=` do template não mudou porque `dta_foto` não foi atualizado no banco.

**Solução:** verificar se `update_photo(...)` ou `remove_photo(...)` persistiu a mudança e
se o template está usando `?v={{ current_user.dta_foto or '0' }}`.

---

### Exemplos Reais no Projeto

- **Template do perfil:** `web/template/user/user_profile.html`
- **JavaScript:** `web/static/js/image-cropper.js` (detecção de contexto, crop, submit)
- **Navbar com avatar:** `web/template/_base.html`
- **Tela admin de edição:** `web/template/user/edit_user.html`
- **Rotas de imagem:** `web/routes/profile.py`
- **Serviço de perfil:** `web/services/profile_service.py`
- **Serviço de imagem:** `web/services/imageprocessing_service.py`
- **Modelo:** `web/models/users.py` (`User.apply_processed_photo`, `User.avatar`, `User.foto`)

---

## Identidade visual compartilhada (tokens e tema)

A identidade visual dos módulos web (Contest) e arena (Arena) é unificada por uma
única fonte de verdade compartilhada, para que ajustes futuros não divirjam entre
os módulos.

### Links da Arena

Os links textuais da Arena usam um contrato único em
`arena/static/css/arena/_links.css`. Novos links comuns devem usar um elemento
`a` sem classes utilitárias locais de cor ou decoração.

- Links textuais usam Arena Green (`--arena-primary`, `#2f9e41`) nos estados
  normal e visitado. Em `hover` e `focus-visible`, usam Dark Arena Green
  (`--arena-primary-container`, `#09872d`) e exibem sublinhado de `1px`.
- Links na `.arena-topbar` e na `.arena-footer` usam a cor de texto secundária
  (`--arena-on-surface-variant`) nos estados normal e visitado. Em `hover` e
  `focus-visible`, usam Dark Arena Green, nunca exibem sublinhado, e mantêm um
  indicador de foco visível.
- Links da barra lateral mantêm o estilo próprio de `.arena-sidebar .nav-link`.
- Botões, navegação por abas, paginação, controles somente com ícone e links que
  ocupam cartões ou painéis inteiros mantêm seus estilos de componente. Use
  `.arena-icon-link` para identificar um controle de link somente com ícone.
- Cabeçalhos de ordenação não exibem sublinhado. Links que repetem o destino de
  uma linha inteira clicável podem usar `.arena-link-no-underline` para evitar
  ruído visual sem alterar as cores do contrato.
- Não use `text-decoration-none`, `text-reset`, `text-muted` ou utilitários de cor
  em links textuais comuns. Esses utilitários podem permanecer apenas nos
  componentes excluídos do contrato compartilhado.

### Tokens de design (`shared/static/css/tokens.css`)

- Importado primeiro por `shared/static/css/common.css`, que cada módulo carrega
  logo após o Bootstrap. Define no `:root` a paleta neutra `--noca-*` (superfícies
  em tons de slate, texto, bordas), o acento da marca (verde do logo,
  `--noca-brand: #2f9e41`), semânticos (success/danger/warning/info) e a
  tipografia (`Inter` no corpo, `Public Sans` nos títulos, `IBM Plex Mono` em
  código e dados literais, base `1rem`).
- Os tokens `--noca-font-variant-*` separam três necessidades: algarismos
  tabulares em todas as tabelas e, fora delas, em placares, cronômetros, ratings
  e progresso; zero cortado em usos monoespaçados; e ligaturas desativadas em
  código, credenciais, amostras e saída de máquina. Tabelas herdam o recurso de
  `common.css`; use `.noca-tabular-nums` em novos valores dinâmicos fora delas e
  `.noca-literal-text` em texto literal que ainda não tenha elemento semântico
  como `code`, `pre`, `kbd` ou `samp`.
- Todo `code` inline mantém `1em` (o mesmo tamanho do texto ao redor), peso 600
  e a cor de código adaptável ao tema. A regra compartilhada exclui `pre code`,
  portanto blocos de código preservam sua hierarquia própria.
- Propaga a identidade para o Bootstrap sobrescrevendo tokens `--bs-*`
  (`--bs-body-font-family`, `--bs-link-color`, etc.), então componentes Bootstrap e
  as classes de `common.css` herdam fonte/cores sem regra por elemento.
- Também traz a regra base de `body` e de títulos (mantendo `common.css` sem
  `:root`/`body`). Os tokens `--arena-*` referenciam os `--noca-*` no seu núcleo, e
  o web passou a linkar `noca-fonts.css` no `_base.html`.
- **Não** sobrescreve `--bs-primary` (botões `.btn-primary` são compilados via Sass
  e não reagem à variável em runtime); acentos usam `--noca-brand` diretamente.

### Modo escuro

- Alternado por `shared/static/js/theme-toggle.js` (compartilhado pelos dois
  módulos via a rota `static_shared_js`), que grava `noca-theme` no `localStorage` e
  define `data-bs-theme` no `<html>`. Um script *pre-paint* no `<head>` (antes dos
  CSS) evita o flash claro.
- O bloco `[data-bs-theme="dark"]` em `tokens.css` remapeia a rampa `--noca-*` para
  slate escuro, então os componentes `--arena-*` do Arena escurecem
  automaticamente; ajustes específicos do Arena ficam em
  `arena/static/css/arena/_dark.css` e os do web em `web/static/css/contest/_dark.css`.
- Componentes de terceiros compartilhados que só trazem tema claro têm overrides
  em `shared/static/css/components-dark.css` (importado por `common.css`):
  os visualizadores highlight.js (paleta *github-dark*) e o editor Markdown
  EasyMDE/CodeMirror do formulário de problema.
- O editor de código Ace (`problem-code-editor.js`) não segue CSS: ele lê
  `data-bs-theme` e alterna entre os temas `ace/theme/github` e
  `ace/theme/github_dark` (ambos baixados por `fetch_assets.py`), reagindo ao
  toggle via `MutationObserver`.
- Os gráficos ECharts do Arena também definem cor em JS. O helper compartilhado
  `shared/static/js/noca-echarts-theme.js` registra os temas `noca-light`/
  `noca-dark` (eixos, legenda, tooltip, dataZoom, paleta) e expõe
  `NocaECharts.create(el)` — um wrapper que inicializa o gráfico com o tema ativo,
  re-renderiza no toggle e cuida do resize. Todo gráfico novo deve usar esse
  wrapper (carregue o helper logo após `echarts.min.js`, antes do script do
  gráfico); cores por série específicas leem `NocaECharts.tokens()`.
- O botão de alternância vive no rodapé de cada módulo (`#theme-toggle-btn`).

### Marca (`brand_name`)

O nome público da marca vem de `NOCA_WEB_BRAND_NAME` / `NOCA_ARENA_BRAND_NAME`
(ver `docs/CONFIG.md`), injetado nos templates como o global `brand_name` e nos
e-mails do Arena por `arena/services/email_rendering.py`. Use `{{ brand_name }}`
em vez de escrever "NOCA Arena"/"NOCA Contest" literalmente.
