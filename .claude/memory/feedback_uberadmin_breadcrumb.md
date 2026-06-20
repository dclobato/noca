---
name: Uberadmin breadcrumb — no /c/{slug}/admin link
description: Uberadmin must never have /c/{slug}/admin in breadcrumbs or navigation
type: feedback
---

Uberadmin does not have access to `/c/{slug}/admin`, so that URL must never appear as a breadcrumb link when `current_user.role == "ua"`.

**Why:** Uberadmins reach contest admin sub-pages directly from the uberadmin dashboard, not through the contest admin hub. The `/c/{slug}/admin` route is for contest admins only.

**How to apply:**
- Uberadmin never sees `/c/{slug}/admin` — not in breadcrumbs, not in back buttons, not in navigation links. They access only the internal sub-pages directly (metadata, users, import/export).
- Breadcrumbs and back buttons must be role-aware in every contest admin sub-page template:
  - ua: `Uberadmin Dashboard` (link) › `{slug}` (text) › `{current page}` (active); back button → `/uberadmin`
  - admin: `Contest Dashboard` (link) › `Administration` (link to `/c/{slug}/admin`) › `{current page}` (active); back button → `/c/{slug}/admin`