# Mobile access via cloudflared

The study companion app is desktop-first but as of 2026-05-18 has a
mobile responsive layer that makes it usable on iPhone for the
"in-between minutes" use case Bryan flagged — reading existing work,
typing short messages, dictating ideas/prayer.

## What's been done on the app side

1. **Mobile responsive CSS** (`static/study.css`, after line 1610) —
   `@media (max-width: 700px)` block that collapses to single column,
   hides crowded stripes, bumps tap targets to iOS 44pt minimum, sets
   `font-size: 16px` on inputs to prevent the iOS-Safari zoom-on-focus,
   and respects `env(safe-area-inset-bottom)` for the home-indicator.

2. **HTTPS-aware session cookies** (`app.py:144`) — when the request
   comes through Cloudflare (`X-Forwarded-Proto: https`), `ProxyFix`
   surfaces it so Flask sees the public scheme. Setting
   `LOGOS_FORCE_HTTPS_COOKIE=1` in the env then turns on the
   `Secure` flag on the session cookie. iOS Safari accepts HTTPS-set
   cookies cleanly; without `Secure` on a Cloudflare connection,
   Safari is finicky about cross-redirect cookie retention.

3. **`viewport` meta tags** added to the four `sermons/*.html`
   templates that didn't have them. The `study_*.html` templates
   already had them.

## What you need to do for Cloudflare

You said the Mac is already cloudflared as `mightyoakshybrid`. To add
the study companion (port 5111), pick one of two patterns:

### Pattern A: a new hostname (clean separation)

In `~/.cloudflared/config.yml` (whichever path you're using):

```yaml
tunnel: <your-tunnel-id>
credentials-file: /Users/family/.cloudflared/<tunnel-id>.json

ingress:
  # existing route — keep this
  - hostname: mightyoakshybrid.something.com
    service: http://localhost:3000

  # NEW route
  - hostname: study.mightyoakshybrid.something.com   # or any subdomain you own
    service: http://localhost:5111

  - service: http_status:404
```

Then `cloudflared tunnel route dns <tunnel-id> study.mightyoakshybrid.something.com`
and `pm2 restart` whatever runs cloudflared. The phone hits
`https://study.mightyoakshybrid.something.com/login`.

### Pattern B: path-based routing on the existing hostname

```yaml
ingress:
  - hostname: mightyoakshybrid.something.com
    path: ^/study/.*
    service: http://localhost:5111
  - hostname: mightyoakshybrid.something.com
    service: http://localhost:3000
  - service: http_status:404
```

Caveat with B: the app's routes are rooted at `/` (e.g. `/login`,
`/sermons/`, `/study/*`). Path-based routing only catches `/study/*`;
hitting `/login` or `/sermons/` would land on the LMS app. You'd need
to set `APPLICATION_ROOT=/study` and configure Flask URL prefixes —
more work. **Recommend Pattern A.**

## After Cloudflare is wired

On the Mac:

```bash
# Turn on the Secure cookie flag so iOS Safari keeps the session
export LOGOS_FORCE_HTTPS_COOKIE=1
pm2 restart study-companion --update-env
```

The `--update-env` flag forces PM2 to pick up the new env var.
Verify with `pm2 env 2` (the process id for study-companion).

## Microphone on iOS Safari

Once HTTPS is live, the existing dictation flow works on iPhone:
`navigator.mediaDevices.getUserMedia()` requires a secure context;
Cloudflare provides that. First mic press will trigger an iOS
permission prompt — accept it and it sticks for the site.

## Known mobile UX bugs/limitations

- **Outline drawer on phone** is a single column at the bottom of the
  page rather than a side panel. Pleasant for reading, awkward for
  multi-tap reordering. If you find yourself reordering on phone,
  flag it and we'll add a fullscreen mode.
- **`/study/patterns` charts** are still horizontal-scroll. Acceptable
  but not pretty.
- **Series-split modal** clamps to viewport-width-minus-24px on
  phone. Functional but cramped — defer fixing unless you try to
  split a series from phone.
