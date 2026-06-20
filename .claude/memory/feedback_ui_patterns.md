---
name: always follow docs/PADROES_UI.md for templates
description: All template/view work must follow the UI patterns documented in docs/PADROES_UI.md
type: feedback
---

Always read and follow `docs/PADROES_UI.md` when creating or editing templates/views.

**Why:** The project has established UI boilerplate patterns for autocomplete with pending state, row highlight after CRUD (hash fragment + highlight-row.js), and image upload with Cropper.js. These must be applied consistently across all screens.

**How to apply:** Before writing any new template or modifying an existing one, check `docs/PADROES_UI.md` for applicable patterns. Key patterns to look for:
- List pages with CRUD → use `id="{{ record.id }}"` on rows/items + include `highlight-row.js` + redirect with `#id` anchor after create/update
- Multi-item forms → consider autocomplete with pending state pattern
- Photo upload → use the Cropper.js boilerplate with correct IDs and script loading order (no `defer`)