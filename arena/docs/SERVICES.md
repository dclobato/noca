# Arena Service Reference

All arena services live in `arena/services/` and follow the same conventions:

- **No Flask dependencies** — pure Python with async SQLAlchemy.
- **Caller owns the transaction** — services call `session.flush()` to persist changes
  within the current unit of work, but never call `session.commit()`.
- **Dependencies are injected** — `AsyncSession`, `JWTService`, and `EmailService` are
  passed as function parameters, not pulled from global state.
- **No implicit HTTP context** — services do not read from `request`; routes pass
  IP address, user-agent, and URL base explicitly.
- `arena/main.py` initializes shared infrastructure used by Arena routes, including
  `app.state.jwt_service`, `app.state.email_service`, `app.state.image_service`,
  and `app.state.geo_service`.

---

## Service files

### `startup_seeds.py`

Owns idempotent startup upserts that guarantee well-known rows exist in the database
before the arena application starts serving requests.

| Symbol | Description |
|--------|-------------|
| `ensure_sem_afiliacao(session_factory)` | Upserts the `"Sem afiliação"` affiliation with `exclude_from_ranking=True`. Called by `arena/main.py` immediately after the database pool is opened. Safe to call on every restart. |

**Note:** Unlike most arena services, `ensure_sem_afiliacao` owns its own transaction and
calls `session.commit()` internally, because it runs outside any request context during
the lifespan startup phase.

---

### `token_service.py`

Defines `ArenaTokenAction(StrEnum)` — the single source of truth for JWT action
claims used across all Arena flows — and re-exports the full `jwtservice` public API
(`JWTService`, `TokenConfig`, `TokenVerificationResult`, etc.).

Import from here rather than from `jwtservice` directly.
`ARENA_JWT_ISSUER` is fixed to `"noca-arena"` and is used by `arena/main.py`
when constructing the module JWT service.

**`ArenaTokenAction` values:**

| Value | Use |
|-------|-----|
| `LOGIN` | Full authenticated session |
| `VALIDATE_EMAIL` | Email confirmation link (24 h) |
| `PARENTAL_CONSENT` | Parent/legal guardian consent link (24 h) |
| `RESET_PASSWORD` | Password reset link (1 h) |
| `PENDING_2FA` | Gate token while awaiting TOTP/backup code (90 s) |
| `ACTIVATING_2FA` | Gate token for the 2FA activation flow (90 s) |
| `PENDING_PASSWORD_CHANGE` | Gate token for forced password change (5 min) |

---

### `qrcode_service.py`

Framework-agnostic QR Code generation with a PIL-based implementation.

| Symbol | Description |
|--------|-------------|
| `QRCodeConfig` | Dataclass configuring box size, border, and colours |
| `QRCodeError` | Exception raised on generation failure |
| `QRCodeGenerator` | ABC for custom generator implementations |
| `QRCodePILGenerator` | Concrete implementation using `qrcode[pil]` |
| `QRCodeService` | Wrapper with `generate_qr_code()` and `generate_totp_qrcode()` |

Typical usage:
```python
svc = QRCodeService.create_default()
qr_b64 = svc.generate_totp_qrcode(secret, user=email, issuer="Arena", as_bytes=False)
```

---

### `backup2fa_service.py`

Module-level async functions for managing single-use 2FA backup codes stored in
`arena_backup_2fa`.

| Function | Description |
|----------|-------------|
| `consumir_token(usuario, token, session, keep_for_days=30)` | Verify and soft-delete a backup code |
| `contar_tokens_disponiveis(usuario, session)` | Count unused backup codes |
| `invalidar_codigos(usuario, session, keep_for_days=30)` | Soft-delete all unused codes |
| `gerar_novos_codigos(usuario, session, quantidade=10)` | Invalidate old codes and generate fresh ones |
| `remover_codigos_expirados(session)` | Hard-delete records past their scheduled removal date |

---

### `user_2fa_service.py`

Async functions for the full TOTP 2FA lifecycle.

| Function | Description |
|----------|-------------|
| `iniciar_ativacao_2fa(usuario, session, jwt_service)` | Store tentative secret and issue `ACTIVATING_2FA` token |
| `confirmar_ativacao_2fa(usuario, secret, codigo, session, ...)` | Validate first TOTP and activate 2FA |
| `desativar_2fa(usuario, session)` | Clear OTP fields and invalidate backup codes |
| `validar_codigo_2fa(usuario, codigo, session)` | Accept TOTP or backup code |
| `validar_token_ativacao_2fa(usuario, token, jwt_service)` | Validate activation session token (sync) |
| `otp_secret_formatado(value)` | Format TOTP secret in groups of 4 for display (sync) |

Result types: `TwoFASetupResult`, `TwoFAValidationResult`.

### `arena_class_email_service.py`

Best-effort email notifications for Arena class membership and registration events. All helpers
send email only after the caller has committed the database change. Delivery failures are caught
and logged; they never roll back or raise to the caller. Templates are plain-text Jinja2 files
in `arena/template/emails/` rendered with `StrictUndefined`.

| Function | Recipient | Trigger |
|----------|-----------|---------|
| `send_class_registration_request_email(*, teacher_email, teacher_name, student_name, class_name, members_url, email_service)` | Class teacher | Student requests self-registration |
| `send_class_registration_approved_email(*, student_email, student_name, class_name, class_url, email_service)` | Requesting student | Teacher/admin approves the request |
| `send_class_registration_denied_email(*, student_email, student_name, class_name, denial_reason, email_service)` | Requesting student | Teacher/admin denies the request (optional reason) |
| `send_class_membership_added_email(*, student_email, student_name, class_name, class_url, email_service)` | Added student | Teacher/admin directly adds a student |
| `send_class_membership_removed_email(*, student_email, student_name, class_name, email_service)` | Removed student | Teacher/admin removes a student (self-removal excluded) |

---

### `user_security_notification_service.py`

Single home for all Arena security event email notifications. All functions return `True` on successful delivery; callers log warnings on failure but do not block the request flow. Templates are plain-text Jinja2 files in `arena/template/emails/`.

| Function | Description |
|----------|-------------|
| `send_password_changed_email(usuario, email_service)` | User changed their own password |
| `send_2fa_enabled_email(usuario, email_service)` | User enabled 2FA on their account |
| `send_2fa_disabled_self_email(usuario, email_service)` | User disabled 2FA on their account |
| `send_backup_code_used_email(usuario, email_service, remaining)` | A backup code was consumed at login (includes remaining count) |
| `send_admin_2fa_disabled_email(usuario, email_service)` | Administrator disabled 2FA on the user's account |
| `send_admin_password_change_required_email(usuario, email_service)` | Administrator required the user to change their password |
| `send_ai_credits_topped_up_email(usuario, email_service, quantity, balance)` | Administrator added AI review credits to the user's account |

---

### `user_service.py`

Async functions for account lifecycle management.

| Function | Description |
|----------|-------------|
| `registrar_usuario(nome, email, password, session, jwt_service, email_service, url_base, ..., aceitou_termos_privacidade, dta_aceitacao_termos_privacidade)` | Register a new user; persists ToS and Privacy Policy acceptance flag and timestamp |
| `revalidar_email(user_id, session, jwt_service, email_service, url_base)` | Re-send email confirmation |
| `revalidar_consentimento_responsavel(user_id, session, jwt_service, email_service, url_base)` | Re-send parental consent |
| `atualizar_email_responsavel(user_id, email_responsavel_legal, session, jwt_service, email_service, url_base)` | Store guardian email and send consent |
| `validar_email_por_token(token, session, jwt_service)` | Confirm email via JWT |
| `validar_consentimento_responsavel_por_token(token, session, jwt_service)` | Confirm parental consent via JWT |
| `regularizar_data_nascimento(user_id, dta_nascimento, session)` | Store missing date of birth and apply age-gate defaults |
| `update_date_of_birth(usuario, date_of_birth, session)` | Store a changed date of birth; deactivate users under 13, clear consent and invalidate sessions for all minors, and preserve consent fields for adults |
| `ativar_conta(usuario, session)` | Activate account |
| `ativar_conta_se_pronta(usuario, session)` | Activate account only after email and parental-consent gates are clear |
| `confirmar_email(usuario, session)` | Mark email confirmed |
| `desativar_conta(usuario, session)` | Deactivate account |
| `invalidate_sessions(usuario, session)` | Bump `session_version` to invalidate JWTs |
| `marcar_para_trocar_senha(usuario, session)` | Set forced password-change flag |
| `aceitar_termos_privacidade(usuario, session)` | Set `aceitou_termos_privacidade=True` and record acceptance timestamp |
| `top_up_ai_credits(usuario, quantity, session, *, admin_id=None)` | Add `quantity` AI backend credits to the user's balance and log the transaction; returns `False` for non-positive quantities |
| `consume_ai_credit(usuario, session, *, submission_id=None)` | Atomically decrement one AI backend credit (`SELECT FOR UPDATE`) and log the transaction; returns `True` on success, `False` when balance ≤ 0 |
| `conta_ativa(usuario)` | Predicate: account is active and email confirmed (sync) |
| `verificar_idade_senha(usuario)` | Password age in days, or `None` (sync) |

Result type: `UserServiceResult` / `UserOperationStatus` (imported from this module in sibling services).

---

### `user_registration_service.py`

New user creation, email confirmation, and parental consent email delivery. Handles
the registration flow including JWT token generation and email dispatch for activation
and guardian consent links.

| Function | Description |
|----------|-------------|
| `registrar_usuario(nome, email, password, session, jwt_service, email_service, url_base, ..., aceitou_termos_privacidade, dta_aceitacao_termos_privacidade)` | Register a new user; persists ToS and Privacy Policy acceptance flag and timestamp; dispatches activation email. |

---

### `user_email_service.py`

Email/consent JWT token validation and revalidation flows. Handles re-sending
confirmation emails, processing JWT confirmation links, and parental consent
management.

| Function | Description |
|----------|-------------|
| `revalidar_email(user_id, session, jwt_service, email_service, url_base)` | Re-send email confirmation link for an unconfirmed account. |
| `revalidar_consentimento_responsavel(user_id, session, jwt_service, email_service, url_base)` | Re-send parental consent link. |
| `atualizar_email_responsavel(user_id, email_responsavel_legal, session, jwt_service, email_service, url_base)` | Store guardian email and dispatch consent link. |
| `validar_email_por_token(token, session, jwt_service)` | Confirm email via JWT link. |
| `validar_consentimento_responsavel_por_token(token, session, jwt_service)` | Confirm parental consent via JWT link. |

---

### `user_ai_credit_service.py`

Atomic AI backend credit top-up and consumption with full transaction logging.

| Function | Description |
|----------|-------------|
| `top_up_ai_credits(usuario, quantity, session, *, admin_id=None)` | Add `quantity` AI backend credits to the user's balance and log the transaction; returns `False` for non-positive quantities. |
| `consume_ai_credit(usuario, session, *, submission_id=None)` | Atomically decrement one AI backend credit (`SELECT FOR UPDATE`) and log the transaction; returns `True` on success, `False` when balance ≤ 0. |

---

### `arena_auth_service.py`

Async functions for authentication and intermediate flow tokens.

| Function | Description |
|----------|-------------|
| `efetuar_login(email, password, session, ip_address, user_agent, mode, geo_service)` | Verify credentials and record password-only login history |
| `efetuar_logout(token, jwt_service)` | Revoke session JWT |
| `registrar_login_concluido(usuario, session, ip_address, user_agent, mode, geo_service)` | Record completed login history after final authentication |
| `set_pending_2fa_token(usuario, jwt_service, remember_me, next_page, session_started_at)` | Issue `PENDING_2FA` token (sync) |
| `get_pending_2fa_token_data(token, session, jwt_service)` | Validate `PENDING_2FA` token, return user |
| `set_pending_password_change_token(usuario, jwt_service, remember_me, next_page, session_started_at)` | Issue `PENDING_PASSWORD_CHANGE` token (sync) |
| `get_pending_password_change_token_data(token, session, jwt_service)` | Validate token, return user |

