# fastapi-flash

Flask-style flash messages for FastAPI, with modern dependency injection,
full typing, and native Jinja2 integration.

## Installation

```bash
pip install fastapi-flash
```

## Quick Start

### 1. Configure the application

```python
# main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from fastapi_flash import FlashDep, setup_flash

templates = Jinja2Templates(directory="templates")
setup_flash(templates)  # registers get_flashed_messages in the Jinja2 environment

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="your-secret-key")
```

### 2. Use it in routes

```python
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi_flash import FlashDep, FlashCategory

@app.post("/login")
async def login(flash: FlashDep, ...):
    flash("Login completed successfully!", FlashCategory.SUCCESS)
    return RedirectResponse("/dashboard", status_code=303)

@app.post("/error")
async def with_error(flash: FlashDep):
    flash("Invalid credentials.", "danger")
    return RedirectResponse("/login", status_code=303)
```

### 3. Render it in templates

```html
<!-- base.html -->
{% for category, message in get_flashed_messages(with_categories=True) %}
<div class="alert alert-{{ category }}">
    {{ message }}
</div>
{% endfor %}
```

## API Reference

### `FlashDep`

Annotated type for dependency injection via `Depends`. Use it as a route parameter:

```python
async def my_route(flash: FlashDep): ...
```

### `FlashService`

Class that encapsulates flash operations. Obtained via `FlashDep`.

| Method | Description |
|---|---|
| `flash(message, category="info")` | Adds a flash message |
| `__call__(message, category="info")` | Shortcut: `flash("msg", "cat")` |
| `get_flashed_messages(with_categories, category_filter)` | Reads and removes messages |

### `setup_flash(templates)`

Registers the context processor on `Jinja2Templates`. Call it once at startup.

### `FlashCategory`

`StrEnum` with constants for the standard Bootstrap 5 categories:
`SUCCESS`, `DANGER`, `WARNING`, `INFO`, `PRIMARY`, `SECONDARY`.

### `get_flashed_messages()` in templates

```jinja2
{# Messages only #}
{% for msg in get_flashed_messages() %}
    {{ msg }}
{% endfor %}

{# With categories #}
{% for category, msg in get_flashed_messages(with_categories=True) %}
    <div class="alert alert-{{ category }}">{{ msg }}</div>
{% endfor %}

{# Filtered by category #}
{% for msg in get_flashed_messages(category_filter=["danger", "warning"]) %}
    <div class="alert alert-danger">{{ msg }}</div>
{% endfor %}
```

## Internal Behavior

The package uses Starlette's `SessionMiddleware` to persist messages between the
POST and the subsequent GET request (the PRG, Post/Redirect/Get, pattern).
Messages are stored as a list of JSON-serializable dictionaries in
`request.session["_flash_messages"]` and consumed with a pop on read, ensuring
that each message is displayed exactly once.

```
POST /form  →  flash("Saved!", "success")  →  session: [{msg, cat}]
     ↓
  303 Redirect
     ↓
GET /result  →  get_flashed_messages()  →  render + clear session
```
