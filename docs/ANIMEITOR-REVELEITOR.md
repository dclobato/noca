# Using the NOCA Export with maratona-animeitor

This document explains how to use the Animeitor-compatible ZIP exported by NOCA
with the [maratona-animeitor](https://github.com/wuerges/maratona-animeitor)
tools: the **animeitor** (live animated scoreboard) and the **reveleitor**
(post-freeze revelation).

## Prerequisites

- A built copy of the `simples` binary from the maratona-animeitor repository
- A TOML configuration file describing your contest (sedes, team regex patterns,
  medal positions)
- A NOCA contest with at least one team and one problem

## Exporting the ZIP from NOCA

1. Log in as a contest **Admin** or **Uberadmin**.
2. Navigate to **Administration > Export/Import**.
3. Click **Download Animeitor ZIP**.

The ZIP can be exported at any time:

- **During the contest** — for a live animeitor scoreboard that updates on each
  new download.
- **After the contest** — for post-contest revelation. Make sure all submissions
  have been judged before exporting so that no runs appear as `?` (pending).

## ZIP contents

The export produces a ZIP with five files at the root:

| File | Content |
|------|---------|
| `contest` | Contest metadata, team list, timing, freeze |
| `runs` | Full submission history with real (unmasked) verdicts |
| `time` | Elapsed contest time in seconds |
| `version` | Protocol version (`1.0`) |
| `icpc` | Empty (required by consumer layout) |

Fields in `contest` and `runs` use the ASCII File Separator (`0x1C`) as
delimiter. Run times are in ICPC-rounded minutes. The penalty field is
hardcoded to `20` for consumer compatibility.

Verdicts are **not** freeze-masked — the consumer reapplies freeze locally
using the `score_freeze_time` from the `contest` file.

## TOML configuration file

The `simples` binary requires a TOML file describing the contest structure.
Create one matching your NOCA contest:

```toml
[titulo]
name = "My Contest 2026"
codes = [".*"]          # regex matching all team usernames
ouro   = 3              # gold medal up to rank 3
prata  = 6              # silver medal up to rank 6
bronze = 9              # bronze medal up to rank 9
```

If your contest has multiple sites/groups and you want separate scoreboards:

```toml
[titulo]
name = "My Contest 2026"
codes = [".*"]
ouro   = 3
prata  = 6
bronze = 9

[[sedes]]
name  = "Site-A"
codes = ["site_a_.*"]   # regex matching Site A team usernames
ouro  = 1
prata = 2
bronze = 3

[[sedes]]
name  = "Site-B"
codes = ["site_b_.*"]
ouro  = 1
prata = 2
bronze = 3
```

The `codes` patterns are matched against the `team_login` field in the exported
`contest` file, which corresponds to the NOCA `User.username`.

## Running the animeitor (live scoreboard)

### From a local file

```bash
./simples /path/to/animeitor-my-contest.zip \
    --port 8000 \
    --sedes config/my-contest.toml:default
```

Open `http://localhost:8000` in a browser to see the animated scoreboard.

### From a URL (polling mode)

If you serve the ZIP from a web server, `simples` can poll it every second:

```bash
./simples https://your-noca-server/path/to/export.zip \
    --port 8000 \
    --sedes config/my-contest.toml:default
```

> **Note:** NOCA does not currently expose the ZIP as an unauthenticated URL.
> To use polling mode, you would need to periodically re-download the ZIP and
> serve it from a static file server, or place it on a shared filesystem.

## Running the reveleitor (post-freeze revelation)

The reveleitor reveals frozen results one team at a time, from the bottom of
the standings upward — the classic ICPC-style revelation ceremony.

### Step 1: Generate a secret

```bash
SECRET=$(openssl rand -hex 4)
echo "Secret: $SECRET"
```

### Step 2: Start the server

```bash
./simples /path/to/animeitor-my-contest.zip \
    --port 8000 \
    --sedes config/my-contest.toml:default \
    --secret $SECRET
```

### Step 3: Open the reveleitor

Open the following URL in a browser:

```
http://localhost:8000/reveleitor.html?secret=<SECRET>
```

The reveleitor will:

1. Load all runs with their real verdicts.
2. Build the scoreboard with freeze applied.
3. Let you step through the revelation using keyboard controls.

### Step 4: Revelation controls

The reveleitor frontend is controlled via keyboard. Typical bindings:

- **Space** or **Enter** — reveal the next frozen result
- **Arrow keys** — navigate between teams

Refer to the maratona-animeitor documentation for the full key mapping.

## Important notes

### Verdicts are real, not masked

The exported `runs` file contains the actual final verdict for every submission,
including those submitted after the scoreboard freeze. The `simples` binary and
its consumers handle freeze masking internally — they use `score_freeze_time`
from the `contest` file to determine which results should be hidden and which
should be revealed.

### Penalty compatibility

The exported penalty is always `20`, regardless of the `wa_penalty` configured
in NOCA. This matches the maratona-animeitor consumer behavior, which hardcodes
penalty at `20` per wrong answer internally.

### Verdict mapping

| NOCA verdict | Exported status | Consumer behavior |
|---|---|---|
| AC | `Y` | Accepted, adds to solved count |
| PE (accept_pe=True) | `Y` | Treated as accepted |
| PE (accept_pe=False) | `N` | Wrong, adds 20 penalty |
| WA, RE, TLE, MLE, OLE | `N` | Wrong, adds 20 penalty |
| CE | `X` | Non-penalizing, ignored by scoreboard |
| Pending/judging | `?` | Shown as "waiting" in the UI |

### Timing

- The `time` file is in **seconds**.
- Run times in the `runs` file are in ICPC-rounded **minutes**.
- The `contest` file timing fields (duration, freeze) are in **minutes**.
- After the contest ends, the `time` file is clamped to `duration_minutes * 60`.

### Team identification

Teams are identified by their NOCA `username` (the `team_login` field in the
export). The institution field is filled with the contest name since NOCA does
not have a separate institution attribute. The display name is the user's
`fullname`.

### Best practices for revelation

1. **Wait for all judgments to complete** before exporting. Any submission still
   being judged will appear as `?` in the export, which the consumer treats as
   "waiting" — it will not be revealed.
2. **Export after the contest ends** so that the `time` file reflects the full
   contest duration and all submissions are captured.
3. **Test the export** by loading it into `simples` and checking that teams,
   problems, and scores appear correctly before the live ceremony.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| No teams in scoreboard | Team usernames don't match TOML `codes` regex | Adjust the regex patterns in your TOML to match NOCA usernames |
| All runs show as `?` | Exported before judgments completed | Re-export after all submissions are judged |
| Wrong number of problems | TOML config not needed for problem count | Problem count comes from the ZIP; check the NOCA export |
| Reveleitor shows 403 | Wrong secret in URL | Use the exact secret passed to `--secret` |
| Scores don't match NOCA | `wa_penalty` differs from 20 in NOCA | Expected — the export always uses penalty=20 for consumer compatibility |