`efetuar_login` returns `UserServiceResult` with the authenticated `ArenaUser`.
For 2FA-enabled users, login history is recorded only after successful TOTP or backup-code validation.
The optional `geo_service` parameter is a `GeolocationIP` instance from `app.state.geo_service`;
when present, the client IP is resolved to a location string stored in `ArenaLoginHistory`.
JWT issuance for the full session (LOGIN action) is performed by the route.
The route now always issues 1-hour LOGIN JWTs. When `remember_me` is enabled it also stores
`remember_me=true` plus the original `session_started_at` marker so middleware can rotate the cookie
near half-life up to the 30-day absolute cap.
Login refuses accounts with missing date of birth, under-13 age status, or
pending parental consent before issuing a session token.

---

### `session_service.py`

Helpers for Arena session cookies, safe login redirects, and remembered-login
token rotation.

| Function | Description |
|----------|-------------|
| `build_current_next_url(request)` | Builds a safe current request target from path plus query string only. |
| `safe_next_url(next_url, request)` | Accepts same-origin path targets and falls back to `arena_dashboard` for missing or unsafe values. |
| `missing_profile_fields(user)` | Returns display labels for missing affiliation, preferred programming language, country, and AI-feedback language values. |
| `post_login_redirect_url(user, next_url, request)` | Sends users with incomplete profiles to the completion notice; otherwise returns the safe next destination. |
| `build_login_redirect_response(request, next_url, status_code)` | Builds a `303` login redirect and includes `next` only when the value is safe. |
| `write_flash_message(request, message, category)` | Writes a `fastapi_flash`-compatible message directly into the Starlette session. |
| `build_login_token_extra_data(tid, remember_me, session_started_at)` | Builds `LOGIN` JWT extra data for session identity and remembered-session rotation. |
| `build_refreshed_login_token(jwt_service, validation)` | Issues a replacement `LOGIN` token for remembered sessions inside the refresh window. |

The login redirect helpers are used by protected browser pages and by the
Arena `HTTPException` handler. They keep redirect targets same-origin-only:
absolute URLs and protocol-relative URLs, such as `//evil.example`, fall back
to the dashboard. Completed password, 2FA, and forced-password-change login
flows prioritize the profile completion notice over the requested destination
when required profile information is missing.

---

### `user_timezone_service.py`

Helpers for deriving a display timezone from the signed-in Arena user's saved
profile location and formatting UTC backend datetimes for user-facing pages.

| Function | Description |
|----------|-------------|
| `timezone_name_for_user(user)` | Resolves `country_code` and `subdivision_code` to an IANA timezone name, falling back to `UTC`. |
| `to_user_timezone(value, user)` | Converts a UTC-aware or naive UTC datetime into the derived user timezone. |
| `format_user_datetime(value, user, fmt, fallback)` | Formats a datetime for templates, JSON display labels, and flash messages. |
| `format_relative_datetime(value, fallback)` | Formats a datetime as a relative phrase such as `5 minutes ago`. |
| `datetime_local_value(value, user)` | Formats a UTC datetime for a browser `datetime-local` input. |
| `parse_user_datetime_local(value, user)` | Parses a browser `datetime-local` value in the user's timezone and returns UTC. |

Timezone resolution uses `pytz`. Subdivision mappings cover common multi-zone
countries first, including Brazil, the United States, Canada, Australia, and
Portugal. If no subdivision mapping exists, Arena uses a curated country default
and then `pytz.country_timezones(country_code)[0]`. Users without a saved
location see UTC.

---

### `arena_password_service.py`

Async functions for password reset and basic profile updates.

| Function | Description |
|----------|-------------|
| `solicitar_reset_senha(email, session, jwt_service, email_service, url_base)` | Send reset link |
| `redefinir_senha_por_token(token, nova_senha, session, jwt_service)` | Reset password via JWT |
| `atualizar_perfil(usuario, session, novo_nome, nova_dta_nascimento)` | Update name / date of birth |

Photo and email changes are handled by dedicated service calls (not yet implemented).

---

### `admin_ai_credits_service.py`

Admin-only service for querying AI credit consumption transactions across all Arena users.

**`get_batch_turnaround_seconds(session, submission_ids) → dict[str, int]`**

Bulk-loads platform-key batch timing for the supplied submissions. Each value is
the non-negative whole number of seconds from batch staging to AI review storage.
The result omits personal-key reviews and submissions without complete timing
data.

**`list_consumption_transactions_paginated(session, *, page, per_page, search, sort_dir, date_from_utc, date_to_utc) → Pagination[ArenaAiCreditTransaction]`**

