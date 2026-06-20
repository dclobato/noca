---
name: feedback_form_validation
description: Form validation must collect all errors at once and return them as a list, never fail-fast
type: feedback
---

Always collect all validation errors before returning, never return on the first error.

**Why:** Fail-fast validation forces the user to submit multiple times to discover all problems — poor UX.

**How to apply:** For every POST form handler:
1. Declare `errors: list[str] = []` at the top of validation.
2. Use `errors.append(...)` for every check that can run independently.
3. Gate dependent checks (cross-field constraints, DB uniqueness) behind `if errors: return _render(errors)` so they only run when upstream data is valid.
4. Pass `errors` (list) to the template, never a single `error` string.
5. In the template, render a single error as plain text and multiple errors as a `<ul>`.

See `web/routes/uberadmin_dashboard.py::add_contest_submit` for the reference implementation.
