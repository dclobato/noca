# Setting up Google sign-in for Arena

This is the Google Cloud side of the Arena "Sign in with Google" door. The NOCA
side is three settings, documented in [CONFIG.md](CONFIG.md#google-sign-in); this
page walks through obtaining the values those settings need and registering the
one URL Google has to know about.

Arena speaks plain OpenID Connect to Google through Authlib. It requests exactly
the three authentication scopes -- `openid`, `email`, `profile` -- verifies the ID
token, and keeps the stable subject identifier plus the address and optional profile-picture
URL for display. If the linked user explicitly selects Google as their avatar source, Arena
downloads, validates, resizes, and locally caches that picture; browsers never load it from
Google directly.
It never calls a Google API after sign-in, never asks for offline access, and
never stores a refresh token. That is what keeps the Cloud setup small: no APIs to
enable, no sensitive scopes, and no scope verification.

## Before you start

Decide the public base URL of the Arena deployment, because the redirect URI is
derived from it and Google requires an exact match. Arena registers
`<NOCA_ARENA_URL_BASE>/auth/google/callback`. Two consequences:

- In production set `NOCA_ARENA_URL_BASE` to the `https://` origin users type
  (for example `https://arena.example.com`). Behind a TLS-terminating proxy a
  request-derived base would be `http://`, which Google would then refuse.
- In development, with `NOCA_ARENA_URL_BASE` empty, the base is whatever the
  browser used to reach Arena. `http://localhost:8001` and `http://127.0.0.1:8001`
  are different redirect URIs to Google. Either pin the base or register both.

Use one OAuth client per environment. A development client with a `localhost`
redirect URI must never share a project or a secret with production.

## 1. Choose or create a Google Cloud project

Open the [Google Cloud console](https://console.cloud.google.com/) and select the
project that will own the credential, or create one. Any project works; nothing
else in NOCA depends on it. Billing is not required for sign-in.

## 2. Configure the Google Auth Platform

In the console, go to **APIs & Services > Google Auth Platform** (older
documentation and screenshots call this the *OAuth consent screen*). On a fresh
project the page offers **Get started**; fill in the four short sections it
walks through:

1. **App information.** The app name users see on Google's consent screen (for
   example the Arena brand name) and a user support email.
2. **Audience.** Choose **External** unless every Arena user belongs to your
   Google Workspace organisation, in which case **Internal** restricts sign-in to
   that organisation and skips the publishing step below.
3. **Contact information.** The address Google notifies about the project.
4. Agree to the user data policy and create.

Then review two of the pages the platform exposes in its left-hand menu:

- **Branding.** Optional, but worth filling in: the home page, privacy policy,
  and terms of service links. Arena serves the last two at `/legal/privacy` and
  `/legal/terms` on the deployment. Add the deployment's domain under
  **Authorized domains**; Google requires the redirect URI's host to belong to
  one of them for non-`localhost` URIs. Uploading a logo triggers Google's brand
  verification review; leave it empty if you do not want one.
- **Data Access.** Add the three non-sensitive scopes Arena requests:
  `openid`, `.../auth/userinfo.email`, and `.../auth/userinfo.profile`. Do not
  add anything else; Arena will not use it, and any sensitive or restricted scope
  would put the app into Google's full verification process.

## 3. Publishing status

Under **Audience**, an External app starts in **Testing**. In that state Google
lets only the accounts listed under **Test users** sign in (at most 100), shows a
warning that the app is unverified, and blocks everyone else with an
*access blocked* error. That is the right state for a development or staging
Arena: add the Google accounts you will test with and stop there.

For a public Arena click **Publish app**. Because the app requests only the three
authentication scopes, publishing requires no scope verification and takes effect
immediately. Google's separate **brand verification** governs whether your app
name and logo are displayed as-is on the consent screen; it is optional and does
not gate sign-in.

## 4. Create the OAuth client

Go to **Clients** (also reachable as **APIs & Services > Credentials**) and
choose **Create client**:

- **Application type:** Web application.
- **Name:** anything that identifies the environment, such as
  `noca-arena-production`.
- **Authorized JavaScript origins:** not needed. Arena performs a server-side
  redirect flow and loads no Google script in the browser. Leave it empty.
- **Authorized redirect URIs:** exactly one entry per base URL,
  `<base>/auth/google/callback`. Examples:

  ```text
  https://arena.example.com/auth/google/callback
  http://localhost:8001/auth/google/callback
  ```

  Google accepts plain `http://` only for `localhost` and loopback addresses. No
  trailing slash, no query string, and the path is case-sensitive.

Create it. The console shows the **client ID** and the **client secret** once, on
creation; copy both now. The secret is not retrievable later. If it is lost, add
a new secret to the same client and delete the old one.

## 5. Configure Arena

Put the values in Arena's environment (see `.env.arena.full`):

```dotenv
NOCA_ARENA_URL_BASE=https://arena.example.com
NOCA_ARENA_GOOGLE_OAUTH_ENABLED=true
NOCA_ARENA_GOOGLE_OAUTH_CLIENT_ID=1234567890-abc.apps.googleusercontent.com
NOCA_ARENA_GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-...
```

Restart Arena. It refuses to start when the flag is on and either credential is
empty, so a typo fails at boot rather than on a user's first click. The redirect
URI is derived, never configured; there is no setting for it.

## 6. Verify

1. Open the Arena login page. A **Sign in with Google** button now sits under the
   password form, and the signup page offers its counterpart. While the flag is
   off, neither renders and every `/auth/google` route answers `404` --
   **except** `GET /auth/google/blocked`, which stays reachable on purpose so a
   visitor already refused there for being under 13 can still see why even
   after the deployment disables the feature (see `arena/docs/ROUTES.md`).
2. Click it. Google shows its consent screen for the app name you configured,
   listing name, email address, and profile picture.
3. Approve. A Google account Arena has never seen lands on the completion step
   that asks for a date of birth and terms acceptance; an account already linked
   goes straight through the usual login gates. The completion step also offers
   **Already have an Arena account under another email?** for a visitor whose
   Arena account uses a different address: it sends them to the password login
   and, once the whole login chain has run, to a confirmation page that links the
   Google account to that account and discards the unfinished signup.
4. Optionally, from a logged-in Arena account, open the profile page and use
   **Linked accounts** to link a Google account to an existing password login. Once linked,
   choose **Arena avatar** or **Google avatar** there; the Arena upload remains stored when
   Google is selected, and a new Arena photo automatically selects Arena again.
5. As an `ARENA_ADMIN`, open a user's admin profile (**Personal & Security** tab): the
   **Google account** row shows the linked address and an **Unlink Google** control,
   password-confirmed. It is allowed even when Google is the account's only way in --
   the user is then told, by email, to set a password through **Forgot password?**
   before signing in again -- and it keeps working after the feature is disabled, so
   identities created while it was on can always be detached. The one refusal is an
   unfinished Google-first signup (never completed, held for a guardian's consent, or
   refused under 13), whose only way forward is the Google door itself; the row shows
   the reason instead of the control.

## Troubleshooting

| Symptom | Cause |
|---------|-------|
| Google page: *Error 400: redirect_uri_mismatch* | The URI Arena sent is not on the client's list. Read the `redirect_uri` shown in the error details and register that exact string. In production this almost always means `NOCA_ARENA_URL_BASE` is unset or not `https://`. |
| Google page: *Access blocked: ... has not completed the Google verification process* | The app is in **Testing** and the signing-in account is not a test user. Add the account, or publish the app. |
| Google page: *Error 401: invalid_client* | The client ID or secret in Arena's environment does not match the client. Re-copy them; if the secret was never captured, create a new one. |
| Arena refuses to start naming the two credential settings | `NOCA_ARENA_GOOGLE_OAUTH_ENABLED` is true with an empty ID or secret. |
| Arena flashes a generic sign-in failure after Google returns | Deliberately generic; the `google_login_failure` row in the admin security events log names the reason (unverified email, rejected token exchange, missing subject). |
| A user cannot link Google from the profile: *That Google account could not be linked. It may already be linked to an account* | The Google account was used to sign in before the user linked it, under an address that differs from their Arena address, so it created a separate Google-first account that holds the subject. If that signup was never finished, the user can resolve it alone: sign in with Google again, and on the completion step choose **Already have an Arena account under another email?**. If it was completed, an `ARENA_ADMIN` unlinks Google from the mistaken account on its admin profile (**Unlink Google**), after which the user links from their real profile. |

## Rotating the secret

Under **Clients**, open the client and **Add secret**. Update
`NOCA_ARENA_GOOGLE_OAUTH_CLIENT_SECRET`, restart Arena, confirm a sign-in
succeeds, then delete the old secret in the console. Sign-ins in flight during
the restart are asked to start again; nothing persistent references the secret.