Two-query paginated list (count + data). Filters to `transaction_type = 'consumption'`. Optional `search` matches `ArenaUser.nome` or `ArenaUser.email_normalizado` via `ilike` subquery. `sort_dir='asc'` orders oldest-first; any other value orders newest-first. Secondary `id` ordering ensures stable pagination when timestamps collide. Uses `clamp_page()` to prevent out-of-range pages. Eager-loads `user` and `submission.ai_review` chains to avoid N+1. `date_from_utc` is an inclusive lower bound; `date_to_utc` is an exclusive upper bound (callers pass start-of-next-day UTC so the filter is inclusive for the end date in the admin's timezone).

---

### `ai_turnaround_stats_service.py`

Arena presentation service for the recent platform-key AI review turnaround
statistics published in Valkey.

**`get_batch_turnaround_stats(valkey_runtime) → AIBatchTurnaroundStats | None`**

Reads `ai:batch:turnaround:stats` and validates the JSON against the shared
versioned schema. Returns `None` when the key is missing, Valkey is unavailable,
or the payload is invalid, so Arena pages can render an explicit unavailable
state.

---

### `admin_login_history_service.py`

Admin-only service for browsing the successful login records stored for an
Arena user.

**`list_login_history_paginated(session, *, user_id, page, per_page, sort_dir, date_from_utc, date_to_utc) → Pagination[ArenaLoginHistory]`**

Uses count and data queries restricted to one `arena_user_id`. It supports
oldest-first or newest-first ordering with `id` as a stable tie-breaker,
inclusive lower and exclusive upper UTC date bounds, and page clamping. The
admin profile route converts inclusive local dates from the viewing admin's
timezone before calling the service.

**`list_global_login_history_paginated(session, *, page, per_page, sort_dir, date_from_utc, date_to_utc, search) → Pagination[ArenaLoginHistory]`**

Cross-user paginated login history for the global admin dashboard page. Uses an
explicit SQL JOIN on `arena_users` so the optional `search` condition can filter
by `nome`, `email_normalizado`, or `location` in the same WHERE clause.
Two-step loading: a Core ID query (with JOIN for filtering) followed by an ORM
`selectinload(ArenaLoginHistory.arena_user)` query so relationship access works
in templates.

---

### `admin_submission_service.py`

Admin-only service for listing all Arena submissions across all users on the
admin dashboard.

**`AdminSubmissionListRow`** — frozen dataclass with fields: `submission_id`, `user_id`, `user_name`, `problem_number`, `problem_title`, `language_id`, `language_name`, `submitted_at`, `verdict` (None when pending), `status`, `submit_to_ai`, `has_ai_review`.

**`list_submissions_paginated(session, *, page, per_page, search, verdict_filter, ai_filter, language_filter, problem_filter, sort_dir="desc", date_from_utc=None, date_to_utc=None) → Pagination[AdminSubmissionListRow]`**

Delegates to `build_arena_submission_query(include_user=True, ...)`. The `ai_filter` parameter filters the `submit_to_ai` boolean flag on the submission itself (not completed review presence). `problem_filter` is an exact match on `cast(arena_number, String)` — "10" matches only problem 10. `sort_dir` (`"asc"`/`"desc"`) orders by `created_at` (with a stable secondary key on `id`); `date_from_utc`/`date_to_utc` bound `created_at` (inclusive lower, exclusive upper). User columns appear at indices 14 (nome) and 15 (user id).

---

### `admin_user_service.py`

Admin-only service containing the paginated user list query, role display labels, and all user mutation helpers for the admin panel.

**`ARENA_ROLE_DISPLAY`** — `dict[ArenaRole, str]` mapping each role to its friendly label:

| ArenaRole | Label |
|---|---|
| `ARENA_ADMIN` | `"Arena Admin"` |
| `ARENA_JUDGE` | `"Judge"` |
| `ARENA_USER` | `"Regular User"` |

Registered as the `arena_role_labels` Jinja2 global in `arena/main.py`.

**`list_users_paginated(session, *, page, per_page, search, role_filter) → Pagination[ArenaUser]`**

Two-query paginated list (count + data). Uses `selectinload(ArenaUser.affiliation)` to avoid N+1. Searches name, email, and legal-guardian email with `ilike`. Orders by `nome ASC`.

**`count_admins(session) → int`**

Count of all Arena users with `role == ARENA_ADMIN` (active or not). Used by the last-admin guard in route helpers.

**Mutation functions** — each accepts `(usuario: ArenaUser, session: AsyncSession)` and calls `session.flush()`:

| Function | Effect |
|---|---|
| `change_role(usuario, new_role, session)` | Sets `usuario.role = new_role` |
| `toggle_active(usuario, session)` | Deactivate: `desativar_conta + invalidate_sessions`; activate: `ativar_conta` |
| `toggle_force_password_change(usuario, session)` | Set: `marcar_para_trocar_senha`; clear: clears `precisa_trocar_senha` + `dta_marcacao_troca_senha` |
| `toggle_can_edit(usuario, session)` | Flips `usuario.can_edit`, the admin-granted permission to add/edit Arena problems |
| `toggle_ranking_visible(usuario, session)` | Flips `usuario.ranking_visible`; when False, the user is hidden from all public ranking lists and excluded from affiliation rating computation |
| `admin_remove_photo(usuario, session)` | Calls `usuario.clear_foto_fields()` |
| `admin_disable_2fa(usuario, session)` | Calls `desativar_2fa + invalidate_sessions` |
| `admin_change_name(usuario, new_name, session)` | Sets `usuario.nome`; raises `ValueError` if empty |
| `admin_remove_location(usuario, session)` | Sets `country_code = None`, `subdivision_code = None` |
| `admin_remove_affiliation(usuario, session)` | Sets `affiliation_id = None` |
| `admin_reset_api_key(usuario, session)` | Sets `ai_api_key = None` (clears encrypted personal AI API key) |
| `admin_toggle_email_confirmed(usuario, session)` | Confirm: delegates to `user_service.confirmar_email`; unconfirm: clears `email_confirmado` + `dta_validacao_email` |
| `admin_toggle_parental_consent(usuario, session)` | Grant: sets `consentimento_responsavel=True` + timestamp; revoke: clears both |
| `get_credit_transactions_paginated(session, user_id, *, params) → Pagination[ArenaAiCreditTransaction]` | Paginated reverse-chronological credit statement for a user; eager-loads `submission` and `admin` relationships |

---

### `admin_category_service.py`

Admin-only service for Arena category CRUD.

**`list_categories_paginated(session, *, page, per_page) → Pagination[CategoryListItem]`**

Two-query paginated list (count + data). Orders categories by name and includes a `problem_count` computed from `arena_problem_category_map`.

**`validate_category_data(session, *, name, slug, color, exclude_id=None) → CategoryFormData`**

Normalizes and validates submitted category data. Name and slug are required and limited to 128 characters. Name uniqueness is case-insensitive. Slugs are normalized to lowercase ASCII hyphen form and must be unique. Colors must match `#RRGGBB` and are stored lowercase.

**CRUD helpers** — each accepts an `AsyncSession` and calls `session.flush()` for mutations:

| Function | Effect |
|---|---|
| `get_category(session, category_id)` | Fetch category by ID |
| `get_problem_count(session, category_id)` | Count linked problems |
| `create_category(session, *, name, slug, color)` | Validate, create, and flush a category |
| `update_category(session, category, *, name, slug, color)` | Validate, update, and flush a category |
| `delete_category(session, category)` | Delete the category; map rows are removed by FK cascade |

---

### `admin_affiliation_service.py`

Admin-only service for Arena affiliation CRUD.

**`validate_affiliation_data(session, *, name, url, country_code, subdivision_code, exclude_id=None) → AffiliationFormData`**

Normalizes and validates submitted affiliation data. Name is required (max 200 chars). URL is optional and must start with `http://` or `https://` (max 500 chars). Country and subdivision codes are validated via `profile_location_service`. Name uniqueness is case-insensitive. Returns a frozen `AffiliationFormData` dataclass.

**`list_affiliations_paginated(session, *, page, per_page, search, country_code, subdivision_code) → Pagination[ArenaAffiliation]`**

Two-query paginated list (count + data). Supports case-insensitive name search, optional country/subdivision filters, ordered by `LOWER(name) ASC`.

**`effective_per_page(value) → int`**

Returns an allowed page size from `{10, 25, 50, 100}`, falling back to 25.

**CRUD helpers** — each accepts an `AsyncSession` and calls `session.flush()` for mutations:

| Function | Effect |
|---|---|
| `get_affiliation(session, affiliation_id)` | Fetch affiliation by ID |
| `create_affiliation(session, *, name, url, country_code, subdivision_code)` | Validate, create, and flush an affiliation |
| `update_affiliation(session, affiliation, *, name, url, country_code, subdivision_code)` | Validate, update, and flush an affiliation |
| `set_logo(session, affiliation, *, logo_base64, logo_mime)` | Apply a new logo to an existing affiliation and flush |
| `clear_logo(session, affiliation)` | Remove the logo from an existing affiliation and flush |
| `delete_affiliation(session, affiliation)` | Bulk-null `ArenaUser.affiliation_id`, re-fetch with `selectinload`, then delete and flush |

---

### `admin_problem_service.py`

Admin/judge service for Arena problem management. When `is_admin=False`, lookups and list queries are
scoped to `caller_id`; judges may only see and mutate their own problems.

**Key behavior**

- validates titles, sources, authorship, licenses, limits, and Markdown statements before persistence
- creates new problems with `enabled=False`
- updates category links through direct SQL on `arena_problem_category_map`
- supports sorting by title, public number, and rating
- supports AND-semantics category filtering for the admin problem list

**Public API:**

| Symbol / Function | Description |
|---|---|
| `ProblemListItem` | Dataclass containing one `ArenaProblem` plus public/private test-case counts, rating, and loaded categories for list rendering. |
| `list_problems_paginated(session, *, page, per_page, search, category_ids, category_slugs, owner_id, sort_by, caller_id, is_admin)` | Paginated problem list with search over public number/title/statement/source, optional admin-only owner filter, AND category filter by ID or slug, and selectable sorting. |
| `get_problem(session, problem_id, *, caller_id, is_admin)` | Fetch one problem with categories and test cases, applying owner scoping for non-admin editors. |
| `create_problem(session, *, caller_id, author, author_is_owner, license, ...)` | Validate and create a disabled problem owned by `caller_id`. Stores either a free-text author of at most 80 characters or owner-backed authorship, an optional license of at most 256 characters, and category links. |
| `update_problem(session, problem, *, author, author_is_owner, license, ...)` | Validate and update mutable fields without transferring ownership. Owner-backed authorship clears the free-text author; a blank license becomes `None`. |
| `toggle_enabled(session, problem)` | Flip the problem `enabled` flag and refresh `updated_at`. |
| `delete_problem(session, problem)` | Delete a problem and all its dependent data. Deletes submissions first (cascading to judgments, test results, AI reviews, batch jobs) then the problem itself (cascading to test cases, category map, ratings, solvers, tried, favourites, rating history). Returns the `arena_number` for flash messages. Caller commits. |
| `build_rejudge_jobs(session, problem_id)` | Create new `QUEUED` `ArenaSubmissionJudgment` rows for every existing submission for the problem and return the corresponding list of `ArenaSubmissionJob` objects ready to enqueue. Caller commits then enqueues. |
| `list_owners(session)` | Return administrators and users with `can_edit=True`, ordered by display name, for the owner filter. |
| `search_categories(session, *, query, limit=15)` | Case-insensitive category search; consumed by both the JSON autocomplete API (`GET /admin/problems/categories/search`) and server-side `selected_cats_data` pre-population. |

---

### `admin_problem_tc_service.py`

Admin/judge service for Arena test-case management. Test-case content lives on the shared filesystem
under `<root>/arena/<problem_id>/NNN.in|out`; the database row stores only metadata and the normalized
(LF) on-disk byte sizes (`input_size_bytes` / `output_size_bytes`). All functions that touch content
take a `testcase_dir` (the Arena root, `settings.PROBLEM_TESTCASE_DIR`).

**Key behavior**

- ordinals are 1-based and contiguous per problem; deletes and moves renumber both rows and files in lockstep
- inline create/edit is gated: a normalized side larger than `MAX_INLINE_TESTCASE_BYTES` (10 KB) raises `ValueError`; large cases use the offline single-case ZIP download/replace path (no cap)
- ZIP replace deletes all existing rows and files and rebuilds the set from parsed archive pairs (no cap)

**Public API:**

| Function | Description |
|---|---|
| `list_testcases(session, problem_id)` | Return all test cases for one problem ordered by `ordinal`. |
| `list_testcase_views(session, problem_id, testcase_dir)` | Lightweight per-case views (`id`, `ordinal`, `is_sample`, `has_explanation`, `input_preview`, `output_preview`, `input_size_bytes`, `output_size_bytes`, `is_large`) — previews read from disk, no full-content load. |
| `get_testcase(session, tc_id, *, problem_id)` | Fetch one test case scoped to its parent problem. |
| `create_testcase(session, problem, *, input_content, output_content, is_sample, explanation=None, testcase_dir)` | Append a new test case (next ordinal); write files + sizes. Raises `ValueError` if either normalized side exceeds `MAX_INLINE_TESTCASE_BYTES`. |
| `update_testcase(session, tc, *, input_content, output_content, is_sample, explanation=None, testcase_dir)` | Overwrite files + sizes, sample flag, explanation. Same inline size gate. |
| `replace_single_testcase(session, tc, *, input_bytes, output_bytes, explanation, testcase_dir)` | Replace one case's content from an offline upload (no size cap). |
| `toggle_sample(session, tc)` | Flip the sample/secret flag without touching content and bump `updated_at`. |
| `delete_testcase(session, tc, *, testcase_dir)` | Delete one test case + files, then renumber remaining rows and files contiguously. |
| `move_testcase(session, tc, new_ordinal, *, testcase_dir)` | Move one test case to a clamped 1-based ordinal; reorder rows and files. |
| `replace_all_from_zip(session, problem, zip_bytes, *, default_is_sample=False, testcase_dir)` | Replace the full set from a ZIP parsed by `shared.tc_zip.parse_testcases_zip`; writes files + sizes (no cap). |

---

### `admin_problem_io_service.py`

Admin/judge service for problem ZIP import and export. The package format is kept compatible with
the web module's problem packages (shared `problem.json` keys, `statement.md`, `in/NNN.in` +
`out/NNN.out` test-case layout, optional `explanation/NNN.txt`) so packages can move between
platforms.

**Key behavior**

- export writes all problem data: `problem.json` (author as plain text, optional license, and
  categories as a list of strings), `statement.md`, every test case (with optional
  `explanation/NNN.txt`), and the image decoded from base64 to a real image file
- import sets the importing user as owner and preserves a non-empty package author as free text;
  packages without an author use owner-backed authorship
- import preserves `source` and `license` independently, marks test cases secret, validates
  optional images, and links only existing categories; unknown categories are dropped
- web-only keys (`color`, `language_limits`) are accepted and ignored on import
- delegates problem creation to `admin_problem_service.create_problem` and test-case parsing to
  `shared.tc_zip.parse_testcases_zip`

**Public API:**

| Function | Description |
|---|---|
| `build_export_zip(problem, owner_name, testcase_dir)` | Build the in-memory export ZIP, reading test-case content from `<testcase_dir>/<problem_id>/NNN.in\|out` and resolving its plain-text author from the problem's authorship mode. |
| `import_problem_from_zip(session, *, zip_bytes, caller_id, image_service, testcase_dir)` | Parse, validate, and persist a problem package; writes test-case files + sizes under `testcase_dir`; returns the committed `ArenaProblem`. |

---

## Route-to-service mapping

### `arena/routes/auth.py`

Uses the following services:

- **`user_2fa_service.validar_codigo_2fa()`** — Validate TOTP or backup code during 2FA login (via `POST /auth/2fa`)
- **`user_service.aceitar_termos_privacidade()`** — Record ToS/PP acceptance during the login gate (via `POST /auth/accept-terms`)

### `arena/routes/user_security.py`

Uses the following services:

- **`user_2fa_service`** — Full 2FA lifecycle:
  - `iniciar_ativacao_2fa()` — Initiate TOTP 2FA activation
  - `confirmar_ativacao_2fa()` — Confirm TOTP code and activate 2FA
  - `desativar_2fa()` — Disable 2FA
  - `validar_codigo_2fa()` — Validate TOTP or backup code
  - `validar_token_ativacao_2fa()` — Validate activation session token
- **`backup2fa_service`** — Backup code management:
  - `gerar_novos_codigos()` — Generate new backup codes
  - `contar_tokens_disponiveis()` — Count remaining unused backup codes
- **`qrcode_service.generate_totp_qrcode()`** — Generate TOTP QR code for authenticator apps

### `arena/routes/admin_users.py` (GET routes)

Uses the following services:

- **`admin_user_service.list_users_paginated()`** — Paginated user list for `GET /admin/users`
- **`admin_user_service.get_credit_transactions_paginated()`** — Credits tab for `GET /admin/users/{id}`
- **`admin_login_history_service.list_login_history_paginated()`** — Filtered Login History tab for `GET /admin/users/{id}`

### `arena/routes/admin_dashboard.py`

Uses `admin_worker_service` to render and update the administration worker
cards:

- **`admin_worker_service.list_worker_cards()`** — Group all seen workers into
  autojudge, rating, and AI assistant cards with online/offline status, process
  start time, latest heartbeat time, last job start time (`WorkerRow.last_job_at`,
  None when the worker has not yet processed a job), and (for autojudge/aiassistant)
  the authoritative paused flag + `paused_by` actor read from
  `arena_worker_pause_state`. `pause_enabled` reflects whether a command secret
  is configured.
- **`admin_worker_service.aggregate_worker_statuses()`** — Reduce the detailed
  cards to one status per worker class for `/status`. A class is available when
  any worker is online and unpaused, and unavailable otherwise.
- **`admin_worker_service.unknown_worker_statuses()`** — Return unknown states
  for all classes when the status page cannot retrieve worker data.
- **`admin_worker_service.remove_worker_from_dashboard()`** — Remove one
  worker's durable and live presence records until its next heartbeat.
- **`admin_worker_service.pause_worker()` / `resume_worker()`** — Implement the
  strict issue ordering: validate (reject unknown classes and `rating` as
  `rejected_bad_request`, and an empty secret as `rejected_disabled`), commit
  `bump_worker_pause_state` + an `arena_worker_command_audit` row in one
  transaction, then sign and publish the Valkey nudge and record
  `transport_status`. Rejected class values are also committed to the audit
  table. The operation is reported successful whenever the PG commit succeeded,
  even if transport failed. See `docs/SHARED_SERVICES.md` for the trust and
