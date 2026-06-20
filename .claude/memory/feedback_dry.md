---
name: feedback_dry
description: Always check existing static JS files before writing inline scripts or new files
type: feedback
---

Before writing any inline `<script>` or creating a new JS file, search `web/static/js/` for existing implementations first.

**Why:** User expects DRY to be applied proactively — "DRY is basic". Writing inline scripts when a shared file already exists (or when the logic will clearly be reused) is unacceptable.

**How to apply:** When adding JS behavior to a template, grep static/js for related keywords before writing anything new. If the logic is shared across pages, extract to a named file immediately rather than duplicating.