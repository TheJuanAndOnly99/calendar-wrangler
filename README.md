# LFX Calendar Normalizer

The LFX subscription feed at
`https://webcal.prod.itx.linuxfoundation.org/lfx/<sub-id>_sub`
produces `.ics` output that is not fully RFC 5545 conformant. Google Calendar
silently drops a bunch of events when subscribed to it, in particular:

- **Every `RECURRENCE-ID` override** (moved / rescheduled meetings) — because
  the feed emits an `ATTENDEE;VALUE=TEXT:` line with an *empty* value on those
  events, which is malformed.
- **`DTSTAMP` / `CREATED` / `LAST-MODIFIED` with a `TZID=` parameter**, which
  RFC 5545 forbids (these must be bare UTC).
- **Occurrences past ~12 months** — the feed puts `UNTIL=21250305T180000Z`
  (year 2125) on every `RRULE`, which triggers overly conservative expansion
  in Google Calendar.

This repo ships two independent ways to serve a **cleaned** feed to Google
Calendar as a URL subscription, plus a local CLI for one-off testing.

| Method | Where does it run? | Auto-refresh? | Zoom passcodes stay private? | Setup |
| --- | --- | --- | --- | --- |
| **Cloudflare Worker** (`worker.js`) | Cloudflare edge | Live on every Google poll | Yes — never on public URL | ~5 min, free |
| **GitHub Actions cron** (`.github/workflows/refresh.yml`) | GitHub runner | Every 3h, commits to repo | Only if repo is private + PAT-in-URL | ~10 min, free |
| **Local CLI** (`normalize.py`) | Your PC | Only when you run it | Yes (local file) | Zero setup |

**Recommended: Cloudflare Worker.** It's the only option that keeps your
personalized subscription URL *and* the Zoom passcodes inside the file off any
public surface.

---

## Method A — Cloudflare Worker (recommended)

### 1. Sign up for Cloudflare (free)

- Create a free account at <https://dash.cloudflare.com/sign-up>.

### 2. Install Wrangler and deploy

You need Node.js installed. Then, from this folder:

```powershell
npm install -g wrangler
wrangler login
wrangler secret put LFX_ICS_URL
# Paste: https://webcal.prod.itx.linuxfoundation.org/lfx/<your-sub-id>_sub

# Optional but recommended: add a token so the Worker URL is not fully open.
wrangler secret put ACCESS_TOKEN
# Paste any long random string, e.g. openssl rand -hex 24.

wrangler deploy
```

Wrangler prints a URL like `https://lfx-calendar.<subdomain>.workers.dev`.

### 3. Test it

```powershell
curl "https://lfx-calendar.<subdomain>.workers.dev/?token=<your-token>" -o Calendar-clean.ics
```

You should get a `.ics` file. Open it in a text editor and confirm:

- Lines like `ATTENDEE;VALUE=TEXT:` (empty) are gone.
- `DTSTAMP` / `CREATED` / `LAST-MODIFIED` no longer carry `TZID=`.
- `UNTIL=` values are capped at 2028.

### 4. Subscribe Google Calendar

**Other calendars** (left sidebar) → **+** → **From URL** → paste
`https://lfx-calendar.<subdomain>.workers.dev/?token=<your-token>` → **Add
calendar**.

First sync can take up to a few hours (Google's own polling cadence). After
that it refreshes on its own.

### Deploying without the CLI

If you'd rather not install Wrangler:

1. Cloudflare dashboard → **Workers & Pages** → **Create** → **Worker**.
2. Give it a name (e.g. `lfx-calendar`), click **Deploy** with the placeholder.
3. Click **Edit code**, paste the contents of `worker.js`, click **Deploy**.
4. **Settings → Variables → Encrypted variables → Add**:
   - `LFX_ICS_URL` = your webcal URL (as `https://...`)
   - `ACCESS_TOKEN` = your random token
5. Copy the `.workers.dev` URL from the Worker overview page and use it as
   above.

---

## Method B — GitHub Actions cron

This works, but Zoom passcodes end up in a public GitHub file unless you
jump through PAT-in-URL hoops. Prefer Method A unless you already run
everything through GitHub.

1. `git init && git add . && git commit -m "initial" && git push` to a repo.
2. Repository secret **`LFX_ICS_URL`** = your webcal URL.
3. Actions → *Refresh cleaned LFX calendar* → Run workflow.
4. Subscribe Google Calendar to:
   `https://raw.githubusercontent.com/<you>/<repo>/main/Calendar-clean.ics`

If the repo is private, embed a fine-scoped classic PAT in the URL:
`https://<PAT>@raw.githubusercontent.com/<you>/<repo>/main/Calendar-clean.ics`.

The workflow runs every 3 hours (cron `17 */3 * * *`) and only commits when
the cleaned output actually changed.

---

## Method C — Local CLI

For one-off testing or a manual "clean once, import once" workflow:

```powershell
# Against a local dump of the feed:
python normalize.py --input Calendar.ics --output Calendar-clean.ics

# Or against the live URL:
$env:LFX_ICS_URL = "https://webcal.prod.itx.linuxfoundation.org/lfx/<id>_sub"
python normalize.py --output Calendar-clean.ics
```

Prints a summary of what was changed on stderr:

```
VEVENT count:                    before=86   after=86
Empty ATTENDEE lines:            before=10   after=0
TZID-on-timestamp lines:         before=171  after=0
Far-future UNTIL values:         before=48   after=0
```

Then in Google Calendar: **Settings → Import & export → Import** →
`Calendar-clean.ics`. This is a *one-time* import, not a live subscription.

---

## What the cleaner actually does

For each line of the feed, after unfolding continuations:

| Original | Rewritten |
| --- | --- |
| `ATTENDEE;VALUE=TEXT:` (empty value) | *removed* |
| `DTSTAMP;TZID=America/New_York:20260514T124824Z` | `DTSTAMP:20260514T124824Z` |
| `CREATED;TZID=America/New_York:20260514T124824Z` | `CREATED:20260514T124824Z` |
| `LAST-MODIFIED;TZID=America/New_York:20260514T124824Z` | `LAST-MODIFIED:20260514T124824Z` |
| `RRULE:...;UNTIL=21250305T180000Z;...` | `RRULE:...;UNTIL=20280101T000000Z;...` (only if UNTIL was past the cap) |

Set the `UNTIL_CAP` env var (both `normalize.py` and the Worker read it) if
you want a different horizon.

## Files

- `worker.js` — Cloudflare Worker (Method A).
- `wrangler.toml` — Worker deployment config.
- `.github/workflows/refresh.yml` — GitHub Actions cron (Method B).
- `normalize.py` — local CLI cleaner (Method C).
- `.gitignore` — keeps your personal `Calendar.ics` (with sub-id in its
  header) out of git.