- **`admin_worker_service.trigger_worker()`** — Send a one-shot FLUSH_NOW or
  POLL_NOW command to an aiassistant worker (only `aiassistant` class is in
  `TRIGGER_CLASSES`; all others return `rejected_bad_request`). Does **not**
  touch `arena_worker_pause_state` or bump any generation. Commits a single
  `arena_worker_command_audit` row with `action=flush_now|poll_now`,
  `generation=NULL`, and `outcome=triggered` before publishing the signed Valkey
  nudge (generation=0 in the payload). Updates `transport_status` to `delivered`
  or `transport_failed` after publish. The operation succeeds whenever the PG
  commit succeeds, regardless of transport outcome.
  ordering model.

### `arena/routes/admin_users_actions.py` (POST routes)

Uses the following services:

- **`admin_user_service.count_admins()`** — Last-admin guard check before mutations
- **`admin_user_service.change_role()`** — Role update via `POST /admin/users/{id}/role`
- **`admin_user_service.toggle_active()`** — Block/unblock via `POST /admin/users/{id}/toggle-active`
- **`admin_user_service.toggle_force_password_change()`** — Force-reset via `POST /admin/users/{id}/force-password-change`
- **`admin_user_service.toggle_can_edit()`** — Problem-edit permission grant/revoke via `POST /admin/users/{id}/toggle-can-edit`
- **`admin_user_service.toggle_ranking_visible()`** — Public-ranking visibility toggle via `POST /admin/users/{id}/toggle-ranking-visible`
- **`admin_user_service.admin_remove_photo()`** — Photo removal via `POST /admin/users/{id}/remove-photo`
- **`admin_user_service.admin_disable_2fa()`** — 2FA disable via `POST /admin/users/{id}/disable-2fa`
- **`admin_user_service.admin_change_name()`** — Name change via `POST /admin/users/{id}/change-name`
- **`admin_user_service.admin_remove_location()`** — Location removal via `POST /admin/users/{id}/remove-location`
- **`admin_user_service.admin_remove_affiliation()`** — Affiliation removal via `POST /admin/users/{id}/remove-affiliation`
- **`admin_user_service.admin_reset_api_key()`** — Personal API key reset via `POST /admin/users/{id}/reset-api-key`
- **`admin_user_service.admin_toggle_email_confirmed()`** — Email confirmation toggle via `POST /admin/users/{id}/toggle-email-confirmed`
- **`admin_user_service.admin_toggle_parental_consent()`** — Parental consent toggle via `POST /admin/users/{id}/toggle-parental-consent`
- **`admin_user_security_service.send_password_change_required_email()`** — Notification email for `POST /admin/users/{id}/force-password-change`
- **`admin_user_security_service.send_2fa_disabled_email()`** — Notification email for `POST /admin/users/{id}/disable-2fa`
- **`user_ai_credit_service.top_up_ai_credits()`** — Credit top-up via `POST /admin/users/{id}/topup-credits`
- **`user_service.update_date_of_birth()`** — Date-of-birth + age-policy application via `POST /admin/users/{id}/date-of-birth`

### `arena/routes/admin_affiliations.py`

Uses the following services:

- **`admin_affiliation_service.list_affiliations_paginated()`** — Paginated affiliation list for `GET /admin/affiliations`
- **`admin_affiliation_service.create_affiliation()`** — Affiliation creation via `POST /admin/affiliations/new`
- **`admin_affiliation_service.update_affiliation()`** — Affiliation update via `POST /admin/affiliations/{id}/edit`
- **`admin_affiliation_service.set_logo()`** — Logo upload via `POST /admin/affiliations/{id}/logo`
- **`admin_affiliation_service.clear_logo()`** — Logo removal via `POST /admin/affiliations/{id}/logo` (when `remove_logo=1`)
- **`admin_affiliation_service.delete_affiliation()`** — Affiliation removal via `POST /admin/affiliations/{id}/delete`
- **`admin_affiliation_service.get_affiliation()`** — Shared fetch helper for edit/delete/logo routes
- **`profile_location_service.list_countries()`** — Country select options for list filters and modals
- **`profile_location_service.list_subdivisions()`** — Subdivision select options for list filters and modals

### `arena/routes/affiliations.py`

Public route returning stored affiliation logo images:

- **`session.get(ArenaAffiliation, affiliation_id)`** — Direct ORM fetch for logo data
- **`image_service.build_image_response()`** — Builds `Response` with correct `Content-Type` and cache headers

### `arena/routes/admin_categories.py`

Uses the following services:

- **`admin_category_service.list_categories_paginated()`** — Paginated category list for `GET /admin/categories`
- **`admin_category_service.create_category()`** — Category creation via `POST /admin/categories/new`
- **`admin_category_service.update_category()`** — Category update via `POST /admin/categories/{id}/edit`
- **`admin_category_service.delete_category()`** — Category removal via `POST /admin/categories/{id}/delete`
- **`admin_category_service.get_problem_count()`** — Linked-problem count for edit pages and delete modal context

---

### `arena/routes/admin_problems.py`

Uses the following services:

- **`admin_problem_service.list_problems_paginated()`** — Paginated problem list for `GET /admin/problems`
- **`admin_problem_service.list_owners()`** — Admin-only owner filter options for the problem list
- **`admin_problem_service.search_categories()`** — Builds `selected_cats_data` for JS tag-picker pre-population on edit/error re-renders
- **`admin_problem_service.get_problem()`** — Shared fetch + judge ownership enforcement helper for edit/toggle routes
- **`admin_problem_service.create_problem()`** — Problem creation via `POST /admin/problems/new`
- **`admin_problem_service.update_problem()`** — Problem update via `POST /admin/problems/{id}/edit`
- **`admin_problem_service.toggle_enabled()`** — Enable/disable action via `POST /admin/problems/{id}/toggle-enabled`
- **`admin_problem_tc_service.list_testcase_views()`** — Lightweight test-case list (previews + size badges, `is_large` flag) shown on the problem edit page

### `arena/routes/admin_problem_io.py`

Uses the following services:

- **`admin_problem_io_service.import_problem_from_zip()`** — Import a problem package via `POST /admin/problems/import`
- **`admin_problem_io_service.build_export_zip()`** — Build the export ZIP for `GET /admin/problems/{problem_id}/export`
- **`admin_problem_service.get_problem()`** — Shared fetch + judge ownership enforcement helper before export

### `arena/routes/admin_problem_tc.py`

Uses the following services:

- **`admin_problem_service.get_problem()`** — Shared fetch + judge ownership enforcement helper for all test-case routes
- **`admin_problem_tc_service.create_testcase()`** — Add one test case via `POST /admin/problems/{problem_id}/testcases/add`
- **`admin_problem_tc_service.get_testcase()`** — Scoped lookup for edit/delete routes
- **`admin_problem_tc_service.update_testcase()`** — Test-case update via `POST /admin/problems/{problem_id}/testcases/{tc_id}/edit`
- **`admin_problem_tc_service.toggle_sample()`** — Sample/secret flip via `POST /admin/problems/{problem_id}/testcases/{tc_id}/toggle-sample`
- **`admin_problem_tc_service.delete_testcase()`** — Test-case removal + renumber via `POST /admin/problems/{problem_id}/testcases/{tc_id}/delete`
- **`admin_problem_tc_service.replace_all_from_zip()`** — Bulk replacement via `POST /admin/problems/{problem_id}/testcases/zip-replace`
- **`admin_problem_tc_service.replace_single_testcase()`** — Single-case offline replace via `POST /admin/problems/{problem_id}/testcases/{tc_id}/replace`; the matching `GET .../download` builds the single-case ZIP via `shared.tc_zip.build_single_testcase_zip`

### `arena/routes/admin_problem_api.py`

Uses the following services:

- **`admin_problem_service.search_categories()`** — Category autocomplete JSON endpoint at `GET /admin/problems/categories/search`
- **`admin_problem_service.get_problem()`** — Judge/admin access control before returning problem rating-history JSON

---

## Shared infrastructure

The following shared services are available from `shared/services/`.
See [docs/SHARED_SERVICES.md](../../docs/SHARED_SERVICES.md) for the full API reference.

- `email_service.py` / `email_models.py` / `email_providers.py` / `email_validation.py` — email delivery and validation
- `password_service.py` — diceware password generation and password policy validation
- `network_utils/` — SSRF-safe outbound HTTP requests and IP validation
- `geolocation.py` — IP geolocation for login history
- `imageprocessing_service/` — user photo uploads, avatars, and image validation
- `token_revocation.py` — JWT revocation at logout
- `lock_service.py` — Valkey TTL locks
- `valkey_service/` — Valkey connection pool and queue operations

Arena uses `arena/services/valkey_service.py` as its local shim over the shared
Valkey package. The shim exposes `create_arena_valkey_runtime(...)`, which reads
Arena settings while keeping Arena independent from `web.services`.

---

### `rate_limit_service.py`

Per-user submission rate limiting via a PostgreSQL sliding-window count. Uses a transaction-scoped advisory lock (`pg_advisory_xact_lock`) to serialise concurrent attempts from the same user; the lock step is skipped on non-PostgreSQL dialects so SQLite test fixtures work without modification. The cutoff is computed in Python (`datetime.now(UTC) - timedelta(...)`) for portability and testability.

| Function | Description |
|----------|-------------|
| `acquire_submission_rate_lock(session, user_id)` | Acquires a per-user transaction advisory lock (PostgreSQL only; no-op on other dialects). Isolated in its own function so tests can monkeypatch it. |
| `check_submission_rate_limit(session, user_id, window_minutes, max_submissions)` | Acquires the lock, counts submissions in the rolling window, and returns `(True, None)` when within the limit or `(False, next_allowed_at)` when the limit is reached. `next_allowed_at` is the earliest moment the window will have a free slot. Must be called inside the same transaction as the subsequent INSERT. |

---

### `submission_service.py`

Service-only entry point for Arena source submissions. Called from the Arena HTTP layer (`POST /problems/{arena_number}/submit`) to create submission rows and prepare the autojudge job.

| Function | Description |
|----------|-------------|
| `create_arena_submission(session, user_id, problem_id, language_id, source_code, problem_set_id=None, bypass_rate_limit, rate_limit_window_minutes, rate_limit_max_submissions)` | Enforces the per-user rate limit, validates the problem/language/test cases, creates `ArenaSubmission` + `ArenaSubmissionJudgment`, updates tried/progress counters, and returns an `ArenaSubmissionCreationResult`. Aggregate rating counters count `ARENA_USER` attempts only; staff submissions still create submissions, judgments, and tried rows. The rate-limit window count is always computed. When the limit is reached and `bypass_rate_limit=False` it raises `ArenaSubmissionRateLimitError`; when `bypass_rate_limit=True` (staff) the submission is still created and the result carries `rate_limit_exceeded=True` / `rate_limit_next_allowed_at` so the route can flash a warning. When `problem_set_id` is given, validates the set is currently accepting, contains the problem, and the user is an active member of its class, then ties the submission to the set (teacher-visible); otherwise the submission stays private. Raises `ArenaSubmissionServiceError` for validation failures. |

---

### `submission_list_service.py`

Paginated query service for a user's submission history.

| Function | Description |
|----------|-------------|
| `get_user_submissions(session, user_id, search, verdict_filter, params)` | Returns `Pagination[SubmissionListRow]` for the given user, ordered by `created_at DESC` (newest first). Supports optional `search` (ilike on problem title or number) and `verdict_filter` (exact `final_verdict` match). Uses a subquery to find the most-recent non-SUPERSEDED judgment per submission. |
| `build_arena_submission_query(..., id_filter=None, ...)` | Shared query builder. `id_filter` (a sequence of submission IDs) restricts results via `IN` when not `None`; an **empty** sequence matches no rows, while `None` disables the filter. Used by the per-user status snapshot endpoint to scope to a validated, owner-checked ID set. |

