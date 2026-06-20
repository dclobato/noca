---
name: Always update ROUTES.md and URL_FOR_REFERENCE.md when routes change
description: Both docs/ROUTES.md and docs/URL_FOR_REFERENCE.md must be updated every time a route is added, edited, or removed — never skip URL_FOR_REFERENCE.md
type: feedback
---

Every time a route is added, edited, or removed, both `docs/ROUTES.md` and `docs/URL_FOR_REFERENCE.md` must be updated in the same commit.

**Why:** Both files are canonical route references. `URL_FOR_REFERENCE.md` is the quick-lookup table used when writing templates and debugging `url_for` calls — it must stay in sync with the code or templates break silently.

**How to apply:** After implementing any route change, immediately update both files — add new routes to the appropriate section (or create a new section for new files), update names/paths for changed routes, and remove entries for deleted routes. `URL_FOR_REFERENCE.md` requires: hardcoded path, endpoint name, path params, and source file. Do not wait for the user to ask.