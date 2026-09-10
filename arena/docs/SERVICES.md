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

## `arena/template_globals.py`

The single definition of what Arena templates may read. `_base.html` and its
partials resolve a fixed set of names, and
`register_arena_template_globals(templates, *, app_version)` is where that set is
declared -- called by `arena/main.py` when it builds the environment, and by
`tests/arena/conftest.py::install_arena_templates` when a test builds one.

That sharing is the point. The block used to live inline in `arena/main.py`, so
each test application re-declared whichever subset its page happened to touch;
those subsets drifted (27 set `app_version`, 25 `next_rating_update_text`, 3
`token_expiry_text`, 1 `brand_name`) and the templates papered over the gaps with
`is defined` guards, which hid a genuine omission just as readily as a test-only
one. Those guards are gone: a template global missing from a rendering
application is now a loud failure.

The module also owns the request-scoped helpers the templates call:

| Function | Description |
|----------|-------------|
| `heartbeat_config(request)` | Heartbeat URLs, interval, and presence flag for the `_base.html` keepalive client. Raises `NoMatchFound` when the presence routes are absent, rather than rendering a page whose session will silently expire. |
| `session_heartbeat_seconds()` | `PRESENCE_HEARTBEAT_SECONDS` when presence is enabled, else `JWT_EXPIRE_SECONDS / 4` (minimum 60 s) -- enough to land inside the token refresh window. |
| `token_expiry_text(request)` | Absolute-cap deadline as a display string, or `None` when no cap is configured. |
| `pending_required_announcement_for(request)` | The `PendingRequiredAnnouncement` the `load_pending_required_announcement` dependency left on `request.state`, or `None` (also `None` on a page rendered without that dependency, so single-router test apps show no pop-up rather than failing). `_base.html` includes the mandatory-announcement modal when it is set. |
| `next_rating_update_text(request)` | Footer text for the next rating cycle, read from `app.state`. |
| `arena_online_user_count(request)` | Cached online-user count, read from `app.state`. |
| `fmt_shell_cmd(cmd)` | Jinja filter joining a command list and splitting on `&&`. |

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
The JWT issuer claim comes from `settings.APP_NAME` (`NOCA_ARENA_APP_NAME`,
default `"noca-arena"`), which `arena/main.py` passes when constructing the
module JWT service. It must differ from the web module's `NOCA_WEB_APP_NAME`
so tokens issued by each server are not mutually valid.

**`ArenaTokenAction` values:**

| Value | Use |
|-------|-----|
| `LOGIN` | Full authenticated session |
| `VALIDATE_EMAIL` | Email confirmation link (24 h) |
| `PARENTAL_CONSENT` | Parent/legal guardian consent link (24 h) |
| `PARENTAL_CONSENT_REVOKE` | Guardian consent-withdrawal link (10 years). Long by design: LGPD art. 8 §5 grants withdrawal *at any time*, no flow re-sends the link, and the longest window it must cover is 13 → 18. Expiry is not the security boundary — the route refuses the token once the holder turns 18, and any consent transition invalidates it earlier through the `gen` epoch claim |
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
| `abortar_ativacao_2fa(usuario, session)` | Void a tentative secret after a confirmation lockout (no-op once 2FA is active) |
| `desativar_2fa(usuario, session)` | Clear OTP fields and invalidate backup codes |
| `validar_codigo_2fa(usuario, codigo, session)` | Accept TOTP or backup code |
| `validar_token_ativacao_2fa(usuario, token, jwt_service)` | Validate activation session token (sync) |
| `otp_secret_formatado(value)` | Format TOTP secret in groups of 4 for display (sync) |

Result types: `TwoFASetupResult`, `TwoFAValidationResult`.

### `arena/email_templates/`

Arena's catalogue and packaged defaults for every outbound Arena email. Each
stable snake-case key maps to one TOML file that contains its subject and
plain-text body. Catalogue entries declare separate subject/body placeholder
sets, required placeholders, and sample values. `render_email(key, **context)`
injects `settings.BRAND_NAME` and delegates grammar, validation, and one-pass
substitution to `shared/services/email_templates/`.

Conditional prose is prepared before rendering. The class service supplies a
complete denial-reason line, the security service supplies inflected credit
nouns and selects one of four complete administrator Google-unlink variants,
and the signup reputation service supplies display-ready IP and email report
sections. No Arena email executes Jinja or reads object attributes in a
template.

A deployment can replace any of that wording without a rebuild: with
`NOCA_EMAIL_TEMPLATE_OVERRIDE_DIR` set, a key with a file under the root's
`arena/` namespace renders from that file instead of the packaged default.
`arena_email_templates()` builds the registry on first use rather than at import,
so the validation CLI can read this catalogue on a host with no Arena
configuration, and `validate_email_template_overrides()` is what the lifespan
calls to refuse a start on an invalid tree. Startup fails closed; a later invalid
edit is logged and the last valid version keeps sending. See
[SHARED_SERVICES.md](../../docs/SHARED_SERVICES.md).

`GET /admin/dashboard/email-templates` is read-only visibility for the same
process registry: it renders declared sample values, reports the effective
source and `based_on` state, and shows a retained runtime error without reading
or validating the override tree a second time. Its diagnostics are replica-local.
Each key is rendered on its own, so a template that cannot render is reported in
its own row rather than failing the page, and retained errors name the file
rather than its path on the host.

### `arena_class_email_service.py`

Best-effort email notifications for Arena class membership and registration events. All helpers
are async, send email only after the caller has committed the database change, and take the
`actor_key` / `tier` of whoever caused the email (the requesting student, or the acting teacher
or admin) for the shared email budget. Delivery failures -- a spent budget included -- are caught
and logged; they never roll back or raise to the caller. Subjects and bodies render from the
module catalogue through the constrained shared renderer.

| Function | Recipient | Trigger |
|----------|-----------|---------|
| `send_class_registration_request_email(*, teacher_email, teacher_name, student_name, class_name, members_url, email_service, actor_key, tier="user")` | Class teacher | Student requests self-registration |
| `send_class_registration_approved_email(*, student_email, student_name, class_name, class_url, email_service, actor_key, tier="user")` | Requesting student | Teacher/admin approves the request |
| `send_class_registration_denied_email(*, student_email, student_name, class_name, denial_reason, email_service, actor_key, tier="user")` | Requesting student | Teacher/admin denies the request (optional reason) |
| `send_class_membership_added_email(*, student_email, student_name, class_name, class_url, email_service, actor_key, tier="user")` | Added student | Teacher/admin directly adds a student |
| `send_class_membership_removed_email(*, student_email, student_name, class_name, email_service, actor_key, tier="user")` | Removed student | Teacher/admin removes a student (self-removal excluded) |

---

### `user_security_notification_service.py`

Single home for all Arena security event email notifications. All functions are async and return `True` on successful delivery; callers log warnings on failure but do not block the request flow. The self-service ones charge the user's own email budget; the `admin_*` ones take the acting `admin_id` and charge that administrator's budget. Complete subjects and bodies come from `arena/email_templates/`.

| Function | Description |
|----------|-------------|
| `send_password_changed_email(usuario, email_service)` | User changed their own password |
| `send_2fa_enabled_email(usuario, email_service)` | User enabled 2FA on their account |
| `send_2fa_disabled_self_email(usuario, email_service)` | User disabled 2FA on their account |
| `send_backup_code_used_email(usuario, email_service, remaining)` | A backup code was consumed at login (includes remaining count) |
| `send_admin_2fa_disabled_email(usuario, email_service, *, admin_id)` | Administrator disabled 2FA on the user's account |
| `send_admin_password_change_required_email(usuario, email_service, *, admin_id)` | Administrator required the user to change their password |
| `send_google_linked_email(usuario, email_service)` | A Google account was linked (a second way in, so it is announced like one) |
| `send_google_unlinked_email(usuario, email_service)` | The linked Google account was removed |
| `send_admin_google_unlinked_email(usuario, email_service, *, admin_id, password_reset_url, account_inactive=False)` | Administrator unlinked the user's Google account; with `password_reset_url` set (an account with no usable password) the message says a password must be set there before signing in with one, and with `account_inactive` it says that neither a password nor a reset is enough until an administrator reactivates the account |
| `send_ai_credits_topped_up_email(usuario, email_service, quantity, balance, *, admin_id)` | Administrator added AI review credits to the user's account |

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
| `resolver_consentimento_por_token(token, session, jwt_service, *, lock=False)` | Resolve a consent-grant JWT to its account without mutating (backs the review page) |
| `validar_consentimento_responsavel_por_token(token, session, jwt_service)` | Confirm parental consent via JWT (the `POST` half; resolves under a row lock, then grants) |
| `regularizar_data_nascimento(user_id, dta_nascimento, session)` | Store missing date of birth and apply age-gate defaults; a non-`ALLOWED` outcome also clears `public_profile` / `full_name_public`, and `consent_generation` is bumped on **both** branches because each is a consent-state transition. Deliberately does not invalidate sessions — that is the guardian-revocation concern, and the account gates are re-checked per request anyway |
| `update_date_of_birth(usuario, date_of_birth, session)` | Store a changed date of birth; deactivate users under 13, clear consent **and both public-identity opt-ins** and invalidate sessions for all minors, preserve consent fields for adults, and bump `consent_generation` on every effective change |
| `grant_parental_consent(usuario, session)` | Record guardian consent and bump `consent_generation`. The single grant write path, shared by the token route and the admin toggle. Returns whether it actually transitioned, so a re-opened link neither emails nor audits |
| `revoke_parental_consent(usuario, session)` | Withdraw guardian consent (LGPD art. 8 §5). The single revocation write path, shared by the guardian link and the admin toggle so the two cannot drift: clears the consent fields, deactivates the account, bumps `session_version`, clears both public-identity opt-ins, and bumps `consent_generation`. **Suspends, never erases** — submissions, badges and ratings are untouched. Deliberately leaves `ranking_visible` alone, because the affiliation aggregation in `shared/services/arena_rating.py` filters on that flag alone and clearing it would move a third party's rating |
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

**Why the date-of-birth paths clear the publication flags.** `_clear_public_identity_flags()`
is the write half of the Arena minor shield, and it exists because the read half cannot
stand on its own. A 13-17 year-old whose `public_profile` is merely *masked* at read time
still carries a stored `True`; on the morning of their eighteenth birthday the mask lifts
and the profile publishes itself, with nobody having chosen anything. Turning 18 must only
*unblock* the opt-in, never flip it — so the stored flags are cleared on every transition
into the shielded band, and the user opts back in explicitly if they still want to. The
read-side masking in `user_visibility_service` remains necessary for rows that never
re-authenticate and as defence against a path added later; the two halves are not
alternatives.

---

### `user_registration_service.py`

New user creation, email confirmation, and parental consent email delivery. Handles
the registration flow including JWT token generation and email dispatch for activation
and guardian consent links.

| Function | Description |
|----------|-------------|
| `registrar_usuario(nome, email, password, session, jwt_service, email_service, url_base, ..., aceitou_termos_privacidade, dta_aceitacao_termos_privacidade)` | Register a new user; persists ToS and Privacy Policy acceptance flag and timestamp; dispatches activation email. |
| `async enviar_email_ativacao(usuario, token, email_service, url_base, *, actor_key)` / `async enviar_email_consentimento_responsavel(...)` / `async enviar_email_conta_existente(email, email_service, url_base, *, actor_key)` | The signup-time sends, called by the route after the commit with the client IP as `actor_key` (the requester is not logged in yet). |

---

### `signup_reputation_service.py`

Post-signup reputation recording and admin notification. Runs as a background task
after the signup response. The signup IP is **always** persisted to
`arena_user_reputation` (even when the IPQualityScore integration is disabled) so it
can be scored later by the backfill script; when `NOCA_IPQUALITYSCORE_APIKEY` is set,
the blocking IPQualityScore IP and email lookups run in a worker thread, the snapshot
is updated with fraud scores plus the full JSON reports, and every `ARENA_ADMIN` is
emailed the `new_user_reputation` catalogue entry. Python converts both optional
reputation objects into display-ready text before rendering. All failures are logged and swallowed
so the flow can never affect the already-created account.

| Function | Description |
|----------|-------------|
| `record_signup_reputation(session, *, user_id, email, ip_address, ip_service, email_reputation_service, email_service, reputation_enabled)` | Persist the signup IP, optionally look up IP/email reputation, and notify admins when enabled. |

---

### `user_email_service.py`

Email/consent JWT token validation and revalidation flows. Handles re-sending
confirmation emails, processing JWT confirmation links, and parental consent
management.

| Function | Description |
|----------|-------------|
| `revalidar_email(user_id, session, jwt_service, email_service, url_base, *, actor_key)` | Re-send email confirmation link for an unconfirmed account; `actor_key` is the requester's budget identity (the client IP). |
| `revalidar_consentimento_responsavel(user_id, session, jwt_service, email_service, url_base, *, actor_key)` | Re-send parental consent link. |
| `atualizar_email_responsavel(user_id, email_responsavel_legal, session, jwt_service, email_service, url_base, *, actor_key)` | Store guardian email and dispatch consent link. Bumps `consent_generation`, which strips authority from the **previous** guardian by invalidating every revocation link minted for them. Deliberately does not deactivate or invalidate sessions: this path is reachable only from the pending-parental login flow, so consent is already withheld and the consent gate already refuses both a new login and a live session — there is nothing left to suspend. |
| `validar_email_por_token(token, session, jwt_service)` | Confirm email via JWT link. |
| `resolver_consentimento_por_token(token, session, jwt_service, *, lock=False)` | Resolve a consent-grant JWT to its account, **mutating nothing** — the single validation gate both halves of the grant flow consult, so the review page and the submit cannot disagree about whether a link may act. Refuses an account currently in the under-13 blocked band (a stale link must not restore consent an age change cleared, since `ativar_conta_se_pronta` tests only email and consent); deliberately still resolves for an adult, because this link is the only self-service recovery for an account whose holder turned 18 while consent was pending. Carries no consent-epoch claim — the epoch binds *revocation* links; a grant link is bounded by its own expiry. |
| `validar_consentimento_responsavel_por_token(token, session, jwt_service)` | Confirm parental consent via JWT link: resolves through `resolver_consentimento_por_token` **under a row lock** and grants inside it, so concurrent clicks cannot both transition. Delegates the write to `user_service.grant_parental_consent` and reports the outcome on `extra_data["transitioned"]`, which the caller must consult before emailing or auditing. Called only by `POST /auth/parental-consent` — the `GET` review page uses the unlocked resolver and never mutates. |

---

### `parental_consent_service.py`

Guardian consent-revocation tokens and the three consent notifications (LGPD art. 8 §5).

| Function | Description |
|----------|-------------|
| `build_revocation_url(url_base, token)` | Absolute `/auth/parental-consent/revoke?token=…` link for an email body |
| `mint_revocation_token(usuario, jwt_service)` | Mint a `PARENTAL_CONSENT_REVOKE` token carrying `sub` and a `gen` claim bound to the account's current `consent_generation`. Callers mint **after** the consent transition commits, so the link holds the post-transition epoch |
| `resolve_revocation_token(token, session, jwt_service, *, lock=False)` | The single gate for both the confirmation page and the revocation itself. Requires a valid token, an exact `gen` match, consent currently granted, and a holder still in the 13-17 band (unknown date of birth refuses). `lock=True` takes a row lock so the caller can re-validate and mutate atomically. Returns `RevocationTokenResult`, whose `log_reason` is for the server log **only** |
| `send_consent_confirmed_email(usuario, *, jwt_service, email_service, url_base, actor_key)` | Confirm a granted consent to the guardian, carrying their revocation link |
| `send_consent_revoked_email(usuario, *, recipient, email_service, actor_key)` | Tell one party (`"guardian"` or `"child"`) that the account was suspended |

**One live link at a time.** Every consent transition — grant, revoke, guardian-email
change, date-of-birth change — bumps `consent_generation`, so a link is invalidated by the
next transition of any kind. That single mechanism closes replay (a used link cannot revoke
twice) and strips authority from a **former** guardian once the address changes.

**Why the link ships only after the grant.** The `parental_consent_confirmed` email is the
sole carrier. A link placed in the consent *invitation* could never work: before the grant
it fails the consent check, and after it the epoch is already stale — there is no window in
between. The invitation therefore carries only wording about the right to withdraw. The
cost of that choice is stated openly: the confirmation mail is load-bearing, so a failed
delivery is recorded at `warning` severity, and a guardian who loses it recovers through an
administrator until a dedicated re-send flow exists.

The revocation link goes to the **guardian only**. The right being exercised is theirs, and
a child holding the link could suspend their own account. The *revocation-happened* notice
goes to both, each recipient attempted independently so one spent budget or provider
refusal cannot silence the other.

**Residual limitation.** Nothing here proves the person opening the link is the guardian;
possession of the mailbox is the whole credential, exactly as it is for the consent grant
itself. That is the same trust model the grant already relies on, and raising it would mean
authenticating guardians, which Arena does not do.

---

### `consent_timeline.py`

Presentation data for the chronology the two guardian consent pages draw their decision
on. No ORM writes, no queries — it reads columns off an already-loaded `ArenaUser`.

| Function | Description |
|----------|-------------|
| `format_consent_date(value)` | Format a stored date or timestamp as `12 March 2026`, or `None` when the column is unset |
| `consent_no_longer_required_on(birth_date)` | The day parental consent stops being required, i.e. the eighteenth birthday; a 29 February birth crosses on 1 March in a non-leap year, matching how `shared.age_check.calculate_age_years` counts it |
| `build_consent_timeline(user)` | The whole `timeline` mapping the templates read: `created_on`, `consent_given_on`, `consent_ends_on` |