`SubmissionListRow` fields: `submission_id`, `problem_id`, `problem_number`, `problem_title`, `language_id`, `language_name`, `language_icon`, `submitted_at`, `verdict`, `status`, `max_wall_time_ms`, `submit_to_ai`, `has_ai_review`, `has_teacher_feedback`, `is_final`. `is_final` is `True` when `status` is one of the terminal `TERMINAL_JUDGMENT_STATUSES` (`DONE`/`FAILED`/`SUPERSEDED`); `FAILED`/`SUPERSEDED` are final without a verdict, so this — not "has a verdict" — is the authoritative "stop watching" signal for the realtime profile updates.

The service does not commit and does **not** enqueue. Callers must commit the
database transaction and then call `enqueue_arena_submission_job(valkey_runtime,
result.job)` — this ordering guarantees the worker never picks up a job whose
rows are not yet visible in the database. The autojudge adapter publishes an
`ArenaVerdictEvent` to the `arena:results` Valkey channel that powers both the
public live feed (see `live_feed_service.py`) and the per-user profile submissions
tab. The profile tab consumes that channel through a user-scoped SSE endpoint
(`arena/routes/user_submission_status.py`) which only signals a refresh when one
of the viewer's own submissions finalizes; the browser then refetches the
owner-scoped `status.json` snapshot and updates verdict/runtime cells in place
(firing confetti on a fresh `AC`). A low-frequency client fallback poll bounds
staleness because Valkey pub/sub is not durable.

---

### `arena_teacher_feedback_service.py`

Teacher feedback on Arena submissions — a sparse 1:1 record keyed by
`submission_id` (`arena_submission_teacher_feedback`). Mirrors the AI-review
shape. All helpers leave transaction ownership to the caller and never commit.

| Function | Description |
|----------|-------------|
| `upsert_teacher_feedback(session, submission_id, teacher_id, feedback_text, feedback_at=None)` | Inserts or replaces the feedback row via dialect-aware `INSERT ... ON CONFLICT (submission_id) DO UPDATE` (PostgreSQL/SQLite). Editing overwrites the text and refreshes `teacher_id`/`feedback_at`. Returns the written `feedback_at` (used by the route to build a per-update notification `source_ref`). |
| `get_teacher_feedback_text(session, submission_id)` | Returns the feedback text for a submission, or `None` when absent. |

Authorization is enforced at the route layer (`arena/routes/submissions.py`,
`_can_manage_feedback`): the set's assigned teacher or an `ARENA_ADMIN`, derived
from the submission's persisted `problem_set_id`. The POST route also creates a
`TEACHER_FEEDBACK_POSTED` Arena notification for the student.

---

### `live_feed_service.py`

Snapshot query for the public Arena live submission feed (across all users).

| Function | Description |
|----------|-------------|
| `build_arena_live_feed_snapshot(session)` | Returns `ArenaLiveFeedSnapshot` — the newest finalized submissions (`final_verdict IS NOT NULL` on the most-recent non-SUPERSEDED judgment), ordered by `created_at DESC`, plus `limit` and `has_more` metadata. It fetches one extra SQL row to detect whether older entries exist beyond `NOCA_ARENA_LIVE_FEED_LIMIT`. The "active judgment" subquery is shared via `shared.services.arena_query_helpers.active_arena_judgment_subquery`. |

`ArenaLiveFeedRow` fields: `submission_id`, `created_at`, `affiliation_id`,
`affiliation_name`, `affiliation_has_logo`, `country_code`, `country_name`,
`problem_number`, `problem_title`, `language_name`, `language_icon`, and `verdict`. The
route builds affiliation logo, country flag, and problem URLs plus verdict labels/badges;
the service stays request-agnostic.
Real-time refresh is driven by `iter_arena_verdict_events` over the `arena:results`
channel.

---

### `problem_service.py`

Lookup helpers for Arena problems. UUID `id` remains the relational identifier
used by submissions, judging, rating, and queue payloads. `arena_number` is the
public sequential reference for URLs and admin/user-facing screens.

| Function | Description |
|----------|-------------|
| `get_problem_by_arena_number(session, arena_number)` | Returns the problem with the given positive public number, or `None` when not found. |

`arena_number` is unique, starts at 1, is generated by the database sequence
`arena_problem_arena_number_seq`, and is never reused after deletion.

---

### `valkey_service.py`

Arena-local shim over `shared.services.valkey_service`.

**Public API:**

| Symbol | Description |
|--------|-------------|
| `create_arena_valkey_runtime(*, healthcheck_interval_s)` | Builds a `ValkeyRuntime` from Arena settings so Arena code does not depend on `web.services`. |
| `ValkeyRuntime` | Re-export of the shared runtime used by Arena background workers and queue producers. |
| `enqueue_arena_submission_job(...)` | Re-export used after the caller commits a newly created submission/judgment pair. |
| `dequeue_job_id(...)` | Re-export for consumers that pop queued work. |
| `enqueue_job(...)`, `enqueue_profiling_job(...)`, `publish_verdict(...)`, `remove_from_inflight(...)` | Shared queue helpers re-exported through the Arena module boundary. |
| `get_contest_queue_metrics(...)`, `get_all_contest_queue_metrics(...)` | Read queue metrics through the Arena-local shim. |
| `WorkerClass`, `WorkerPresence`, `list_all_workers(...)`, `remove_worker(...)` | Shared worker-presence API used by the administration dashboard. |
| `build_command(...)`, `publish_command(...)`, `WorkerCommandType` | Shared signed worker pause/resume command helpers used by `admin_worker_service`. |

The module also re-exports queue-key constants and low-level client helpers from
the shared Valkey package for Arena-only integrations.

---

## Middleware (`arena/middleware/`)

### `auth_middleware.py`

Pure ASGI middleware that validates the `arena_access_token` HttpOnly cookie on every HTTP
request without touching the database, and rotates remembered sessions after downstream code confirms
that a real authenticated Arena user was resolved.

**Behaviour:**
- Reads the `arena_access_token` cookie and calls `jwt_service.validar()` (signature + expiry +
  revocation check).
- Stores the result in `request.state.validated_token` (`TokenVerificationResult | None`).
- Stores `request.state.allow_token_refresh = False` and `request.state.token_cap_exceeded` so
  downstream auth dependencies can opt eligible requests into cookie rotation.
- Also stores the raw token string in `request.state.raw_arena_token` for downstream revocation.
- On the response: if a cookie was present but its token is invalid, has the wrong action claim, or
  a remembered session exceeded the 30-day absolute cap, appends a `delete_cookie` `Set-Cookie`
  header so the browser discards the stale value silently.
- For remembered sessions near half-life, appends a refreshed cookie only when the request path is
  not one of the auth routes that already own the cookie (`/auth/login`, `/auth/logout`,
  `/auth/2fa`, `/auth/change-password`) and `get_current_arena_user()` marked the request as
  refresh-eligible.

Registered in `arena/main.py` *before* `SessionMiddleware` (outermost wrap order) so the
session is available when downstream dependencies write flash messages.

`arena/main.py` also registers an `HTTPException` handler for browser-facing
auth failures. When the request explicitly accepts `text/html`, `401` writes a
warning flash, clears `arena_access_token`, and redirects to
`/auth/login?next=<current-path-and-query>`. Browser `403` writes a permission
warning and redirects to `/dashboard`. Requests that don't ask for HTML, and
all non-`401`/`403` exceptions, keep FastAPI's default JSON/error response.

---

## Dependencies (`arena/dependencies/`)

### `auth.py`

FastAPI dependency for resolving the authenticated Arena user from the session cookie.

| Symbol | Description |
|--------|-------------|
| `ForceLogoutException` | Raised when the token's `tid` claim no longer matches `user.get_token_id()`. Caught by the exception handler in `main.py`. |
| `get_current_arena_user(request, session)` | Optional dependency. Returns `ArenaUser` on a valid, consistent session; `None` when no session exists or a remembered-session absolute cap was exceeded; raises `ForceLogoutException` on identity mismatch. |

**Token identity check:** The `LOGIN` JWT stores `extra_data={"tid": user.get_token_id()}` at
login time.  On each request using this dependency the compound value
`"{id}|{last_15_chars_of_hash}|{session_version}"` is recomputed from the database and compared
to the claim.  A mismatch (password changed, admin force-logout) triggers JWT revocation in
Valkey, writes a danger flash to the session, and raises `ForceLogoutException`.

Use in any route that renders a template extending `_base.html`:
```python
from arena.dependencies.auth import get_current_arena_user

@router.get("/some-page", ...)
async def some_page(
    request: Request,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
):
    ...
    return templates.TemplateResponse(request, "some_page.html", {"current_user": current_user})
```

When the dependency resolves a real authenticated user, it marks the request as eligible for
remembered-session cookie rotation. Invalid, expired, capped, or force-logout sessions never opt in.

---

---

### `leaderboard_service.py`

Reusable read queries for Arena leaderboard surfaces.

**Public API:**

| Symbol | Description |
|--------|-------------|
| `TopRatedUser` | Frozen DTO with `id`, `rank`, `name`, `rating`, `confidence`, and `solved_problems` for presentation-safe user rankings. |
| `_eligible_users_where()` | Shared eligibility predicate list: `ativo=True`, `email_confirmado=True`, and `ranking_visible=True`. Used by both `build_ranked_users_cte()` and `get_top_rated_users()` so all ranking surfaces honour the visibility flag. |
| `get_top_rated_users(session, *, limit)` | Returns active, email-confirmed, ranking-visible `ARENA_USER` accounts ordered by `user_rating` descending, confidence descending, creation date ascending, and id ascending. Equal ratings share the same competition rank. Values of `limit < 1` return an empty list. |

Used by the public dashboard Top Users card; reuse for any compact top-k Arena user ranking.

---

### `pagination_service.py`

Small internal pagination helper for server-rendered Arena pages.

**Public API:**

| Symbol | Description |
|--------|-------------|
| `PaginationParams(page, per_page)` | Validated page settings with an `offset` property. |
| `Pagination(items, page, per_page, total)` | Template-friendly page object with `pages`, `first`, `last`, `has_prev`, `has_next`, `prev_num`, `next_num`, and `iter_pages()`. |
| `parse_page(value)` | Converts raw query input to an integer page number clamped to at least 1. |
| `build_pagination_params(page, *, per_page)` | Builds clamped `PaginationParams` from a raw page value. |
| `clamp_page(page, *, total, per_page)` | Clamps a requested page to the available page range for a known row count. |

Used by `/user/profile` so Solved Problems and Attempted Problems tabs can paginate independently through `solved_page` and `attempted_page`.

---

### `profile_location_service.py`

Validates and formats Arena profile country, subdivision, and affiliation data.

**Public API:**

| Symbol | Description |
|--------|-------------|
| `country_name(country_code)` | Returns the country display name for an ISO 3166-1 alpha-2 code. |
| `list_countries()` | Returns ISO 3166-1 alpha-2 countries sorted by display name for the profile dropdown. |
| `list_subdivisions(country_code)` | Returns ISO 3166-2 subdivisions for one country, sorted by display name. |
| `subdivision_name(subdivision_code)` | Returns the subdivision display name for an ISO 3166-2 code. |
| `update_user_location(user, country_code, subdivision_code)` | Validates optional country/subdivision input and mutates the user profile fields. |
| `map_reverse_geocode_response(data)` | Maps a Nominatim-compatible JSON response to country/subdivision display data. |
| `reverse_geocode_location(...)` | Calls the configured reverse-geocoder through `NetworkService` and returns mapped ISO values. |
| `search_affiliations(session, query, limit)` | Case-insensitive partial affiliation name search ordered by lowercase name. |
| `update_user_affiliation(session, user, affiliation_id)` | Sets or clears the user's selected affiliation. |

