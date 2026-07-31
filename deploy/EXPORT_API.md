# Export API — mirroring sent programs into another platform

A read-only feed of the programs this platform has **sent**, for a sibling
service to mirror. Machine-facing: its own key, no session, no write access.

---

## What it returns

Programs with `sent_at` set — the curated output, i.e. what the Send action in
the dashboard stamps and emails.

Deliberately **not** `status = 'accepted'`: the accept/reject split was replaced
by the flat `sent_at` / `is_deleted` model, and `status='accepted'` now holds
only a few legacy rows that are all soft-deleted. Keying an export on it would
return a handful of stale records and then nothing, forever.

Withdrawn programs are **not** omitted — they come back with `is_deleted: true`
so the consumer can mirror the state change instead of watching a row silently
vanish.

## Setup (once, on the server)

Generate a key and put it in `settings.json` (git-ignored — never in `config.py`,
which is in a public repo):

```bash
cd ~/grants-monitor
.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(32))"
```

```json
{ "export_api_key": "<the generated key>" }
```

Then `pm2 restart grants-monitor --update-env`.

With no key configured the endpoint returns **404**, as if it were not
registered — an unconfigured deployment gives nothing away.

## Calling it

```
GET /api/export/grants?since=<cursor>&limit=<1..1000>
X-API-Key: <the key>
```

Both apps run on the same host, so the consumer calls it over loopback and the
endpoint never needs to be internet-reachable:

```bash
curl -s -H "X-API-Key: $KEY" "http://127.0.0.1:5000/api/export/grants?limit=50"
```

### Response

```jsonc
{
  "items": [ { /* see below */ } ],
  "next_cursor": "2026-07-28T12:53:25.046332|113775",
  "has_more": true
}
```

### Incremental sync

Store `next_cursor`, pass it back as `since`, repeat while `has_more` is true.
Persist it only after the batch is committed on your side — replaying a cursor
is safe (records are idempotent by `key`), losing one means a gap.

```python
cursor = load_cursor() or ""
while True:
    r = requests.get(URL, params={"since": cursor, "limit": 200},
                     headers={"X-API-Key": KEY}, timeout=30).json()
    for item in r["items"]:
        upsert(item)              # key on item["key"]
    cursor = r["next_cursor"]
    save_cursor(cursor)
    if not r["has_more"]:
        break
```

Hourly is ample — the scanner itself only runs once an hour.

## Record shape

| field | notes |
|---|---|
| `key` | **Primary key. Use this, not `id`.** Normalized URL; stable across re-discovery. |
| `id` | Internal row id. **Not stable** — the deferred-probe path hard-deletes rows and a later scan re-inserts them with a new id. Do not key on it. |
| `title`, `url`, `description` | As harvested from the source listing. |
| `detailed_description` | Body text from the program page (up to 5000 chars); may be empty. |
| `published_date` | `YYYY-MM-DD`, the announcement's publish date. |
| `deadline` | Free text as printed on the page (e.g. `01.08.2026`, or a phrase). |
| `deadline_date` | `YYYY-MM-DD` parsed from the above when parseable, else `""`. |
| `funding_amount`, `eligibility`, `application_url`, `contact_info` | Best-effort extraction; **may be empty**. Treat as hints, not guarantees. |
| `item_type` | `funding` or `news`. |
| `keywords_matched` | Array of the keywords that matched. |
| `site_name`, `site_category` | Source institution and its category. |
| `sent_at`, `found_at`, `updated_at` | UTC ISO-8601. |
| `is_deleted` | `true` once withdrawn — mirror the change, don't drop the row. |
| `status` | Legacy field. Ignore it; `sent_at` + `is_deleted` are authoritative. |

The extraction fields are regex-based over Turkish institutional pages and fail
open — empty means "not found", never "none exists". Anything shown to a user
should fall back to `url`.

## Security notes

- The key authenticates **only** `/api/export/grants`. It is rejected on every
  other route, including all writes, `/api/settings` and `/api/scan`.
- Compared with `secrets.compare_digest`, so a wrong key leaks nothing by timing.
- Rotate by changing `export_api_key` and restarting; human logins are unaffected.
- Do **not** hand out a copy of `grants_monitor.db` or mount it remotely. It is a
  single-writer SQLite database in WAL mode that the scanner writes to hourly;
  a second process reading it over a network mount risks corruption.