**Every node is backed by a stored column, and an unset one yields no node.** A dated
chronology printing a placeholder reads as a fault in the record rather than as an
absence. There is deliberately **no "consent requested" node**: no column records that
moment, and the alternative — one undated node among dated ones — is worse than its
omission.

Dates are formatted here rather than through `arena_format_datetime` because a guardian
holds no Arena session, so there is no user whose timezone that helper could resolve;
a day is also the whole precision a consent decision needs.

### `user_ai_credit_service.py`

Atomic AI backend credit top-up and consumption with full transaction logging.

| Function | Description |
|----------|-------------|
| `top_up_ai_credits(usuario, quantity, session, *, admin_id=None)` | Add `quantity` AI backend credits to the user's balance and log the transaction; returns `False` for non-positive quantities. |
| `consume_ai_credit(usuario, session, *, submission_id=None)` | Atomically decrement one AI backend credit (`SELECT FOR UPDATE`) and log the transaction; returns `True` on success, `False` when balance ≤ 0. |

---

### `google_oauth_service.py`

Google OpenID Connect client for the Arena login door. Owns the conversation with
Google only; the database side lives in `google_identity_service.py`.

| Function | Description |
|----------|-------------|
| `build_google_oauth_client(settings)` | Register the Authlib client via OIDC discovery, or `None` when the feature is disabled |
| `google_redirect_uri(request, url_base)` | Build the absolute callback URI Google is configured with |
| `extract_claims(token)` | Read verified `sub` / `email` / `email_verified` / `name` and the optional `picture` URL out of an Authlib token, or `None` |
| `GoogleIdentityClaims` | Frozen dataclass carrying those identity claims and optional picture URL |

The flow is delegated to Authlib rather than hand-rolled because the parts that must
not be got wrong -- `state`, `nonce`, PKCE, and verifying the ID token against
Google's *rotating* JWKS -- are exactly what its OIDC discovery integration owns.
PKCE is the one of those that is **opt-in**: Authlib emits a `code_challenge` only
when the client declares a challenge method, so `build_google_oauth_client` passes
`code_challenge_method` (`GOOGLE_CODE_CHALLENGE_METHOD`, `S256`) alongside the
scope. Omitting it silently downgrades the flow to a plain authorization-code
exchange with no proof of possession, which is why
`tests/arena/test_arena_google_oauth_client.py` builds the real client and asserts
the parameters on the authorization URL rather than trusting the configuration.
`authorize_access_token` populates `token["userinfo"]` only after that verification
succeeds, so `extract_claims` reads checked data.

This deliberately does **not** go through `shared.services.network_utils`: that helper
is a synchronous, SSRF-validating `requests` wrapper for arbitrary operator-supplied
URLs, whereas Google's endpoints are fixed and public and this flow needs async plus
JWKS caching. Arena depends on `httpx2` rather than `httpx` for the same reason:
Authlib 1.8 prefers it and falls back to `httpx` only with a deprecation warning on
every import, so tracking the supported client keeps Arena off a removal path it does
not control. `httpx` stays a dev-only dependency, where the test client uses it. `build_google_oauth_client` returning `None` is what makes every
`/auth/google` route answer `404` on an unconfigured deployment -- except
`GET /auth/google/blocked`, deliberately left reachable (see
`arena/docs/ROUTES.md`).

---

### `google_avatar_service.py`

Bounded downloader and image processor for an optional Google profile picture. It never
serves or redirects to Google's URL: the URL is accepted only over HTTPS from Google's
profile-image hosts, fetched without redirects or environment proxies, capped at the
configured image upload limit while streaming, and passed through the existing
`ImageProcessingService` validation and resizing pipeline.

| Function | Description |
|----------|-------------|
| `download(picture_url)` | Validate and download a Google picture, then return the standard processed full-size/avatar result |
| `GoogleAvatarError` | Stable application error for unsafe URLs, network/status failures, oversized responses, and invalid image bytes |

The picture is presentation data rather than identity evidence. A failure is non-fatal to
Google login and does not overwrite the last valid cached picture.

---

### `google_identity_service.py`

Database side of the Google identity linked to an Arena account. The caller owns the
transaction; these functions flush but never commit.

| Function | Description |
|----------|-------------|
| `get_identity_by_sub(session, google_sub)` | Load an identity by Google's subject claim |
| `get_identity_for_user(session, user_id)` | Load the identity linked to an Arena account |
| `link_identity(session, user_id, claims)` | Insert an identity row; raises `IntegrityError` on a claimed account |
| `unlink_identity(session, identity)` | Delete an identity row |
| `touch_last_login(session, identity)` | Stamp `last_login_at` |
| `update_picture_claim(identity, claims)` | Store the latest optional Google picture URL from a verified login token |
| `refresh_google_avatar(session, usuario, identity, avatar_service)` | Download and replace the local Google avatar cache; bump the visible revision only when the selected image changes |
| `set_google_avatar_selected(session, usuario, identity, selected)` | Change the explicit Arena/Google source preference and bump the visible avatar revision |

Every lookup keys on `google_sub`, never on the email: a Google account's address can
change while its subject identifier cannot, so matching on email would let a re-used
or re-assigned address land on the wrong Arena account. `link_identity` does not
pre-check availability -- uniqueness is guaranteed by the table's two UNIQUE
constraints, never by a lookup-then-insert, which is a TOCTOU. The caller catches the
`IntegrityError` and presents it as a stated conflict.

---

### `google_signup_service.py`

Account creation for a Google-first Arena signup.

| Function | Description |
|----------|-------------|
| `create_google_first_account(session, claims, jwt_service, email_service, url_base)` | Create an inactive account plus its identity row in one transaction |
| `describe_pending_google_signup(usuario)` | Classify an inactive account as an in-flight Google-first signup (sync, pure) |
| `transfer_pending_signup_identity(session, orphan, user_id)` | Fold a never-completed Google-first signup into an existing account: re-create its Google identity under `user_id` and delete the orphan row, in the caller's transaction. Refuses (`ValueError`) anything but a `needs_completion` orphan, so a completed account can never be removed here; lets the target's own `IntegrityError` through as a stated conflict. Deletes with a Core `DELETE` because the ORM relationships on `ArenaUser` cascade through collections the async session cannot lazily load; the database cascades take the orphan's satellites (its reputation row) with it |
| `admin_unlink_refusal(usuario)` | Why an administrator may not unlink this account's Google identity, or `None` (sync, pure). Built on `describe_pending_google_signup`: every in-flight Google-first signup is refused, because the admin unlink's justification -- the password reset remains a way back in -- is false for a row that neither door could finish afterwards, a 13-17 account held for consent that reaches the consent flow only through Google, or an under-13 refusal whose explanatory page is reached only through Google (without the identity the next Google sign-in ends at a dead-end "already registered" message) |

Completing that signup -- date of birth, terms acceptance, activation -- is
deliberately **not** owned here. `arena/routes/auth_google_complete.py` composes
`user_service`'s canonical primitives (`regularizar_data_nascimento`,
`aceitar_termos_privacidade`, `ativar_conta`) instead, so the Google door reaches
exactly the account state the password door reaches, stamping the same timestamps
and bumping the same `consent_generation` epoch. The earlier Google-only
`apply_adult_completion()` / `apply_terms_acceptance()` helpers skipped
`dta_ativacao_conta` and `dta_consentimento_responsavel`, and are gone.

Google can prove who someone is, but not the date of birth the LGPD age gate turns on
nor acceptance of the Terms of Service. So an unknown Google subject creates an account
that is deliberately unusable -- `ativo=False`, no date of birth, terms not accepted --
and `arena/routes/auth_google_complete.py` collects the rest. The account and its
identity row are written in one transaction, so a crash between them cannot leave a
Google identity pointing at nothing or an Arena account nobody can reach. An abandoned
completion leaves an account that fails `evaluate_account_access_gates` everywhere.

The account gets a random `secrets.token_urlsafe(64)` password, so `password_hash` is a
real Werkzeug hash and `get_token_id()` / `session_version` behave exactly as for any
other account; `arena_users.password_is_placeholder` is what records that no password
can match it. The flag is set **after** `registrar_usuario` returns, because that
function assigns through the `password` setter, which clears it.

`describe_pending_google_signup` is what makes leaving the flow mid-way recoverable
rather than a dead end. It returns `"needs_completion"` when the date of birth was
never collected, `"blocked"` when it was and the account is under 13, `"needs_guardian_consent"`
when it was and a guardian has not yet consented, or `None` when the account is not an
in-flight Google-first signup at all. `password_is_placeholder`, not `ativo` alone, is
the discriminator: an administrator can deactivate an ordinary password account too, and
that account must still fail into the generic "deactivated, contact support" message
rather than being swept into a flow it never went through. Both
`arena/routes/auth_google.py` (resuming a returning Google login) and
`arena/routes/auth_google_complete.py` (the under-13 submit branch) consult it, so the
three outcomes -- resume the form, wait for a guardian, or the permanent refusal -- are
decided in exactly one place.

---

### `arena_auth_service.py`

Async functions for authentication and intermediate flow tokens.

| Function | Description |
|----------|-------------|
| `evaluate_account_access_gates(usuario)` | Evaluate the account-state gates every authenticated login must clear (sync, pure, non-logging) |
| `efetuar_login(email, password, session, ip_address, source_port, user_agent, mode, geo_service)` | Verify credentials and record password-only login history |
| `efetuar_logout(token, jwt_service)` | Revoke session JWT |
| `registrar_login_concluido(usuario, session, ip_address, source_port, user_agent, mode, geo_service)` | Record completed login history after final authentication |
| `set_pending_2fa_token(usuario, jwt_service, remember_me, next_page, session_started_at, login_method)` | Issue `PENDING_2FA` token (sync); `login_method` carries the originating door across the 2FA hop |
| `get_pending_2fa_token_data(token, session, jwt_service)` | Validate `PENDING_2FA` token, return user |
| `set_pending_password_change_token(usuario, jwt_service, remember_me, next_page, session_started_at)` | Issue `PENDING_PASSWORD_CHANGE` token (sync) |
| `get_pending_password_change_token_data(token, session, jwt_service)` | Validate token, return user |

`efetuar_login` returns `UserServiceResult` with the authenticated `ArenaUser`.
For 2FA-enabled users, login history is recorded only after successful TOTP or backup-code validation.
The optional `geo_service` parameter is a `GeolocationIP` instance from `app.state.geo_service`;
when present, `get_details_by_ip` resolves the client IP into structured geolocation columns
(`country_code`, `subdivision_code`, `district`, `city`, `is_eu`, `as_number`) stored in
`ArenaLoginHistory`. Display names are derived on the model via `LocationMixin`
(`country_name` / `subdivision_name`) and `detailed_location`.
JWT issuance for the full session (LOGIN action) is performed by the route.
The route always issues 1-hour LOGIN JWTs stamped with a `session_started_at` marker, so middleware
can rotate the cookie near half-life for any session and apply the optional
`NOCA_JWT_REFRESH_MAX_SESSION_SECONDS` cap. `remember_me=true` is stored alongside it and controls
cookie persistence only: a 30-day `max_age` instead of a browser-session cookie.
Login refuses accounts with missing date of birth, under-13 age status, or
pending parental consent before issuing a session token.

Those account-state checks live in `evaluate_account_access_gates(usuario)`, which
returns `None` when every gate is clear and otherwise a `UserServiceResult` carrying
the blocking status and the user. It is the **single** expression of that rule:
`efetuar_login` calls it, the Google callback calls it, and
`arena.dependencies.auth._user_access_gates_are_clear` is defined as
`evaluate_account_access_gates(user) is None`, so a request-time check and a
login-time check cannot disagree about who is allowed in. It is deliberately pure
and silent -- the caller decides when a failure is worth logging, so a wrong-password
attempt never emits the account's age or consent state to the log.

`set_pending_2fa_token` accepts `login_method` (`"password"` by default, `"google"`
from the Google callback) and stores it in the token's extra data. `auth_2fa.py`
reads it back so a completed second factor is recorded as `2fa`/`backup_code` for a
password login and `google_2fa`/`google_backup_code` for a Google one. A token minted
before this field existed reports the password path.

---

### `session_service.py`

Helpers for Arena session cookies, safe login redirects, and sliding-session
token rotation. Every authenticated session rotates at half-life; "remember me"
affects cookie persistence only.

| Function | Description |
|----------|-------------|
| `build_current_next_url(request)` | Builds a safe current request target from path plus query string only. |
| `safe_next_url(next_url, request)` | Accepts same-origin path targets and falls back to `arena_dashboard` for missing or unsafe values. |
| `build_login_redirect_response(request, next_url, status_code)` | Builds a `303` login redirect and includes `next` only when the value is safe. |
| `write_flash_message(request, message, category)` | Writes a `fastapi_flash`-compatible message directly into the Starlette session. |
| `build_login_token_extra_data(tid, remember_me, session_started_at)` | Builds `LOGIN` JWT extra data for session identity and rotation. Always stamps `session_started_at`, so the absolute cap can be applied to any session. |
| `build_refreshed_login_token(jwt_service, validation)` | Issues a replacement `LOGIN` token for any active session inside the refresh window, unless the configured absolute cap has been exceeded. |

The login redirect helpers are used by protected browser pages and by the
Arena `HTTPException` handler. They keep redirect targets same-origin-only:
absolute URLs and protocol-relative URLs, such as `//evil.example`, fall back
to the dashboard. Completed password, 2FA, and forced-password-change login
flows send the user straight to the safe next destination; Arena no longer
gates login on profile completeness.

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
| `solicitar_reset_senha(email, session, jwt_service, email_service, url_base)` | Send reset link. Always answers `SUCCESS`: the email is budgeted per **recipient** (`recipient:<addr>`), and a spent budget or a provider failure is logged and swallowed, since any distinguishable answer would be an enumeration oracle. |
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

### `admin_terms_service.py`

Admin-only service backing the platform-wide Terms of Service re-acceptance
reset. Publishing new Terms of Service or a new Privacy Policy retires every
acceptance already on file -- what each user agreed to no longer exists -- so
this module reads the current figures and clears them all at once.

**`TermsAcceptanceStats`** -- frozen dataclass with `total_users`, `accepted`,
`pending`, and `last_accepted_at`.

**`get_acceptance_stats(session) -> TermsAcceptanceStats`**

One aggregate query splitting the user base into accounts that currently accept
the documents on file and accounts already awaiting acceptance, plus the most
recent acceptance timestamp.

**`count_pending_reset(session, *, exclude_user_id=None) -> int`**

How many rows a reset would clear right now, used to state the blast radius on
the page and in its confirmation prompt before the admin commits to it.

**`reset_all_acceptances(session, *, actor_user_id=None) -> int`**

Clears `aceitou_termos_privacidade` and `dta_aceitacao_termos_privacidade` in
one bulk `UPDATE`, returning the affected row count. The caller owns the commit,
so the `admin_action` audit row lands in the same transaction as the reset.

A single statement rather than a per-row loop for two reasons: the reset must be
atomic, since a partial one would leave two populations bound to two different
documents, and it must stay affordable on a large install.

The predicate matches a row holding **either** the flag or the timestamp, not
the flag alone, so a row that somehow carries a date without the flag cannot
survive the reset still pointing at a document that no longer exists.

`actor_user_id` names the acting admin, whose acceptance is **re-dated to now**
rather than cleared. Clearing it would sign them out of the operation they are
running, and Arena offers no way to re-accept from the profile page. Re-dating
records the true fact anyway -- the person publishing the new documents accepts
them at that moment -- and leaves the stored date pointing at the current
documents rather than at the retired ones. The returned count covers the
*other* accounts only.

The same statement always bumps `session_version`, mirroring
`user_service.invalidate_sessions` (including its `% 65536` wrap). This ends
each affected live session so the acceptance gate takes effect on the user's
next request instead of waiting for the session cookie to expire.

---

### `lockout_admin_service.py`

Arena's vocabulary for the administrative lockout reset. The shared primitive
(`shared/services/auth_lockout_admin.py`) works on account *hashes*; this
module knows which raw identifiers Arena's throttle buckets hash for one
account, and scopes every subject to the `arena` key module, which is the only
one an Arena administrator may clear.

**`ARENA_LOCKOUT_MODULES = ("arena",)`**