Used by the profile Personal Data tab and JSON endpoints in `user_profile_api.py`.

---

### Profile language preferences (`user_profile_api.py`)

The `POST /user/profile/personal-data` endpoint persists two distinct language
preferences. `preferred_language_id` is the optional programming language used
by submission forms. `prefered_language` is the user's locale preference and is
limited to `en-US` and `pt-BR`; the AI assistant uses it to request response
text in the user's preferred language.

---

### API Key management (`user_profile_api.py`)

The `POST /user/profile/api-key` endpoint (`arena_user_profile_api_key_update`) is handled
directly in `user_profile_api.py` without a separate service, since it is a single field
assignment on `ArenaUser`.

| Operation | Description |
|-----------|-------------|
| Set / replace | Strips the submitted value and assigns it to `current_user.ai_api_key`. The `EncryptedString` column type encrypts the value before persisting. |
| Clear | An empty or absent `api_key` value sets `current_user.ai_api_key = None`, removing any stored key. |

Returns `{"ok": true, "cleared": <bool>}`. The plaintext key is never included in any response.

---

### `user_progress_service.py`

Queries the current user's profile rating and problem-list tabs.

**Public API:**

| Symbol | Description |
|--------|-------------|
| `get_user_progress(*, session, user, solved_params, attempted_params)` | Returns `UserProgress` with summary fields from `ArenaUser`, solved rows ordered by `solved_at DESC`, and attempted-but-unsolved rows ordered by `last_tried_at DESC`. Each row includes the public problem number and category chips for profile links. |
| `get_solved_progress(*, session, user, params)` | Returns `Pagination[ProgressProblemRow]` for solved problems only. |
| `get_attempted_progress(*, session, user, params)` | Returns `Pagination[ProgressProblemRow]` for attempted-but-unsolved problems only. |

Solved rows join `arena_problem_solvers`, `arena_problems`, and `arena_problem_ratings`. Attempted rows use `arena_problem_tried` and exclude problems already present in `arena_problem_solvers`. The current page's problem IDs load their categories in a separate ordered query.

---

### Rating (moved out of Arena)

The rating logic no longer lives in the Arena module. It was split to keep a single
set of recomputation cycles regardless of how many Arena replicas run:

- **Pure logic** (constants, `rate_*`, `format_next_rating_update`,
  `format_rating_interval`, `NEXT_RATING_UPDATE_KEY`,
  `RATING_INTERVAL_TEXT_KEY`, `RATING_AFFILIATION_FACTOR_KEY`) lives in `shared/services/arena_rating.py` — see
  `docs/SHARED_SERVICES.md`. Arena's `/help/rating` page and footer import from there.
- **Background loops** (`run_problem_rating_loop`, `run_user_rating_loop`,
  `run_affiliation_rating_loop`) live in the standalone `rating/` worker module
  (`rating.loops`, driven by `rating.worker:main` / console script `noca-rating`).
  Run exactly one replica.
- **Footer countdown**: the worker publishes the next cycle timestamp to the Valkey
  key `arena:rating:next_update`; each Arena instance polls it into
  `app.state.next_rating_update` (`_next_rating_update_poller` in `arena/main.py`).
- **Help-page metadata**: the worker publishes the formatted active interval to
  `arena:rating:interval_text` and the affiliation decay factor to
  `arena:rating:affiliation_factor`; each Arena instance polls them into
  `app.state.rating_interval_text` and `app.state.affiliation_rating_factor`.

The historical algorithm reference below is retained for convenience but now describes
`shared/services/arena_rating.py`.

**Algorithm constants** (tunable at the module level):

| Constant | Default | Effect |
|----------|---------|--------|
| `ALPHA` | `10.0` | Prior weight for solve-rate; higher = new problems stay near 50 % solve-rate longer |
| `BETA` | `10.0` | Prior weight for avg-tries; higher = new problems stay near 2 tries longer |
| `PRIOR_SOLVE_RATE` | `0.50` | Bayesian prior for solve-rate (neutral value = 50 %) |
| `PRIOR_TRIES` | `2.0` | Bayesian prior for average tries |
| `MAX_RELEVANT_TRIES` | `10.0` | Caps the tries component at this value |
| `W_SOLVE_RATE` | `0.80` | Weight of solve-rate component in difficulty |
| `W_TRIES` | `0.20` | Weight of avg-tries component |
| `CONTRAST_GAIN_MAX` | `4.0` | Maximum logit-space gain of the bimodal contrast for well-attempted problems |
| `CONTRAST_GAIN_SCALE` | `25.0` | Attempts at which the contrast gain reaches ~63 % of its span |
| `PIVOT_MIN_ATTEMPTS` | `10` | Minimum attempts for a problem to inform the population median pivot |
| `BASE_POINTS` | `10.0` | Points for a difficulty-1 problem |
| `GROWTH` | `1.45` | Exponential growth per difficulty unit (difficulty-10 ≈ 283 pts) |

**Difficulty pipeline:** `_raw_difficulty(...)` produces a raw weighted estimate in `[0, 1]`
(Bayesian solve-rate + avg-tries). `_apply_contrast(raw, pivot, attempted)`
then reshapes it with a logistic gain in logit space, recentred on `pivot`, with the gain
gated by attempt count (`_contrast_gain`) so only well-attempted problems are pushed toward the
`[1, 100]` extremes. The pivot is the population median raw of problems with at least
`PIVOT_MIN_ATTEMPTS` attempts (falling back to `_NEUTRAL_PIVOT`, the raw of a perfectly average
problem), which keeps the easy/hard split balanced and maps unknown problems to the scale centre.

**Public API:**

| Symbol | Description |
|--------|-------------|
| `rate_problem(*, session, problem_id, pivot=None)` | Ensure an `arena_problem_ratings` row exists, compute the raw difficulty, apply the bimodal contrast (using `pivot`, default `_NEUTRAL_PIVOT`), UPDATE `rating` + `dta_rating_update`. Does not commit. |
| `rate_all_problems(session)` | Two passes over all `arena_problems` `id`s: compute each raw difficulty + derive the population median pivot, then apply the gated contrast and persist. Returns count. Does not commit. |
| `rate_user(*, session, user_id)` | JOIN `arena_problem_solvers` + `arena_problem_ratings`, sum exponential points, UPDATE `user_rating`, `solved_problems`, `dta_rating_update`. Score 0 for unsolved users. Does not commit. |
| `rate_all_users(session)` | SELECT all `id`s from `arena_users`, call `rate_user` for each. Returns count. Does not commit. |
| `rate_affiliation(*, session, affiliation_id, f)` | SELECT non-null `user_rating` values for members of the affiliation where `ranking_visible=True`, apply geometric weighting formula, UPDATE `arena_affiliations.rating` + `dta_rating_update`. Users who have opted out of the ranking are excluded from this computation. Does not commit. |
| `rate_all_affiliations(session, f)` | SELECT all `id`s from `arena_affiliations`, call `rate_affiliation` for each. Returns count. Does not commit. |
| `format_next_rating_update(next_update)` | Formats the next scheduled rating recomputation as a relative duration for the Arena footer. Returns `None` when no active deadline is available. |
| `format_rating_interval(seconds)` | Formats a rating interval in seconds. Used by the rating worker before publishing display metadata. |
| `run_problem_rating_loop(session_factory, interval_seconds, stop_event, logger, problem_done, *, run_immediately, next_update_callback=None)` | Background loop. When `run_immediately=True`, skips the initial wait. Sets `problem_done` **only on success** and publishes the next problem-rating deadline through `next_update_callback`. |
| `run_user_rating_loop(session_factory, interval_seconds, stop_event, logger, problem_done, user_done)` | Background loop. Waits on `problem_done`; on success clears `problem_done` and sets `user_done` to unblock the affiliation loop. |
| `run_affiliation_rating_loop(session_factory, stop_event, logger, user_done, f)` | Background loop. Waits on `user_done` before each cycle; clears `user_done` after each run (success or failure). |

**Sequential coordination:**

All three loops share `asyncio.Event` objects managed by `rating/worker.py`:
- `stop_event` — shared stop signal for all loops (set on SIGTERM/SIGINT).
- `problem_done` — problem loop sets after a successful commit; user loop awaits it, runs, then clears it.
- `user_done` — user loop sets after a successful commit; affiliation loop awaits it, runs, then clears it.
- `next_rating_update` — published by the problem loop to the Valkey key `arena:rating:next_update` for the shared footer countdown.
- `rating_interval_text` — published by `rating/worker.py` to `arena:rating:interval_text` for Arena's help page.
- `affiliation_rating_factor` — published by `rating/worker.py` to `arena:rating:affiliation_factor` for Arena's help page.

The chain `problems → users → affiliations` guarantees affiliation scores always reflect the most recently computed user scores, which themselves reflect the most recently computed problem difficulties. A failed cycle at any stage never propagates downstream.

**Affiliation rating formula:**
`S = (1/f) × Σ(i=0…n-1) (1 − 1/f)^i × s_i` where s_i are member `user_rating` values sorted descending.
Factor `f` is configurable via `NOCA_RATING_AFFILIATION_FACTOR` (default 5).

**Configuration** (see `NOCA_RATING_INTERVAL`, `NOCA_RATING_COMPUTE_ON_STARTUP`, and `NOCA_RATING_AFFILIATION_FACTOR` in `docs/CONFIG.md`).

---

---

### `problem_browse_service.py`

Public-facing service for browsing enabled Arena problems. Only `enabled=True` problems are
exposed. No ownership or role check applies — this module is for the public problem list and
detail pages at `/problems` and `/problems/{arena_number}`.

