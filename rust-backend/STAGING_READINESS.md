# Staging readiness for the three Rust read pilots

**Local acceptance is not a deployment approval.** Django remains the default.
No staging or production routes are enabled by this checklist.

## What can move, and what stays on Django

| Read family        | Candidate GET/HEAD paths                                 | Read includes to remove to roll back                              |
| ------------------ | -------------------------------------------------------- | ----------------------------------------------------------------- |
| Saved snippets     | `/api/v1/saved_snippets`, `/json/saved_snippets`         | `saved-snippets-routes.conf` and `saved-snippets-web-routes.conf` |
| Scheduled messages | `/api/v1/scheduled_messages`, `/json/scheduled_messages` | `scheduled-messages-routes.conf`                                  |
| Realm domains      | `/api/v1/realm/domains`, `/json/realm/domains`           | `realm-domains-routes.conf`                                       |

The read maps accept only the configured host and GET/HEAD requests for
these lists. The API uses Basic Auth; the web uses Django-issued signed
sessions. Django still owns login, password and CSRF handling, account
and session issuance, writes, OPTIONS, detail paths, and requests
outside the read maps. The tested configuration does not enable
write maps, reminder reads, or other route families. A
[separate local reminder rehearsal](README.md#rehearse-reminder-read-routing-and-rollback-locally)
covers Basic Auth, Django-issued sessions, TLS, Redis/RabbitMQ, and explicit
rollback. It is not part of this three-family browser gate.

## Reproduce the local gate

Set up the [private PostgreSQL fixture](README.md#rehearse-saved-snippet-read-routing-and-rollback-locally)
and follow the [Chrome rehearsal](README.md#verify-a-browser-session-across-the-three-read-routes)
inside a fresh Linux namespace with only loopback and no external routes.
Run from the fixture project directory with its test settings and
private PostgreSQL socket already selected. Use absolute paths for the
cached Chrome, Node, and Puppeteer:

```sh
env -i HOME="$HOME" PATH="$PATH" PYTHONDONTWRITEBYTECODE=1 \
  DJANGO_SETTINGS_MODULE="$DJANGO_SETTINGS_MODULE" \
  ISOLATED_PG_ROLE="$ISOLATED_PG_ROLE" ISOLATED_PG_SOCKET="$ISOLATED_PG_SOCKET" \
  PYTHONPATH="$PWD" ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  "$venv/bin/python" "$checkout/rust-backend/tests/check_read_proxy_browser.py" \
  --fixture-root "$fixture_root" --nginx /usr/sbin/nginx --openssl /usr/bin/openssl \
  --uwsgi "$venv/bin/uwsgi" --venv "$venv" \
  --chrome "$chrome" --node "$node" --puppeteer "$puppeteer" \
  --redis-server /usr/bin/redis-server \
  --rabbitmq-server /usr/lib/rabbitmq/bin/rabbitmq-server --epmd /usr/bin/epmd
```

This runs one real Django password/CSRF login and the three web GETs
in each of the Django, Rust, and restored Django states. Including the
login redirect's successful read, ten successful reads retain one signed
host cookie; each must charge Redis once and deliver one matching
RabbitMQ activity. Three reads with Rust stopped must return the
standard JSON `502` without charging quota or producing activity.
The proxy log must identify the intended upstream each time. The
[paired HTTP/TLS, Basic Auth, and HEAD checks](README.md#verify-quota-and-activity-together-over-private-https)
cover the other client and method combinations locally.

## Staging go/no-go

In a separately authorized staging environment with synthetic accounts:

1. **Before opt-in:** confirm nginx hostname and port select the same
   organization in both backends. Confirm least-privilege database grants
   for each read and its support operations, matching session configuration,
   the same Redis namespace and rules, and the intended RabbitMQ vhost and
   activity shards. Verify the declared ingress hostname and browser trust
   chain. Do not copy production credentials into the local fixture.
2. **After opt-in, one family at a time:** validate nginx before reload.
   Verify a small ordinary authenticated GET/HEAD sample and confirm the
   upstream, exact expected JSON/empty HEAD, owner or realm isolation,
   no duplicate quota or activity, and Django ownership of excluded
   methods. Check the expected production-like consumers and browsers.
3. **No-go or rollback:** any wrong upstream, mismatch, repeated or
   missing side effect, session break, or unacceptable error/latency
   blocks the rollout. Remove that family's read include(s), run
   `nginx -t` and reload, and verify Django serves the same account and
   preserves its session. No automatic failover is expected. Do not
   introduce deliberate production downtime to test this.

Actual ingress, SSO, deployed worker processing, representative load,
and Redis/RabbitMQ failure behavior have not been tested in the local
gates. Agree on acceptable latency and error thresholds, monitoring,
and a rollback owner before any real rollout. These rehearsals did not
add independent HTTP operations. See the [API inventory](API_INVENTORY.md)
for current coverage. Whole-backend completion is unmeasured.