**`identifiers_for_user(user) -> list[str | None]`** -- `email_normalizado`
(login, 2FA, password reset, and the activation link's `sub:`),
`email_canonical`, `user.id` (`password_verify`, `2fa_confirm`), and the
`sub:<id>` / `sub:<email>` subjects the token-redeeming links carry. Blanks are
dropped by the hasher.

**`hashes_for_user(user) -> frozenset[str]`** -- those identifiers hashed with
`JWT_SECRET_KEY`, deduplicated.

**`resolve_identifier(session, raw) -> ResolvedIdentifier`** -- the typed text is
always hashed (it is what the login and signup forms hashed); when it is a
valid address of a known account, that account's whole recipe is added.
`ResolvedIdentifier.user` is `None` for an unknown address.

**`subject_for_ip(ip)`**, **`subject_for_hashes(hashes)`** -- Arena-scoped
`LockoutSubject` builders.

**`describe_user_lockouts(store, user) -> list[ActiveLockout] | None`** -- the
profile page's status row; `None` when the store cannot answer, which the
template renders as *Status unknown* rather than *Not locked*.

**`list_lockout_overview(store, session) -> LockoutOverview | None`** -- lists
all active Arena lock keys and groups IP buckets by address. Account subjects
are non-reversible HMACs, so the service resolves only the active hashes through
the indexed `arena_user_throttle_hashes` mapping in batches of 1,000. It never
scans or hashes the user table on a request. The mapping is many-to-many: when
email canonicalization makes one hash valid for two users, both emails receive
the lock in stable user-id order. The overview reports how many account
identifiers could not be matched instead of guessing or exposing an unknown
login.

The wording and the audited flow live in the shared
`auth_lockout_flow.py`, so Web and Arena record an unlock identically.

---

### `user_throttle_hash_service.py`

Maintains the forward mapping from the HMAC identifiers stored in Arena
authentication throttle keys to registered users. The
`arena_user_throttle_hashes` table has a composite primary key over the secret
generation, identifier hash, and user id, so lookups are indexed while
legitimate canonical-email collisions remain many-to-many. The generation is a
4-byte foreign key into `arena_throttle_secret_versions`, one row per distinct
`JWT_SECRET_KEY`, rather than a 64-character fingerprint repeated on every
mapping row; deleting a generation cascades to its mappings.

**`current_secret_version_id(secret=None)`** -- a scalar subquery selecting the
active generation's id. Readers embed it instead of resolving the id first, so
one indexed lookup on the unique fingerprint keeps the equality on the leading
primary-key column. It matches no rows when the secret has never been indexed.

**`refresh_user_throttle_hashes(session, user)`** -- replaces one user's rows
inside the caller's transaction. Registration calls it after the user insert,
so a committed account and its lookup rows become visible together.

**`rebuild_user_throttle_hash_index(session_factory)`** -- runs before Arena
starts serving, and is gated so that a boot which changes nothing costs one
indexed anti-join. The gate asks directly whether any user is missing from the
current generation, so it assumes nothing about how many hashes a user yields
and stops at the first uncovered row; each probe is an index lookup on
`ix_arena_user_throttle_hashes_user_version`. A crashed rebuild leaves no
partial state to mistake for coverage because a rebuild commits exactly once. When the counts disagree -- a backfill after the migration, a
`JWT_SECRET_KEY` rotation, or a genuine gap -- it streams user identities in
batches of 1,000, computes each batch's HMACs in a worker thread, writes the
replacement transactionally, and then removes obsolete generations, whose
mapping rows follow by cascade.

Gating matters because the alternative is not free: rewriting every row on every
boot makes readiness scale with the user count and leaves a full table's worth
of dead tuples behind each restart. With the gate the table is genuinely
low-churn -- it changes on user creation or deletion, on a login-identity
change, and in one bounded rebuild after a backfill or rotation -- so
server-wide autovacuum defaults are sufficient.

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
by `nome`, `email_normalizado`, or the structured geolocation columns (`city`,
`district`, `subdivision_code`, `country_code`) in the same WHERE clause. Full
country *names* are not searchable since only the ISO code is stored.
Two-step loading: a Core ID query (with JOIN for filtering) followed by an ORM
`selectinload(ArenaLoginHistory.arena_user)` query so relationship access works
in templates.

---

### `admin_submission_service.py`

Admin-only service for listing all Arena submissions across all users on the
admin dashboard.

**`AdminSubmissionListRow`** — frozen dataclass with fields: `submission_id`, `user_id`, `user_name`, `problem_number`, `problem_title`, `language_id`, `language_name`, `submitted_at`, `verdict` (None when pending), `status`, `submit_to_ai`, `has_ai_review`, `avatar_revision` (appended as `?v=` to the row's avatar URL so the admin list hits the browser cache instead of refetching every avatar per view).

**`list_submissions_paginated(session, *, page, per_page, search, verdict_filter, status_filter, ai_filter, language_filter, problem_filter, sort_dir="desc", date_from_utc=None, date_to_utc=None) → Pagination[AdminSubmissionListRow]`**

Delegates to `build_arena_submission_query(include_user=True, ...)`.
`status_filter` matches the active judgment status, which lets the dashboard
find internal `FAILED` judge/backend errors that don't have a verdict. The
`ai_filter` parameter filters the `submit_to_ai` boolean flag on the submission
itself (not completed review presence). `problem_filter` is an exact match on
`cast(arena_number, String)` — "10" matches only problem 10. `sort_dir`
(`"asc"`/`"desc"`) orders by `created_at` (with a stable secondary key on
`id`); `date_from_utc`/`date_to_utc` bound `created_at` (inclusive lower,
exclusive upper). User columns appear at indices 14 (nome) and 15 (user id).

**`reenqueue_failed_submission(session, *, submission_id) → ArenaSubmissionJob | None`**

Loads the submission's most recent judgment `FOR UPDATE` (so an overlapping second request waits and then finds it already `QUEUED`) and resets it from the terminal `FAILED` state back to `QUEUED` — clearing `autojudge_verdict`, `final_verdict`, `compile_log`, `error_message`, `worker_id`, `started_at`, `finished_at`, `max_wall_time_ms`, `max_memory_kb` — and returns a fresh `ArenaSubmissionJob` (requeue_count 0) to enqueue. Resets the existing judgment in place (no supersede) because `FAILED` produced no verdict or test results, so no orphan judgment is left. Returns `None`, leaving the session unchanged, when the submission is missing or its latest judgment is not `FAILED`. The caller owns the transaction: commit first, then call `enqueue_arena_submission_job`.

**`force_rejudge_arena_submission(session, *, submission_id) → ArenaSubmissionJob | None`**

Forces a fresh judgment for a submission in any active state (used by the ARENA_ADMIN "Force rejudgment" action). Marks the active (most recent non-superseded) judgment `SUPERSEDED` and inserts a new `QUEUED` judgment, then returns a fresh `ArenaSubmissionJob` (requeue_count 0) to enqueue. Unlike `reenqueue_failed_submission`, it supersedes rather than resetting in place, so it works on a `DONE` submission that already produced a verdict while preserving the previous judgment's history; the Arena detail page shows the most recent non-superseded judgment, so the new queued judgment becomes the displayed one. Returns `None`, leaving the session unchanged, when the submission is missing or has no non-superseded judgment. The caller owns the transaction: commit first, then call `enqueue_arena_submission_job`.

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

Two-query paginated list (count + data). Uses `selectinload(ArenaUser.affiliation)` to avoid N+1. Searches the legal name, the **public handle** (`username`), the account email, and the legal-guardian email with `ilike`. Orders by `nome ASC`.

The handle is searchable, and the list renders it and the unmasked address, because the age shield is a *public* read-path rule: it governs what anonymous and peer surfaces publish, not what an operator may see. An admin acting on a report has only the handle to go on, since that is the only name the reporter could have seen. The corresponding public search stays shielded through `identity_search_service.prepare_public_user_search()`.

**`count_admins(session) → int`**

Count of all Arena users with `role == ARENA_ADMIN` (active or not). Used by the last-admin guard in route helpers.

**Mutation functions** — each accepts `(usuario: ArenaUser, session: AsyncSession)` and calls `session.flush()`:

| Function | Effect |
|---|---|
| `change_role(usuario, new_role, session)` | Sets `usuario.role = new_role` |
| `toggle_active(usuario, session)` | Deactivate: `desativar_conta + invalidate_sessions`; activate: `ativar_conta` |
| `toggle_force_password_change(usuario, session)` | Set: `marcar_para_trocar_senha`; clear: clears `precisa_trocar_senha` + `dta_marcacao_troca_senha` |
| `toggle_can_edit(usuario, session)` | Flips `usuario.can_edit`, the admin-granted permission to add/edit Arena problems |
| `toggle_ranking_visible(usuario, session)` | Flips `usuario.ranking_visible`; when False, the user is hidden from all public ranking lists and excluded from affiliation rating computation. Auto-clears `public_profile` when hiding the user and returns `True` to signal the side-effect to the caller |
| `toggle_public_profile(usuario, session) → str \| None` | Flips `usuario.public_profile` opt-in; blocks enabling when `ranking_visible` is False **or when the account is age-shielded** and returns the human-readable reason (returns `None` on success). Disabling is never blocked, so a stale flag can always be cleared. An admin may not override the age shield: it is a legal control, not a moderation control |
| `admin_remove_photo(usuario, session)` | Calls `usuario.clear_foto_fields()` |
| `admin_disable_2fa(usuario, session)` | Calls `desativar_2fa + invalidate_sessions` |
| `admin_unlink_google(usuario, identity, session)` | Bumps `avatar_revision` when the Google avatar was selected, calls `google_identity_service.unlink_identity`, then `invalidate_sessions`. Deliberately **not** guarded by `has_usable_password`: the last-method guard protects a user from locking themselves out, and an administrator detaching a credential does so on purpose, with the password reset as the account's recovery path |
| `admin_change_name(usuario, new_name, session)` | Sets `usuario.nome`; raises `ValueError` if empty |
| `admin_change_username(usuario, new_username, session, *, allow_immediate_change=False) → str` | Renames the public handle through `username_service.change_username(bypass_cooldown=True)`; returns the **previous** handle so the caller can name both in the audit trail. `allow_immediate_change` clears the target's cooldown instead of restarting it. Raises `UsernameError` / `UsernameTakenError` |
| `admin_remove_location(usuario, session)` | Sets `country_code = None`, `subdivision_code = None` |
| `admin_remove_affiliation(usuario, session)` | Sets `affiliation_id = None` |
| `admin_reset_api_key(usuario, session)` | Sets `ai_api_key = None` (clears encrypted personal AI API key) |
| `admin_toggle_email_confirmed(usuario, session)` | Confirm: delegates to `user_service.confirmar_email`; unconfirm: clears `email_confirmado` + `dta_validacao_email` |
| `admin_toggle_parental_consent(usuario, session)` | Grant or withdraw consent through the **same** `user_service` write paths the guardian's own link uses, so an admin revocation is never weaker than a guardian revocation: it deactivates the account and kills live sessions, and a grant re-activates it when the other gates are clear. Both branches bump `consent_generation`. Returns a `ConsentToggleOutcome(granted, activated)` so the route audits what actually happened rather than what it assumed |
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

Field-level rules (slug normalization and stop words, `#RRGGBB` color, 128-character
limits) live in `taxonomy_validation.py` and are shared with collections;
`normalize_slug` is re-exported here for callers that already import it from this path.

---

### `taxonomy_validation.py`

Field rules shared by Arena's flat taxonomies — categories and collections. Both share the slug and length rules; the color helpers serve categories, the only taxonomy with a badge color.
A problem has many categories and at most one collection, but their `name`, `slug`,
and `color` fields obey exactly the same rules, so those rules live here and the two
cannot drift apart.

| Symbol | Description |
|---|---|
| `MAX_FIELD_LENGTH`, `COLOR_PATTERN`, `SLUG_PATTERN` | The shared field caps and shapes |
| `SLUG_STOP_WORDS` | Portuguese + English function words dropped from slugs; mirrored in `arena/static/js/taxonomy-slug.js`, which must stay in sync |
| `normalize_slug(value)` | Strip diacritics and stop words, join the rest with hyphens |
| `validate_required_text(value, field_name)` | Strip and length-check a required field |
| `validate_slug(raw_slug)` | Normalize a slug and enforce its shape |
| `validate_color(raw_color)` | Normalize and enforce the 6-digit hex format |
| `random_badge_color()` | A vivid, readable random badge color for a new row |

---

### `admin_collection_service.py`

Admin-only service for Arena collection CRUD. A collection is an event (ICPC,
Maratona SBC, InterIF) or a class (Iniciantes, Expressões regulares), and a problem
belongs to **at most one** — the link is the nullable `arena_problems.collection_id`
column, not a junction table, so the cardinality is enforced by the database.

**`list_collections_paginated(session, *, page, per_page, sort_by, search) → Pagination[CollectionListItem]`**

Two-query paginated list (count + data), with `problem_count` from an outer join on
`arena_problems.collection_id`. Sorts by `name_asc` (default), `name_desc`,
`problems_asc`, or `problems_desc`; `search` is a case-insensitive name substring.

**`validate_collection_data(session, *, name, slug, exclude_id=None) → CollectionFormData`**

Same name and slug rules as categories, via `taxonomy_validation.py`. There is no color field.

| Function | Effect |
|---|---|
| `list_collections(session)` | Every collection ordered by name, for pickers and filter dropdowns |
| `get_collection(session, collection_id)` | Fetch a collection by ID |
| `get_collection_by_slug(session, slug)` | Fetch by normalized slug; `None` for a blank slug or a miss |
| `get_problem_count(session, collection_id)` | Count the problems filed under it |
| `create_collection(session, *, name, slug)` | Validate, create, and flush |
| `update_collection(session, collection, *, name, slug)` | Validate, update, and flush |
| `delete_collection(session, collection)` | Delete it; its problems are **unfiled** by the `SET NULL` FK, never deleted |

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
scoped to `caller_id`; judges may only see and mutate their own problems. Suggestion autocomplete is the
exception: it can reuse values from any enabled problem and from the caller's own disabled drafts.

**Key behavior**

- validates titles, sources, authorship, licenses, limits, Markdown statements, and optional editorials before persistence
- creates new problems with `enabled=False`
- updates category links through direct SQL on `arena_problem_category_map`
- supports sorting by relevance, title, public number, and rating
- supports OR-semantics category filtering for the admin problem list

**Public API:**

| Symbol / Function | Description |
|---|---|
| `ProblemListItem` | Immutable list projection containing only the problem ID, public number, title, enabled state, public/private test-case counts, an evidence-gated `difficulty: DifficultyDisplay`, rendered categories, custom-validator marker, and editorial presence/release-policy markers. |
| `list_problems_paginated(session, *, page, per_page, search, category_ids, category_slugs, collection_id, owner_id, language, enabled, editorial, sort_by, caller_id, is_admin)` | Paginated problem list with shared weighted PostgreSQL full-text search over title/source/statement/free-text author, trigram substring fallback over every text field, fuzzy trigram matching over title/source/resolved author, and compatible number matching. Owner-backed author names use the separately indexed `arena_users.nome` trigram path because they cannot participate in the problem-row FTS expression index. Search defaults to deterministic relevance order; callers can select another sort. Optional filters cover admin-only owner, OR category IDs or slugs, `StatementLanguage`, enabled/disabled state, and editorial state (`none` for no editorial text, or a release policy for problems that have editorial text). The count remains filter-only; categories, test-case counts, and validator markers are loaded with bounded page-ID queries. |
| `get_problem(session, problem_id, *, caller_id, is_admin)` | Fetch one problem with categories and test cases, applying owner scoping for non-admin editors. |
| `get_problem_definition(session, problem_id, *, caller_id, is_admin)` | Fetch the definition-editor profile with categories only, applying the same owner scope while deliberately excluding test cases, validator, and sample interactions. |
| `create_problem(session, *, caller_id, author, author_is_owner, license, statement_language, editorial=None, editorial_release_policy=ArenaEditorialReleasePolicy.NEVER, expected_difficulty=None, validator_type, ...)` | Validate and create a disabled problem owned by `caller_id`. Stores owner-backed or free-text authorship, optional license and editorial metadata, an optional `StatementLanguage`, the editorial release policy (defaulting to `never`), the optional author `expected_difficulty` (internal 1–100, validated in range; the rating worker uses it as the solve-rate prior), category links, and an optional `collection_id`. Blank editorial content becomes `None`; nonblank content uses the statement Markdown restrictions. `validator_type` is required and immutable. |
| `update_problem(session, problem, *, author, author_is_owner, license, statement_language, editorial=None, editorial_release_policy=ArenaEditorialReleasePolicy.NEVER, expected_difficulty=None, validator_type=None, ...)` | Validate and update mutable fields without transferring ownership. `expected_difficulty` is stored as given (`None` clears the estimate). Owner-backed authorship clears the free-text author; blank license and editorial values become `None`; `statement_language` is already resolved by `statement_language_service`; the editorial release policy (defaulting to `never`) is stored as given. A differing `validator_type` is rejected. An optional `collection_id` files the problem under a collection (blank or `None` unfiles it). |
| `toggle_enabled(session, problem)` | Flip the problem `enabled` flag and refresh `updated_at`. |
| `_resolve_collection_id(session, collection_id)` | Validate a submitted `collection_id` before assignment. Blank normalizes to `None`; an ID matching no collection raises `ValueError` so a stale form becomes an ordinary field error rather than a database integrity error. Used by both `create_problem` and `update_problem`. |
| `delete_problem(session, problem)` | Delete a problem and all its dependent data. Deletes submissions first (cascading to judgments, test results, AI reviews, batch jobs) then the problem itself (cascading to test cases, category map, ratings, solvers, tried, favourites, rating history). Returns the `arena_number` for flash messages. Caller commits. |
| `list_owners(session)` | Return administrators and users with `can_edit=True`, ordered by display name, for the owner filter. |
| `search_categories(session, *, query, limit=15)` | Case-insensitive category search; consumed by both the JSON autocomplete API (`GET /admin/problems/categories/search`) and server-side `selected_cats_data` pre-population. |
| `search_problem_suggestions(session, *, field, query, caller_id, is_admin)` | Return at most 15 distinct, trimmed source, free-text-author, or license strings for the create and edit problem forms, in field-specific relevance/name order. Each whitespace-separated term of the query (at most 8) is matched independently as a substring, so a term the author has not finished typing, terms in the wrong order, and terms beginning mid-word all match; a query is declined outright (returning `[]`) unless every term carries three letters or digits, since neither the `ILIKE` branch nor the `%` similarity branch can be served from `ix_arena_problems_*_trgm` below that and both would degrade to a sequential scan. Admins search all enabled and disabled problems; other editors search all enabled problems plus caller-owned disabled drafts. Null and blank values are omitted, owner-backed authors never appear, and the service always caps results at 15. |

---

### `rejudge_service.py`

Arena bulk rejudge, split out of `admin_problem_service` (at its size ceiling) because the rules are about judgments, not problems.

| Function | Description |
|---|---|
| `build_rejudge_jobs(session, problem_id) -> RejudgeBuildResult` | Lock the problem's `arena_submissions` rows `FOR UPDATE`, then for every submission with **no** judgment outside `TERMINAL_JUDGMENT_STATUSES` mark its non-superseded judgments `SUPERSEDED` and insert a fresh `QUEUED` `ArenaSubmissionJudgment`; return the ready-to-enqueue `ArenaSubmissionJob` list plus `skipped_in_flight`, the count of submissions left alone because a judgment of theirs is still `QUEUED`/`DISPATCHED`/`JUDGING`. That skip is what makes `POST /admin/problems/{id}/rejudge-all` safe to repeat, and the lock is what keeps two overlapping requests from each building a judgment for the same submission. Caller commits then enqueues. |

### `problem_list_query_service.py`

Shared page-scoped projections for the public and admin problem lists. These
helpers never load statements, images, or validator source bodies.

| Symbol / Function | Description |
|---|---|
| `ProblemListCategory` | Immutable rendered category projection containing `name`, `color`, and `foreground_color`. |
| `categories_by_problem_id(session, problem_ids)` | Return one page of category projections grouped by problem ID. |
| `configured_validator_problem_ids(session, problem_ids)` | Return page problem IDs with a non-null active or candidate validator source, without selecting source contents. |
| `test_case_counts_by_problem_id(session, problem_ids)` | Return public/private test-case counts grouped by problem ID for one page. |

---

### `problem_search_service.py`

This service keeps public and administrative problem search semantics aligned. It shares
its wildcard-escaping, operator-detection, and trigram-threshold rules with the ranking
search through `text_search_primitives.py`.

| Symbol | Description |
|---|---|
| `prepare_problem_search(session, query)` | Return hybrid full-text, literal substring, fuzzy name, exact-number, and ranking expressions for a normalized query. PostgreSQL uses one weighted FTS expression index plus trigram indexes (including the Arena-number text expression), parses the query with each row's statement-language configuration, and combines independently indexable candidate branches with `UNION`. Queries containing negation, quoted phrases, or `OR` disable raw fallback matching so web-search operators remain authoritative. SQLite uses a portable substring predicate and resolves relevance ties by Arena number for unit tests. Submission-history filters remain deliberately narrower. |
| `ProblemPickerSearchExpressions` / `prepare_problem_picker_search(session, query)` | Return the narrow number-and-title predicate and ranking expressions used by problem-set autocomplete. PostgreSQL narrows each language-specific weighted FTS index branch to title matches, adds escaped number/title substring branches and a 3+-character title-trigram branch, and suppresses fallback branches for web-search operators. Number matching is intentionally substring-based for every query, so `42` includes `142` and `420`, but exact Arena number `42` ranks first. Remaining ordering uses FTS match, FTS rank, title similarity, and Arena number. The call site must wrap `exact_number_match` in an integer `CASE` before descending ordering: for nonnumeric queries the expression is a constant `false`, which PostgreSQL and SQLite reject as a bare `ORDER BY` term. SQLite keeps the same escaped title/number substring scope with exact-number-first ordering. |
| `ProblemSuggestionField` / `prepare_problem_suggestion_search(session, field, query)` | Typed author, license, and source autocomplete expressions. PostgreSQL treats `query` as literal text with `plainto_tsquery`. Author and source searches narrow through the weighted composite problem-search FTS index and then confirm the requested field; license searches use a dedicated `simple`-configuration FTS expression index so licenses don't become part of general problem search. Separate `UNION` branches provide literal substring and 3+-character fuzzy matching through each field's trigram index. SQLite uses field-only escaped-substring matching. |

---

### `admin_problem_interaction_service.py`

Admin/judge service for Arena **sample interactions** — the worked conversations an
interactive problem shows instead of sample test cases. Unlike test cases they live only in
the database, so nothing here returns a post-commit filesystem callback. All parsing and
format rules come from `shared.services.sample_interactions`; this module owns only the SQL.

**Public API:**

| Function | Description |
|---|---|
| `list_interactions(session, problem_id, *, include_hidden=False)` | Interactions ordered by `ordinal`. Hidden ones are excluded unless asked for. |
| `count_interactions(session, problem_id)` | Count every interaction, hidden ones included (this is what the cap is judged against). |
| `create_interaction(session, problem, *, transcript, explanation=None)` | Append at the next ordinal. Raises `ValueError` past `MAX_SAMPLE_INTERACTIONS`. |
| `update_interaction(session, interaction, *, transcript, explanation=None)` | Replace transcript and explanation in place. |
| `delete_interaction(session, interaction)` | Delete one and close the ordinal gap. |
| `move_interaction(session, interaction, new_ordinal)` | Move to a clamped 1-based ordinal, rewriting the order densely. |
| `hide_interactions(session, problem_id)` / `unhide_interactions(session, problem_id)` | Set / clear `hidden_at` for the whole set. Hiding is what "keep" does when a validator is removed; staging a validator again un-hides. |
| `delete_all_interactions(session, problem_id)` | Permanently drop the whole set, hidden ones included. |
| `convert_sample_testcases_to_secret(session, problem_id)` | Demote the problem's public test cases to secret. Called whenever a validator is staged. |
| `interactive_testcase_error(session, problem_id)` | Why an interactive problem's test cases are invalid (a public case, or no secret case at all), else `None`. Used by the enable gate. |

Inline transcript parsing and rejected-row retention are shared with Contest in
`shared.services.interaction_pending_ops`; see `docs/SHARED_SERVICES.md`. Parsing remains a separate
step so a malformed transcript is returned beside its original row **before** anything is written.

### `admin_problem_tc_service.py`

Admin/judge service for Arena test-case management. Test-case content lives on the shared filesystem
under `<root>/arena/<problem_id>/NNN.in|out`; the database row stores only metadata and the normalized
(LF) on-disk byte sizes (`input_size_bytes` / `output_size_bytes`). All functions that touch content
take a `testcase_dir` (the Arena root, `settings.PROBLEM_TESTCASE_DIR`).

**Key behavior**

- ordinals are 1-based and contiguous per problem; deletes and moves renumber both rows and files in lockstep
- inline create/edit is gated: a normalized side larger than `MAX_INLINE_TESTCASE_BYTES` (10 KB) raises `ValueError`; large cases use the offline single-case ZIP download/replace path (no cap)
- ZIP replace deletes all existing rows and files and rebuilds the set from the parsed archive (no cap)
- on an **interactive** (custom-validator) problem a case carries **input only**: any submitted
  expected output is discarded, no `.out` file is written, and `output_size_bytes` is stored null.
  The service decides this itself from `is_interactive()`, so routes never have to
- file helpers delegate to `shared.services.testcase_files`, which validates
  UUID/slug-like problem ids and verifies resolved paths stay under the Arena
  test-case root

**Public API:**

| Function | Description |
|---|---|
| `list_testcases(session, problem_id)` | Return all test cases for one problem ordered by `ordinal`. |
| `list_testcase_views(session, problem_id, testcase_dir)` | Lightweight per-case views (`id`, `ordinal`, `is_sample`, `has_explanation`, `input_preview`, `output_preview`, `input_size_bytes`, `output_size_bytes`, `is_large`) — previews read from disk, no full-content load. |
| `get_testcase(session, tc_id, *, problem_id)` | Fetch one test case scoped to its parent problem. |
| `is_interactive(session, problem_id)` | Whether the problem's **stored strategy** is interactive, so its cases carry no expected output. Never derived from validator source presence: a problem that lost its source stays interactive, and a stale validator row never makes a standard problem interactive. |
| `judgeability_facts_for(session, problem)` | Gather the shared strategy, case, expected-output, secrecy, and active-validator facts used by execution gates and by the judgment editor's readiness summary. |
| `judgeability_error_for(session, problem)` | Why the problem cannot judge submissions yet, else `None`, via the shared cross-domain contract in `shared/services/problem_judgeability.py`. Used by the enable gate, so enablement, submission creation, and the worker cannot disagree about what "ready" means. |
| `create_testcase(session, problem, *, input_content, output_content, is_sample, explanation=None, testcase_dir)` | Append a new test case (next ordinal); write files + sizes. On an interactive problem the output is discarded **and `is_sample` is forced false**. Raises `ValueError` if a normalized side exceeds `MAX_INLINE_TESTCASE_BYTES`. |
| `update_testcase(session, tc, *, input_content, output_content, is_sample, explanation=None, testcase_dir)` | Overwrite files + sizes, sample flag, explanation. Same interactive rule and inline size gate. |
| `replace_single_testcase(session, tc, *, input_bytes, output_bytes, explanation, testcase_dir)` | Replace one case's content from an offline upload (no size cap). `output_bytes=None` for an interactive problem. |
| `toggle_sample(session, tc)` | Flip the sample/secret flag without touching content and bump `updated_at`. Raises `ValueError` on an **interactive** problem: such a problem shows sample interactions, so none of its cases may be public. |
| `delete_testcase(session, tc, *, testcase_dir)` | Delete one test case + files, then renumber remaining rows and files contiguously. |
| `move_testcase(session, tc, new_ordinal, *, testcase_dir)` | Move one test case to a clamped 1-based ordinal; reorder rows and files. |
| `replace_all_from_zip(session, problem, zip_bytes, *, default_is_sample=False, testcase_dir)` | Replace the full set from a ZIP parsed by `shared.tc_zip.parse_testcases_zip` (with `require_output=False` on an interactive problem); writes files + sizes (no cap). |

---

### `admin_problem_tc_pending.py`

The Arena half of joining a Save's test-case plan to rows. The plan itself is decided in
`shared.services.testcase_save_plan`, which knows nothing about either module's models;
`web.services.problem_edit_save` is the Contest half.

Filesystem work is no longer deferred to post-commit callbacks. Every case the Save wants already
exists in a staging directory by the time these rows are written, and the artifact swap renames
that directory in as part of the commit — so a rolled-back Save cannot leave rows describing files
that were never written, nor files describing rows that were never committed.

| Function | Purpose |
|----------|---------|
| `load_current_cases(session, problem_id)` | The planner's view of the problem's rows, in ordinal order. |
| `apply_materialized_cases(session, problem, materialized)` | Make the rows describe the staged directory exactly: delete the dropped ones, push survivors through a disjoint temporary range (`(problem_id, ordinal)` is unique, so a direct renumbering collides midway), then take the final 1..n positions and append the added rows. |

---

### `admin_problem_validator_service.py`

Stages custom-validator candidates for the judgment-data validator page, which applies an upload
immediately. Split so a bad upload can be rejected before any database write. The caller owns the transaction: staging returns the queue payload to
enqueue *after* the commit, so a delayed worker never sees a token that was rolled back.

| Function | Purpose |
|----------|---------|
| `parse_validator_upload(session, *, language_id, source_file)` | Read and check the upload without touching the database. Returns a `ValidatorUpload`, or `None` when neither field was supplied. Raises `ValueError` if only one of language/file is given, the language is not globally active, or the source is not valid UTF-8. |
| `stage_candidate_revision(session, problem, upload)` | Stage the parsed upload as the problem's candidate revision and return its `CustomValidatorValidationJob`. Raises `ValueError` if the problem's stored `validator_type` is not `INTERACTIVE`, or if a validator is already configured — two independent axes, so an interactive problem whose source was removed may still upload a replacement. A validator's test cases parametrize it, so they may be secret like any other problem's — there is no all-samples rule. |
| `stage_validator_source(session, problem, *, language_id, source_file)` | Parse and stage in one step, for callers that have a persisted problem already. |

---

### `admin_problem_io_service.py`

Arena's thin adapter over the shared problem-package subsystem
(`shared/services/problem_package/`), which owns the entire format: archive safety, coercion,
defaults, field widths, UTF-8, the integrity manifest, and the export field set. What remains here
is what only Arena knows.

**Key behavior**

- consumes an already-validated, frozen `ProblemPackage`; the route spools the upload to disk and
  the shared reader stages its payloads, so nothing is held in RAM
- sets the importing user as owner and preserves a non-empty package author as free text;
  packages without an author use owner-backed authorship
- applies the package's `sample_testcases` to decide which imported cases are public
- links only existing categories, matched by name or slug; unknown ones are dropped and reported
  as a structured `PackageWarning` the route flashes
- resolves the statement language: a stated `pt`/`en`/`es` is authoritative, otherwise it is
  detected from the statement, and the outcome is reported through
  `ArenaProblemImportResult.language_source` (`package` / `detected` / `undetermined`) so the route
  can ask the importer to confirm it
- refuses a package whose statement is a PDF — Arena stores Markdown statements
- orders its writes through the shared `ArtifactPromoter`: rows are flushed, test-case files are
  promoted, and only then is the transaction committed; a failed commit deletes what was promoted.
  This replaces the old order, which committed rows *before* writing files
- reconciles any stale import journal before starting, so a crashed earlier import is cleaned up
- exports through the shared writer, which writes to a path on disk and **fails loudly** when a
  stored test-case file is missing rather than shipping an empty member. Contest-only keys
  (`color`, `language_limits`) are still written, as `null` / `{}`
- delegates problem creation to `admin_problem_service.create_problem`

**Public API:**

| Function | Description |
|---|---|
| `export_problem_package(problem, owner_name, testcase_dir, destination, *, profile="full")` | Write the package ZIP to `destination`, reading test-case content from `<testcase_dir>/<problem_id>/NNN.in\|out` and resolving its plain-text author from the problem's authorship mode. Full version-2 exports include optional `editorial.md` with its nested digest and the problem's `editorial_release_policy` as the nested `editorial.release_policy`; `profile="public"` omits both. Validator source is attached only for interactive problems. |
| `import_problem_package(session, package, *, caller_id, image_service, testcase_dir)` | Persist a validated `ProblemPackage`; promotes test-case files under `testcase_dir` before committing; resolves the statement language from the package or by detection; sets the new problem's `editorial_release_policy` from the package's nested `editorial.release_policy`, falling back to `never` when the package states none (an unknown value is refused by the shared parser); sets the new problem's `validator_type` from the package's normalized strategy — stated explicitly by a version-2 package, derived from `custom_validator` presence by the shared parser for a version-1 one; returns an `ArenaProblemImportResult` holding the committed `ArenaProblem`, the resolved `statement_language`, its `language_source`, `is_interactive` (read from the stored strategy, not the package), and the structured `warnings` the route flashes. |

---

### `required_announcement_cache.py`

The process-local short-circuit in front of the mandatory-announcement pop-up.
`load_pending_required_announcement` runs on every authenticated HTML page load,
and its answer is user-independent whenever no required Arena announcement
exists at all -- the state almost every deployment is in almost all the time. This
module caches that one boolean per process over the shared `SingleFlightCache`
for `REQUIRED_ANNOUNCEMENTS_CACHE_SECONDS` (30, a constant), so the common page
view costs a dictionary lookup rather than a pooled connection and a query. The
per-user ledger query is unchanged and uncached: it is one index probe per
required announcement and runs only when one exists.

| Symbol | Description |
|--------|-------------|
| `required_announcements_known_absent()` | `True` only when this process holds a live "none exist" answer. Read without building, so the dependency can skip opening a session. Unknown or expired is `False`: unknown is not absent. |
| `required_announcements_exist(session)` | The cached answer, rebuilt on a miss through `has_required_announcements`; concurrent misses share one query. A failed build caches nothing. |
| `invalidate_required_announcements_cache()` | Drops the answer. Called by the admin publish and delete routes **after** commit, and only when the announcement was required -- before commit, a concurrent page load could rebuild from the pre-commit state and pin the stale answer for another TTL. Other replicas rely on the TTL, which bounds how late a mandatory notice reaches their users. |

### `statement_language_service.py`

Owner of everything about a problem statement's natural language: detection, parsing of the
untrusted form/package value, and the rule deciding whether an author's explicit choice may be
committed as-is.

**Key behavior**

- detection uses `lingua`, built once and restricted to Portuguese, English, and Spanish, over a
  cleaned copy of the Markdown (code blocks, inline code, math, URLs and link targets removed) so
  sample code cannot skew the result
- detection is deliberately **unthresholded**: it reports the most probable of the three languages,
  which is why an author's explicit choice is confirmed rather than silently overridden. The only
  guard is a minimum cleaned length (`MIN_DETECTION_CHARS`), below which detection is not attempted
  and the result is `None`; a broken `lingua` installation is infrastructure failure and still
  propagates
- routes and package import must use the async wrapper: detection is CPU-bound and loads models on
  first use. The synchronous entry point exists for the backfill script
  (`scripts/arena/backfill_statement_language.py`), which has no event loop to protect

**Public API:**

| Symbol | Description |
|---|---|
| `LanguageResolved` / `LanguageConflict` | Frozen result types of `resolve_statement_language`: a language ready to persist, or the choice/detection pair still needing confirmation. |
| `parse_statement_language(raw)` | Parse an untrusted value: empty/`None` means "not stated"; anything outside `pt`/`en`/`es` raises `ValueError` with a user-facing message. |
| `safe_statement_language(raw)` | Same parse for *list filters*: an unknown value simply means "all languages" so a hand-typed query string cannot break a page. |
| `clean_statement(statement)` | Strip Markdown noise and truncate before detection. |
| `detect_statement_language(statement, *, title="")` | Synchronous most-probable-language detection; for the backfill script only. `None` when the cleaned text is under `MIN_DETECTION_CHARS`. |
| `detect_statement_language_async(statement, *, title="")` | Threaded detection for every event-loop caller. |
| `resolve_statement_language(*, chosen_raw, confirmed_raw, statement, title="")` | Async decision rule: no selection uses detection; a selection matching detection (or with no detection) is accepted; a disagreement is a `LanguageConflict` unless `confirmed_raw` equals `confirmation_token(chosen, detected)`. |
| `confirmation_token(chosen, detected)` | `"<chosen>:<detected>"` — the acknowledgement is bound to both values so a stale hidden field cannot authorize a different language. |
| `conflict_context(conflict)` | Template context for the confirmation modal: both values, both labels, and the token. |

---

### `output_diff.py`

Bounded side-by-side comparison of a student's stored output with the expected
output, rendered by `_partials/_submission_output_diff.html` on the submission
detail page and the teacher's batch-feedback page. Pure: no I/O, no ORM, no
settings.

Why it exists: the pages used to render the failing case's **whole** expected
output, sample or secret, which handed a secret case's answer to anyone who got
Wrong Answer on it. Hiding it instead would teach nothing on an educational
platform. The comparison is bounded on both sides before it is built: the
student's side is the judge's persisted `stdout_excerpt` (at most
`NOCA_JUDGE_STDOUT_EXCERPT_BYTES`, default 8 KB), and the expected side is the
`EXPECTED_PREFIX_BYTES` (16 KB) prefix `read_testcase_output_prefix` returns. The
excerpt cap therefore also caps how much of a secret answer any sequence of
wrong submissions can reveal -- see the accepted trade-off in
`docs/ARCHITECTURE_ARENA.md`.

**Constants:** `OUTPUT_MISMATCH_VERDICTS` (`WA`, `PE`: the only verdicts for
which a comparison is built, so no expected-output read happens for TLE, RE,
and the rest), `DEFAULT_EXCERPT_BYTES`, `EXPECTED_PREFIX_BYTES`, `MAX_DIFF_ROWS`
(6), `CONTEXT_LINES` (1), `MAX_LINE_CHARS` (300).

**Dataclasses:** `DiffRow` (`kind` in `equal` / `changed` / `whitespace` /
`missing` / `extra`, plus both sides' line numbers and text) and
`OutputComparison` (the rows to render, total and hidden differing-line counts,
and the flags `actual_cut`, `expected_cut`, `difference_beyond_excerpt`,
`no_visible_difference` that the partial turns into an honest footer).

| Function | Description |
|----------|-------------|
| `build_output_comparison(actual_excerpt, expected_prefix, *, expected_cut)` | Line diff (`difflib.SequenceMatcher`, no junk heuristic) of the two bounded texts. A student excerpt at or past the judge's default cap with no trailing newline is treated as cut and its partial last line dropped; a cut expected prefix likewise drops its partial line, and each side is compared only as far as the other is known. Keeps the first `MAX_DIFF_ROWS` differing pairs with `CONTEXT_LINES` of **unchanged** context (a folded difference never resurfaces as context), marks same-token spacing differences as `whitespace`, and clips lines to `MAX_LINE_CHARS`. |

## Route-to-service mapping

### `arena/routes/announcements.py` and `arena/routes/admin_announcements.py`

Uses the following services (no Arena-side wrapper exists; the routes call the
shared service directly with `domain=AnnouncementDomain.ARENA`):

- **`shared.services.announcement_service`** — `list_announcements` /
  `get_announcement` for the public pages; `create_announcement`,
  `delete_announcement`, `record_announcement_published` (info) and
  `record_announcement_deleted` (warning, only after a successful delete) for the
  admin routes, all audited through `shared.services.admin_audit` with
  `module="arena"` and the admin's `email_normalizado` as the actor label
- **`shared.services.announcement_acknowledgment_service`** —
  `acknowledge_announcement` behind `POST /announcements/{id}/acknowledge`
  (idempotent; `False` for anything that is not a required Arena announcement,
  answered `404`); `pending_required_announcement` behind the app-level
  `load_pending_required_announcement` dependency that feeds the pop-up
- **`arena.services.required_announcement_cache`** —
  `invalidate_required_announcements_cache` after a committed publish or delete
  of a *required* announcement, so this process's page loads see the change at
  once rather than after the cache TTL
- **`arena.routes.safe_redirect.same_origin_referer_path`** — where the
  acknowledge route sends the user back to (fallback: the dashboard)
- **`shared.services.pagination_service.parse_page`** — the forgiving `page`
  query/form value on the list, detail (Back link), and delete routes

### `arena/routes/auth.py`

Uses the following services:

- **`shared.services.auth_rate_limit`** — Valkey-backed failure/lockout
  throttling for login, 2FA, password reset, and the already-registered-email
  signup path; the same IP-only quota (`enforce_resend_throttle` in
  `auth_common.py`) caps the email-resend actions and the pending-session writes
  `POST /auth/update-date-of-birth` and `POST /auth/accept-terms`
- **`shared.services.request_rate_limit`** — two per-IP fixed windows on
  `POST /auth/signup`: the `arena:signup-requests` custom route wrapper runs
  before FastAPI parses form or multipart input, while the `arena:signup`
  attempt limit runs after free form validation and before the account lookup,
  user insert, activation email, and IPQualityScore lookups
- **`shared.services.sse_connection_limit`** — the `arena:sse` concurrent-stream
  lease held by `arena/dependencies/sse_limits.py` on both Arena SSE routes (see
  *Dependencies* below)
- **`shared.services.security_events`** — Persistent records for lockouts,
  repeated failures, duplicate signup submissions, and suspicious token/session
  mismatches
- **`user_2fa_service.validar_codigo_2fa()`** — Validate TOTP or backup code during 2FA login (via `POST /auth/2fa`)
- **`user_service.aceitar_termos_privacidade()`** — Record ToS/PP acceptance during the login gate (via `POST /auth/accept-terms`)

### `arena/routes/auth_common.py`

Not a route module. Beyond the throttle and token helpers listed above it owns the
two pieces of the login path that every authentication door must share:

| Function | Description |
|----------|-------------|
| `complete_arena_login(request, session, flash, *, usuario, jwt_service, remember_me, next_url, method)` | The post-authentication gate chain and session issue |
| `render_login_gate_failure(request, flash, *, templates, result)` | Route a blocked login to the remediation flow its status calls for |

`complete_arena_login` runs, in order, the terms gate (`pending_tos_uid`), the 2FA gate
(`pending_2fa_token`, carrying `method` through so the completed login can be recorded
as `google_2fa`), the forced-password-change gate, the password-age flash, the LOGIN
token, the `auth_success` security event, the commit, and the redirect with the
`arena_access_token` cookie. Both `POST /auth/login` and the Google callback call it,
which is what keeps a new door from silently skipping one of those gates. It does
**not** record login history: that call is asymmetric in the existing code
(`efetuar_login` makes it for the password path, `auth_2fa` after the second factor),
so each caller keeps ownership of it.

The forced-password-change gate is skipped for an account with no usable password:
there is nothing to change, and the change-password form requires a current password
such an account cannot supply, so the flow would trap the user.

`render_login_gate_failure` sends an unconfirmed email to the resend prompt, a pending
minor to the guardian-consent prompt, an account with no date of birth to the
reconfirmation prompt, and a blocked or deactivated account back to the login page.
The caller records the failure first, since each door throttles under its own action.

### `arena/routes/auth_google_common.py`

Not a route module. It owns the session markers and helpers the Google route
modules (`auth_google.py`, `auth_google_complete.py`, `auth_google_existing.py`, and
the link/unlink handlers) share, so a marker's meaning is defined in exactly one place:

| Name | Description |
|------|-------------|
| `PENDING_LINK_UID` | Session key: the logged-in account an `/auth/google/link` round trip is for. Consumed by the callback only once that round trip has been redeemed — after throttle admission and a successful authorization — so a `429` or a forged callback leaves the intent in place for the genuine retry |
| `PENDING_SIGNUP_UID` | Session key: "resume the completion form" for a Google-first signup |
| `PENDING_CONSENT_UID` | Session key: "the form is done, a guardian has not yet consented" |
| `PENDING_NEXT_URL` | Session key: the validated post-login destination carried across the round trip; consumed at the same point as `PENDING_LINK_UID`, so a throttled callback keeps its destination for the retry |
| `PENDING_EXISTING_UID` / `PENDING_EXISTING_SET_AT` | Session keys: "fold this never-completed Google-first signup into the account I am about to sign in to", and when that was asked. Replaces `PENDING_SIGNUP_UID` (the two mean opposite things), is read only by the authenticated `link-existing` confirmation page, and expires after `EXISTING_ACCOUNT_MARKER_MAX_AGE_SECONDS` (30 minutes) so a marker planted on a shared browser cannot wait indefinitely for whoever logs in next |
| `set_existing_account_marker(request, orphan_uid)` / `clear_existing_account_marker(request)` | Write and drop that pair together |
| `peek_existing_account_marker(request)` | Read the orphan id **without consuming it**, clearing the pair once stale; the confirmation page and its own `POST` both need it |
| `existing_account_next_path(request)` | The confirmation page's path while the marker is live, or `None`; `GET /auth/login` uses it as its default `next`, which is what lets the whole post-login chain (2FA and forced password change included) deliver the user there without any gate knowing about Google |
| `take_session_value(request, key)` | Read a marker **and consume it**, for state that must decide exactly one request |
| `peek_session_value(request, key)` | Read a marker **without consuming it**, for a page that is revisited |
| `require_google_client()` | Dependency: `404` when the feature is off, the built Authlib client otherwise |
| `record_google_event(...)` | Append a `google_*` security event with the request's client IP and request id |

`PENDING_SIGNUP_UID` and `PENDING_CONSENT_UID` are deliberately two keys rather than one
key plus an inference from account state: the completion `GET` and the waiting-page
`GET` each trust their own marker, so neither has to reconstruct which stage of the
flow the visitor is in. `take_session_value` is the right reader for the link marker
and the signup marker on the callback -- a stale marker must not alter a later login --
while `peek_session_value` is the right reader for the waiting page, which a reload
and its own resend `POST` both need to see again; that marker is cleared explicitly
once its flow resolves, not on every read.

---

---

### `arena/routes/user_security.py`

Uses the following services:

- **`user_2fa_service`** — Full 2FA lifecycle:
  - `iniciar_ativacao_2fa()` — Initiate TOTP 2FA activation
  - `confirmar_ativacao_2fa()` — Confirm TOTP code and activate 2FA
  - `abortar_ativacao_2fa()` — Void the tentative secret when the confirmation throttle locks
  - `desativar_2fa()` — Disable 2FA
  - `validar_codigo_2fa()` — Validate TOTP or backup code
  - `validar_token_ativacao_2fa()` — Validate activation session token
- **`backup2fa_service`** — Backup code management:
  - `gerar_novos_codigos()` — Generate new backup codes
  - `contar_tokens_disponiveis()` — Count remaining unused backup codes
- **`qrcode_service.generate_totp_qrcode()`** — Generate TOTP QR code for authenticator apps
- **`arena.routes.auth_throttle`** — Per-user-id + IP throttling of the TOTP
  confirm (`2fa_confirm` bucket) and of every route that re-verifies the acting
  account's own password (`password_verify` bucket, shared by
  `POST /auth/change-password`, `POST /user/profile/2fa/disable`, the problem
  `rejudge-all` and `delete`, the seven password-confirmed
  `admin_users_actions.py` routes, and the problem-set delete). One bucket for
  all of them, so alternating routes cannot multiply the guess budget.
- **`arena.routes.auth_token_redeem`** — Throttling of the anonymous
  token-redeeming endpoints (`token_redeem` bucket, shared by
  `GET /auth/activate` and `POST /auth/parental-consent`; the consent `GET` is
  a review page that neither counts nor consults the bucket, because a page a
  mail scanner follows must stay free of side effects). Keyed by the token's
  *unverified* `sub` claim, falling back to the raw token, so tampering one
  link accumulates against one account. Three deliberate departures from the
  password buckets: only a rejected token is counted (a working link clicked
  twice costs nothing); the lock refuses bad tokens only, so a genuine link
  still redeems while its bucket is locked and nobody can hold an account's
  activation hostage; and a success clears the account counter but never the
  per-IP one, because the identifier is caller-chosen and the IP bucket is the
  only cap on guessing. Helpers: `build_token_redeem_identity()`,
  `reject_token_redemption()` (lockout page while locked, otherwise counts the
  failure and flashes), `reset_token_redeem_throttle()`.

### `arena/routes/auth_throttle.py`

Route-level helpers, not a service: they wrap `shared.services.auth_rate_limit`
for routes that verify a secret behind a session or pending token.

- `check_verification_throttle(request, session, *, action, user)` — pre-check
  keyed by `user.id` and client IP; records `auth_throttle_lockout` and returns
  the retry-after seconds while locked
- `record_verification_failure(request, session, identity, *, action, user)` —
  counts one wrong secret, records `auth_failure`, and reports whether this
  failure tripped the lockout
- `reset_verification_throttle(request, identity)` — clears the counters once
  the secret verified
- `throttled_response(request, flash, retry_after, *, back_route, message=...)`
  — renders `auth/throttled.html` as a `429` with `Retry-After`; also used by
  the pending-flow writes guarded by `enforce_resend_throttle`

Consumers: `user_security.py` (2FA confirm/disable), `auth_password.py`
(change-password, forced and voluntary), `auth.py` (update-date-of-birth) and
`auth_signup.py` (accept-terms).

### `arena/routes/admin_users.py` (GET routes)

Uses the following services:

- **`admin_user_service.list_users_paginated()`** — Paginated user list for `GET /admin/users`
- **`admin_user_service.get_credit_transactions_paginated()`** — Credits tab for `GET /admin/users/{id}`
- **`admin_login_history_service.list_login_history_paginated()`** — Filtered Login History tab for `GET /admin/users/{id}`

### `arena/routes/admin_dashboard_security.py`

Uses the following services:

- **`shared.services.security_events`** — `list_security_events_paginated()` and
  `list_security_event_filter_values()` for the paginated viewer, scoped to
  `module in (arena, aiassistant)`
- **`shared.services.security_events_export`** — `stream_security_events_csv()`
  and `csv_filename()` for the "Download as CSV" export, which covers the whole
  Arena-scoped log and ignores the page's filters

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
- **`admin_worker_service.remove_worker_from_dashboard()`** — Remove one
  worker's durable and live presence records until its next heartbeat.
- **`admin_worker_service.pause_worker()` / `resume_worker()`** — Implement the
  strict issue ordering: validate (reject unknown classes and `rating` as
  `rejected_bad_request`; `autojudge`, `aiassistant` and `mailer` are pausable,
  and the mailer card shows the mail queue size from `get_mail_queue_size()`), and an empty secret as `rejected_disabled`), commit
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
- **`admin_user_service.toggle_ranking_visible()`** — Public-ranking visibility toggle via `POST /admin/users/{id}/toggle-ranking-visible` (auto-clears `public_profile` when hiding the user)
- **`admin_user_service.toggle_public_profile()`** — Public-profile opt-in toggle via `POST /admin/users/{id}/toggle-public-profile` (refuses to enable when ranking visibility is off, or when the target is age-shielded)
- **`admin_user_service.admin_remove_photo()`** — Photo removal via `POST /admin/users/{id}/remove-photo`
- **`admin_user_service.admin_disable_2fa()`** — 2FA disable via `POST /admin/users/{id}/disable-2fa`
- **`admin_user_service.admin_change_name()`** — Name change via `POST /admin/users/{id}/change-name`
- **`admin_user_service.admin_change_username()`** — Handle rename via `POST /admin/users/{id}/change-username` (bypasses the cooldown; password-confirmed and audited twice). The modal probes `GET /admin/users/{id}/username/available` while the admin types, so a clash is reported before the form is submitted rather than as a flash afterwards
- **`admin_user_service.admin_remove_location()`** — Location removal via `POST /admin/users/{id}/remove-location`
- **`admin_user_service.admin_remove_affiliation()`** — Affiliation removal via `POST /admin/users/{id}/remove-affiliation`
- **`admin_user_service.admin_reset_api_key()`** — Personal API key reset via `POST /admin/users/{id}/reset-api-key`
- **`admin_user_service.admin_toggle_email_confirmed()`** — Email confirmation toggle via `POST /admin/users/{id}/toggle-email-confirmed`
- **`admin_user_service.admin_toggle_parental_consent()`** — Parental consent toggle via `POST /admin/users/{id}/toggle-parental-consent`
- **`admin_user_security_service.send_password_change_required_email()`** — Notification email for `POST /admin/users/{id}/force-password-change`
- **`admin_user_security_service.send_2fa_disabled_email()`** — Notification email for `POST /admin/users/{id}/disable-2fa`
- **`user_ai_credit_service.top_up_ai_credits()`** — Credit top-up via `POST /admin/users/{id}/topup-credits`
- **`user_service.update_date_of_birth()`** — Date-of-birth + age-policy application via `POST /admin/users/{id}/date-of-birth`

### `arena/routes/admin_users_google.py` (POST route)

Uses the following services:

- **`google_identity_service.get_identity_for_user()`** — Resolves the identity to detach for `POST /admin/users/{id}/unlink-google`
- **`google_signup_service.admin_unlink_refusal()`** — Refuses the unlink, with the reason, for an unfinished Google-first signup; the profile page (`admin_users.py`) calls the same function to show that reason in place of the control
- **`admin_user_service.admin_unlink_google()`** — The unlink itself (password-confirmed; allowed for a *completed* Google-only account, unlike the self-service route; audited twice)
- **`user_security_notification_service.send_admin_google_unlinked_email()`** — Notification email, carrying the password-reset link when the account has no usable password

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
- **`admin_problem_service.get_problem_definition()`** — Narrow definition-editor fetch + judge ownership enforcement without loading judgment relationships
- **`admin_problem_service.get_problem()`** — Full fetch + judge ownership enforcement helper for judgment, toggle, and export routes
- **`admin_problem_service.create_problem()`** — Problem creation via `POST /admin/problems/new`
- **`admin_problem_service.update_problem()`** — Problem update via `POST /admin/problems/{id}/edit`
- **`admin_problem_service.toggle_enabled()`** — Enable/disable action via `POST /admin/problems/{id}/toggle-enabled`
- **`admin_problem_tc_service.list_testcase_views()`** — Lightweight test-case list (previews + size badges, `is_large` flag) shown on the problem edit page
- **`statement_language_service.resolve_statement_language()`** — Decides the statement language on save, or refuses the commit until the author confirms a mismatch
- **`statement_language_service.safe_statement_language()`** — Validates the `language` list filter on `GET /admin/problems`

### `arena/routes/admin_problem_io.py`

Uses the following services:

- **`admin_problem_io_service.import_problem_package()`** — Import a problem package via `POST /admin/problems/import`
- **`admin_problem_io_service.export_problem_package()`** — Write the export ZIP for `GET /admin/problems/{problem_id}/export`
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
- **`admin_problem_service.search_problem_suggestions()`** — Field-specific Source, free-text Author, and License suggestions at `GET /admin/problems/suggestions`: admins see all problems; editors see enabled problems plus their own disabled drafts
- **`admin_problem_service.get_problem()`** — Judge/admin access control before returning problem rating-history JSON
- **`statement_language_service.detect_statement_language_async()`** — Statement-language detection JSON endpoint at `POST /admin/problems/detect-language`

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

### `ai_review_request_service.py`

The workflow behind `POST /submissions/{submission_id}/request-ai-review`, kept out of the
route so the handler is auth → cap → outcome → flash/redirect.

| Symbol | Description |
|--------|-------------|
| `AIReviewRequestOutcome` | `NOT_FOUND` (missing or not owned), `COMPLETED` (review row exists), `PENDING` (already flagged — **nothing enqueued**), `INSUFFICIENT_CREDIT` (nothing written), `ENQUEUED` (flag set, credit consumed when on the platform key, committed, one job pushed). |
| `request_ai_review(session, valkey_runtime, user, submission_id)` | Reads the submission row `SELECT … FOR UPDATE` (`submission_lookup()`) before judging its state or consuming a credit, so two overlapping first requests cannot both pass the `submit_to_ai=False` check — the second blocks, then sees the flag. A PostgreSQL row lock, global across replicas; a no-op on SQLite. Commit-then-enqueue is preserved: a crash between the two leaves a flagged row the reconciler recovers. |
| `ai_review_request_verdict(request, user_id)` | `(allowed, retry_after)` from the shared keyed fixed window (`shared.services.request_rate_limit.check_rate_limit`, bucket `arena:ai-review`, key = user id, `NOCA_ARENA_AI_REVIEW_RATE_LIMIT_*`); `AI_REVIEW_RATE_LIMITER` is the in-memory fallback the arena conftest resets. |

Why it is shaped this way:

- **No route-side re-enqueue.** The former "self-heal" pushed a duplicate job on every repeat
  request — `ai:queue:pending` has no dedupe — for free, and re-derived `use_platform_key` from
  the user's *current* key. Recovery of a job lost after commit is the aiassistant reconciler's
  alone; it applies the complete predicate and freezes the key mode from the database.
- **No Valkey `SET NX` enqueue guard.** With the row lock it would be a weaker, fail-open,
  TTL-scoped copy of a control the database already provides, needing cleanup on every
  pre-commit failure.
- **Not `rate_limit_service.py`.** That module counts `arena_submissions` rows and cannot
  express "N review requests per window"; the shared Valkey window can, keyed per user.

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
public live feed (see `live_feed_service.py`), the per-user profile submissions
tab, and an owner's pending submission detail page. The authenticated Arena
surfaces consume that channel through a user-scoped SSE endpoint
(`arena/routes/user_submission_status.py`) which only signals a refresh when one
of the viewer's own submissions finalizes. The browser then refetches the
owner-scoped `status.json` snapshot and updates the visible verdict state in
place. A low-frequency client fallback poll renders intermediate states and
bounds staleness because Valkey pub/sub is not durable. Submission detail fires
confetti only when an SSE refresh resolves to a fresh `AC`, never for an `AC`
that was already present when the page loaded.

---

### `arena_teacher_feedback_service.py`

Teacher feedback on Arena submissions — a sparse 1:1 record keyed by
`submission_id` (`arena_submission_teacher_feedback`). Mirrors the AI-review
shape. All helpers leave transaction ownership to the caller and never commit.

| Function | Description |
|----------|-------------|
| `upsert_teacher_feedback(session, submission_id, teacher_id, feedback_text, feedback_at=None)` | Inserts or replaces the feedback row via dialect-aware `INSERT ... ON CONFLICT (submission_id) DO UPDATE` (PostgreSQL/SQLite). Editing overwrites the text and refreshes `teacher_id`/`feedback_at`. Returns the written `feedback_at` (used by the route to build a per-update notification `source_ref`). |
| `delete_teacher_feedback(session, *, submission_id)` | Deletes the feedback row for a submission, if any. Returns `True` when a row existed and was deleted, `False` otherwise (idempotent). |
| `get_teacher_feedback_text(session, submission_id)` | Returns the feedback text for a submission, or `None` when absent. |

### `arena_problem_set_feedback_service.py`

Owns the single current overall-feedback message for one student in one problem
set. Feedback is statement-grade Markdown with external links allowed; raw HTML
and images are refused. All helpers leave transaction ownership to the caller.

| Function | Description |
|----------|-------------|
| `get_problem_set_student_feedback(session, problem_set_id, student_id)` | Returns the current feedback record, or `None`. |
| `student_has_problem_set_submission(session, problem_set_id, student_id)` | Returns whether the student has at least one submission tied to the set. The teacher drill-down and both feedback mutations share this eligibility predicate. |
| `upsert_problem_set_student_feedback(session, problem_set_id, student_id, teacher_id, feedback_text, feedback_at=None)` | Validates Markdown and inserts or replaces the current feedback through dialect-aware upsert. Returns the written timestamp. |
| `delete_problem_set_student_feedback(session, problem_set_id, student_id)` | Deletes the current feedback idempotently and returns whether a row existed. |

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
`subdivision_code`, `subdivision_name`, `problem_number`, `problem_title`,
`language_name`, `language_icon`, and `verdict`. The route builds affiliation
logo, country flag, Brazilian state flag, and problem URLs plus verdict labels
and badges; the service stays request-agnostic.
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

### `required_announcements.py`

`load_pending_required_announcement(request)` — the second app-level dependency
`arena/main.py` registers beside the authentication gate. On an authenticated HTML
`GET` page load outside `/auth` (no `HX-Request`, `Accept` naming `text/html`) it
reads the user id from `request.state.validated_token` (no user load), returns at
once when `required_announcement_cache` holds a live "no required announcement
exists" answer, otherwise opens a session **only then**, confirms (or rebuilds)
that answer, asks `pending_required_announcement` for the oldest
required Arena announcement the user has not acknowledged (with the pending
count), and stores the answer -- or `None` -- in
`request.state.pending_required_announcement` for the template global
`pending_required_announcement_for`. JSON polls, SSE streams, heartbeats, HTMX
fragments, `POST`s and the login-chain pages never run the query nor borrow a
connection; a `POST` that re-renders a page shows no pop-up for that render and
the next `GET` restores it. A database failure is logged and treated as "nothing
pending" -- the pop-up is not worth failing the page.

### `sse_limits.py`

Holds the `arena:sse` concurrent-connection slots for both Arena SSE streams over
the shared `shared.services.sse_connection_limit` lease.

| Symbol | Description |
|--------|-------------|
| `sse_slot_policy()` | Rebuilt from `settings` on every call (`SSE_LIMIT_ENABLED`, `SSE_MAX_PER_IP`, `SSE_MAX_PER_USER`, `SSE_CONNECTION_TTL_SECONDS`, `SSE_TRUSTED_CIDRS`). |
| `enforce_sse_connection_caps(request, current_user)` | Yield dependency on `GET /live/events` and `GET /user/submissions/status/events`. Takes a per-IP slot and, when `get_streaming_arena_user` resolves a user, a per-user slot keyed on `ArenaUser.id` (an anonymous request holds only the IP slot -- the app-wide gate decides who may connect). Teardown runs on client disconnect and releases both. Refusal is `429` + `Retry-After: 5`; a Valkey outage admits the stream. |

### `export_rate_limit.py`

Per-user fixed-window budgets for Arena's heavy admin exports and teacher
reports (#157), built with `shared.services.request_rate_limit.make_user_rate_limit_dependency`
and keyed through `user_read_rate_limit.arena_user_key`, the one account key every
Arena per-user limiter shares. One bucket per surface, deliberately, so a teacher's
report browsing and an administrator's downloads never share a count.

| Symbol | Description |
|--------|-------------|
| `arena_admin_export_rate_limit` | Bucket `arena:admin-export` (`NOCA_ARENA_ADMIN_EXPORT_RATE_LIMIT_*`, default 20 per 10 min) on `GET /admin/problems/{id}/export` and `GET /admin/dashboard/security-events.csv`. |
| `arena_teacher_report_rate_limit` | Bucket `arena:teacher-report` (`NOCA_ARENA_TEACHER_REPORT_RATE_LIMIT_*`, default 60 per 10 min) on the four `/classes/{id}/problem-sets/...` report routes. |
| `admin_export_policy()` / `teacher_report_policy()` | Rebuilt from `settings` per request, so a knob change (and a test's monkeypatch) takes effect without rebuilding the dependency. |
| `ADMIN_EXPORT_LIMITER` / `TEACHER_REPORT_LIMITER` | Process-local fallbacks used when Valkey is unavailable; both are reset around every test by `tests/arena/conftest.py`. |

Charged on every request, including one the route's own guard then refuses. These
bound *frequency*, not one request's cost.

### `auth.py`

FastAPI dependency for resolving the authenticated Arena user from the session cookie.

| Symbol | Description |
|--------|-------------|
| `ForceLogoutException` | Raised when the token's `tid` claim no longer matches `user.get_token_id()`. Caught by the exception handler in `main.py`. |
| `get_streaming_arena_user(request)` | Regular (non-yield) dependency for SSE routes: opens its own short-lived session, resolves the user exactly like `get_current_arena_user`, and closes the session before the streaming response starts, so an open stream pins no pooled connection. Shared by `/live/events` and `/user/submissions/status/events`. |
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
| `TopRatedUser` | Frozen DTO with `id`, `rank`, `name`, `is_pseudonymous`, `rating`, `confidence`, `solved_problems`, `public_profile`, and `avatar_revision` for presentation-safe user rankings. `name` and `public_profile` are the **resolved** values from `user_visibility_service`: the username unless the user is an adult who opted in to their legal name, and the stored profile flag after the age shield. |
| `TopRatedUser.from_row(row, *, rank)` | The **only** permitted constructor. Resolves the row through `resolve_display_identity()`. Building the dataclass directly is how `name=row.nome` reappears and puts a minor's legal name back on the anonymous dashboard. |
| `_eligible_users_where()` | Shared eligibility predicate list: `ativo=True`, `email_confirmado=True`, and `ranking_visible=True`. Used by both `build_ranked_users_cte()` and `get_top_rated_users()` so all ranking surfaces honour the visibility flag. Deliberately carries **no** age predicate: the shield never removes a minor from the ranking, it only replaces the name they appear under. |
| `get_top_rated_users(session, *, limit)` | Returns active, email-confirmed, ranking-visible Arena accounts of any role ordered by `user_rating` descending, confidence descending, creation date ascending, and id ascending. Equal ratings share the same competition rank. Values of `limit < 1` return an empty list. Selects `username`, `dta_nascimento`, `full_name_public` and `ranking_visible` so every row can be resolved through the age shield — this list is rendered to **anonymous** visitors on `/dashboard`. |

Used by the public dashboard Top Users card; reuse for any compact top-k Arena user ranking.

---

### `ranking_medals.py`

Resolves the medal band shown behind a ranking position.

**Public API:**

| Symbol | Description |
|--------|-------------|
| `arena_medal_band(rank)` | Returns `"gold"`, `"silver"`, `"bronze"`, or `None` for a 1-based ranking position, delegating to `shared.services.balloon_assets.medal_band_for_rank` with the `NOCA_ARENA_RANKING_MEDAL_*_CUTOFF` settings. The cutoffs are read at call time, so the value always reflects current configuration. |

Registered as the `arena_medal_band` Jinja global in `arena/main.py` and consumed by the
`render_rank_medal(request, rank)` macro in `_macros.html` (dashboard leaderboard card,
`/ranking/users`, `/ranking/affiliations`).

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
| `map_reverse_geocode_response(data)` | Maps a Nominatim-compatible JSON response to country/subdivision display data. An answer this build's ISO tables cannot map -- an unknown country code included -- is a result whose fields are all `None`, never an error: the caller sent coordinates, never a country, and the profile page already renders that as "Location could not be detected." |
| `reverse_geocode_location(...)` | Calls the configured reverse-geocoder through `NetworkService` and returns mapped ISO values. Synchronous: callers must not run it on the event loop -- `geocode_service` is the only caller and runs it in a worker thread. **Every** failure of the call becomes one fixed `ValueError` message. `make_json_request` validates the URL, params and headers *outside* its own `try`, so those validators raise plain `ValueError` naming the configured endpoint's scheme, a malformed URL or the User-Agent; the route relays that message verbatim to any logged-in caller, so it is replaced here rather than at the route, where a `ValueError` can no longer be told apart from a coordinate complaint. |
| `validate_coordinates(latitude, longitude)` | Validates a WGS84 pair (finite, in range) and returns it. Extracted so a caller can refuse a bad coordinate before spending any rate-limit budget or building a cache key from it. |
| `search_affiliations(session, query, limit)` | Indexed affiliation autocomplete. Matching delegates to `identity_search_service.prepare_affiliation_search` for full-text, escaped substring, and fuzzy name candidates, then orders with `affiliation_relevance_ordering` so literal and closest matches survive the result limit. Blank queries return no rows. |
| `update_user_affiliation(session, user, affiliation_id)` | Sets or clears the user's selected affiliation. |

Used by the profile Personal Data tab and JSON endpoints in `user_profile_api.py`.

---

### `geocode_service.py`

Throttles, caches, and de-blocks the reverse-geocoder proxy behind
`POST /user/profile/location/detect`. The upstream provider's usage policy is stated
**per application** (Nominatim: an absolute maximum of one request per second), not per
user, so a per-user cap alone cannot keep a deployment inside it.

**Public API:**

| Symbol | Description |
|--------|-------------|
| `detect_profile_location(request, *, user_id, latitude, longitude, endpoint_url, user_agent, network_service)` | Runs the whole sequence below and returns a `ReverseGeocodeResult`, cached or fresh. |
| `cache_key(latitude, longitude)` | The Valkey key for the 0.001-degree cell holding these coordinates. |
| `GeocodeUserLimitError` / `GeocodeGlobalLimitError` | Refusals carrying `retry_after`; the route turns both into `429` with `Retry-After`. |
| `GeocodeUnavailableError` | The gate could not be evaluated; the route returns `503` and nothing was called. |
| `GEOCODE_USER_RATE_LIMIT_BUCKET` / `GEOCODE_USER_RATE_LIMITER` | The shared-limiter bucket (`arena:geocode:user`) and its process-local fallback. |

**Order of operations** (the order *is* the design):

1. **Coordinates are validated first** (`profile_location_service.validate_coordinates`),
   so a malformed request consumes no budget and no cache key is built from an
   out-of-range value.
2. **The per-user fixed window** (`shared.services.request_rate_limit.check_rate_limit`,
   keyed by Arena user id) is counted on *every* valid request, cache hits included: it
   is per-person abuse control, and a spinning client is still work. It is counted
   *before* the cache is read, so a refusal never reveals whether a cell is cached. It
   keeps the shared limiter's ordinary fail-open fallback, because the deployment-wide
   gate below is the real protection.
3. **The Valkey cache** at `noca:geocode:cache:{lat}:{lon}` (three decimal places, about
   100 m; `-0.0` normalized to `0.0` so a cell is never stored twice). A hit returns
   without consuming the gate and without any upstream call -- which is what makes a
   lecture hall full of students cost one provider request. Only validated results are
   cached, a negative one (no country resolved) included; errors never are.
4. **The deployment-wide gate**, one Lua script over `noca:geocode:pace` and
   `noca:geocode:budget`, admitting the call only when both the per-second pacing and
   the windowed budget allow it. Both are decided in one atomic step against *Valkey
   server time*, so replicas cannot disagree, no client clock is trusted, and a request
   refused by pacing burns no budget. It is consumed *before* the call, so a failing
   provider spends its slot rather than becoming a retry loop against itself.
5. **The provider call runs in a worker thread** (`anyio.to_thread.run_sync`). The
   underlying `NetworkService` call is synchronous; on the event loop it would stall
   every other Arena request for the whole upstream round trip.

Unlike every other limiter in NOCA, the gate **fails closed**: any Valkey failure
refuses the request rather than admitting it. `ValkeyRuntime.eval` converts only
connection, timeout and OS errors to `None` and re-raises the rest (a script error,
`NOSCRIPT`, `READONLY` on a replica, an OOM), so the service catches those explicitly --
otherwise they would escape as a `500` having already skipped the gate.
`request_rate_limit`'s fallback is process-local, so a Valkey outage across N Arena
replicas would otherwise multiply the deployment-wide budget by N, exactly the upstream
ban this service exists to prevent. The cache is the opposite: a failed read is a miss
and a failed write is logged and ignored, because neither can let an extra request
through and a write failure must not lose an answer already paid for.

The gate has **no off switch**. `ARENA_REVERSE_GEOCODER_ENABLED` disables the proxy
itself, and a deployment needing more headroom raises the ceilings, which keeps the
structure intact; only pacing may be set to zero.

Knobs: `NOCA_ARENA_GEOCODE_RATE_LIMIT_*` and `NOCA_ARENA_GEOCODE_CACHE_TTL_SECONDS`
(see [CONFIG.md](../../docs/CONFIG.md)).

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

Solved rows join `arena_problem_solvers`, `arena_problems`, and `arena_problem_ratings`. Attempted rows use `arena_problem_tried` and exclude problems already present in `arena_problem_solvers`. The current page's problem IDs load their categories in a separate ordered query. Each `ProgressProblemRow` carries `difficulty: DifficultyDisplay` (built by `shared.services.arena_difficulty_display.difficulty_display` from the raw rating and attempter count) rather than a bare number, so a problem with too few attempters renders as "not enough data" instead of `0.0`.

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
| `rate_user(*, session, user_id)` | JOIN `arena_problem_solvers` + `arena_problem_ratings`, excluding solves for problems owned by that user, sum exponential points, UPDATE `user_rating`, `solved_problems`, `dta_rating_update`. Score 0 for users with no counted solves. Does not commit. |
| `rate_all_users(session)` | SELECT all `id`s from `arena_users`, call `rate_user` for each. Returns count. Does not commit. |
| `rate_affiliation(*, session, affiliation_id, f)` | SELECT non-null `user_rating` and precomputed `solved_problems` values for affiliation members where `ranking_visible=True`. Apply the geometric weighting formula, sum the member solve counts, and update `arena_affiliations.rating`, `solved_problems`, and `dta_rating_update`. Users who opt out of ranking are excluded from both computations. Does not commit. |
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
| `affiliation_subdivision_code` | `str \| None` | ISO 3166-2 code for the affiliation's subdivision, or `None` |

**`PublicProblemListItem` dataclass:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Problem UUID |
| `arena_number` | `int` | Sequential public problem number |
| `title` | `str` | Problem title |
| `difficulty` | `DifficultyDisplay` | Evidence-gated presentation from `shared.services.arena_difficulty_display`: the measured value once the problem has `MIN_ATTEMPTS_FOR_DISPLAY` attempters, otherwise the unknown state |
| `categories` | `list[ProblemListCategory]` | Rendered categories linked to this problem |
| `author_name` | `str \| None` | Free-text author or owner fullname, according to `author_is_owner` |
| `is_favorite` | `bool` | `True` when the viewing user has favorited this problem; always `False` for guests |
| `ac_rate` | `float \| None` | Fraction from rating stats that count every non-owner, regardless of role |
| `is_solved` | `bool` | Personal solved status for the viewing user, including staff users |
| `solved` | `int \| None` | Count of distinct non-owner solvers for the aggregate problem list column |
| `has_custom_validator` | `bool` | `True` when the problem's stored strategy is interactive |

**Functions:**

| Symbol | Description |
|--------|-------------|
| `list_enabled_problems_paginated(session, *, page, per_page=25, search, category_slugs, collection_id=None, language=None, sort_by, user_id=None)` | Paginated enabled-problem list returned as narrow immutable projections. An optional `StatementLanguage` narrows the list to problems written in that language. Search delegates to `problem_search_service`, including resolved free-text or owner-backed authors, and defaults to relevance when active. Category filtering uses OR semantics; `collection_id` is a separate axis that ANDs with it, narrowing the OR-set to one collection. The caller resolves the slug itself so an unknown one can 404 rather than render an empty, unnamed scope. Solver aggregates exclude only problem owners. A missing rating row yields no rating or AC rate; a zero-attempt rating row yields a `0.0` AC rate. |
| `get_enabled_problem_by_number(session, arena_number)` | Fetch a single enabled problem by its public `arena_number`. Returns `(ArenaProblem, AuthorInfo)` or `None` if not found or disabled. Also outer-joins `arena_affiliations` to populate the affiliation name, country code, and subdivision code. Eagerly loads `rating`, `categories`, `collection`, `test_cases`, and `custom_validator`. |
| `get_all_categories(session)` | Return all categories alphabetically by name, for the filter dropdown. |
| `get_all_collections(session)` | Return all collections alphabetically by name, for the filter dropdown. |
| `list_collections_with_counts(session)` | Return one `CollectionCard` per collection (`name`, `slug`, `color`, `foreground_color`, `problem_count`) for the `/collections` index. Counts **only enabled** problems, so a card never advertises problems the catalogue will not show. |
| `get_user_problem_status(session, *, user_id, problem_id)` | Return `(solved_at, tried_at, is_favorite)` from the solver, tried, and favorites tables. Datetime values may be `None`; `is_favorite` is `True` only when a favorites row exists. |
| `get_problem_rating_history(session, problem_id)` | Return rating history for the last 730 days as `[{"ts": ISO8601, "rating": int}, ...]`, chronological. Used by the public ECharts sparkline endpoint. |
| `get_latest_problems(session, *, limit=10)` | Return the `limit` most recently created or edited enabled problems as `LatestProblemItem` (`arena_number`, `title`, `updated_at`), ordered by `updated_at` descending. Backs the dashboard "Latest Problems" card. |

### `problem_stats_service.py`

Read-only access to precomputed per-problem statistics. The snapshots are computed periodically
by the rating worker (`shared.services.arena_problem_stats`) and stored in `arena_problem_statistics`;
this service performs no aggregation — it only reads the latest snapshot for the statistics page and
decorates it for the requesting viewer, at request time, because the snapshot goes stale (a solver's
profile visibility can change between two rating cycles).

**Functions:**

| Symbol | Description |
|--------|-------------|
| `get_problem_statistics(session, problem_id)` | Return the latest statistics payload (verdicts, languages, per-language time/memory stats, wall-time histogram, solver milestones, attempts histogram, submission heatmap) augmented with `computed_at` (ISO-8601), or `None` when statistics have not been computed yet. |
| `get_problem_statistics_for_viewer(session, problem_id, viewer, profile_url_for)` | Return the payload decorated for one viewer, or `{}` without a snapshot: adds `computed_at_display` and each non-null `first_solver` / `last_solver`'s `solved_at_display` (viewer timezone, via `format_user_datetime`), and sets `profile_url` on a solver only when the viewer may open that profile — the shared `profile_visibility.can_view_public_profile` predicate the public-profile route also uses (`ARENA_ADMIN` sees all; otherwise active + `public_profile` + `ranking_visible` + not age-shielded). It also **replaces** each solver's `name`: the snapshot stores `arena_users.nome` frozen at the last rating cycle, so rendering it as-is would publish an age-shielded solver's legal name to every logged-in visitor. The name is re-resolved here rather than in the snapshot because the snapshot is written by the rating worker in `shared/`, and the age shield is Arena product policy that must not leak into a cross-module service. A solver whose account no longer exists gets `UNKNOWN_SOLVER_NAME` and no link — with no row there is no date of birth, so publishing the stored name would be a guess, and this fails closed. |
| `UNKNOWN_SOLVER_NAME` | The neutral placeholder shown in place of a deleted solver's frozen legal name. |

### `profile_visibility.py`

The single predicate deciding who may open an Arena user's public profile, shared by the
public-profile route (which answers 404 otherwise) and by every surface that decides whether to
link to a profile, so a link can never lead where the route refuses.

| Symbol | Description |
|--------|-------------|
| `can_view_public_profile(*, ativo, public_profile, ranking_visible, date_of_birth, viewer_role)` | True when the viewer is an `ARENA_ADMIN` (moderation bypass), or when the profile is active, opted in (`public_profile`), ranking-visible, **and not age-shielded**. `date_of_birth` is required and keyword-only precisely so a call site added later cannot skip the shield by omitting it: leaving it out fails at type-check time rather than opening a silent hole. The refusal stays a 404, never a 403, so a shielded profile is indistinguishable from one that does not exist. |

### `user_visibility_service.py`

**The single owner of Arena's age-to-visibility rule (the "minor shield").** Every public read
path resolves a user's displayed identity here and never from `arena_users.nome` directly, so
the rule lives in exactly one place. The rule is:

```python
status   = NEEDS_PARENTAL_CONSENT if dob is None else check_age(dob)
shielded = status is not AgeStatus.ALLOWED
```

`shared.age_check` stays a pure age oracle evaluated **per request**, so a shielded account stops
being shielded the moment it turns 18 — no scheduler, no stored expiry, no new column. An unknown
date of birth **fails closed** and is treated as a minor.

This lives in `arena/services/` rather than `shared/` because the shield is Arena product policy.
It never touches `ranking_visible` — a shielded user stays in the ranking, under their pseudonym —
and the only consumer of any of these flags outside Arena is `shared.services.arena_rating`, which
reads `ranking_visible` for affiliation aggregation and has no interest in age.

| Symbol | Description |
|--------|-------------|
| `DisplayIdentity` | Frozen DTO: `user_id`, `display_name`, `is_pseudonymous`, `public_profile` (the **effective** flag, after the shield), `masked_email` (`None` when shielded). |
| `is_shielded(date_of_birth, *, reference_date=None)` | True for anyone under 18 and for an unknown date of birth. False only for a confirmed adult. |
| `resolve_display_identity(*, user_id, full_name, username, date_of_birth, full_name_public, public_profile, ranking_visible, email=None, reference_date=None)` | The resolver every public surface calls. `display_name = full_name if (full_name_public and not shielded) else username`; `is_pseudonymous = not (full_name_public and not shielded)` — one boolean drives both, so a surface can never render a legal name while reporting a pseudonym; `public_profile = public_profile and ranking_visible and not shielded`; `masked_email = None` when shielded. |
| `display_identity_for_user(user, *, reference_date=None)` | The same, for a loaded `ArenaUser`. |
| `may_show_full_name(user)` / `may_have_public_profile(user)` | Whether the account is *permitted* to publish its legal name / carry a public profile. They report permission, not the opt-in, so a caller can offer the choice without granting it. |
| `shielded_users_clause(users=arena_users, *, reference_date=None)` | The SQL mirror of `is_shielded`, and the rule's one unavoidable duplication. `users` takes the table **or an alias**: callers building candidate-ID branches query an alias, and binding the predicate to the base table would add an unjoined FROM element. The cutoff is bound with an explicit `Date` type — never a PostgreSQL `INTERVAL` literal (the SQLite test path cannot execute one) and never an ISO string (asyncpg is strictly typed and rejects it). A table-driven test executes both forms over the same boundary rows and asserts they agree. |

`ArenaUser.public_display_name` and `ArenaUser.effective_public_profile` delegate here, so a
template cannot reach past the shield by reading `nome`.
`tests/arena/test_public_templates_no_full_name.py` scans every template and fails on any
unclassified `.nome`, so a new public template is unclassified-by-default rather than silently
unshielded.

`is_pseudonymous` is carried on `TopRatedUser` and `RankedUser` but rendered by no template today
— the name is already resolved server-side. It exists so a later surface can badge a pseudonym
without re-deriving the rule.

### `user_stats_service.py`

Read-only access to precomputed per-user statistics. The snapshots are computed periodically by
the rating worker (`shared.services.arena_stats`) and stored in `arena_user_statistics`; this
service performs no aggregation — it only reads the latest snapshot for the public profile page.

**Functions:**

| Symbol | Description |
|--------|-------------|
| `get_user_statistics(session, user_id)` | Return the latest statistics payload (total submissions, verdicts, languages) augmented with `computed_at` (ISO-8601), or `None` when statistics have not been computed yet. |

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
| `difficulty` | `DifficultyDisplay` | Evidence-gated difficulty presentation (`shared.services.arena_difficulty_display`) |
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

- `RankedUser` — flat presentation DTO with `id`, `rank`, `name`,
  `is_pseudonymous`, `email_mascarado`, `affiliation_name`, `country_code`,
  `country_name`, `subdivision_code`, `subdivision_name`, `rating`, `solved`,
  `public_profile`, and `avatar_revision`. `name`, `public_profile`, and `email_mascarado` are the
  resolved values from `user_visibility_service`: the username unless the user
  is an adult who opted in, the profile flag after the age shield, and `None`
  for a shielded user's email. Built only through `RankedUser.from_row(row)`.
- `RankedAffiliation` — flat presentation DTO with `id`, `rank`, `name`,
  `has_logo`, `country_code`, `country_name`, `subdivision_code`,
  `subdivision_name`, `rating`, and `solved`.

| Function | Description |
|----------|-------------|
| `get_ranked_users_paginated(session, *, search, affiliation_id, page, per_page)` | Returns `Pagination[RankedUser]`. Uses a CTE to compute global `RANK()` before applying search/affiliation filters. Eligible: `ativo=True`, `email_confirmado=True`, `ranking_visible=True`. Search delegates to `identity_search_service.prepare_public_user_search` — the age-shielded sibling, not the teacher-scoped one — and is applied as `ranked_cte.c.id.in_(...)`; it filters only and never reorders, so a user's rank is the same whether or not a search is active. |
| `get_ranked_affiliations_paginated(session, *, search, country_code, subdivision_code, page, per_page)` | Returns `Pagination[RankedAffiliation]` with global affiliation rank via `RANK()` CTE ordered by rating desc, name asc. Search delegates to `identity_search_service` under the same filter-only contract. |
| `get_affiliation_filter_options(session, *, country_code)` | Returns `(countries, subdivisions)` as two `list[LocationChoice]` sourced only from distinct values in the affiliations table. |
| `get_affiliation_or_404(session, affiliation_id)` | Fetches `ArenaAffiliation` by ID or raises `HTTPException(404)`. |

---

### `identity_search_service.py`

Indexed candidate-ID search over Arena users and affiliations, shared by the Ranking
pages and the class-membership student autocomplete. Callers receive a **candidate-ID
selectable against the base table** and apply it as `<outer query>.c.id.in_(...)`. That
shape exists because the ranked CTEs carry a `RANK()` window function, so a `WHERE`
written against their columns can never be pushed down to the base table and can never
use an index; the same selectable also serves callers that query `arena_users` directly.
Each branch constrains exactly one column so PostgreSQL can serve it from one index and
BitmapOr the branches together.

The candidate query deliberately omits every eligibility predicate (`ativo` /
`email_confirmado` / `ranking_visible` / role, `exclude_from_ranking`): each caller
already applies the ones it needs, so the join discards any ineligible ID the search
matched.

User search comes in **two** flavours, and the split is the point. `prepare_user_search`
matches real names and serves *teacher-scoped* contexts (`arena_class_detail_service`'s
member and student autocompletes), where a teacher looking a student up by the name on
the roll has a legitimate basis. `prepare_public_user_search` serves the anonymous and
public ranking surfaces and suppresses name matching for age-shielded users: a page that
renders a pseudonym while still answering "is this real name in the ranking?" is a
confirmation oracle that reconstructs the shield's own secret. It is a separate function
rather than a flag so a change made for the public path cannot break the teacher path.

The shield predicate is bound to the **branch's alias**, never to the bare `arena_users`
table. Binding it to the base table while the branch queries `search_arena_user` would
add an unjoined FROM element — a cross join with an uncorrelated age test. A unit test
compiles a branch and asserts it has exactly one FROM element.

| Function | Description |
|----------|-------------|
| `prepare_user_search(session, query)` | Returns a selectable of matching `arena_users.id`. On PostgreSQL: a `simple`-configuration FTS branch on `nome` (`ix_arena_users_nome_fts_gin`), plus escaped-substring branches on `nome` (`ix_arena_users_nome_trgm`) and `email_normalizado` (`ix_arena_users_email_normalizado_trgm`) and a 3+-character fuzzy `%` branch on `nome`. Email is substring-only — an address is an exact identifier, so fuzzy email matching would be noise. Queries with quoted phrases, `OR`, or `-` negation keep only the FTS branch so operators stay authoritative. SQLite uses a portable escaped-substring predicate. |
| `prepare_public_user_search(session, query)` | The **age-shielded** sibling, for every anonymous or public surface (today: the two ranking pages). Same dialect dispatch and branch shape as `prepare_user_search`, with two differences: each `nome` branch is conjoined with `~shielded_users_clause(alias)`, and substring plus 3+-character fuzzy branches on `username` are added unconditionally (`ix_arena_users_username_trgm`, migration `202608310002`) so a shielded user stays findable by their handle. There is deliberately **no** username FTS branch: it would need a second expression index alongside `_USER_NAME_VECTOR_SQL`. The SQLite path is shielded too — the whole test suite runs on it, so an unshielded portable branch would let the shield's own tests pass against code that never applies it. |
| `prepare_affiliation_search(session, query)` | Returns a selectable of matching `arena_affiliations.id` using the same three-branch shape on `name` (`ix_arena_affiliations_name_fts_gin`, `ix_arena_affiliations_name_trgm`) and the same operator-suppression and SQLite fallback rules. |
| `user_relevance_ordering(session, query)` | Returns `ORDER BY` terms over `arena_users` — literal (substring) hits first, then descending `similarity(nome, query)`, then name. **Opt-in, and only for callers that truncate their result set**: fuzzy matching adds rows containing no literal trace of the query, so an alphabetical order can push the intended row past the cut-off. Empty for a blank query, name-only off PostgreSQL. The ranking pages must not use it — they order by `global_rank`. Deliberately **not** age-shielded: its only callers are the teacher-scoped class autocompletes, and suppressing the literal-hit and similarity terms for a shielded student would sort them to the bottom of a list that then truncates, so a teacher typing the exact name could fail to see them. `tests/arena/test_public_templates_no_full_name.py` pins that caller set with an AST scan, so a public surface adopting this helper fails loudly and has to grow a shielded sibling. |
| `affiliation_relevance_ordering(session, query)` | Returns opt-in `ORDER BY` terms over `arena_affiliations` for truncated autocomplete lists: literal substring hits first, then descending `similarity(name, query)`, then case-insensitive name with the original name as a deterministic case-only tie-breaker. Empty for a blank query, and deterministic name ordering off PostgreSQL. Ranking pages do not use it because they retain global-rank ordering. |

The FTS configuration is `simple` on purpose: names are proper nouns, so stemming and
stopword removal would lose information rather than add recall. Both vector expressions
must stay byte-identical to the DDL in migration `202608030001` or PostgreSQL will not
match the expression index; a unit test asserts that. The username trigram index lands
separately in migration `202608310002`: the `UNIQUE` B-tree `username` already carries
serves neither a leading-wildcard `ILIKE` nor the `%` operator.

---

### `text_search_primitives.py`

The narrow kernel shared by `problem_search_service` and `identity_search_service`, holding
the rules that must not diverge between search paths.

| Symbol | Description |
|--------|-------------|
| `escaped_substring_pattern(query)` | Builds a `%`-wrapped ILIKE pattern in which the user's own `%`, `_`, and backslash match literally (backslash escaped first). |
| `uses_websearch_syntax(query)` | True for a quoted phrase, an `OR`, or a leading `-` negation — the signal to suppress substring and fuzzy fallback branches. |
| `apply_trigram_threshold(session)` | Pins `pg_trgm.similarity_threshold` for the current transaction (`SET LOCAL`) so `%` matching is deterministic regardless of server configuration. |
| `LIKE_ESCAPE` / `MIN_FUZZY_QUERY_LENGTH` / `TRIGRAM_SIMILARITY_THRESHOLD` | The shared constants (`\`, `3`, `0.3`). |

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
| `search_teacher_autocomplete(session, *, query, affiliation_id=None, limit=10)` | Teacher search helper returning judge users formatted as `Full name <email>`. Matching delegates to `identity_search_service.prepare_user_search` for full-text, escaped substring, and fuzzy name candidates, then orders with `user_relevance_ordering`. Blank queries return all judges ordered by name. When `affiliation_id` is set, the outer query restricts results to that affiliation. |
| `search_student_autocomplete(session, *, actor_id, actor_role, class_id, query, limit=10)` | Teacher/admin student search helper for direct class assignment. Returns active, confirmed `ARENA_USER` accounts formatted as `Full name <email>`, excluding active members and pending registration requests for the class. Matching delegates to `identity_search_service.prepare_user_search` (full-text, substring, and fuzzy on the name; substring-only on the email) and orders by `user_relevance_ordering` — the result set is truncated to `limit`, so the best match must come first or it is never shown. |

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
| `search_teacher_autocomplete(session, *, query, affiliation_id, limit)` | Judge-only teacher autocomplete using indexed identity matching and relevance ordering, with an optional outer-query affiliation restriction. Blank queries return judges ordered by name. |
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
| `request_registration(session, *, user_id, class_id)` | Any registered user except the class teacher. Creates a `PENDING` request; rejects if the requesting user is the teacher, the class forbids self-registration, the user is already a member, a pending request exists, or the user's last request for the class was **denied** within `NOCA_ARENA_CLASS_REGISTRATION_RETRY_SECONDS` (each request emails the teacher, so the deny/re-request loop must be bounded). |
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
| `set_tied_verdicts_for_sets(session, set_ids)` | Single source of truth for set-tied results, batched over any number of sets. Maps `(set_id, user_id, problem_id)` to that pair's verdicts, taking exactly one verdict per submission (from its active, non-superseded judgment) and joining `arena_problem_set_problems` so a problem removed from the set stops counting. Used by both the per-set report and the class-wide report so the two cannot diverge. |
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
| `get_student_problem_submissions_for_set(session, *, actor_id, actor_role, set_id, user_id)` | Teacher/admin only. All submissions by one student for the problems in a set, grouped by problem (tuple of `StudentProblemGroup`), submissions ordered newest-first. Each `StudentSubmissionEntry` carries `has_feedback` (teacher feedback present); each `StudentProblemGroup` carries `needs_feedback` (`True` when the student has no Accepted submission for that problem yet **and** their most recent non-AC attempt has no teacher feedback, via the shared `_needs_feedback` predicate — feedback on an older attempt does not clear it, because the student submitted again and is still not passing). |
| `can_teacher_view_submission(session, *, teacher_id, set_id)` | Returns True if the teacher manages the class that owns the given problem set. Used by the submission detail route to authorize ARENA_JUDGE access. |

### `arena_batch_feedback_service.py`

Teacher-facing batch feedback over one problem in a problem set — the per-problem
counterpart to `arena_problem_set_report_service.py` (which aggregates per student across
all problems). Every active class member's most-recent submission on the given problem is
bucketed by verdict; non-AC entries are feedback-ready regardless of whether feedback
already exists, so the manage-problems badge count and the batch page's card count always
agree.

**Dataclasses:** `VerdictCount`, `BatchFeedbackStudentEntry`, `BatchFeedbackData`.

| Function | Description |
|----------|-------------|
| `get_non_ac_counts_for_set(session, *, actor_id, actor_role, set_id)` | Teacher/admin only. Returns `{problem_id: count}` of active class members with no Accepted set-tied submission on that problem yet whose most recent non-AC attempt carries no teacher feedback (via the shared `_needs_feedback` predicate over that student's full attempt history). Problems with zero qualifying students are omitted. |
| `get_batch_feedback_data(session, *, actor_id, actor_role, set_id, problem_id)` | Teacher/admin only. Returns `BatchFeedbackData`: problem statement/title/number, the 8-bucket verdict summary (fixed order AC, WA, PE, TLE, MLE, OLE, CE, RE), one `BatchFeedbackStudentEntry` per non-AC most-recent submission (with source code, compile log, first failing testcase context when available -- a `BatchFeedbackTestResult` whose `output_diff` is the bounded `output_diff.py` comparison for WA/PE and `None` otherwise -- optional AI review context, highlight language, and any existing feedback text), and the deduped set of highlight languages needed by the page's script tags. Raises `ArenaProblemSetNotFoundError` when the problem is not in the set. |
| `validate_batch_submission_ids(session, *, set_id, problem_id, submission_ids)` | Re-derives, for a candidate list of submission ids, which are still the active member's most-recent non-AC submission for the problem; returns `{submission_id: (user_id, existing_feedback_text)}` for the still-valid subset, silently dropping stale/tampered ids. Used by the POST route to close the rejudge/supersede TOCTOU window. |

### `arena_problem_set_snapshot_service.py`

Freezing and reading the post-deadline rating snapshot.

**Dataclasses:** `SnapshotUserTotal`, `SnapshotProblemRating`.

| Function | Description |
|----------|-------------|
| `snapshot_problem_set(session, *, set_id, now)` | Idempotent per set (delete-then-insert). Per user with a set-tied submission, stores `total_rating` = sum of current ratings of AC'd set problems (0 if none) and a per-problem row for each AC. Requires the set to have a deadline. No authz (worker/admin caller). |
| `list_snapshot_user_totals(session, *, actor_id, actor_role, set_id)` | Teacher/admin only. Per-user frozen totals. |
| `list_snapshot_ratings(session, *, actor_id, actor_role, set_id)` | Teacher/admin only. Per (user, problem) frozen AC ratings. |

### `arena_problem_assignment_service.py`

Teacher-facing queries and mutation validation for the problem-detail
assignment card.

**Dataclasses:** `ProblemSetAssignment`, `ProblemSetAssignmentGroup`,
`ProblemAssignmentOverview`.

| Function | Description |
|----------|-------------|
| `get_problem_assignment_overview(session, *, actor_id, actor_role, problem_id, today, now)` | Returns `None` unless the actor is exactly `ARENA_JUDGE` and owns a class whose end date is today or later. Groups existing assignments and eligible targets by teacher-owned class, with classes ordered by latest start date. Existing sets use earliest-deadline order with open deadlines last. Eligible targets have no past deadline and do not already contain the problem; their start date does not restrict eligibility. |
| `add_problem_to_problem_set(session, *, actor_id, actor_role, arena_number, problem_set_id, today, now)` | Revalidates the exact judge role, enabled problem existence, set existence, ownership, class end date, deadline, and non-membership against current database state. Calls the shared `add_problems_to_set` membership operation after validation. The caller owns commit and rollback. |

---

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
| `list_problem_set_problems(session, *, actor_id, actor_role, set_id)` | Teacher/admin only. Returns the problem rows for the manage-problems page with Arena number, title, plain-text categories, and an evidence-gated `difficulty: DifficultyDisplay`. `ProblemAutocompleteRow` carries the same field; the autocomplete route appends its `text` to the label only when the state is not unknown. |
| `search_set_candidate_problems(session, *, actor_id, actor_role, set_id, query, limit=10)` | Teacher/admin only. Autocomplete source for adding enabled problems not already in the set. Delegates to `prepare_problem_picker_search` for indexed, escaped, and typo-tolerant number/title matching. Relevance ordering puts an exact Arena number before other number-substring/title matches; blank queries remain ordered by Arena number. |
| `build_teacher_problem_set_report(session, *, actor_id, actor_role, set_id, now)` | Teacher/admin only. Builds the UI-ready report matrix over active class members and set problems, with best verdict per cell and an optional snapshot rating column when a due-set snapshot already exists. |

---

### `arena_class_full_report_service.py`

Builds the class-wide problem-set report: one row per active class member, one
column per problem set whose deadline has already passed, and a weighted total.
Every query is batched across the whole class, so adding a set or a student does
not add a query. Read-only; the caller owns the transaction boundary.

Columns are numbered from 1 in deadline-descending order, matching the default
order of the teacher problem-set list. A cell's AC rate counts *distinct*
problems accepted through the submission's active (non-superseded) judgment, so
a re-judged or repeatedly accepted problem still counts once, and it is clamped
to the set's problem count. The total weights each set by its problem count,
which reduces to the student's accepted problems over every problem in the
report.

Each column also carries a `histogram`: `HISTOGRAM_BINS` (10) counts binning the
class's AC rates for that set across 0-100% in equal steps, with a perfect 100%
closed into the last bin. It is empty for a set with no problems, since there is
then no rate to distribute. The legend table renders it as a small chart, so the
page needs no extra endpoint; `ClassFullReport.histogram_max` carries the
tallest bin across every set, which the charts share as a fixed y maximum.

**Dataclasses:** `FullReportSetColumn`, `FullReportCell`, `FullReportStudentRow`,
`ClassFullReport`.

| Function | Description |
|----------|-------------|
| `build_class_full_report(session, *, actor_id, actor_role, class_id, now)` | Teacher/admin only. Returns the legend columns, one row per active student, and the per-set and class-wide averages. Raises `ArenaProblemSetNotFoundError` for an unknown class and `ArenaProblemSetPermissionError` for any other actor. A class with no closed sets or no students is a valid empty report, not an error. |

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

### `username_service.py`

Owns Arena's policy around the pseudonymous handle stored in
`arena_users.username`: what a handle may look like, which handles are refused,
and how one is made unique. Drawing a handle is **not** this module's job — that
is `shared.services.random_username_service.generate_username()`, which owns the
word lists.

Two rules govern every caller:

- **The username is not a login identifier.** Email remains the only way to log
  in. Accepting a handle at `/auth/login` would turn the pseudonym into an
  account-enumeration vector, which is the thing it exists to prevent.
- **Uniqueness is guaranteed by the database, not by this module.**
  `is_username_available()` is a TOCTOU: another transaction may take the name
  between the check and the insert. The `UNIQUE` constraint is the real
  guarantee, so every caller must catch `IntegrityError` — see
  `user_registration_service._insert_with_unique_username`, which redraws and
  retries rather than failing the signup.

| Function | Description |
|----------|-------------|
| `normalize_username(raw)` | NFKC → strip → casefold. The stored form *is* the display form: there is no separate canonical column and no functional `lower()` index, so a handle has no display casing. |
| `validate_username(raw)` | Normalizes, then enforces length (3–64), `USERNAME_PATTERN`, `RESERVED_USERNAMES`, and UUID-shape rejection. Raises `UsernameError`. |
| `is_username_available(session, username, *, exclude_user_id=None)` | Advisory availability check. `exclude_user_id` keeps a user's own handle from counting as a clash. |
| `generate_unique_username(session, *, max_attempts=8)` | Draws until unclaimed; the final attempt appends a four-digit suffix, which makes termination unconditional. |
| `username_cooldown_remaining(user, *, now=None)` | Whole days left against `dta_troca_username`, rounded up. `0` when the cooldown is disabled, when the user has never renamed, or when the window has elapsed. Tolerates a naive timestamp, which is what SQLite hands back. |
| `change_username(session, user, raw, *, bypass_cooldown=False, clear_cooldown=False)` | Validate → cooldown → advisory availability → assign, then stamp `dta_troca_username` (or clear it when `clear_cooldown`). Returns the canonical handle. Raises `UsernameError` (malformed/reserved), `UsernameTakenError` (a well-formed handle another account holds), or `UsernameCooldownError` (carrying `remaining_days`). |

**Constraints on a handle:** lowercase `[a-z0-9_-]`, alphanumeric at both ends,
3–64 characters. NFKC folds compatibility forms (fullwidth `ｕｓｅｒ` → `user`)
but deliberately does *not* map Cyrillic or Greek look-alikes onto Latin
letters — those are different letters, not compatibility variants, and the
`[a-z]` restriction is what refuses them. UUID-shaped values are reserved
because Arena addresses public profiles as `/profile/{user_id}`.

**Out of scope:** residual homoglyph confusability. `0` versus `o` and `-`
versus `_` remain distinguishable only by careful reading; closing that needs a
skeleton-form index, which is a different feature.

**The cooldown.** `NOCA_ARENA_USERNAME_CHANGE_COOLDOWN_DAYS` (default 30) is
enforced by `change_username()` against `dta_troca_username`. It is a privacy
control, not tidiness: the handle is the pseudonym a 13-17 year-old is published
under, and unlimited churn would let an observer watching the ranking correlate
a user's old and new handles and undo the pseudonymity. Two consequences follow
from that reasoning rather than from convenience:

- **Resubmitting the handle already held is a no-op** that returns early without
  stamping the timestamp. Saving a form you did not edit must not cost a 30-day
  wait.
- **An administrator bypasses it** (`bypass_cooldown=True`, reached only through
  `admin_user_service.admin_change_username`). The cooldown protects a user from
  their own churn; it was never meant to block a rename made in response to a
  report. That path re-confirms the admin's password and is audited twice.
- **What the admin rename leaves behind is the admin's choice.** By default it
  *restarts* the target's window, so a handle taken down after a report cannot be
  restored a moment later. `clear_cooldown=True` (the "let this user change their
  username again right away" checkbox) sets `dta_troca_username` to `NULL`
  instead, for a typo or a rename the user asked for -- where the default would
  penalise someone for something they did not do. The server cannot distinguish
  the two situations, so it does not guess; the decision is recorded in both
  audit records as `cooldown=restarted|cleared`.

The cooldown check is a read-then-write with no row lock. Two concurrent
requests from the same user could both pass the window check, whose only
consequence is a re-stamped timestamp — `UNIQUE` still protects the handle
itself — so the contention a lock would add is not worth buying that out.

**Why this is hand-rolled.** `python-usernames` was evaluated when the handle was
server-drawn and again once users could choose one. It is still not adopted: its
pattern admits `.` where `USERNAME_PATTERN` does not, its blocklist is
English-language profanity while Arena's primary audience is pt-BR, and it
supplies none of the NFKC canonicalization, the route-derived reserved set, the
UUID-shape rejection, the uniqueness check, or the cooldown. Adopting it would
install a *second* validator disagreeing with this one, which is the drift this
module exists to prevent. `better-profanity` was rejected as unmaintained (last
release 2020) and likewise English-only.

---

## Still planned (Phase 2)

- `arena_problem_service.py` — problem CRUD and ZIP import beyond current public-number lookup
- `arena_verdict_handler.py` — Valkey subscriber: VerdictEvent → DB updates
## Custom validators

Arena problem import and export services use the shared validator package
schema. Imports stage a new candidate, force package test cases to samples, and
leave the imported problem disabled until its owner explicitly enables it.

`submission_service` skips the "problem has no test cases" precondition for
problems with a configured validator: interactive judgments never read
test-case files, so such a problem may legitimately ship zero test cases. The
validator must still be `VALID` before any submission is accepted.