**`AuthorInfo` dataclass (returned by `get_enabled_problem_by_number`):**

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str \| None` | Author's full display name from `arena_users.nome` |
| `affiliation_name` | `str \| None` | Affiliation display name, or `None` if not set |
| `affiliation_country_code` | `str \| None` | ISO 3166-1 alpha-2 code for the affiliation's country, or `None` |

**`PublicProblemListItem` dataclass:**

| Field | Type | Description |
|-------|------|-------------|
| `problem` | `ArenaProblem` | The problem ORM instance |
| `rating` | `int \| None` | Current rating (1–10), `None` if not yet computed |
| `categories` | `list[ArenaCategory]` | Categories linked to this problem |
| `author_name` | `str \| None` | Free-text author or owner fullname, according to `author_is_owner` |
| `is_favorite` | `bool` | `True` when the viewing user has favorited this problem; always `False` for guests |
| `ac_rate` | `float \| None` | Fraction from role-filtered rating stats; staff attempts/solves are excluded |
| `is_solved` | `bool` | Personal solved status for the viewing user, including staff users |
| `solved` | `int \| None` | Count of distinct `ARENA_USER` solvers for the aggregate problem list column |

**Functions:**

| Symbol | Description |
|--------|-------------|
| `list_enabled_problems_paginated(session, *, page, per_page=25, search, category_slugs, sort_by, user_id=None)` | Paginated enabled-problem list. Search uses the resolved author: free text for external authors or the owner fullname for owner-authored problems. Category filtering uses AND semantics. Solver aggregates exclude administrators and owners. |
| `get_enabled_problem_by_number(session, arena_number)` | Fetch a single enabled problem by its public `arena_number`. Returns `(ArenaProblem, AuthorInfo)` or `None` if not found or disabled. Also outer-joins `arena_affiliations` to populate `AuthorInfo.affiliation_name` and `affiliation_flag`. Eagerly loads `rating`, `categories`, and `test_cases`. |
| `get_all_categories(session)` | Return all categories alphabetically by name, for the filter dropdown. |
| `get_user_problem_status(session, *, user_id, problem_id)` | Return `(solved_at, tried_at, is_favorite)` from the solver, tried, and favorites tables. Datetime values may be `None`; `is_favorite` is `True` only when a favorites row exists. |
| `get_problem_rating_history(session, problem_id)` | Return rating history for the last 730 days as `[{"ts": ISO8601, "rating": int}, ...]`, chronological. Used by the public ECharts sparkline endpoint. |

### `problem_stats_service.py`

Read-only access to precomputed per-problem statistics. The snapshots are computed periodically
by the rating worker (`shared.services.arena_stats`) and stored in `arena_problem_statistics`;
this service performs no aggregation — it only reads the latest snapshot for the statistics page.

**Functions:**

| Symbol | Description |
|--------|-------------|
| `get_problem_statistics(session, problem_id)` | Return the latest statistics payload (verdicts, languages, per-language time/memory stats, wall-time histogram) augmented with `computed_at` (ISO-8601), or `None` when statistics have not been computed yet. |

### `arena_favorite_service.py`

Manages the `arena_problem_favorites` many-to-many table. Only enabled problems appear in
paginated results. Favorites are ordered by `arena_number ASC` (no timestamp is stored).

**`FavoriteProblemRow` dataclass:**

| Field | Type | Description |
|-------|------|-------------|
| `problem_id` | `str` | UUID of the problem |
| `arena_number` | `int` | Public sequential problem number |
| `title` | `str` | Problem title |
| `categories` | `list` | Category chip list |
| `rating` | `float` | Display-scale difficulty (0.1–10.0) |
| `activity_at` | `datetime \| None` | Always `None`; favorites carry no timestamp |

**Functions:**

| Symbol | Description |
|--------|-------------|
| `is_favorite(session, *, user_id, problem_id)` | Return `True` if the (user_id, problem_id) pair exists in `arena_problem_favorites`. |
| `get_favorites_for_problems(session, *, user_id, problem_ids)` | Bulk check — return the subset of `problem_ids` that are favorited by `user_id`. |
| `toggle_favorite(session, *, user_id, problem_id)` | Add or remove a favorite row idempotently. Returns `True` if the problem is now a favorite. Caller is responsible for committing the session. |
| `get_favorites_paginated(session, *, user_id, params)` | Return `Pagination[FavoriteProblemRow]` ordered by `arena_number ASC`, filtered to `enabled=True` problems. |

---

### `problem_tc_export_service.py`

ZIP export helpers for Arena problem test cases. Content is read from the shared filesystem under
`<root>/arena/<problem_id>/NNN.in|out`. Follows the same Layout A format used by the web module's
`build_public_export_zip()`.

**Functions:**

| Symbol | Description |
|--------|-------------|
| `build_sample_testcases_zip(problem_id, test_cases, testcase_dir)` | Build an in-memory ZIP of the given `ArenaTestCase` objects, reading content from disk. Layout A: `in/{ordinal:03d}.in` + `out/{ordinal:03d}.out` (plus optional `explanation/{ordinal:03d}.txt`), sorted by ordinal, DEFLATE compressed. Missing files are written empty. Synchronous — callers in async context must use `anyio.to_thread.run_sync`. Returns `bytes`. |

### Notifications

Arena notification storage uses the shared
`shared/services/arena_notification_service.py` helper and the
`arena_notifications` table. Routes in `arena/routes/notifications.py` expose
the current user's latest notifications to the topbar dropdown. The profile
page (`arena/routes/users.py`) exposes the full paginated list in the
Notifications tab.

**Shared service functions** (`shared/services/arena_notification_service.py`):

| Function | Description |
|----------|-------------|
| `create_arena_notification(executor, *, user_id, notification_kind, title, message, target_url, source_ref, context)` | Inserts one notification with idempotent upsert logic (no-op on duplicate `(user_id, notification_kind, source_ref)`). Returns the attempted notification id. Caller commits. |
| `count_unread_arena_notifications(executor, *, user_id)` | Returns count of unread notifications (`read_at IS NULL`) for the user. |
| `list_latest_arena_notifications(executor, *, user_id, limit)` | Returns up to 20 newest notification rows (newest first). Used by the topbar dropdown. |
| `mark_arena_notification_read(executor, *, notification_id, user_id)` | Sets `read_at` on a single notification if not already set. Returns `True` if found. Caller commits. |
| `paginate_arena_notifications(executor, *, user_id, page, per_page)` | Returns `(rows, total)` for a paginated slice of all notifications (newest first). Page is clamped to valid range. Caller commits. |
| `delete_arena_notification(executor, *, notification_id, user_id)` | Deletes one user-owned notification. Returns `True` if deleted, `False` if not found. Caller commits. |
| `delete_all_arena_notifications(executor, *, user_id)` | Deletes all notifications for one Arena user. Returns the number of deleted rows. Caller commits. |

**HTTP endpoints** (`arena/routes/notifications.py`):

| Symbol | Description |
|--------|-------------|
| `GET /arena/notifications` | Returns the latest 20 current-user notifications and unread count. |
| `POST /arena/notifications/{notification_id}/read` | Marks one current-user notification as read and returns the updated unread count. |

The topbar badge is rendered from `request.state.arena_unread_notification_count`,
which is populated by `get_current_arena_user()` during authenticated requests.
The dropdown fetches the latest 20 rows when first opened and shows only
unread notifications. The profile page Notifications tab shows all notifications
for the current user paginated at 25 per page, with unread items highlighted and
per-item and bulk-delete actions (each guarded by a browser confirmation
dialog). Worker-side producers currently emit:

- `SUBMISSION_JUDGED` from `autojudge` after an Arena judgment reaches `DONE`.
- `AI_REVIEW_COMPLETED` from `aiassistant` after the AI review row is stored in
  `arena_submission_ai_reviews`.

---

### `ranking_service.py`

Public-facing ranking queries for the Arena Ranking section.

**Dataclasses:**
- `RankedUser` — flat presentation DTO with `id`, `rank`, `name`, `email_mascarado`, `affiliation_name`, `country_code`, `country_name`, `subdivision_name`, `rating`
- `RankedAffiliation` — flat presentation DTO with `id`, `rank`, `name`, `has_logo`, `country_code`, `country_name`, `subdivision_name`, `rating`

| Function | Description |
|----------|-------------|
| `get_ranked_users_paginated(session, *, search, affiliation_id, page, per_page)` | Returns `Pagination[RankedUser]`. Uses a CTE to compute global `RANK()` before applying search/affiliation filters. Eligible: `ativo=True`, `email_confirmado=True`, `role=ARENA_USER`. |
| `get_ranked_affiliations_paginated(session, *, search, country_code, subdivision_code, page, per_page)` | Returns `Pagination[RankedAffiliation]` with global affiliation rank via `RANK()` CTE ordered by rating desc, name asc. |
| `get_affiliation_filter_options(session, *, country_code)` | Returns `(countries, subdivisions)` as two `list[LocationChoice]` sourced only from distinct values in the affiliations table. |
| `get_affiliation_or_404(session, affiliation_id)` | Fetches `ArenaAffiliation` by ID or raises `HTTPException(404)`. |

---

### `arena_class_service.py`

Class lifecycle and discovery. A class is owned by an *assigned teacher*
(`ARENA_JUDGE`); `ARENA_ADMIN` may perform any operation. Authorization is enforced
in-service via `actor_id` + `actor_role`. The caller owns the transaction.

**Exceptions:** `ArenaClassServiceError` (base), `ArenaClassNotFoundError`,
`ArenaClassPermissionError`, `ArenaClassValidationError`.

**Dataclasses:** `ClassSummary` — discovery DTO with `class_id`, `name`, `teacher_id`,
`teacher_name`, `starts_on`, `finishes_on`, `member_count`, `is_upcoming`, `is_running`.
UI-facing DTOs: `ClassDetail`, `UserClassRow`, `ManagedClassRow`,
`ClassMemberManagementRow`, and `TeacherAutocompleteRow`.

**Helpers (shared):** `_assert_teacher_or_admin(...)` and `_active_members_subquery()`
(latest-`event_date` resolution of the current membership, used by membership listings).

| Function | Description |
|----------|-------------|
| `create_class(session, *, actor_id, actor_role, name, starts_on, finishes_on, description=None, teacher_id=None, allow_self_registration=False)` | `ARENA_JUDGE` becomes the teacher; `ARENA_ADMIN` must designate an `ARENA_JUDGE` `teacher_id`. Validates non-empty name and `finishes_on >= starts_on`. `allow_self_registration` defaults to False. |
| `update_class(session, *, actor_id, actor_role, class_id, today, name, starts_on, finishes_on, description=None, teacher_id=None, allow_self_registration=False)` | Teacher/admin update helper. Rejects past dates, end before start, start-date changes after the class has started, end-date changes after the class has finished, and non-judge teacher assignment. |
| `get_class_detail(session, *, class_id, today)` | Returns class details with assigned teacher email, teacher affiliation, active member count, and upcoming/running flags. Raises `ArenaClassNotFoundError` when missing. |
| `list_classes(session, *, today, affiliation_id=None)` | Discovery listing: only classes with `allow_self_registration = True` (and not finished before `today`), for any registered user, ordered by start date. When `affiliation_id` is given, restricts to classes whose assigned teacher belongs to that affiliation (the caller passes it explicitly; it is not derived from the logged-in user). |
| `list_user_classes(session, *, user_id, today)` | Classes whose latest membership row for `user_id` is `ACTIVE`. |
| `list_user_class_rows_paginated(session, *, user_id, today, params, search="", sort="name", direction="asc")` | UI list for the registered tab. Includes active memberships, pending registration requests, and latest denied registration requests. |
| `list_open_class_rows_paginated(session, *, user_id, user_affiliation_id, actor_role, today, params, search="", teacher_id=None, sort="starts_on", direction="desc")` | UI list for the open tab. Excludes active members, pending requests, and classes where the user is the assigned teacher; non-admin users are restricted to their affiliation, while admins see all open classes. |
| `list_managed_class_rows_paginated(session, *, actor_id, actor_role, today, params, search="", sort="name", direction="asc")` | UI list for the manage tab. Judges see their assigned classes; admins see all classes. |
| `list_class_members_management_paginated(session, *, actor_id, actor_role, class_id, params, sort="name", direction="asc")` | Teacher/admin membership page list. Combines active members and pending registration requests. |
| `search_teacher_autocomplete(session, *, query, affiliation_id=None, limit=10)` | Teacher search helper returning judge users formatted as `Full name <email>`. When `affiliation_id` is set, results are restricted to that affiliation. |
| `search_student_autocomplete(session, *, actor_id, actor_role, class_id, query, limit=10)` | Teacher/admin student search helper for direct class assignment. Returns active, confirmed `ARENA_USER` accounts formatted as `Full name <email>`, excluding active members and pending registration requests for the class. |

### `arena_class_query_service.py`

Class discovery and listing queries split out from `arena_class_service.py`. Covers
listing upcoming/existing classes for any registered user, the user's enrolled classes,
teacher-managed classes, and open registration classes.

| Function | Description |
|----------|-------------|
| `normalize_class_sort(value, default)` | Normalizes the class list sort field to `name` or `starts_on`. |
| `normalize_sort_dir(value, default)` | Normalizes sort direction to `asc` or `desc`. |
| `normalize_member_sort(value)` | Normalizes the member list sort field to `name` or `registered_at`. |
| `list_classes(session, *, today, affiliation_id=None)` | Lists unfinished classes open for self-registration, optionally restricted by teacher affiliation. |
| `list_user_classes(session, *, user_id, today)` | Lists classes where the user's latest membership is active. |
| `list_user_class_rows_paginated(session, *, user_id, today, params, search, sort, direction)` | Paginated user class list (enrolled, pending, latest denied). |
| `list_open_class_rows_paginated(session, *, user_id, user_affiliation_id, actor_role, today, params, search, teacher_id, sort, direction)` | Paginated open-registration list; excludes classes where the user is the teacher; non-admins restricted to their affiliation. |
| `list_managed_class_rows_paginated(session, *, actor_id, actor_role, today, params, search, sort, direction)` | Paginated teacher/admin class management list. |

---

### `arena_class_detail_service.py`

Class detail DTO helpers, member-management listing, and teacher autocomplete, split
out from `arena_class_service.py`. Provides the `_base_class_detail_stmt`,
`_class_detail_columns`, and `_class_detail_from_row` internal helpers reused by
`arena_class_query_service.py`.

| Function | Description |
|----------|-------------|
| `get_class_detail(session, *, class_id, today)` | Returns `ClassDetail` DTO with teacher info, active member count, and upcoming/running flags. Raises `ArenaClassNotFoundError` when missing. |
| `list_class_members_management_paginated(session, *, actor_id, actor_role, class_id, params, sort, direction)` | Teacher/admin membership page list. |
| `search_teacher_autocomplete(session, *, query, affiliation_id, limit)` | Teacher search helper. |
| `search_student_autocomplete(session, *, actor_id, actor_role, class_id, query, limit)` | Student search helper for direct class assignment. |

---

### `arena_class_membership_service.py`

Class membership (a dated status history) and self-service registration requests. A
same-day status flip overwrites that day's row, so only the last situation per day is
kept; the current status is the row with the latest `event_date`.

**Dataclasses:** `ClassMemberRow` — `user_id`, `name`, `email`, `user_rating`,
`registered_on`.

| Function | Description |
|----------|-------------|
| `is_active_member(session, *, class_id, user_id)` | Returns ``True`` when the user has an active (ACTIVE) membership in the class. Used by routes to authorize class-scoped pages. |
| `assign_users(session, *, actor_id, actor_role, class_id, user_ids, on_date)` | Teacher/admin only. Marks each user `ACTIVE` on `on_date`. Validates users exist; dedupes ids. |
| `remove_users(session, *, actor_id, actor_role, class_id, user_ids, on_date)` | Teacher/admin may remove anyone; any other user only themselves. Marks `REMOVED` on `on_date`. |
| `list_class_members(session, *, actor_id, actor_role, class_id)` | Teacher, admin, or active member only. Returns currently-active members with current rating, ordered by name. |
| `request_registration(session, *, user_id, class_id)` | Any registered user except the class teacher. Creates a `PENDING` request; rejects if the requesting user is the teacher, the class forbids self-registration, the user is already a member, or a pending request exists. |
| `decide_registration(session, *, actor_id, actor_role, request_id, approve, on_date, reason=None)` | Teacher of the request's class / admin only. Sets `APPROVED`/`DENIED` + decider audit; approval also marks the user `ACTIVE` on `on_date`. On denial, an optional `reason` is stored in `denial_reason` (trimmed, ≤256 chars); cleared on approval. |

### `arena_problem_set_service.py`

Problem-set lifecycle, scheduling, problem membership, and membership-aware
lookups. A problem set belongs to one class and is created only by the assigned
teacher (`ARENA_ADMIN` may act on any class). A set "accepts submissions" only
while `starts_on <= now <= deadline` (both set). Removing a problem from a set, or
deleting the set, resets the related submissions to private
(`arena_submissions.problem_set_id = NULL`).

**Exceptions:** `ArenaProblemSetServiceError` (base), `ArenaProblemSetNotFoundError`,
`ArenaProblemSetPermissionError`, `ArenaProblemSetValidationError`.

**Dataclasses:** `ProblemSetRow` (`set_id`, `class_id`, `name`, `description`,
`starts_on`, `deadline`, `is_accepting`, `problem_count`), `ProblemRow`
(`problem_id`, `arena_number`, `title`), `AcceptingSetInfo` (`set_id`, `name`,
`class_id`, `class_name`, `deadline`) — drives the problem-detail
banner/checkbox.

| Function | Description |
|----------|-------------|
| `create_problem_set(session, *, actor_id, actor_role, class_id, name, description=None)` | Teacher/admin only. Validates non-empty name and trims optional teacher-facing notes/description to `None` when blank. |
| `delete_problem_set(session, *, actor_id, actor_role, set_id)` | Teacher/admin only. Resets related submissions to private, then deletes the set. |
| `set_problem_set_schedule(session, *, actor_id, actor_role, set_id, starts_on, deadline, now)` | Teacher/admin only. Used at creation time; rejects any date in the past; validates `deadline > starts_on` when both present. |
| `update_problem_set_schedule(session, *, actor_id, actor_role, set_id, starts_on, deadline, now)` | Teacher/admin only. Used for post-creation edits; allows existing past values to be kept unchanged (minute-precision comparison); rejects a new `starts_on` in the past; validates `deadline > starts_on` when both present; when only deadline changes and no `starts_on` is set, deadline must be in the future. |
| `update_problem_set_details(session, *, actor_id, actor_role, set_id, description, starts_on, deadline, now)` | Teacher/admin only. Updates trimmed notes/description and the validated schedule together. Blank notes become `None`. |
| `stop_problem_set_now(session, *, actor_id, actor_role, set_id, now)` | Teacher/admin only. Sets `deadline = now` unconditionally, immediately closing the problem set to new submissions. |
| `list_problem_sets_for_class(session, *, actor_id, actor_role, class_id, now)` | Teacher/admin or active member. All sets with accepting flag and problem count. |
| `list_accepting_problem_sets_for_class(session, *, actor_id, actor_role, class_id, now)` | Same auth, filtered to sets currently accepting submissions. |
| `list_problems_in_set(session, *, actor_id, actor_role, set_id)` | Teacher/admin or active member. Problems ordered by `arena_number`. |
| `add_problems_to_set(session, *, actor_id, actor_role, set_id, refs)` | Teacher/admin only. `refs` resolve by `arena_number` (digits) or UUID `id`; idempotent. Unknown refs raise validation. |
| `remove_problems_from_set(session, *, actor_id, actor_role, set_id, refs)` | Teacher/admin only. Removes junction rows and resets related submissions to private. |
| `problem_accepting_set_for_user(session, *, problem_id, user_id, now)` | The most urgent (earliest deadline) accepting set containing the problem in a class the user is active in, or None. |
| `problem_in_any_set_for_user(session, *, problem_id, user_id)` | True when the problem is in any set (any window) of a class the user is active in. |

### `arena_problem_set_report_service.py`

Teacher-facing reporting over set-tied submissions (`problem_set_id`).

**Dataclasses:** `SetSubmissionRow`, `UserProblemVerdictRow`, `StudentSubmissionEntry`,
`StudentProblemGroup`. Helper `best_verdict(verdicts)` selects the best via `VERDICT_PRIORITY`
(AC best).

| Function | Description |
|----------|-------------|
| `list_set_submissions(session, *, actor_id, actor_role, set_id)` | Teacher/admin only. Every set-tied submission with its verdict. |
| `list_users_best_verdicts(session, *, actor_id, actor_role, set_id)` | Teacher/admin only. Each submitting user's best verdict per set problem. |
| `list_problems_without_submissions_for_user(session, *, actor_id, actor_role, set_id, user_id)` | Teacher/admin or the user. Set problems with no set-tied submission by the user. |
| `list_problems_without_ac_for_user(session, *, actor_id, actor_role, set_id, user_id)` | Teacher/admin or the user. Set problems with no set-tied AC submission by the user. |
| `get_student_problem_submissions_for_set(session, *, actor_id, actor_role, set_id, user_id)` | Teacher/admin only. All submissions by one student for the problems in a set, grouped by problem (tuple of `StudentProblemGroup`), submissions ordered newest-first. Each `StudentSubmissionEntry` carries `has_feedback` (teacher feedback present); each `StudentProblemGroup` carries `has_unfeedback_non_ac` (any non-AC submission still lacking feedback). |
| `can_teacher_view_submission(session, *, teacher_id, set_id)` | Returns True if the teacher manages the class that owns the given problem set. Used by the submission detail route to authorize ARENA_JUDGE access. |

### `arena_problem_set_snapshot_service.py`

Freezing and reading the post-deadline rating snapshot.

**Dataclasses:** `SnapshotUserTotal`, `SnapshotProblemRating`.

| Function | Description |
|----------|-------------|
| `snapshot_problem_set(session, *, set_id, now)` | Idempotent per set (delete-then-insert). Per user with a set-tied submission, stores `total_rating` = sum of current ratings of AC'd set problems (0 if none) and a per-problem row for each AC. Requires the set to have a deadline. No authz (worker/admin caller). |
| `list_snapshot_user_totals(session, *, actor_id, actor_role, set_id)` | Teacher/admin only. Per-user frozen totals. |
| `list_snapshot_ratings(session, *, actor_id, actor_role, set_id)` | Teacher/admin only. Per (user, problem) frozen AC ratings. |

### `arena_problem_set_management_service.py`

Teacher-facing query helpers used by the class-scoped problem-set management
pages.

**Dataclasses:** `ProblemSetManagementRow`, `ProblemSetProblemManagementRow`,
`ProblemAutocompleteRow`, `ReportProblemColumn`, `ReportStudentRow`,
`TeacherProblemSetReport`.

| Function | Description |
|----------|-------------|
| `normalize_problem_set_sort(value, default='deadline')` | Normalizes the teacher list sort field to `deadline`, `name`, or `starts_on`. |
| `normalize_sort_dir(value, default='desc')` | Normalizes the teacher list sort direction. |
| `list_problem_sets_paginated(session, *, actor_id, actor_role, class_id, now, params, sort='deadline', direction='desc')` | Teacher/admin only. Returns the paginated list page rows with problem count and `is_accepting`. |
| `list_problem_set_problems(session, *, actor_id, actor_role, set_id)` | Teacher/admin only. Returns the problem rows for the manage-problems page with Arena number, title, plain-text categories, and display rating. |
| `search_set_candidate_problems(session, *, actor_id, actor_role, set_id, query, limit=10)` | Teacher/admin only. Autocomplete source for adding problems. Searches enabled problems not already in the set by title and, for numeric queries, Arena number prefix. |
| `build_teacher_problem_set_report(session, *, actor_id, actor_role, set_id, now)` | Teacher/admin only. Builds the UI-ready report matrix over active class members and set problems, with best verdict per cell and an optional snapshot rating column when a due-set snapshot already exists. |

---

### `arena_student_problem_set_service.py`

Student-facing query helpers used by the student problem-set list and detail pages.
All counts and verdicts are scoped to the *requesting student's* own set-tied submissions,
not across the whole class.

**Dataclasses:** `StudentProblemSetRow`, `StudentProblemRow`, `StudentProblemSetDetail`.

| Function | Description |
|----------|-------------|
| `normalize_student_ps_sort(value, default='deadline')` | Normalizes the student list sort field to `deadline` or `name`. |
| `normalize_sort_dir(value, default='desc')` | Normalizes a sort direction; correctly accepts both `"asc"` and `"desc"`. |
| `list_student_problem_sets_paginated(session, *, actor_id, actor_role, class_id, now, params, sort='deadline', direction='desc')` | Active member/teacher/admin. Returns the paginated student list rows with problem count, per-user AC count, and per-user no-submission count. |
| `get_student_problem_set_detail(session, *, actor_id, actor_role, set_id, now)` | Active member/teacher/admin. Returns problem-set metadata, the ordered problem list with the student's best verdict per problem, and snapshot data (availability flag and the student's frozen total rating if the snapshot has been computed). |

---

## Still planned (Phase 2)

- `arena_problem_service.py` — problem CRUD and ZIP import beyond current public-number lookup
- `arena_verdict_handler.py` — Valkey subscriber: VerdictEvent → DB updates
