# Zulip Gold backend migration

See `MIGRATION_HANDOFF.md` in this directory for
the verified baseline, Multipass results, and remaining work.

The first Rust component chooses display colors for new channel subscriptions.
The Rust library builds without Python. While Django still handles
subscriptions, it can call the Rust implementation through a Python extension.
The Python implementation stays as the default and as the reference for
comparison. The Rust implementation accepts signed 64-bit recipient IDs,
matching the database ID type.

## Run the compatibility checks

Run from the repository root with Rust 1.89 or newer and a virtual environment
with CPython 3.10 or newer activated:

```sh
cargo test --manifest-path rust-backend/Cargo.toml --locked
python -m pip install ./rust-backend
ZULIP_RUST_STREAM_COLOR=1 python -m unittest discover -s rust-backend/tests
```

The comparison suite checks that the two implementations return the same
colors in the same order and that Rust leaves the input unchanged. It can run
without Django or the database. GitHub Actions runs these checks when the
Rust code or its Python reference changes.

To exercise the existing subscription tests with Rust, install the extension
in Zulip's own virtual environment in your provisioned development environment:

```sh
.venv/bin/python -m pip install ./rust-backend
ZULIP_RUST_STREAM_COLOR=0 ./tools/test-backend zerver.tests.test_subs
ZULIP_RUST_STREAM_COLOR=1 ./tools/test-backend zerver.tests.test_subs
```

If the Zulip virtual environment does not have pip, use
`uv pip install --python .venv/bin/python ./rust-backend` to install the
extension. Set `ZULIP_RUST_STREAM_COLOR=1` before starting a development server
to try the same path there. If you enable it without installing the extension,
Python reports the import error instead of silently running the old code.
Unset the variable to switch back.

`ZULIP_RUST_ALERT_WORDS=1` selects the Rust implementation for trimming and
deduplicating additions through `POST /api/v1/users/me/alert_words` and its
web app equivalent. It also executes the owner-scoped PostgreSQL delete
for `DELETE /api/v1/users/me/alert_words` and the matching web route.
Rust uses the cursor from Django's transaction. Django handles HTTP validation,
input lowercasing for deletion, cache invalidation, and post-commit events.
The addition bridge uses Python's Unicode lowercase rules for deduplication.

Reinstall the extension before enabling the flag to load the current
deletion function.

Run the existing alert-word and event tests in both modes:

```sh
ZULIP_RUST_ALERT_WORDS=0 ./tools/test-backend zerver.tests.test_alert_words zerver.tests.test_events.NormalActionsTest.test_alert_words_events
ZULIP_RUST_ALERT_WORDS=1 ./tools/test-backend zerver.tests.test_alert_words zerver.tests.test_events.NormalActionsTest.test_alert_words_events
```

`ZULIP_RUST_USER_STATUS=1` selects Rust for status response fields and the
rules for missing values and clearing an emoji in the user status API.
Django still handles permissions, emoji lookup, and database writes.

```sh
ZULIP_RUST_USER_STATUS=0 ./tools/test-backend zerver.tests.test_user_status
ZULIP_RUST_USER_STATUS=1 ./tools/test-backend zerver.tests.test_user_status
```

`ZULIP_RUST_BOT_STORAGE=1` selects Rust for computing changes to a bot's
character quota on `PUT /api/v1/bot_storage` and when embedded bots write
data. The database reads and writes still use Django.

```sh
ZULIP_RUST_BOT_STORAGE=0 ./tools/test-backend zerver.tests.test_embedded_bot_system
ZULIP_RUST_BOT_STORAGE=1 ./tools/test-backend zerver.tests.test_embedded_bot_system
```

`ZULIP_RUST_DRAFT_TEXT=1` selects Rust for normalizing bodies and channel
topics during draft creation and editing. The extension enforces the Django
character limits and NUL validation order, including for Unicode text.
Reinstall the extension before enabling the flag.

```sh
ZULIP_RUST_DRAFT_TEXT=0 ./tools/test-backend zerver.tests.test_drafts
ZULIP_RUST_DRAFT_TEXT=1 ./tools/test-backend zerver.tests.test_drafts
```

Set `ZULIP_RUST_DRAFT_TIMESTAMP=1` to round draft creation and edit
timestamps in Rust. Rust preserves microsecond precision and rejects
negative values after rounding. Django supplies the current time for
omitted timestamps and handles values outside Python's datetime range.

Set `ZULIP_RUST_DRAFT_INPUT=1` to validate draft JSON in Rust for
`POST` and `PATCH` through Django. Rust checks the entire batch before
Django resolves any recipients. Rust identifies the first ordinary schema
error and its field path. Django uses the existing translated API error
format. Rust keeps the last value of duplicate fields and preserves large
integers and special float values. Pydantic handles JSON parser failures
and inputs near its maximum JSON integer length.
Reinstall the extension before enabling this flag. Unset the flag and
restart Django to restore Pydantic validation for all draft inputs.

Set `ZULIP_RUST_DRAFT_CHANNEL_ACCESS=1` to resolve channel recipients
through Rust during draft creation and editing. The query uses Django's
PostgreSQL connection and checks the organization, archive status,
active subscriptions, and nested group membership. Reinstall the
extension before enabling this flag. Unset it and restart Django to
restore the ORM lookup.

Set `ZULIP_RUST_DRAFT_DIRECT_ACCESS=1` to validate direct-message
recipients in Rust for draft creation and editing. The query uses Django's
PostgreSQL connection and checks guest visibility before creating any
subscriptions. Rust uses Django's configured cross-realm bot emails.
Reinstall the extension before enabling the flag. Unset it and restart
Django to restore the ORM validator.

`ZULIP_RUST_DRAFT_CREATION=1` enables Rust bulk insertion for draft creation
through both the API and web endpoints. Rust uses the PostgreSQL cursor from
the existing Django transaction and returns IDs in request order. Django
validates recipients and schedules the owner-only event for delivery
after commit. This flag is independent of `ZULIP_RUST_DRAFT_TEXT`.
Reinstall the extension before enabling it. Unset it and restart Django to
restore ORM insertion.

```sh
for mode in 0 1; do
    ZULIP_RUST_DRAFT_CREATION=$mode ./tools/test-backend \
        zerver.tests.test_drafts zerver.tests.test_events.DraftActionTest
done
```

Reinstall the extension and set `ZULIP_RUST_DRAFT_EDIT=1` to execute the
owner-scoped draft update through Rust. Django validates the recipient and
uses its current transaction for the update and the `drafts/update` event.
Unset the flag to return to the ORM update. The flag is independent of text
normalization and draft creation.

```sh
for mode in 0 1; do
    ZULIP_RUST_DRAFT_EDIT=$mode ./tools/test-backend \
        zerver.tests.test_drafts zerver.tests.test_events.DraftActionTest
done
```

Reinstall the extension and set `ZULIP_RUST_DRAFT_GROUPS=1` to look up and
create direct-message groups in Rust during draft creation and editing.
The recipient validator runs before Rust uses Django's transaction cursor
to find or create the group, recipient, and subscriptions. PostgreSQL
enforces the same group uniqueness for both writers. A valid earlier draft
can create a group before a later draft in the batch fails validation.
The failed batch saves no drafts and emits no draft event.

Set `ZULIP_RUST_DRAFT_SERIALIZATION=1` to use the Rust draft response
fields in Django's draft listing, initial event state, and add and update
events. Django supplies the recipients from its existing lookup and cache.
The standalone Rust draft reader uses the same serializer.

Reinstall the extension before enabling the timestamp and serialization
flags. Unset the corresponding flag and restart Django to roll back each
bridge independently. Compare draft input, recipient lookup, timestamps,
and serialization using the same tests.

```sh
for mode in 0 1; do
    ZULIP_RUST_DRAFT_INPUT=$mode ZULIP_RUST_DRAFT_CHANNEL_ACCESS=$mode \
        ZULIP_RUST_DRAFT_DIRECT_ACCESS=$mode ZULIP_RUST_DRAFT_GROUPS=$mode \
        ZULIP_RUST_DRAFT_TIMESTAMP=$mode \
        ZULIP_RUST_DRAFT_SERIALIZATION=$mode ./tools/test-backend \
        zerver.tests.test_drafts zerver.tests.test_events.DraftActionTest \
        zerver.tests.test_home.HomeTest.test_limit_drafts
done
```

### Invalidate alert-word caches through Rust

Reinstall the extension and set `ZULIP_RUST_ALERT_WORD_CACHE=1` in Django.
The flag requires Zulip's default Memcached backend with one unauthenticated
server accepting the text protocol on a numeric loopback IP and port. This flag is independent
of `ZULIP_RUST_ALERT_WORDS`. Unset it to use Django's Memcached client.

Django resolves the active deployment prefixes and applies its cache key
version and any configured key function. Rust deletes the alert-word list
and matching automaton entries for each prefix and waits for a reply for
each delete. An unavailable cache or unrecognized reply raises a connection
error to the surrounding transaction. The standalone Rust HTTP listener
still serves alert-word reads.

For the real Memcached comparison, use a dedicated loopback server and set
`ZULIP_RUST_ALERT_WORD_CACHE_TEST_ADDRESS` to its IP and port. Run
`./tools/test-backend zerver.tests.test_alert_words.AlertWordMemcachedTest`
with the cache flag off and on. The connection failure case runs with the
Rust cache flag on.

### Share Django's alert-word cache namespace with zulip-http

On the same host as the Rust service, export the namespace using the
running deployment's Django settings:

```sh
./manage.py export_rust_alert_word_cache "$PWD/var/rust-alert-word-cache.json"
export ZULIP_RUST_ALERT_WORD_CACHE_MANIFEST="$PWD/var/rust-alert-word-cache.json"
```

The command uses the active deployment prefixes and Django's default key
function, including its configured prefix and version. It writes the JSON
with an atomic replacement. Publish it again when deployment prefixes or
Memcached settings change. The destination directory must already exist and
the service user needs read access to the file.

With the manifest configured and the usual [session settings](#read-alert-words-with-a-django-session)
in place, `/health/rust-alert-words` checks the current file and the
unauthenticated loopback Memcached connection. A missing or invalid file or
an unreachable server gives `503`. Republish the file or restore Memcached
to recover in the same Rust process. Use this check before enabling
[deletion through Rust](#delete-alert-words-through-rust).

Use the dedicated Memcached test address from the preceding section and
`ZULIP_RUST_HTTP_BIN` to run
`zerver.tests.test_alert_words.AlertWordsRustHTTPTest.test_alert_word_cache_manifest_readiness`.

### Test the Rust alert-word removal transaction

`http::alert_words::remove` takes exclusive access to a writable PostgreSQL
client, the cache manifest path, the authenticated user's ID and realm ID, and
words already lowercased with Python's rules. It deletes matching rows, clears
both realm caches using the current manifest, and returns the remaining words
after commit. A cache error rolls back the deletion. An empty list still clears
the caches and returns the current words. The [HTTP handler](#delete-alert-words-through-rust)
uses this transaction before publishing the event.

Run the opt-in integration test with an empty disposable PostgreSQL database
and a dedicated loopback Memcached instance. The test creates a `zulip` schema
and seeds both services. Use a new database for each run. Set the database DSN
and the Memcached address to those fixtures:

```sh
export ZULIP_RUST_ALERT_WORD_TEST_DATABASE='host=/var/run/postgresql dbname=zulip_rust_alert_word_test'
export ZULIP_RUST_ALERT_WORD_CACHE_TEST_ADDRESS=127.0.0.1:11212
export ZULIP_RUST_ALERT_WORD_TEST_DIR="$(mktemp -d)"
cargo test --manifest-path rust-backend/Cargo.toml --locked --features http \
    --test alert_word_removal -- --ignored
```

Remove the temporary directory and discard both service fixtures after the test.

## Run the mobile compatibility API over HTTP

`zulip-http` serves `GET /compatibility` and `HEAD /compatibility` directly in
Rust. Start it in the provisioned environment from the repository root so it
can read the compiled translation catalogs under `locale/`:

```sh
cargo run --manifest-path rust-backend/Cargo.toml --locked --features http --bin zulip-http -- 127.0.0.1:9997
```

In a second terminal in the same environment, use the development proxy to
route requests on the canonical development host to Rust:

```sh
source .venv/bin/activate
./tools/run-dev --rust-http-port 9997
```

The production configuration continues to route requests through Django.
The existing test cases run against Django by default. With the Rust server
running, run the same cases over HTTP by selecting its loopback port:

```sh
./tools/test-backend zerver.tests.test_compatibility
ZULIP_RUST_COMPATIBILITY_TEST_PORT=9997 ./tools/test-backend zerver.tests.test_compatibility
```

## Run the alert-word read API over HTTP

Set `ZULIP_RUST_DATABASE_URL` to a PostgreSQL connection string for the local
Zulip database and `ZULIP_RUST_EXTERNAL_HOST` to the base hostname without a
port. The database role needs read access to `zulip.zerver_userprofile`,
`zulip.zerver_realm`, and `zulip.zerver_alertword`. Use a local Unix socket
or loopback PostgreSQL connection.

```sh
export ZULIP_RUST_DATABASE_URL='host=/var/run/postgresql dbname=zulip user=zulip'
export ZULIP_RUST_EXTERNAL_HOST=zulipdev.com
cargo run --manifest-path rust-backend/Cargo.toml --locked --features http --bin zulip-http -- 127.0.0.1:9997
```

Send `GET /api/v1/users/me/alert_words` to the loopback listener with
`Host: <organization>.zulipdev.com` and HTTP Basic authentication. The user
name is the account email and the password is its API key. The base hostname
selects the root-domain organization. This direct development route supports
the standard realm subdomain layout.

To share the API quota, set `ZULIP_RUST_REDIS_URL` to Django's Redis database
0 and `ZULIP_RUST_RATE_LIMIT_RULES` to the effective
`RATE_LIMITING_RULES["api_by_user"]` as JSON, for example `[[60,200]]`. Use
`[]` for Django's empty rule list. Per-user PostgreSQL overrides take
precedence. `ZULIP_RUST_REDIS_KEY_PREFIX` defaults to the empty prefix used
outside tests. Basic Auth requests from the `internal` client are exempt
when the request originates at `127.0.0.1` or `::1`, or when
`ZULIP_RUST_DEBUG_RATE_LIMITING=1` mirrors Django's `DEBUG_RATE_LIMITING=True`.
Client names are case-insensitive for this check. Exempt requests still
authenticate and record activity when RabbitMQ is configured.

Set `ZULIP_RUST_CLIENT_IP_SOURCE=proxy` when the loopback listener is behind
a trusted proxy that overwrites `X-Real-IP` with the verified client address.
The supplied nginx include and the Rust read routes in `tools/run-dev` do
this. For a listener used directly, set `ZULIP_RUST_CLIENT_IP_SOURCE=peer`
to use the TCP peer address. With the setting unset, the server applies
the quota unless debug mode exempts the client.

To publish activity, set `ZULIP_RUST_AMQP_URL` to the AMQP endpoint and vhost
used by Django, including its credentials. Set
`ZULIP_RUST_USER_ACTIVITY_SHARDS` to Django's shard count if it differs from
`1`. The database role needs `SELECT` and `INSERT` on `zulip.zerver_client`
and `USAGE` on its ID sequence so new client names can be registered.
The PostgreSQL session remains read-only when activity is disabled. The
Redis and RabbitMQ settings are independent and optional for direct tests.
If PostgreSQL disconnects after startup, the next database-backed request
or readiness probe can reopen the connection. Requests that fail after
acquiring a connection return an error without retrying the operation.

If RabbitMQ disconnects after startup, the readiness probe or a subsequent
request reopens the connection and declares the activity and notification
queues. Requests fail with `503` while RabbitMQ is unavailable. The listener
does not resend a publication when its confirmation failed. If RabbitMQ
loses the connection after a database commit, the data change may already
be visible when the client receives `503`.

In a provisioned Zulip development environment, the comparison test starts Django
and the Rust binary on loopback and gives them the same test database with
committed fixtures.

```sh
source .venv/bin/activate
cargo build --manifest-path rust-backend/Cargo.toml --locked --features http --bin zulip-http
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ./tools/test-backend zerver.tests.test_alert_words.AlertWordsRustHTTPTest
```

This suite also terminates the Rust connection in the test database and
checks that the same Rust process serves the original response after reconnecting.

To include the shared Redis and RabbitMQ test, create a dedicated RabbitMQ
vhost beginning with `zulip-rust-test-` and grant the development broker
user access. The test purges and reads its own activity queues in that vhost.

```sh
sudo rabbitmqctl add_vhost zulip-rust-test-alert-words
sudo rabbitmqctl set_permissions -p zulip-rust-test-alert-words zulip '.*' '.*' '.*'
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ZULIP_RUST_ACTIVITY_TEST_VHOST=zulip-rust-test-alert-words \
    ./tools/test-backend zerver.tests.test_alert_words.AlertWordsRustHTTPTest
```

### Test RabbitMQ recovery

Use a separate test vhost so the recovery test can inspect and purge its
own queues. A TCP relay interrupts only the Rust listener's broker
connection. The test checks that a rejected deletion leaves the draft
intact, that readiness recovers on the same listener after 90 seconds,
and that the next deletion publishes one event for the owner.

```sh
sudo rabbitmqctl add_vhost zulip-rust-test-broker-recovery
sudo rabbitmqctl set_permissions -p zulip-rust-test-broker-recovery zulip '.*' '.*' '.*'
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ZULIP_RUST_BROKER_RECOVERY_TEST_VHOST=zulip-rust-test-broker-recovery \
    ./tools/test-backend zerver.tests.test_rust_broker_recovery
sudo rabbitmqctl delete_vhost zulip-rust-test-broker-recovery
```

## Read alert words with a Django session

The direct Rust listener also serves `GET /json/users/me/alert_words` and
`HEAD` when session authentication is enabled. It reads Django's session
table and checks the signature and the current password-bound session hash.
The database role needs `SELECT` on `zulip.django_session` in addition to the
permissions above.

Use a private file containing only the current Django `SECRET_KEY`. Set
`ZULIP_RUST_DJANGO_SECRET_KEY_FILE` to its path and set
`ZULIP_RUST_DJANGO_AUTH_BACKENDS` to a JSON list of enabled Zulip authentication
backends whose sessions Rust should accept. For example, to accept development
login sessions:

```sh
export ZULIP_RUST_DJANGO_SECRET_KEY_FILE=/path/to/private/django-secret-key
export ZULIP_RUST_DJANGO_AUTH_BACKENDS='["zproject.backends.DevAuthBackend"]'
```

`ZULIP_RUST_SESSION_COOKIE_NAME` and `ZULIP_RUST_CSRF_COOKIE_NAME` default to
`sessionid` and `csrftoken`. When testing settings with secure cookie names,
set them to `__Host-sessionid` and `__Host-csrftoken`. The current implementation
uses the standard cookie path, SameSite policy, CSRF cookie age and login URL.
It reads database-backed sessions, including Zulip's cached database engine,
through PostgreSQL. Django manages session writes, key rotation and login.

Django and Rust allow these read methods without a CSRF token. Rust replaces a
malformed CSRF cookie and keeps the response private. Run
`AlertWordsRustHTTPTest.test_alert_words_session_and_csrf_parity` with
`ZULIP_RUST_HTTP_BIN` as above. The test creates its own committed sessions.

## Delete alert words through Rust

On the host running the Rust listener, run the exporter using the deployment's
Django settings and Python interpreter. The cache manifest is reloaded on each
deletion. Rust loads the Unicode tables at startup. Re-export the tables and
restart Rust when the Python runtime changes.

```sh
./manage.py export_rust_alert_word_cache "$PWD/var/rust-alert-word-cache.json" \
    --lowercase-output "$PWD/var/rust-alert-word-lowercase.json"
export ZULIP_RUST_ALERT_WORD_CACHE_MANIFEST="$PWD/var/rust-alert-word-cache.json"
export ZULIP_RUST_ALERT_WORD_LOWERCASE_FILE="$PWD/var/rust-alert-word-lowercase.json"
```

With the [alert-word read settings](#run-the-alert-word-read-api-over-http),
including `ZULIP_RUST_AMQP_URL`, start `zulip-http` to serve
`DELETE /api/v1/users/me/alert_words` with Basic Auth. The database role also
needs `DELETE` on `zulip.zerver_alertword`. The handler targets the default
`notify_tornado` queue unless [Tornado routing is configured](#route-write-events-to-multiple-tornado-processes).
The cache requires one unauthenticated loopback Memcached server using the
text protocol.

For `DELETE /json/users/me/alert_words`, also configure
[Django session authentication](#read-alert-words-with-a-django-session) and
set `ZULIP_RUST_WEB_POST_SCHEME` to the external scheme (`http` or `https`).
Use the `X-CSRFToken` header for this method. Rust checks any supplied Origin
against the request's origin. Under HTTPS, it requires a secure same-origin
Referer when the Origin header is absent.

The handler accepts the `alert_words` JSON array of strings from a form or the
query string. It clears both realm caches before database commit and publishes
the remaining list just for the affected user after commit. Unavailable cache
or broker services return `503`. A broker failure after commit can leave the
deletion persisted with its notification unconfirmed.

These settings enable DELETE on the direct listener. See the separate routing
steps for [the development proxy](#route-alert-word-deletion-through-the-development-proxy)
and [nginx staging](#stage-alert-word-deletion-in-nginx). Unset
`ZULIP_RUST_ALERT_WORD_LOWERCASE_FILE` and restart Rust to disable direct DELETE.
If using `ZULIP_RUST_WEB_POST_SCHEME` solely for alert-word deletion, unset it
as well.

For the comparison, set `ZULIP_RUST_ACTIVITY_TEST_VHOST` and
`ZULIP_RUST_ALERT_WORD_CACHE_TEST_ADDRESS` to the dedicated test instances
described above. From the provisioned checkout, run:

```sh
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ./tools/test-backend zerver.tests.test_alert_words.AlertWordWriteHTTPTest.test_delete_http_cache_and_event_parity
```

Omit `ZULIP_RUST_HTTP_BIN` to run the Django baseline. The test uses a single
Tornado queue and compares its messages directly with the HTTP responses,
committed data and Memcached entries.

## Add alert words through Rust

After configuring [deletion](#delete-alert-words-through-rust), set
`ZULIP_RUST_ALERT_WORD_POSTS=1` and restart `zulip-http`. This enables `POST` on
the direct alert-word API and, with sessions and the external web scheme set,
on its web counterpart. The PostgreSQL role also needs `INSERT` on
`zulip.zerver_alertword` and `USAGE` on its ID sequence.

The handler checks the 100-character limit before trimming whitespace. It
discards empty words and matches existing words using the exported Python
lowercase rules. For duplicates within one request, it uses the last spelling
at the first occurrence's position. Like DELETE, it clears the realm caches
before commit and sends the updated list to the same user afterward.

A web POST accepts the form CSRF token or the `X-CSRFToken` header. The usual
POST method overrides for `GET`, `HEAD`, and `DELETE` also check CSRF. Remove
`ZULIP_RUST_ALERT_WORD_POSTS` and restart to disable direct POST. For proxy
routing, see [development](#route-alert-word-post-through-the-development-proxy)
and [nginx staging](#stage-alert-word-post-in-nginx).

Use the same fixtures as the DELETE comparison and run
`zerver.tests.test_alert_words.AlertWordWriteHTTPTest.test_post_http_cache_and_event_parity`.

## Route write events to multiple Tornado processes

Export the routing file with the deployment's Django settings after updating
its [Tornado sharding configuration](https://zulip.readthedocs.io/en/latest/production/system-configuration.html#tornado_sharding).
Django resolves its exact and regex hostname rules to an ordered list of
notification queues per organization.

```sh
./manage.py export_rust_tornado_routing "$PWD/var/rust-tornado-routing.json"
export ZULIP_RUST_TORNADO_ROUTING_FILE="$PWD/var/rust-tornado-routing.json"
```

Start or restart Rust with the file and the deployment's `ZULIP_RUST_AMQP_URL`.
Rust chooses the recipient's queue using the same user ID modulo rule as
Django. This applies to alert-word and saved-snippet changes. A missing
organization in the file causes a write to return `503` before modifying data.
Re-export and restart Rust when organizations or shard assignments change.
For a single Tornado process, the default queue is `notify_tornado`.

The [HTTP comparison](#delete-alert-words-through-rust) fixtures also support
`zerver.tests.test_alert_words.AlertWordWriteHTTPTest.test_delete_tornado_shard_parity`.

## Route alert-word reads through the development proxy

Start the Rust HTTP server with the database and session settings above. Set
`ZULIP_RUST_EXTERNAL_HOST` to Django's base hostname without the port, then start
the proxy with both options:

```sh
./tools/run-dev --rust-http-port 9997 --rust-alert-words
```

The proxy forwards `GET` and `HEAD` on `/api/v1/users/me/alert_words`
and `/json/users/me/alert_words` when the hostname selects the same realm
in Django and Rust. Use a standard realm hostname such as
`zulip.zulipdev.com:9991` with the default development settings.
Custom host mappings such as the default `localhost:9991` mapping to the
`zulip` realm are served by Django when the Rust mapping differs. Django also
serves `OPTIONS`, `POST`, `DELETE`, and other methods on these paths.

To restore Django reads, stop the proxy and restart it without
`--rust-alert-words`. Keep `--rust-http-port` if you still want Rust to
serve the mobile compatibility route. A Rust upstream error uses the proxy's
normal error handling.

The proxy smoke test uses committed fixtures and writes only to a disposable
clone of `zulip_test_template` whose name starts with `zulip_rust_proxy_`.
Point both services and the test at that clone. Use `TEST_EXTERNAL_HOST=testserver`,
`EXTERNAL_HOST=testserver:9981`, and the Rust base host `testserver`.
Configure Rust with `ZULIP_RUST_RATE_LIMIT_RULES='[[60,200]]'` and a Redis key prefix
unique to the clone. The Rust rate header distinguishes the upstream from
Django, where the test settings disable rate limiting. Start the proxy with
`--test --streamlined --rust-http-port 9997 --rust-alert-words` and run:

```sh
DJANGO_SETTINGS_MODULE=zproject.test_settings ZULIP_RUST_PROXY_EXPECT_RUST=1 \
    ./manage.py shell --no-imports -c 'from runpy import run_path; run_path("rust-backend/tests/check_alert_words_proxy.py", run_name="__main__")'
```

Restart the proxy without `--rust-alert-words` and repeat with
`ZULIP_RUST_PROXY_EXPECT_RUST=0`, leaving the Rust service running.

### Route alert-word deletion through the development proxy

Start Rust with the [alert-word DELETE settings](#delete-alert-words-through-rust).
To send both `GET` and `HEAD` and physical `DELETE` requests to Rust, run:

```sh
./tools/run-dev --rust-http-port 9997 --rust-alert-words --rust-alert-word-deletes
```

The supported host and exact paths are the same as for reads. A physical POST
using `method=DELETE` follows the selected POST route. A disconnect during a
proxied DELETE returns `502`. Restart the proxy without
`--rust-alert-word-deletes` to restore Django deletion. The read flag can be
toggled independently.

Run the same proxy smoke test with `ZULIP_RUST_PROXY_EXPECT_DELETE=1` when
the delete flag is enabled and `0` after rollback. Set
`ZULIP_RUST_PROXY_EXPECT_RUST` to match the read flag.

### Route alert-word POST through the development proxy

Start Rust with [alert-word POST enabled](#add-alert-words-through-rust) and
add `--rust-alert-word-posts` to `run-dev` alongside the Rust port. The option
selects the physical POST method, including requests with a `method` override.
Restart without the option to restore Django POST. The read and delete options
can be left enabled. Set `ZULIP_RUST_PROXY_EXPECT_POST` to `1` or `0` when
running the proxy smoke test.

## Read saved snippets through Rust

Start `zulip-http` with the PostgreSQL and host settings used for the
alert-word API. The database role also needs `SELECT` on
`zulip.zerver_savedsnippet`. `GET /api/v1/saved_snippets` and its `HEAD`
variant use the same Basic authentication, Redis quota and RabbitMQ activity
configuration as alert-word reads. The activity query is `get_saved_snippets`.

To use this API through the development proxy, start it with:

```sh
./tools/run-dev --rust-http-port 9997 --rust-saved-snippets
```

The proxy sends `GET` and `HEAD` to Rust for hostnames that select the same
realm in Django and Rust. The web route and write methods have separate
switches. Restart the proxy without `--rust-saved-snippets` to return API
reads to Django.

With the same test binary and database setup as above, run the HTTP comparison:

```sh
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ./tools/test-backend zerver.tests.test_saved_snippets
```

Omit `ZULIP_RUST_HTTP_BIN` to run the same cases on Django alone.

## Read drafts through Rust

The direct listener serves `GET` and `HEAD` on `/api/v1/drafts` with Basic
Auth and on `/json/drafts` with the [Django session settings](#read-alert-words-with-a-django-session).
Use the same PostgreSQL, Redis and activity configuration as the other read
routes. The database role also needs `SELECT` on `zulip.zerver_draft`,
`zulip.zerver_recipient`, and `zulip.zerver_subscription`.

Rust checks the account's draft synchronization preference and returns only
its drafts. The response uses Django's ascending edit-time order, including
the same recipient IDs for channels, direct messages and drafts without a
recipient. See [proxy routing](#route-draft-reads-and-deletion-through-proxies)
to test the routes through development or staging.

```sh
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ./tools/test-backend zerver.tests.test_drafts
```

## Delete drafts through Rust

Set `ZULIP_RUST_DRAFT_DELETES=1` and `ZULIP_RUST_AMQP_URL` before starting
the listener. The database role needs `DELETE` on `zulip.zerver_draft`.
Use the Django session settings and `ZULIP_RUST_WEB_POST_SCHEME` for web
deletion. Web `DELETE` uses the `X-CSRFToken` header.

The operation requires draft synchronization for the authenticated account.
Only a successfully deleted row produces a `drafts/remove` event, published
to its owner's Tornado queue after commit. Missing drafts and drafts owned by
a different account return the same `404` response.

With a dedicated `ZULIP_RUST_ACTIVITY_TEST_VHOST`, the drafts test suite
compares both DELETE routes and checks concurrent requests. Remove the
`ZULIP_RUST_DRAFT_DELETES` setting and restart to disable direct deletion.

## Create drafts through Rust

Set `ZULIP_RUST_DRAFT_POSTS=1` and configure PostgreSQL and RabbitMQ as
for draft deletion. Set `ZULIP_RUST_MAX_MESSAGE_LENGTH` to Django's
`MAX_MESSAGE_LENGTH` and `ZULIP_RUST_CROSS_REALM_BOT_EMAILS` to a JSON array
of Django's `CROSS_REALM_BOT_EMAILS`. For `POST /json/drafts`, also configure
the Django session and `ZULIP_RUST_WEB_POST_SCHEME`.

The database role needs access to channel and direct-message membership and
permission to insert drafts, groups, recipients, and subscriptions. It also
needs to update the new group's recipient and use the corresponding sequences.
The response returns draft IDs in request order. After the batch commits, the
listener publishes a `drafts/add` event to the authenticated user, including
for an empty batch.

Run the direct comparison with a dedicated RabbitMQ test vhost.

```bash
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ZULIP_RUST_ACTIVITY_TEST_VHOST=zulip-rust-test-drafts \
    ./tools/test-backend zerver.tests.test_drafts.DraftCreateHTTPTest
```

Pass `--rust-draft-posts` alongside `--rust-http-port 9997` to enable
creation through the development proxy. For isolated nginx staging with
reads and deletes enabled, use `drafts-post-map.conf` in place of
`drafts-delete-map.conf` with the existing `drafts-routes.conf`. To roll
back creation, remove `--rust-draft-posts` or switch back to
`drafts-delete-map.conf` and reload nginx.

## Edit drafts through Rust

Set `ZULIP_RUST_DRAFT_PATCHES=1` to enable
`PATCH /api/v1/drafts/{draft_id}` and, with sessions and
`ZULIP_RUST_WEB_POST_SCHEME` configured, `PATCH /json/drafts/{draft_id}`.
Use the same PostgreSQL, RabbitMQ, message length, and cross-realm bot
settings as for [draft creation](#create-drafts-through-rust). The database
role needs permission to update drafts. Rust checks the input schema before
looking up the draft by ID and owner. It validates recipients after that
lookup and publishes a `drafts/update` event to the owner after commit.

Run the direct comparison with a dedicated RabbitMQ test vhost.

```bash
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ZULIP_RUST_ACTIVITY_TEST_VHOST=zulip-rust-test-drafts \
    ./tools/test-backend zerver.tests.test_drafts.DraftEditHTTPTest
```

Use `--rust-draft-patches` with `--rust-http-port 9997` for development.
For isolated nginx staging with the full set of draft operations enabled,
replace `drafts-post-map.conf` with `drafts-patch-map.conf` and retain
`drafts-routes.conf`. To roll back editing, remove `--rust-draft-patches`
or restore `drafts-post-map.conf` and reload nginx. Unset
`ZULIP_RUST_DRAFT_PATCHES` and restart the listener to disable direct editing.

## Route draft reads and deletion through proxies

With [Rust draft deletion enabled](#delete-drafts-through-rust), use the two
independent development flags for the same supported realm hosts as alert words:

```sh
./tools/run-dev --rust-http-port 9997 --rust-draft-reads --rust-draft-deletes
```

For nginx staging, include `drafts-delete-map.conf` in the HTTP context and
`drafts-routes.conf` in `app.d/`. Use the sample host and port only in the test
environment or replace them with the verified target values. Validate with
`nginx -t` and reload. This forwards `GET` and `HEAD` on the two collection
paths and physical `DELETE` on their numeric detail paths. Django handles
POST, PATCH, and OPTIONS. When the Rust upstream is down, nginx returns its
API error response for routed requests.

To roll back deletion alone, remove `--rust-draft-deletes` in development or
replace `drafts-delete-map.conf` with `drafts-map.conf` and reload nginx.
To roll back all draft routing, also remove `--rust-draft-reads` or remove
`drafts-routes.conf` and reload after validation.

### Rehearse draft composition and rollback

Use a new [private Django fixture](#rehearse-saved-snippet-read-routing-and-rollback-locally)
with `TORNADO_PROCESSES = 1` in its test settings and only loopback networking.
From the fixture project, use the cached Rust listener and executables:

```sh
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" \
  "$checkout/rust-backend/tests/check_draft_workflow.py" \
  --fixture-root "$fixture_root" --nginx /usr/sbin/nginx \
  --uwsgi "$venv/bin/uwsgi" --venv "$venv" --openssl /usr/bin/openssl \
  --rabbitmq-server /usr/lib/rabbitmq/bin/rabbitmq-server --epmd /usr/bin/epmd
```

The rehearsal creates an ordinary draft using Basic Auth, edits it
using a Django session and its normal CSRF header, and checks both
lists and empty HEAD responses. It sends the draft as a direct message
through Django, then issues the separate draft deletion the client
normally makes. It checks the other account cannot see the owner's
draft and reads each owner-only draft notification from the private
RabbitMQ broker. Fractional edit timestamps survive in storage while
the API and events retain their integer timestamps.

After stopping Rust, the selected draft lists return 502 and a retained
draft remains in storage. Removing the optional route include and
reloading nginx restores the same edited draft and session; the user
can still send it through Django and clear it. This tests ordinary
synthetic composition over private TLS, not browser login, live
Tornado delivery or production. No defaults or independent HTTP
coverage change.

### Rehearse scheduled-message writes, reads, and rollback

Use the [same private Django and RabbitMQ fixture](#rehearse-draft-composition-and-rollback), replacing the script in that command with
`$checkout/rust-backend/tests/check_scheduled_message_workflow.py`.
Use only disposable test data and a loopback-only network namespace.

Basic Auth schedules a future direct message. A Django session then
edits and cancels it with the normal CSRF header. These writes stay
on Django in both routing states. The opt-in Rust GET and HEAD routes
reflect the committed version, keep the other account's list empty,
and leave the owner-only broker events to Django. No delivery worker
runs and the scheduled message is not sent.

Stopping Rust produces 502 on the opted-in reads without deleting the
pending message. Removing the optional route and reloading nginx
restores the same card and session through Django. The owner can
reschedule and cancel it there. This is a private TLS rehearsal, not
a new route, a default-routing change, or a production result.

### Rehearse reminder creation, cancellation, and read rollback

Use the [same private Django and RabbitMQ fixture](#rehearse-draft-composition-and-rollback),
replacing the script with `$checkout/rust-backend/tests/check_reminder_workflow.py`.
The fixture creates a synthetic direct message; no delivery worker is run.

Basic and session requests create reminders through Django and cancel them
using the session's CSRF header. Both opt-in Rust lists show the committed
cards in delivery-time order, preserve the note and message reference,
return empty HEAD bodies, and do not show the owner's cards to the peer.
The harness verifies one owner-only broker event per write.

While Rust is stopped, its opt-in reads return 502, but Django can still
create and cancel reminders. Removing the route include and reloading
private nginx restores the same retained card and selected headers through
Django. No reminder is delivered. This test changes no route configuration,
default, or independent HTTP coverage count.

## Read organization linkifiers through Rust

The listener serves `GET` and `HEAD` on `/api/v1/realm/linkifiers` with Basic
Auth and on `/json/realm/linkifiers` with the [Django session settings](#read-alert-words-with-a-django-session).
The Rust database role needs `SELECT` on `zulip.zerver_realmfilter`. The
query reads the authenticated account's organization from PostgreSQL in
linkifier precedence order. It reads the saved templates without Django's
linkifier cache. The existing Redis quota and RabbitMQ activity settings
also apply to linkifier requests.

```sh
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ./tools/test-backend zerver.tests.test_realm_linkifiers
```

### Route linkifier reads through proxies

```sh
./tools/run-dev --rust-http-port 9997 --rust-linkifier-reads
```

For nginx staging, include `linkifiers-map.conf` in the HTTP context and
`linkifiers-routes.conf` in `app.d/`. Confirm that both services resolve the
allowlisted host and port to the same organization before enabling the routes.
`PATCH` and `OPTIONS` use Django. Remove the development option or the
`linkifiers-routes.conf` include and reload nginx after validation to roll back.

## Read allowed organization email domains through Rust

The listener serves `GET` and `HEAD` on `/api/v1/realm/domains` with Basic
Auth and on `/json/realm/domains` with the [Django session settings](#read-alert-words-with-a-django-session).
The Rust database role needs `SELECT` on `zulip.zerver_realmdomain`. The
response contains the authenticated organization's domains and their
`allow_subdomains` values. The existing Redis quota and RabbitMQ activity
settings apply to these reads.

```sh
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ./tools/test-backend zerver.tests.test_realm_domains
./tools/run-dev --rust-http-port 9997 --rust-realm-domain-reads
```

For nginx staging, include `realm-domains-map.conf` and
`realm-domains-routes.conf` in the same contexts as the linkifier files
above. Confirm that the allowlisted host and port resolve to the same
organization in both services. Domain creation, editing, and deletion
use Django. To roll back reads, remove the development option or the
`realm-domains-routes.conf` include and reload nginx after validation.

### Rehearse realm-domain read routing and rollback locally

Use the [private saved-snippet fixture](#rehearse-saved-snippet-read-routing-and-rollback-locally)
with `check_realm_domains_read_proxy.py` instead and omit `--requests`.
Use two synthetic organizations with distinct domain lists. The same
[optional loopback TLS check](#verify-the-local-tls-readiness-gate) also applies.
Ordinary authenticated members see their organization's list, including each
entry's `allow_subdomains` value. Compare the list without depending on row
order, but do not discard duplicates. Missing credentials, realm mismatches,
HEAD body handling, and routing of OPTIONS and detail GETs are also checked.

The existing read include opts in both the API and web paths on the
allowlisted host. When Rust stops, mapped GET and HEAD requests return
`502` and excluded requests stay on Django. Remove the single include, run
`nginx -t`, and reload the private nginx to restore both routes. The
rehearsal does not issue write requests or change the host configuration.
It does not validate production ingress, Redis quota, or RabbitMQ activity.

## Read custom profile field definitions through Rust

The listener serves `GET` and `HEAD` on `/api/v1/realm/profile_fields` with
Basic Auth and on `/json/realm/profile_fields` with the
[Django session settings](#read-alert-words-with-a-django-session). The Rust
database role needs `SELECT` on `zulip.zerver_customprofilefield`. The
response contains the authenticated organization's field definitions in
display order. The existing Redis quota and RabbitMQ activity settings
apply to these reads.

```sh
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ./tools/test-backend zerver.tests.test_custom_profile_data
./tools/run-dev --rust-http-port 9997 --rust-profile-field-reads
```

For nginx staging, include `profile-fields-map.conf` in the HTTP context
and `profile-fields-routes.conf` in `app.d/`. Confirm that the allowlisted
host and port resolve to the same organization in both services. `POST`,
`PATCH`, `OPTIONS` and field-specific URLs use Django. To roll back the
reads, remove the development option or the `profile-fields-routes.conf`
include and reload nginx after validation.

## Read navigation views through Rust

The listener serves `GET` and `HEAD` on `/api/v1/navigation_views` with Basic
Auth and on `/json/navigation_views` with the
[Django session settings](#read-alert-words-with-a-django-session). The Rust
database role needs `SELECT` on `zulip.zerver_navigationview`. The response
contains the authenticated user's saved navigation views. The existing
Redis quota and RabbitMQ activity settings apply to these reads.

```sh
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ./tools/test-backend zerver.tests.test_navigation_views
./tools/run-dev --rust-http-port 9997 --rust-navigation-view-reads
```

For nginx staging, include `navigation-views-map.conf` in the HTTP context
and `navigation-views-routes.conf` in `app.d/`. Confirm that the allowlisted
host and port resolve to the same organization in both services. This setup
sends `POST`, `OPTIONS` and fragment-specific URLs to Django. To roll back the reads,
remove the development option or the `navigation-views-routes.conf` include
and reload nginx after validation.

### Create navigation views through Rust

Set `ZULIP_RUST_NAVIGATION_VIEW_POSTS=1` and configure
[RabbitMQ activity and notifications](#run-the-alert-word-read-api-over-http).
Grant the Rust database role `INSERT` on `zulip.zerver_navigationview`
and `zulip.zerver_realmauditlog`, with access to the sequences used by
both tables. Browser POST also requires Django session settings and
`ZULIP_RUST_WEB_POST_SCHEME=http` or `https`, matching the external
scheme. The API uses Basic Auth.

Rust stores the view and its audit entry in one transaction. The owner's
`navigation_view/add` event is published after commit. If notification
confirmation fails after commit, the view and audit entry can already
exist when the client receives an error.

Run the direct comparison with a dedicated RabbitMQ test vhost. Omit
`ZULIP_RUST_HTTP_BIN` to run the Django baseline.

```sh
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ZULIP_RUST_ACTIVITY_TEST_VHOST=zulip-rust-test-alert-words \
    ./tools/test-backend zerver.tests.test_navigation_views
./tools/run-dev --rust-http-port 9997 --rust-navigation-view-posts --rust-navigation-view-reads
```

For nginx staging, replace the read map with
`navigation-views-post-map.conf` and keep the routes include. Validate
and reload nginx. Restore `navigation-views-map.conf` and reload to
return POST to Django while keeping GET and HEAD on Rust. In development,
remove `--rust-navigation-view-posts` for the same rollback.

### Edit and delete navigation views through Rust

Set `ZULIP_RUST_NAVIGATION_VIEW_DETAILS=1` with the same RabbitMQ, session,
and scheme settings as for creation. Grant the Rust database role
`UPDATE` and `DELETE` on `zulip.zerver_navigationview`. It also needs
the audit table and sequence permissions used for creation.

`PATCH` and `DELETE` use the authenticated user's fragment, including
fragments with slashes. Web requests use the CSRF header. Each patch
records the old and new value of each supplied field in the same transaction
as the update. Each successful delete records the deleted fragment.
The corresponding owner-only event is published after commit.

Use `--rust-navigation-view-details` in development. In nginx staging,
keep the navigation view collection config and add
`navigation-views-detail-map.conf` and `navigation-views-detail-routes.conf`
in the HTTP context and `app.d/` respectively. Make the host and port
match the collection map, validate and reload nginx. The detail route sends
physical PATCH and DELETE to Rust. Django handles OPTIONS and POST method
overrides. Remove the development option or the detail includes to return
the two methods to Django while preserving the collection routes.

## Read uploaded files through Rust

The listener serves `GET` and `HEAD` on `/api/v1/attachments` with Basic Auth
and on `/json/attachments` with the
[Django session settings](#read-alert-words-with-a-django-session).
Grant the Rust database role `SELECT` on `zulip.zerver_attachment`,
`zulip.zerver_attachment_messages` and `zulip.analytics_realmcount`.

The list contains the authenticated user's attachments and their linked
message IDs. Organization usage is the latest daily byte count plus
uploads at or after that count's cutoff. Rust calculates it from PostgreSQL
per request.

```sh
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ./tools/test-backend zerver.tests.test_attachments
./tools/run-dev --rust-http-port 9997 --rust-attachment-reads
```

For nginx staging, install `attachments-map.conf` in the HTTP context and
`attachments-routes.conf` in `app.d/` for the verified organization host
and port. Validate and reload nginx. To roll back, remove the development
option or remove the nginx routes include, validate and reload. Attachment
detail requests, uploads and OPTIONS use Django.

### Rehearse the attachment lifecycle and rollback

In a new [private Django fixture](#rehearse-saved-snippet-read-routing-and-rollback-locally),
seed the database before choosing an empty, owner-private local upload
directory under the fixture root. In that test settings module only,
set `DEVELOPMENT = True` for local file responses. Set `LOCAL_UPLOADS_DIR` to that directory
and `LOCAL_FILES_DIR` to its `files` subdirectory. From the fixture project,
inside the loopback-only namespace:

```sh
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" \
  "$checkout/rust-backend/tests/check_attachment_workflow.py" \
  --fixture-root "$fixture_root" --nginx /usr/sbin/nginx \
  --uwsgi "$venv/bin/uwsgi" --venv "$venv" --openssl /usr/bin/openssl
```

The rehearsal uploads two small text files through Django and links each
to a direct message, including a new upload while the Rust list is
selected. It compares Basic and Django-session lists, message links,
organization upload usage, and empty HEAD responses. The recipient can
download the shared bytes from Django without seeing the sender's files
in their own list. Rust outage leaves Django downloads working; removing
the optional route include and reloading nginx restores both lists on
the same session.

This gate uses private nginx/uWSGI, verified TLS, and local development
storage. It does not test production file offload, S3, usage under load,
request rate limits or activity delivery. The distinct routes, defaults,
and independent HTTP coverage do not change.

## Read custom emoji through Rust

Set `ZULIP_RUST_EMOJI_URL_BASE` to the public upload root from the Django
deployment. This enables `GET` and `HEAD` on `/api/v1/realm/emoji` with
Basic Auth and on `/json/realm/emoji` with the
[Django session settings](#read-alert-words-with-a-django-session).
The root is `/user_avatars/` for local uploads. For S3 it includes the
configured path and CDN or bucket host.

```sh
./manage.py shell --verbosity 0 -c 'from zerver.lib.upload import get_public_upload_root_url; print(get_public_upload_root_url())'
```

Grant the Rust database role `SELECT` on `zulip.zerver_realmemoji`. The
response includes active and deactivated uploads with a stored filename.
Animated emoji include the still image URL.

```sh
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ./tools/test-backend zerver.tests.test_realm_emoji
./tools/run-dev --rust-http-port 9997 --rust-realm-emoji-reads
```

For nginx staging, install `realm-emoji-map.conf` in the HTTP context and
`realm-emoji-routes.conf` in `app.d/` for the verified organization host and
port. Validate and reload. To return the reads to Django, remove the
development option or the nginx routes include and reload after validation.
Emoji uploads, deactivation and OPTIONS use Django.

## Read bot storage through Rust

The listener serves `GET` and `HEAD` on `/api/v1/bot_storage` with Basic
Auth and on `/json/bot_storage` with the
[Django session settings](#read-alert-words-with-a-django-session).
The Rust database role needs `SELECT` on `zulip.zerver_botstoragedata`.
The authenticated bot can read all of its own storage or request specific
keys with a JSON list. The existing Redis quota and RabbitMQ activity
settings apply.

```sh
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ./tools/test-backend zerver.tests.test_embedded_bot_system
./tools/run-dev --rust-http-port 9997 --rust-bot-storage-reads
```

For nginx staging, install `bot-storage-map.conf` in the HTTP context and
`bot-storage-routes.conf` in `app.d/` for the verified organization host
and port. Validate and reload. With this map, `PUT`, `DELETE`, and `OPTIONS`
use Django.
Remove the development option or the nginx routes include and reload after
validation to return reads to Django.

### Delete bot storage through Rust

Set `ZULIP_RUST_BOT_STORAGE_DELETES=1` for the listener. Web deletes also
require Django sessions and `ZULIP_RUST_WEB_POST_SCHEME=http` or `https`
to match the public origin. The database role needs `DELETE` and `UPDATE`
on `zulip.zerver_botstoragedata`. PostgreSQL requires the latter for
locking the requested rows before deletion.

Omit `keys` or pass JSON `null` to clear the authenticated bot's storage.
A list with a missing or repeated key fails without deleting any entries.

```sh
./tools/run-dev --rust-http-port 9997 --rust-bot-storage-reads --rust-bot-storage-deletes
```

For nginx staging, use `bot-storage-with-deletes-map.conf` in place of
`bot-storage-map.conf` with the same routes include. Restore the read-only
map, or remove `--rust-bot-storage-deletes` from the development proxy, to
return deletion to Django while keeping Rust reads active.

### Update bot storage through Rust

Set `ZULIP_RUST_BOT_STORAGE_UPDATES=1` and set
`ZULIP_RUST_USER_STATE_SIZE_LIMIT` to the same character limit as Django's
`USER_STATE_SIZE_LIMIT` (normally `10000000`). The development proxy and
nginx forward physical `PUT` requests. Web updates require the session and
public scheme settings used for web deletes. Grant `SELECT`, `INSERT`, and
`UPDATE` on `zulip.zerver_botstoragedata` and `USAGE` on its ID sequence to
the listener role.

Deploy the updated Django storage helpers and Rust delete handler before
routing `PUT` to Rust. Django, embedded bots, and Rust take the same
transaction lock for each bot. The quota counts Unicode code points in the
keys and values after replacement. A rejected batch leaves every entry
unchanged.

```sh
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ./tools/test-backend --parallel 1 --include-transaction-tests \
    zerver.tests.test_embedded_bot_system
./tools/run-dev --rust-http-port 9997 --rust-bot-storage-reads \
    --rust-bot-storage-deletes --rust-bot-storage-updates
```

For nginx staging, use `bot-storage-with-updates-map.conf` with
`bot-storage-routes.conf`. To roll back updates, restore
`bot-storage-with-deletes-map.conf` and validate and reload nginx. For the
development proxy, restart it without `--rust-bot-storage-updates`.

## Read channel folders through Rust

The listener serves `GET` and `HEAD` on `/api/v1/channel_folders` with Basic
Auth and on `/json/channel_folders` with the
[Django session settings](#read-alert-words-with-a-django-session). The Rust
database role needs `SELECT` on `zulip.zerver_channelfolder`. The response
contains the authenticated organization's channel folders in display order.
Pass `include_archived=true` to include archived folders. The existing Redis
quota and RabbitMQ activity settings apply to these reads.

```sh
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ./tools/test-backend zerver.tests.test_channel_folders
./tools/run-dev --rust-http-port 9997 --rust-channel-folder-reads
```

For nginx staging, include `channel-folders-map.conf` in the HTTP context
and `channel-folders-routes.conf` in `app.d/`. Confirm that the allowlisted
host and port resolve to the same organization in both services. `PATCH`,
`OPTIONS`, and the create and update URLs use Django. To roll back the reads,
remove the development option or the `channel-folders-routes.conf` include
and reload nginx after validation.

## Parse scheduled job input through Rust

Install a fresh PyO3 wheel and set `ZULIP_RUST_SCHEDULED_JOB_INPUT=1`
to parse strict JSON integers, recipient IDs or arrays, and booleans at
the scheduled-message and reminder endpoints. Pydantic handles malformed
values, invalid argument types, and integers outside the signed 64-bit
range. Unset the flag and restart Django to roll back independently of
the database flags below.

## Validate scheduled jobs and prepare message text through Rust

Install a fresh PyO3 wheel and set the applicable environment variables to
`1` in Django. Set `ZULIP_RUST_MESSAGE_BODY`, `ZULIP_RUST_CHANNEL_MESSAGE_TOPIC`,
`ZULIP_RUST_REMINDER_QUOTE`, and `ZULIP_RUST_SCHEDULED_DM_GROUPS` on the
delivery worker to use the applicable paths when sending scheduled jobs.

- `ZULIP_RUST_SCHEDULED_JOB_TIMESTAMP` compares a whole delivery second to
  the current time at microsecond precision for scheduled-message creation
  and edits and reminder creation. Python handles datetime conversion errors
  for timestamps outside its supported range.
- `ZULIP_RUST_SCHEDULED_MESSAGE_RECIPIENT` classifies channel and direct
  recipients on scheduled-message creation and edits. Django keeps the
  existing recipient lookup, deduplication order, and send permission checks.
- `ZULIP_RUST_MESSAGE_BODY` trims and truncates message bodies before the
  existing Markdown renderer for sends, edits, scheduled jobs, and mirrored
  email. `ZULIP_RUST_DRAFT_TEXT` also controls draft topic normalization.
- `ZULIP_RUST_CHANNEL_MESSAGE_TOPIC` trims channel send topics, recognizes
  the legacy empty-topic names, and reports invalid character positions.
- `ZULIP_RUST_REMINDER_QUOTE` builds the fenced quote and budgets its length
  alongside the localized context supplied by Django.
- `ZULIP_RUST_SCHEDULED_JOB_SERIALIZATION` uses the standalone Rust response
  types to assemble fields for Django list responses and add and update events.
- `ZULIP_RUST_SCHEDULED_DM_GROUPS` finds or creates the direct-message group,
  recipient, and memberships while preparing scheduled messages and reminders
  and while rechecking a scheduled direct message for delivery. Django still
  validates the recipients and applies direct-message permissions.
- `ZULIP_RUST_MESSAGE_CLIENT_DEFAULTS` applies the client rule for initially
  marking ordinary and scheduled messages read by their sender. Scheduled
  self-messages keep their existing exception when the caller omits the field.

Each flag can be unset independently followed by a restart of the affected
processes. Out-of-range limits and strings containing lone surrogates use
the existing Python paths where applicable.

## Check scheduled send permissions and resolve upload paths through Rust

Install a fresh PyO3 wheel and set the applicable flags to `1` in Django
and the scheduled delivery worker.

- `ZULIP_RUST_SCHEDULED_SEND_PERMISSIONS` reads nested posting and topic
  groups, existing topics, shared subscriptions for guests, direct-message
  permission groups, and sent conversation history. It also evaluates direct
  message system-group roles, inherited channel topic policy, and whether a bot
  channel has active subscribers. Django retains validation order, channel
  lookup, localized errors, and rate-limited notifications to bot owners.
- `ZULIP_RUST_REMINDER_ACCESS` looks up message receipt and restricted
  channel content access during reminder creation and delivery. The worker
  uses the stored reminder text when the referenced message is inaccessible.
- `ZULIP_RUST_SCHEDULED_ATTACHMENTS` writes or removes links to authorized
  attachments inside the scheduled job transaction. Public visibility is
  updated when a message using the attachment is delivered.
- `ZULIP_RUST_SCHEDULED_ATTACHMENT_ACCESS` checks direct-message receipt,
  active channel subscriptions, protected history, and nested content-access
  groups before scheduled creation or editing links an upload. It also refills
  an invalidated realm-public cache inside the existing transaction. Ownership
  and cached public access checks remain in Django. This flag is independent
  of `ZULIP_RUST_SCHEDULED_ATTACHMENTS`; either flag can be rolled back alone.
- `ZULIP_RUST_MARKDOWN_UPLOAD_PATHS` extracts path IDs from common local upload
  URLs used by the Markdown renderer. Other URLs are handled by the existing
  Python parser. Django still renders the HTML and authorizes attachment claims.
- `ZULIP_RUST_URL_FRAGMENTS` encodes channel and topic fragment components
  and sorts numeric direct-message slugs for links, including in reminder
  context. Python handles strings with lone surrogates and IDs outside
  the signed 64-bit range.

Unset any flag and restart the affected processes to roll back that slice.
To compare these bridges with their Django paths, run the endpoint,
rendering, upload, and URL suites with both flag values.

```sh
for mode in 0 1; do
    ZULIP_RUST_SCHEDULED_SEND_PERMISSIONS=$mode \
        ZULIP_RUST_REMINDER_ACCESS=$mode \
        ZULIP_RUST_SCHEDULED_ATTACHMENTS=$mode \
        ZULIP_RUST_SCHEDULED_ATTACHMENT_ACCESS=$mode \
        ZULIP_RUST_MARKDOWN_UPLOAD_PATHS=$mode \
        ZULIP_RUST_URL_FRAGMENTS=$mode ./tools/test-backend \
        zerver.tests.test_scheduled_messages zerver.tests.test_reminders \
        zerver.tests.test_markdown zerver.tests.test_upload.FileUploadTest \
        zerver.tests.test_url_encoding zerver.tests.test_topic_link_util
done
```

## Prepare reminder context and Markdown mention data through Rust

Install a fresh PyO3 wheel before enabling these flags in Django and the
scheduled delivery worker. Each flag is independent and can be unset followed
by a restart to return to the Python implementation.

- `ZULIP_RUST_REMINDER_CONTEXT` trims and validates reminder notes, classifies
  widget content, escapes channel and topic labels, and sorts silent direct
  recipient mentions. Django selects the translated context templates and
  conjunction and retrieves the referenced message and recipients.
- `ZULIP_RUST_MARKDOWN_ALERT_WORDS` matches overlapping alert words with the
  existing punctuation boundaries. Matchers are cached by the alert word
  snapshot, so changing words also changes the matcher. Python supplies Unicode
  lowercasing before Rust matches UTF-8 bytes.
- `ZULIP_RUST_MARKDOWN_CHANNEL_LINKS` finds channel and topic link candidates
  before rendering metadata is fetched. Channel and topic patterns retain
  their independent overlap rules.
- `ZULIP_RUST_MARKDOWN_MENTIONS` finds user and group mention candidates,
  classifies wildcards, and gives non-silent group mentions precedence.
  Strings containing lone surrogates use the Python scanner.
- `ZULIP_RUST_MARKDOWN_GROUP_MEMBERS` fetches active members of non-silent,
  active mentioned groups in one recursive query. Membership stays associated
  with each mentioned root when subgroups overlap.
- `ZULIP_RUST_MARKDOWN_FENCES` parses fenced block delimiters, language
  tags, and headers in the fenced block and list preprocessors. Quote
  completion uses the same parser. Strings containing lone surrogates use
  the Python parser.
- `ZULIP_RUST_MARKDOWN_LISTS` locates separators before hanging lists and
  changes in list type, preserving the existing case-sensitive quote rules.
  Decimal markers added after Unicode 13.0 use Python so differences between
  the regex and Python Unicode versions do not change list behavior.
- `ZULIP_RUST_MARKDOWN_BLOCKS` plans fenced block nesting and line handling,
  including quote default-language inheritance, spoiler resets, and the
  unfinished stack used by reminder quote completion. Python retains the
  existing formatting callbacks, content validators, syntax highlighting,
  and TeX rendering. Ordinary Unicode default languages use Python's
  lowercase rules before native planning; unencodable defaults and
  string subclasses stay on the Python path.
- `ZULIP_RUST_MARKDOWN_WHITESPACE` removes control placeholders, normalizes
  line endings, expands tabs by Unicode character columns, and normalizes
  blank lines before rendering and reminder quote completion.
- `ZULIP_RUST_MARKDOWN_QUOTE_MENTIONS` silences user, group, and wildcard
  mentions in quotes, preserving user replacement before group replacement.
- `ZULIP_RUST_MARKDOWN_INLINE_MENTIONS` builds span attributes, display text,
  and notification metadata for resolved users, groups, and wildcards.
  Django checks user visibility and name/ID agreement and supplies localized
  system group names. ElementTree serializes and escapes the span text.
- `ZULIP_RUST_MARKDOWN_TOPIC_ANCHORS` selects topic link anchors after Django
  checks channel content access. Shared history uses message ordering;
  protected history uses the existing user receipt ordering. Anonymous
  rendering does not reveal an anchor for protected history.

These bridges prepare rendering data and preprocessing decisions. The new
string bridges delegate lone surrogates to Python. Python-Markdown still
parses inline syntax, constructs the document tree, produces HTML, and manages
link and image previews. Django still checks permission to notify mentioned
groups and to use wildcard mentions. These flags do not route additional HTTP
operations to the standalone listener.

```sh
for mode in 0 1; do
    ZULIP_RUST_REMINDER_CONTEXT=$mode \
        ZULIP_RUST_MARKDOWN_ALERT_WORDS=$mode \
        ZULIP_RUST_MARKDOWN_CHANNEL_LINKS=$mode \
        ZULIP_RUST_MARKDOWN_MENTIONS=$mode \
        ZULIP_RUST_MARKDOWN_GROUP_MEMBERS=$mode \
        ZULIP_RUST_MARKDOWN_FENCES=$mode \
        ZULIP_RUST_MARKDOWN_LISTS=$mode \
        ZULIP_RUST_MARKDOWN_BLOCKS=$mode \
        ZULIP_RUST_MARKDOWN_WHITESPACE=$mode \
        ZULIP_RUST_MARKDOWN_QUOTE_MENTIONS=$mode \
        ZULIP_RUST_MARKDOWN_INLINE_MENTIONS=$mode \
        ZULIP_RUST_MARKDOWN_TOPIC_ANCHORS=$mode ./tools/test-backend \
        zerver.tests.test_markdown zerver.tests.test_reminders \
        zerver.tests.test_scheduled_messages zerver.tests.test_message_send \
        zerver.tests.test_message_edit zerver.tests.test_alert_words
done
```

## Run scheduled job database operations through Rust

Install a fresh PyO3 wheel in the Zulip environment, then set any of these
flags to `1` in the relevant Django or delivery worker process.

| Environment variable                | Database operation                                                                           |
| ----------------------------------- | -------------------------------------------------------------------------------------------- |
| `ZULIP_RUST_SCHEDULED_JOB_CREATION` | Insert validated scheduled messages and reminders in request order.                          |
| `ZULIP_RUST_SCHEDULED_MESSAGE_EDIT` | Save edits to a scheduled message after Django locks and validates it.                       |
| `ZULIP_RUST_SCHEDULED_JOB_CLAIM`    | Select the first due, undelivered, non-failed job by ID and acquire its PostgreSQL row lock. |
| `ZULIP_RUST_SCHEDULED_JOB_RESULT`   | Store the delivered message ID or delivery failure on the locked row.                        |

All operations use Django's existing database transaction. Django checks
permissions, renders content, claims attachments, sends messages, and
publishes owner events after commit. The worker's lock remains held through
delivery and the result write. Remove a flag and restart the corresponding
process to return that operation to Django.

```sh
for mode in 0 1; do
    ZULIP_RUST_SCHEDULED_JOB_INPUT=$mode \
        ZULIP_RUST_SCHEDULED_JOB_CREATION=$mode \
        ZULIP_RUST_SCHEDULED_MESSAGE_EDIT=$mode \
        ZULIP_RUST_SCHEDULED_JOB_CLAIM=$mode \
        ZULIP_RUST_SCHEDULED_JOB_RESULT=$mode ./tools/test-backend \
        zerver.tests.test_scheduled_messages zerver.tests.test_reminders \
        zerver.tests.test_events.ScheduledMessagesEventsTest \
        zerver.tests.test_events.RemindersEventsTest
done
```

## Read scheduled messages and reminders through Rust

The listener serves `GET` and `HEAD` on `/api/v1/scheduled_messages` and
`/api/v1/reminders` with Basic Auth. The corresponding `/json/` endpoints
use the [Django session settings](#read-alert-words-with-a-django-session).
The Rust database role needs `SELECT` on `zulip.zerver_scheduledmessage`,
`zulip.zerver_recipient`, and `zulip.zerver_subscription`. Each list
contains the authenticated user's undelivered items of the requested kind,
including failed deliveries, in scheduled time order. The existing Redis
quota and RabbitMQ activity settings apply to these reads.

```sh
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ./tools/test-backend zerver.tests.test_scheduled_messages zerver.tests.test_reminders
./tools/run-dev --rust-http-port 9997 --rust-scheduled-message-reads --rust-reminder-reads
```

Enable either development flag independently. For nginx staging, include
`scheduled-messages-map.conf` and `reminders-map.conf` in the HTTP context and
the corresponding `*-routes.conf` files in `app.d/`. Confirm that the
allowlisted host and port resolve to the same organization in both services.
Creation and editing use Django. Each proxy keeps its existing `OPTIONS`
handling. Per-item deletion has its own opt-in routing. To roll back either
read, remove its development flag or the corresponding routes include and
reload nginx after validation.

### Rehearse scheduled-message read routing and rollback locally

Use the [private fixture and command for saved snippets](#rehearse-saved-snippet-read-routing-and-rollback-locally),
but invoke `check_scheduled_messages_read_proxy.py` instead. Use fresh
synthetic users without existing scheduled messages, and do not run a worker.
The test sends only GET, HEAD, and OPTIONS. It never delivers, deletes, or
edits a scheduled message and does not exercise reminders.

The harness uses the existing scheduled-message read map and shared route
include for both the Basic Auth API and Django session web routes. It compares
direct Rust responses and each nginx route state, checks the owner's pending
and failed rows in delivery-time order, and confirms delivered rows and other
users' rows are absent. Successful GET responses are compared as JSON, with
list order preserved. Expires is compared relative to each response's Date,
not against a timestamp captured before benchmarking or reloading. Unsupported
detail reads, OPTIONS, and other realms remain on Django throughout.

There is no automatic failover. While the enabled Rust process is stopped,
mapped GETs return the standard JSON `502` and mapped HEADs return `502` with
no body. Removing the single scheduled-message include, running `nginx -t`,
and reloading the private nginx restores both routes to Django while Rust
remains stopped. The saved-snippet and scheduled-message tests share the
private process fixture but use separate routing definitions and test data.
The harness reports short local throughput and p95 samples, not production
performance.

### Rehearse reminder read routing and rollback locally

With the same disposable PostgreSQL fixture and private network namespace,
run `check_scheduled_messages_read_proxy.py` with
`--reminders --openssl /usr/bin/openssl` and omit `--requests`. It verifies
the existing read-only map for `/api/v1/reminders` and `/json/reminders`,
using Basic Auth and a Django-issued test session. The expected cards
include pending and failed reminders in delivery-time order, but exclude
delivered reminders and scheduled messages. Other owners see only their own
cards. OPTIONS, detail requests, and other hosts stay on Django. The
method check does not send write requests to either application.

Run `check_read_proxy_activity.py` with `--reminders`, the
[private Redis and RabbitMQ arguments](#verify-quota-and-activity-together-over-private-https),
and the same fixture settings. It verifies full reminder cards on healthy
GETs and empty HEAD responses, one Redis charge and one matching activity
per ordinary read, and no charge or activity on Rust-down probes. Removing
the existing reminder read include and reloading the private nginx
restores Django while Rust stays stopped. This does not exercise delivery
or deletion, browser login, or an actual staging or production deployment.

## Delete scheduled messages and reminders through Rust

Set `ZULIP_RUST_SCHEDULED_MESSAGE_DELETES=1` or
`ZULIP_RUST_REMINDER_DELETES=1` on the Rust listener. Each flag enables
`DELETE /api/v1/{scheduled_messages,reminders}/{id}` for its resource.
The `/json/` aliases require the [Django session settings](#read-alert-words-with-a-django-session)
and `ZULIP_RUST_WEB_POST_SCHEME` matching the external HTTP or HTTPS scheme.
Configure the existing [RabbitMQ event delivery settings](#route-write-events-to-multiple-tornado-processes).
The database role needs `SELECT` and `DELETE` on
`zulip.zerver_scheduledmessage` and `zulip.zerver_attachment_scheduled_messages`.

The transaction deletes the authenticated sender's row of the requested
kind and removes its attachment links. The attachment records and their
other links remain available. The listener publishes `scheduled_messages/remove`
or `reminders/remove` to the sender's Tornado queue after commit.

```sh
./tools/run-dev --rust-http-port 9997 --rust-scheduled-message-deletes --rust-reminder-deletes

# Use a dedicated RabbitMQ vhost with permissions for the Zulip test account.
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ZULIP_RUST_ACTIVITY_TEST_VHOST=zulip-rust-test-scheduled-delete \
    ./tools/test-backend zerver.tests.test_scheduled_messages.ScheduledJobDeleteHTTPTest
```

For nginx staging, use `scheduled-messages-delete-map.conf` or
`reminders-delete-map.conf` in place of the corresponding read-only map in
the HTTP context. Include the corresponding `*-routes.conf` in `app.d/`.
Match the allowlisted organization host and port to the staging deployment.
To revert deletion while keeping reads in Rust, remove the relevant development
flag or restore the read-only nginx map, validate the config and reload nginx.

## Stage saved-snippet reads in nginx

Start `zulip-http` on `127.0.0.1:9997` with the saved-snippet read settings,
`ZULIP_RUST_CLIENT_IP_SOURCE=proxy`, and the staging Redis and RabbitMQ
settings. The nginx `proxy` include supplies `X-Real-IP` from
`$trusted_remote_addr`, the same address sent to Django. For a staging
instance that accepts requests directly, set `$trusted_remote_addr` from
`$remote_addr`.

1. Include `saved-snippets-map.conf` from `rust-backend/staging/nginx/` in
   nginx's HTTP context. The sample verifies `zulip.testserver` with or
   without `:8880`. Substitute the hostname and port that select the same
   organization in Rust and Django.
2. Verify an authenticated `GET /api/v1/saved_snippets` directly on the
   Rust listener using the staging hostname. Place `saved-snippets-routes.conf`
   in the staging `zulip-include/app.d/` directory, run `nginx -t`, and reload.
3. Check `GET` and `HEAD` through nginx. Use `OPTIONS` and a test write on a
   disposable database to check the Django route. Compare an authenticated
   ordinary request, a loopback `internal` request, and a nonlocal `internal`
   request against the same quota. The nonlocal request remains subject to
   the quota even when it sends its own loopback `X-Real-IP`.

To roll back, remove `saved-snippets-routes.conf` from `app.d/`, run `nginx -t`,
and reload. While the Rust route is active, an unreachable Rust listener
produces the existing nginx JSON error response. nginx keeps the request on
the selected upstream to avoid duplicating quota and activity side effects.

### Enable saved-snippet creation

Set `ZULIP_RUST_SAVED_SNIPPET_WRITES=1` on `zulip-http` with the staging
`ZULIP_RUST_AMQP_URL`. For multiple processes, configure
[Tornado routing](#route-write-events-to-multiple-tornado-processes).
Use `ZULIP_RUST_MAX_MESSAGE_LENGTH` if Django's
`MAX_MESSAGE_LENGTH` differs from `10000`. The PostgreSQL role also needs
`INSERT` on `zulip.zerver_savedsnippet` and `zulip.zerver_realmauditlog`,
`SELECT` on the returned columns, and `USAGE` on both ID sequences.

To route `POST /api/v1/saved_snippets` on the verified staging host, replace
the read-only HTTP map with `saved-snippets-write-map.conf`, keep
`saved-snippets-routes.conf` installed, run `nginx -t`, and reload. In the
development proxy, use `--rust-http-port 9997 --rust-saved-snippet-writes`.
The `--rust-saved-snippets` option controls reads independently.

Rust commits the snippet and audit row together, then publishes the
`saved_snippets/add` event for the owner to RabbitMQ. If RabbitMQ cannot
confirm the publication after commit, the endpoint returns `503` and logs
the saved snippet ID. Check whether the snippet exists before resubmitting
an ambiguous result.

To roll back writes while keeping Rust reads, reinstall the read-only map
and reload nginx, or remove `--rust-saved-snippet-writes` from the development
proxy. The item routes have separate routing from the collection route.

Use the dedicated RabbitMQ vhost described in the alert-word instructions
to compare PostgreSQL rows and events from both implementations:

```sh
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ZULIP_RUST_ACTIVITY_TEST_VHOST=zulip-rust-test-alert-words \
    ./tools/test-backend zerver.tests.test_saved_snippets.SavedSnippetWriteHTTPTest
```

### Enable saved-snippet editing

The same `ZULIP_RUST_SAVED_SNIPPET_WRITES=1` setting enables `PATCH` on the
Rust listener. Grant the PostgreSQL role `UPDATE` on the `title` and
`content` columns of `zulip.zerver_savedsnippet`. A request can set either
field or both. Sending neither succeeds without changing the snippet or
sending a `saved_snippets/update` event. A committed update
publishes the full snippet to the owner's Tornado queue.

For nginx staging, include `saved-snippet-edit-map.conf` in the HTTP
context and add `saved-snippet-edit-routes.conf` to `zulip-include/app.d/`.
Use the verified realm hostname and port in the map, run `nginx -t`, and
reload. This map selects `PATCH /api/v1/saved_snippets/{id}`. Remove the
detail routes include and reload to return item requests to Django
independently of GET and POST on the collection.

For the development proxy, use `--rust-http-port 9997 --rust-saved-snippet-edits`.
The comparison command above also checks that patches preserve omitted fields,
deny edits to another user's snippet, and publish the same event as Django.

### Enable saved-snippet deletion

`ZULIP_RUST_SAVED_SNIPPET_WRITES=1` enables `DELETE` on the Rust listener.
Grant the PostgreSQL role `DELETE` on `zulip.zerver_savedsnippet`. Only the
authenticated owner's row is eligible for deletion. After PostgreSQL
commits, Rust publishes `saved_snippets/remove` with the deleted ID to the
owner's Tornado queue. A repeated deletion returns `404`. An existing
creation audit record is retained.

For nginx staging, replace `saved-snippet-edit-map.conf` with
`saved-snippet-delete-map.conf`, retaining the detail routes include. The
new map selects both PATCH and DELETE for the verified host. Run `nginx -t`
and reload. To roll back only DELETE, restore the edit map and reload.
For the development proxy, use `--rust-http-port 9997 --rust-saved-snippet-deletes`.

The HTTP comparison above also checks the deleted row, event payload,
and response for repeated or competing delete requests.

### Enable saved-snippet web reads

Configure the Django secret, authentication backends, and cookie names as
described in [Read alert words with a Django session](#read-alert-words-with-a-django-session).
The PostgreSQL role needs `SELECT` on `zulip.django_session` and
`zulip.zerver_savedsnippet`. Rust reads the signed session from PostgreSQL
and returns the authenticated user's snippets. GET and HEAD use the usual
Django behavior for safe methods, including replacement of a malformed
CSRF cookie. The read-only map sends writes and `OPTIONS` to Django.

For nginx staging, include `saved-snippets-web-map.conf` in the HTTP
context and add `saved-snippets-web-routes.conf` to `zulip-include/app.d/`.
Use the verified realm hostname and port in the map, run `nginx -t`, and
reload. The map selects `GET` and `HEAD` on `/json/saved_snippets`. Remove
the web routes include and reload to return reads to Django independently
of the API routes.

For the development proxy, pass `--rust-http-port 9997 --rust-saved-snippets-web`.
The HTTP comparison in `zerver.tests.test_saved_snippets` also checks
session authentication, requester isolation, and CSRF cookie behavior.

### Rehearse saved-snippet read routing and rollback locally

Use a fresh, owner-private temporary Django test fixture with canonical
`zulip` tables, populated synthetic users, and PostgreSQL listening only on
its private Unix socket. The fixture must run with `TEST_SUITE` and without
Tornado or RabbitMQ. Do not point this harness at a shared or production
database. Have the cached `zulip-http`, nginx, and uWSGI available.

From the fixture's project directory, with its isolated settings and
PostgreSQL socket environment selected:

```sh
env -i HOME="$HOME" PATH="$PATH" PYTHONDONTWRITEBYTECODE=1 \
  DJANGO_SETTINGS_MODULE="$DJANGO_SETTINGS_MODULE" \
  ISOLATED_PG_ROLE="$ISOLATED_PG_ROLE" ISOLATED_PG_SOCKET="$ISOLATED_PG_SOCKET" \
  PYTHONPATH="$PWD" ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  "$venv/bin/python" "$checkout/rust-backend/tests/check_saved_snippets_read_proxy.py" \
  --fixture-root "$fixture_root" --nginx /usr/sbin/nginx \
  --uwsgi "$venv/bin/uwsgi" --venv "$venv" --requests 32
```

The harness uses the checked-in read maps and route files, the normal
uWSGI transport over a private Unix socket, and an ephemeral loopback
nginx listener. The process and log fixture is independent of the
saved-snippet request cases. It validates direct Rust responses, reads
through nginx with and without the opt-in, and returns to Django by
removing each route include, validating nginx, and reloading. An
unavailable Rust process returns the existing JSON `502` for GET and
an empty `502` response for HEAD. There is no automatic failover to Django.

Request counts and timings describe only the local fixture. Conditions
include four client workers, three short samples per backend and path,
one uWSGI process with four threads, and no Redis, RabbitMQ, or external
load balancer. Treat them as a baseline, not production capacity.

#### Verify the local TLS readiness gate

For either the saved-snippet or scheduled-message rehearsal, add
`--openssl /usr/bin/openssl` (OpenSSL 3) to terminate TLS at the private
nginx listener. The harness generates a disposable certificate for
`127.0.0.1`, trusts it only in the test client, and checks that a client
without that trust rejects it. It does not change system trust or use host
certificates. Both upstream transports remain private: uWSGI on a Unix
socket and Rust HTTP on loopback. To mirror production cookie naming,
the isolated Django settings can use `__Host-sessionid` and
`__Host-csrftoken` and set `SESSION_COOKIE_SECURE` and
`CSRF_COOKIE_SECURE` to true.

Passing this gate demonstrates local TLS read and rollback parity for
the selected cases. It is not a deployment approval. Before any
production opt-in, verify the intended proxy and certificate chain,
production Redis integration, RabbitMQ activity and its routing, and
rehearse rollback on the intended topology. These
TLS transport checks alone do not test public ingress, browser login, or production load.
The timed requests open a new connection each time, so the TLS samples
include connection setup and should not be compared to production capacity.

#### Verify shared quota accounting for successful reads

From the same private fixture, invoke `check_read_proxy_quota.py` instead
of the saved-snippet script, replace `--requests 32` with
`--redis-server /usr/bin/redis-server`, and optionally keep
`--openssl /usr/bin/openssl`. The harness starts a new Redis process
on a private loopback port and observes its bucket through an owner-only
Unix socket. It never connects to an existing Redis service.

A fresh synthetic member, using Basic Auth and a Django session, makes
12 successful GET/HEAD requests per state across saved snippets,
scheduled messages, and realm domains. The harness checks each advances
the same real Redis bucket exactly once and returns matching remaining
and reset headers. The bucket is not reset when nginx opts into Rust or
when Django is restored with Rust stopped. A separate synthetic account
performs the bounded readiness probes.

This gate tests ordinary successful requests well below the fixture
limit. It does not test exhaustion, rejection or exceptional traffic,
RabbitMQ activity, public ingress, or production Redis configuration.

#### Verify activity delivery for successful reads

From the same private fixture, invoke `check_read_proxy_activity.py`
instead of the saved-snippet script. Replace `--requests 32` with
`--rabbitmq-server /usr/lib/rabbitmq/bin/rabbitmq-server --epmd /usr/bin/epmd`
(on Ubuntu, use the package's server script, not its system-service wrapper).
The optional `--openssl /usr/bin/openssl` tests client-to-nginx TLS.
The harness starts its own unprivileged broker and Erlang port mapper,
checks they listen only on loopback, and removes their scratch data. It
does not use the host broker, configuration, or credentials.

Across three route states, 36 ordinary successful GET/HEAD reads cover
saved snippets, scheduled messages, and realm domains using Basic Auth and
Django sessions. Two synthetic members exercise different activity shards.
The harness verifies the expected endpoint, user, client and timestamp in
each persisted event and checks there are no extra queued events. Manual
rollback restores Django while Rust remains stopped.

This checks the healthy local broker path. It does not guarantee
exactly-once delivery during failures, run production activity workers,
or exercise the production broker or ingress.

#### Verify quota and activity together over private HTTPS

Add `--redis-server /usr/bin/redis-server --openssl /usr/bin/openssl`
to the activity rehearsal. This runs the same six read routes with
Redis and RabbitMQ enabled together. For each of 36 healthy GET and HEAD
requests, the harness verifies one Redis charge in the requesting user's
bucket and one matching activity event on that user's shard. JSON and
empty HEAD responses are checked before, during and after the route change.
Each synthetic account uses 18 of its 100 test requests.

While the Rust process is stopped but the route includes still point to
it, one authenticated Basic GET and one session GET return the standard
JSON `502`. Neither Redis bucket changes and neither activity queue receives
an event. Removing the includes and reloading restores Django without
resetting the accounting.

This gate is local. It does not test real ingress or browser login,
deployed workers, production load, or broker/cache failures. Confirm
those separately on the intended staging topology before production opt-in.

#### Verify a browser session across the three read routes

Use the private fixture with `check_read_proxy_browser.py` instead of the
saved-snippet script. The entire fixture must run in a separate, unprivileged
Linux network namespace with only loopback enabled and no external routes.
Use a cached Chrome, Node and Puppeteer with their ordinary browser security
settings. The launcher uses only explicit headless/profile arguments and
rejects security-disabling browser flags. Replace `--requests 32` with
absolute `--chrome`, `--node`, and
`--puppeteer` paths, and specify `--openssl /usr/bin/openssl`. Use the secure
cookie settings from the TLS rehearsal.

The script creates a temporary NSS profile trust store for the disposable
certificate and uses the existing `zulip.testserver:8880` allowlist in the
private namespace. The real Django password form and CSRF token issue the
signed session. Chrome checks the Secure and HttpOnly host cookie and uses
it without another login for three web reads in each nginx state. The
script compares the JSON and checks the upstream from the private routing
log. When Rust stops, the same session sees the standard `502` on each
route and then reads successfully after manual rollback.

This does not exercise SSO, actual ingress, real user accounts, production
browser assets or load. It never changes the user's Chrome profile or
system trust. The test browser, temporary credentials, database and proxy
are discarded after the rehearsal. For the combined evidence and the
remaining go/no-go checks, see the [three-pilot staging readiness guide](https://github.com/mlkmhd/zulip-gold/blob/main/rust-backend/STAGING_READINESS.md).

### Enable saved-snippet web creation

Configure [Django sessions](#read-alert-words-with-a-django-session) and the
[write settings and database grants](#enable-saved-snippet-creation) for
`zulip-http`. Set `ZULIP_RUST_WEB_POST_SCHEME` to the scheme browsers use
to reach this listener through the proxy. Use `https` for a TLS-terminating
proxy and `http` for local HTTP development. Use one public scheme per
listener and redirect HTTP to HTTPS at the proxy for a TLS deployment.

This route targets Zulip's cookie-based CSRF settings, the default
`X-CSRFToken` header, and same-origin requests. Keep POST on Django for
deployments that use cross-origin CSRF trust, a custom CSRF header,
session-backed CSRF, or a shared CSRF cookie domain. Rust checks the Django
session before comparing the CSRF cookie with the form or header token.
It requires a matching Origin when present and a same-host HTTPS Referer
when Origin is absent on HTTPS. The configured scheme controls this check.

Replace `saved-snippets-web-map.conf` with
`saved-snippets-web-write-map.conf` in the nginx HTTP context. Keep the
same verified host and port and the same `saved-snippets-web-routes.conf`
include. Run `nginx -t` and reload. This map selects the collection route.
To return only POST to Django, restore the read-only map and
reload. nginx returns its JSON upstream error if Rust is unavailable.

With the development proxy, add `--rust-saved-snippets-web-writes` to
`--rust-http-port 9997`. Use `--rust-saved-snippets-web` for web reads.
Remove the write flag to return POST to Django.

Compare session and CSRF handling, the committed rows, and the
owner-only notification with the dedicated RabbitMQ test vhost.

```sh
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ZULIP_RUST_ACTIVITY_TEST_VHOST=zulip-rust-test-alert-words \
    ./tools/test-backend zerver.tests.test_saved_snippets.SavedSnippetWriteHTTPTest.test_saved_snippet_web_post_http_and_event_parity
```

### Enable saved-snippet web editing

Use the [web creation settings](#enable-saved-snippet-web-creation),
including `ZULIP_RUST_WEB_POST_SCHEME`, and grant the Rust database role
`UPDATE` on `zulip.zerver_savedsnippet`. For
`PATCH /json/saved_snippets/{id}`, Rust checks the Django session and
compares the CSRF cookie with the `X-CSRFToken` header before applying
the quota, recording activity, or updating the snippet. Django's PATCH
contract uses the header token. A committed edit publishes the full
snippet to the owner through Tornado.

In nginx staging, include `saved-snippet-web-edit-map.conf` in the HTTP
context and install `saved-snippet-web-edit-routes.conf` in
`zulip-include/app.d/`. Use the verified hostname and port, run
`nginx -t`, and reload. The edit map selects PATCH on the numeric detail
path. Django serves OPTIONS and POST method overrides on that path. An
unavailable Rust listener returns nginx's JSON upstream error. To return
all web detail methods to Django, remove the detail routes include and
reload.

In the development proxy, use
`--rust-http-port 9997 --rust-saved-snippets-web-edits`. Remove the edit
flag to return web PATCH to Django.

```sh
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ZULIP_RUST_ACTIVITY_TEST_VHOST=zulip-rust-test-alert-words \
    ./tools/test-backend zerver.tests.test_saved_snippets.SavedSnippetWriteHTTPTest.test_saved_snippet_web_patch_http_and_event_parity
```

### Enable saved-snippet web deletion

Use the [web creation settings](#enable-saved-snippet-web-creation) and
grant the Rust database role `DELETE` on `zulip.zerver_savedsnippet`.
`DELETE /json/saved_snippets/{id}` requires a Django session and the
`X-CSRFToken` header. After removing the owner's row, Rust publishes the
deleted ID to the owner's Tornado queue. The creation audit entry is
retained. A repeated deletion returns `404`.

For nginx staging, replace `saved-snippet-web-edit-map.conf` with
`saved-snippet-web-delete-map.conf` and retain the detail routes include.
The new map selects PATCH and DELETE for the verified host and port. Run
`nginx -t` and reload. Restore the edit map and reload to return DELETE
to Django while keeping PATCH on Rust.

In the development proxy, use
`--rust-http-port 9997 --rust-saved-snippets-web-deletes`. Remove the
delete flag to return DELETE to Django.

```sh
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ZULIP_RUST_ACTIVITY_TEST_VHOST=zulip-rust-test-alert-words \
    ./tools/test-backend zerver.tests.test_saved_snippets.SavedSnippetWriteHTTPTest.test_saved_snippet_web_delete_http_and_event_parity
```

## Stage alert-word reads in nginx

Start `zulip-http` on `127.0.0.1:9997` with PostgreSQL, session settings, and
the staging Redis and RabbitMQ settings. Use the snippets under
`rust-backend/staging/nginx/` in the staging instance:

1. Include `alert-words-map.conf` in nginx's HTTP context. The sample allows
   `zulip.testserver` with or without port `8880`. Replace this with the exact
   staging host and port after confirming that Django and Rust resolve it to
   the same organization.
2. Place `alert-words-health.conf` in the staging `zulip-include/app.d/`
   directory. Validate and reload nginx, then probe
   `GET /health/rust-alert-words` from loopback. Expect `200` with
   `{"ready":true}` before enabling the routes. The probe checks session
   configuration, reads the relevant PostgreSQL tables, pings Redis when
   configured, and checks the RabbitMQ connection and channel when configured.
   `503` indicates a running but unready service. Django's `/health` remains
   available separately.
3. Place `alert-words-routes.conf` in the same `app.d/` directory. Run
   `nginx -t` and reload the staging instance. The two alert-word routes
   send `GET` and `HEAD` to Rust for the allowed host. Every other method
   follows the existing Django uWSGI route.

To roll back, remove `alert-words-routes.conf` from the staging `app.d/`
directory, validate the configuration, and reload nginx. The readiness probe
can stay installed during rollback. An upstream failure uses the existing
API error response and requires an explicit routing rollback. This avoids
replaying a read that may already have updated quota or activity.

The smoke test above also runs against nginx with
`ZULIP_RUST_PROXY_PORT=8880`. Use `ZULIP_RUST_PROXY_EXPECT_RUST=1` with the
routing include installed, then `0` after removing it and reloading. During
testing, use a disposable Django database and dedicated dependency instances
when exercising outages.

### Stage alert-word deletion in nginx

Start Rust with the [alert-word DELETE settings](#delete-alert-words-through-rust)
and verify readiness. Keep the health and route includes from the read setup.
Replace `alert-words-map.conf` with `alert-words-delete-map.conf` in nginx's HTTP
context, using the same verified host and port. Run `nginx -t` and reload.
The replacement map also routes physical DELETE to Rust on both alert-word
paths. POST and OPTIONS follow the Django route. A Rust upstream failure uses
the API error response.

To restore only DELETE to Django, reinstall `alert-words-map.conf` in place of
the replacement, run `nginx -t`, and reload. To remove all alert-word routing,
remove `alert-words-routes.conf` and reload after validation.

### Stage alert-word POST in nginx

Start Rust with [alert-word POST enabled](#add-alert-words-through-rust).
Replace `alert-words-delete-map.conf` with `alert-words-write-map.conf` using
the same verified host and port. Validate with `nginx -t` and reload. The map
sends `GET`, `HEAD`, `DELETE`, and physical POST to Rust. `OPTIONS` continues
through Django. Reinstall the delete map and reload to roll back POST while
still serving reads and physical DELETE through Rust.

## Migration sequence

1. Port backend logic that does not depend on services. For each component,
   compare Rust and Python on the same inputs and rerun its existing Zulip
   integration tests against both paths.
2. Move whole API operations to Rust and replay the same HTTP requests against
   both servers using equivalent database snapshots. Compare responses,
   database writes, and emitted events. Keep API paths and behavior stable so
   existing clients and tests continue to work while routes move.
3. Move long polling, background workers, and integrations. Verify event
   ordering, retries, and cross-organization isolation before switching traffic.
4. Replace the Python deployment tooling, management commands, and test runner.
   Preserve both fresh installs and upgrades from supported releases: verify the
   Rust migration runner against the schemas and data produced by Django's
   historical migrations before retiring them. Remove each Python reference
   only after the equivalent Rust path is in place and its expected results
   are recorded in tests that run without Python.

## Parse Markdown headings through Rust

Rebuild and install the Python extension, then set
`ZULIP_RUST_MARKDOWN_HEADINGS=1` in Django processes to parse hash headings
through Rust. The parser returns the heading level, trimmed text, and the
surrounding blocks. Django constructs the elements and runs inline rendering.
Python handles strings containing lone surrogates. Unset the flag and restart
the processes to roll back independently of the other Markdown flags.

Run the same collections with the flag unset and set:

```bash
./tools/test-backend --parallel 1 zerver.tests.test_markdown_headings \
    zerver.tests.test_markdown zerver.tests.test_reminders
```

This bridge preserves the existing whitespace, escaped hash, closing hash,
and loose list continuation rules. It does not implement a standalone
Markdown renderer or route scheduled writes to the Rust HTTP listener.

## Parse Markdown horizontal rules and paragraphs through Rust

Rebuild and install the Python extension, then enable either flag in
Django processes:

- `ZULIP_RUST_MARKDOWN_HORIZONTAL_RULES=1` recognizes horizontal rules
  and splits the surrounding blocks, preserving the Python-Markdown
  marker count, indentation, and ASCII spacing rules.
- `ZULIP_RUST_MARKDOWN_PARAGRAPHS=1` drops blank blocks and prepares
  paragraph text, including text and sibling tails in tight lists.

Django constructs the elements and runs inline rendering. Both bridges
preserve Python whitespace semantics and delegate lone surrogates to Python.
Unset either flag and restart the processes to roll back that bridge
independently. The flags do not enable standalone scheduled message writes.

Run the same collections with both flags unset and set, then repeat the
first two collections with only one flag enabled at a time:

```bash
./tools/test-backend --parallel 1 zerver.tests.test_markdown_horizontal_rules \
    zerver.tests.test_markdown_paragraphs zerver.tests.test_markdown \
    zerver.tests.test_reminders
```

## Parse Markdown blockquotes and lists through Rust

Rebuild and install the Python extension, then enable any of these independent
flags in Django processes:

- `ZULIP_RUST_MARKDOWN_BLOCKQUOTES=1` locates blockquote boundaries and removes
  quote markers. It preserves empty quote detection, lazy continuations,
  nested quotes, separate quote blocks, and the existing recursion guard.
  Mention silencing still has its own `ZULIP_RUST_MARKDOWN_QUOTE_MENTIONS` flag.
- `ZULIP_RUST_MARKDOWN_LIST_BLOCKS=1` recognizes ordered and unordered lists
  and splits blocks into items, preserving start numbers and nested item
  grouping. This is separate from the `ZULIP_RUST_MARKDOWN_LISTS` preprocessor.
  Decimal digits added after Unicode 13.0 delegate the block to Python.
- `ZULIP_RUST_MARKDOWN_LIST_INDENT=1` calculates the indentation level and
  selects the parent along the last-child path, then removes complete
  two-space indentation while preserving dedented lines.

All three flags default to disabled. Strings containing lone surrogates use
Python. Negative detab levels also use Python. Django retains tree traversal,
element construction, recursive parsing, inline rendering, and serialization.
Unset any flag and restart the processes to roll back independently. These
bridges do not enable standalone scheduled message writes.

Run these collections with the flags unset and set, then repeat the fixture
and targeted rendering tests in all eight flag combinations:

```bash
./tools/test-backend --parallel 1 zerver.tests.test_markdown_blockquotes \
    zerver.tests.test_markdown_list_blocks zerver.tests.test_markdown_list_indent \
    zerver.tests.test_markdown zerver.tests.test_reminders
```

## Parse Markdown inline code through Rust

Rebuild and install the Python extension, then set
`ZULIP_RUST_MARKDOWN_INLINE_CODE=1` in Django processes. Rust finds backtick
spans and escaped backslash pairs, preserves Python character offsets and
capture groups, and escapes code text without trimming its whitespace.
The adapter implements the match methods used by Python-Markdown. Django
constructs code elements and continues the remaining inline/tree rendering.

The flag defaults to disabled. Lone surrogates, positions outside the Rust
integer range, and custom backtick patterns use Python. Unset the flag and
restart the processes to roll back independently of the block parser flags.
This bridge does not enable standalone scheduled message writes.

Run the same collections with the flag unset and set:

```bash
./tools/test-backend --parallel 1 zerver.tests.test_markdown_inline_code \
    zerver.tests.test_markdown zerver.tests.test_scheduled_messages \
    zerver.tests.test_reminders
```

## Plan Markdown indented code blocks through Rust

Rebuild and install the Python extension, then set
`ZULIP_RUST_MARKDOWN_CODE_BLOCKS=1` in Django processes. Rust removes the
configured indentation, splits off dedented text, escapes code, merges
adjacent code blocks, and restores blank lines after code siblings.
Django constructs elements and preserves `AtomicString` text. The email
gateway continues to omit the indented code processor.

The flag defaults to disabled. Lone surrogates and unsupported indentation
values delegate to Python. Unset the flag and restart the processes to roll
back independently of inline code and the other block parser bridges.
This bridge does not enable standalone scheduled message writes.

Run the same collections with the flag unset and set, then repeat both new
code collections with only one code flag enabled at a time:

```bash
./tools/test-backend --parallel 1 zerver.tests.test_markdown_code_blocks \
    zerver.tests.test_markdown_inline_code zerver.tests.test_markdown \
    zerver.tests.test_reminders
```

## Parse Markdown tables through Rust

Rebuild and install the Python extension, then set
`ZULIP_RUST_MARKDOWN_TABLES=1` in Django processes. Rust validates table
headers and separators, tracks border flags, and splits rows while
preserving escaped pipes and the table parser's backtick pairing rules.
Django retains table element construction, alignment, and inline rendering.

The flag defaults to disabled. Lone surrogates delegate to Python. Unset
the flag and restart the processes to roll back independently of inline
code and indented code blocks. This bridge does not enable standalone
scheduled message writes.

Run the same collections with the flag unset and set:

```bash
./tools/test-backend --parallel 1 zerver.tests.test_markdown_tables \
    zerver.tests.test_markdown zerver.tests.test_scheduled_messages \
    zerver.tests.test_reminders
```

## Parse Markdown text emphasis through Rust

Rebuild and install the Python extension, then set
`ZULIP_RUST_MARKDOWN_EMPHASIS=1` in Django processes to parse bold, italic,
combined bold/italic, and strikethrough markers in Rust. The bridge preserves
Python's capture groups, Unicode whitespace rules, marker boundaries, and
existing processor priorities. Django still builds the elements, renders
nested inline content, and escapes and serializes the result. Strings with
lone surrogates use the Python regular expression.

The flag defaults to disabled and is independent of the other Markdown
bridges. Unset it and restart the processes to roll back. This bridge does
not enable standalone scheduled message writes.

Run these collections with the flag unset and set, and repeat with the other
Markdown bridges enabled to check the independent rollback:

```bash
./tools/test-backend --parallel 1 zerver.tests.test_markdown_emphasis \
    zerver.tests.test_markdown zerver.tests.test_scheduled_messages \
    zerver.tests.test_reminders
```

## Build Markdown channel link fields through Rust

Rebuild and install the Python extension, then set
`ZULIP_RUST_MARKDOWN_CHANNEL_SPANS=1` in Django processes to build URLs,
attributes, and display text for channel, topic, and message links in Rust.
Channel visibility, empty-topic-only channel policy, and topic anchor lookup
stay in Django. Empty-topic labels and child elements, atomic text and tails,
HTML escaping, and serialization stay in the existing Python rendering path.
The bridge preserves message ID strings, including leading zeroes.

The flag defaults to disabled and is independent of the channel reference
scanner, topic anchor lookup, and URL fragment bridges. Lone surrogates and
identifiers outside the supported integer range use the Python builder.
Unset the flag and restart the processes to roll back. This bridge does not
enable standalone scheduled message writes.

Run these collections with the flag unset and set, then test independent
rollback with `ZULIP_RUST_MARKDOWN_EMPHASIS` enabled and disabled:

```bash
./tools/test-backend --parallel 1 zerver.tests.test_markdown_channel_links \
    zerver.tests.test_markdown_emphasis zerver.tests.test_markdown \
    zerver.tests.test_scheduled_messages zerver.tests.test_reminders
```

## Plan Markdown table layout through Rust

Rebuild and install the Python extension, then set
`ZULIP_RUST_MARKDOWN_TABLE_LAYOUT=1` in Django processes to compute column
alignment, trim cell spaces, and pad or truncate table rows in Rust. The
planner reuses the Rust table splitter and preserves code spans and escaped
pipes. A table without body rows still produces the reference's empty row
without alignment attributes; a supplied empty row retains column alignment.
Only ASCII spaces are trimmed. Django still creates elements, processes inline
content, escapes HTML, and serializes the table.

The flag defaults to disabled and is independent of
`ZULIP_RUST_MARKDOWN_TABLES`, which selects recognition and individual row
splitting. Strings with lone surrogates use the Python table processor before
blocks or elements are changed. Unset the flag and restart Django processes
to roll back. This bridge does not enable standalone scheduled message writes.

Run these collections with both table flags disabled and enabled, then with
only one enabled at a time:

```bash
./tools/test-backend --parallel 1 zerver.tests.test_markdown_tables \
    zerver.tests.test_markdown zerver.tests.test_scheduled_messages \
    zerver.tests.test_reminders
```

## Plan Markdown list execution through Rust

Rebuild and install the Python extension, then set
`ZULIP_RUST_MARKDOWN_LIST_LAYOUT=1` in Django processes to choose a list's
parent, continuation of a matching sibling, first loose item, nested item
actions, and ordered start attribute in Rust. The planner reuses the Rust
list splitter and preserves start strings, including Persian digits and
leading zeroes. It also decides when previous text and child tails become
paragraphs, using Python's whitespace rules for trimming the tail. Django
preserves atomic text, creates elements, recursively parses items, and updates
the parser state in the existing order.

The flag defaults to disabled. It is independent of the list block parser,
indentation, and preprocessing flags. Lone surrogates, Unicode decimal digits
outside the parser's supported version, and nonstandard processor settings
use the Python runner before any mutation. Invalid list blocks retain the
reference's errors and block consumption. Unset the flag and restart Django
processes to roll back. This bridge does not enable standalone rendering or
scheduled message writes.

Run these collections with the flag disabled and enabled, then repeat with the
list block and indentation flags independently enabled and disabled:

```bash
./tools/test-backend --parallel 1 zerver.tests.test_markdown_list_layout \
    zerver.tests.test_markdown_list_blocks zerver.tests.test_markdown_list_indent \
    zerver.tests.test_markdown zerver.tests.test_scheduled_messages \
    zerver.tests.test_reminders
```

## Plan Markdown list indentation through Rust

Rebuild and install the Python extension, then set
`ZULIP_RUST_MARKDOWN_LIST_INDENT_LAYOUT=1` in Django processes to choose the
parent and parsing method for indented list blocks. Rust finds the matching
list depth, removes complete two-space indentation, and decides whether an
existing item's text needs a paragraph. Django preserves atomic text, mutates
the tree, recursively parses the block, and updates the `detabbed` state in
the reference order, including when recursive parsing raises an error.

The flag defaults to disabled and is independent of list execution, list
splitting, and indentation helper flags. Unsupported processor configuration
and lone surrogates in the block or tree tags use Python before any mutation.
Unset the flag and restart Django processes to roll back. This bridge does
not enable standalone rendering or scheduled message writes.

Run these collections with the flag disabled and enabled, then with each
combination of the list execution and indentation helper flags:

```bash
./tools/test-backend --parallel 1 zerver.tests.test_markdown_list_indent_layout \
    zerver.tests.test_markdown_list_layout zerver.tests.test_markdown_list_indent \
    zerver.tests.test_markdown zerver.tests.test_scheduled_messages \
    zerver.tests.test_reminders
```

## Serialize Markdown HTML through Rust

Rebuild and install the Python extension, then set
`ZULIP_RUST_MARKDOWN_SERIALIZATION=1` in Django processes to serialize the
Markdown element tree in Rust. The serializer preserves attribute ordering,
HTML boolean attributes, entity spelling (including unrecognized names),
comments, processing instructions, tagless containers, raw script/style text,
empty elements, and escaped tails. HTML attribute newlines remain literal.
The empty-element set comes from the running Python version, retaining its
version-specific treatment of tags such as `source`.
Element construction, inline processing, HTML stash restoration, and other
postprocessors still run in Python. This is serialization, not sanitization.

The flag defaults to disabled and is independent of every other Markdown
bridge. Qualified names, custom element/string classes, invalid Unicode,
nonstring values, and trees deeper than 100 levels use the Python serializer,
preserving its results and errors without mutating the tree. XHTML and other
Markdown instances retain their existing serializers. Unset the flag and
restart Django processes to roll back. This bridge does not enable standalone
rendering or scheduled message writes.

Run these collections with the flag disabled and enabled, then repeat with
the other Markdown bridges enabled to check independent rollback:

```bash
./tools/test-backend --parallel 1 zerver.tests.test_markdown_serialization \
    zerver.tests.test_markdown zerver.tests.test_scheduled_messages \
    zerver.tests.test_reminders
```

## Restore Markdown HTML placeholders through Rust

Rebuild and install the Python extension, then set
`ZULIP_RUST_MARKDOWN_POSTPROCESSING=1` in Django processes to recursively
restore stashed HTML and replace ampersand placeholders in Rust. This stage
restores rendered code, math, and entities after tree serialization. It
preserves paragraph-wrapper removal, nested replacement order, leading-zero
indices, and unmatched placeholders. Python still produces the stashed HTML
and classifies block-level tags using the current Markdown configuration.
The bridge does not change HTML sanitization or the order of postprocessors.

The flag defaults to disabled and is independent of serialization and other
Markdown bridges. Custom callbacks, custom objects, inconsistent stash
counters, lone surrogates, and unusually long indices use Python. Cycles,
deep recursion, and excessive expansion also use the reference processor;
partial native output is discarded, and the stash is never mutated. Python's
configured integer-string limit and existing exceptions remain in effect.
Unset the flag and restart Django processes to roll back. This bridge does
not enable standalone rendering or scheduled message writes.

Run these collections with the flag disabled and enabled, then with each
combination of serialization and postprocessing:

```bash
./tools/test-backend --parallel 1 zerver.tests.test_markdown_postprocessing \
    zerver.tests.test_markdown_serialization zerver.tests.test_markdown \
    zerver.tests.test_scheduled_messages zerver.tests.test_reminders
```

## Restore escaped Markdown characters through Rust

Rebuild and install the Python extension, then set
`ZULIP_RUST_MARKDOWN_UNESCAPE=1` in Django processes to restore numeric
character placeholders in Rust. The existing tree processor still visits
elements in order, skips code-element text, and updates tails and attributes.
Atomic code text remains atomic; restored text follows the reference's string
types. An invalid later attribute retains the same earlier tree mutations.

The flag defaults to disabled and is independent of serialization,
postprocessing, and the other Markdown bridges. Non-ASCII placeholder
candidates use Python's Unicode digit rules; ordinary Unicode text stays in
Rust. Custom callbacks and patterns, lone surrogates, surrogate results,
out-of-range code points, and long integer strings use the reference method,
including its configured integer-string limit and errors. Replacement is a
single pass: newly formed placeholders are not expanded again.

Unset the flag and restart Django processes to roll back. This bridge does
not enable standalone rendering or scheduled message writes. Run these
collections with the flag disabled and enabled, then with each combination
of serialization and postprocessing:

```bash
./tools/test-backend --parallel 1 zerver.tests.test_markdown_unescape \
    zerver.tests.test_markdown_postprocessing zerver.tests.test_markdown_serialization \
    zerver.tests.test_markdown zerver.tests.test_scheduled_messages \
    zerver.tests.test_reminders
```

## Prettify Markdown trees through Rust

Rebuild and install the Python extension, then set
`ZULIP_RUST_MARKDOWN_PRETTIFY=1` in Django processes to plan block newlines,
line-break tails, and trailing code whitespace in Rust. Python applies the
changes to the existing elements. The three passes preserve their reference
order, Python whitespace rules, and atomic code text. Unchanged text retains
its original type. Block classification uses Python's Unicode lowercase rules
and the configured block-element list.

The flag defaults to disabled and is independent of the other Markdown
bridges. Custom callbacks, unusual element or string types, shared nodes,
surrogates, and trees deeper than 100 levels use the reference processor before
any tree mutation. Errors in that path retain the original partial mutations.
Unset the flag and restart Django processes to roll back. This does not enable
standalone rendering or scheduled message writes.

Run these collections with the flag disabled and enabled, including independent
combinations with serialization and character restoration:

```bash
./tools/test-backend --parallel 1 zerver.tests.test_markdown_prettify \
    zerver.tests.test_markdown_unescape zerver.tests.test_markdown_serialization \
    zerver.tests.test_markdown zerver.tests.test_scheduled_messages \
    zerver.tests.test_reminders
```

## Resolve Markdown inline placeholders through Rust

Rebuild and install the Python extension, then set
`ZULIP_RUST_MARKDOWN_INLINE_STASH=1` in Django processes. Rust scans inline
placeholders and selects the text consumed after each accepted or rejected
identifier. Python looks up each identifier in the live stash and performs the
existing recursive element updates. This stash contains inline elements and
strings; it is separate from the raw HTML stash used by postprocessing.

The scanner preserves leading-zero identifiers, unknown placeholders, the
reference's search beyond malformed prefixes, Unicode text, and atomic trailing
text. Stash lookups remain interleaved with callbacks, so recursive mutations
and exceptions retain their original order. Custom search or element-processing methods, custom patterns or string
classes, and surrogates use the reference processor.

The flag defaults to disabled and is independent of prettification,
serialization, and the other bridges. Unset it and restart Django processes to
roll back. Inline pattern matching, tree construction, highlighting, and the
other rendering callbacks still require Python. Run these collections in both
flag modes and with independent combinations of prettification and restoration:

```bash
./tools/test-backend --parallel 1 zerver.tests.test_markdown_inline_stash \
    zerver.tests.test_markdown_prettify zerver.tests.test_markdown_unescape \
    zerver.tests.test_markdown zerver.tests.test_scheduled_messages \
    zerver.tests.test_reminders
```

## Dispatch inline Markdown patterns through Rust

Set `ZULIP_RUST_MARKDOWN_INLINE_PATTERNS=1` to run the inline pattern
dispatch loop in Rust. The flag is independent of
`ZULIP_RUST_MARKDOWN_INLINE_STASH` and defaults to disabled. Restart Django
processes after changing either flag.

The loop retains the initial registry length, looks up each pattern in the
live registry, and retries a matched pattern at the returned search position.
Recursive calls from the Python pattern handlers use the same Rust dispatcher.
`AtomicString` inputs remain untouched; unusual starting indices use the Python
reference. Text, including lone surrogates, remains a Python object without
UTF-8 conversion. Callback exceptions propagate with their existing partial
stash and tree mutations.

Without the separate match-application flag below, regex matching,
`handleMatch`, ancestor exclusions, placeholder creation, and tree mutation
still use Python. This is an incremental migration of control flow, not a
native AST or independent renderer, and does not establish a speedup.

After installing the wheel, run the regression tests in all four combinations
of the two flags:

```bash
ZULIP_RUST_MARKDOWN_INLINE_PATTERNS=1 ZULIP_RUST_MARKDOWN_INLINE_STASH=1 \
    ./tools/test-backend zerver.tests.test_markdown_inline_patterns \
    zerver.tests.test_markdown_inline_stash zerver.tests.test_markdown
```

## Apply inline Markdown matches through Rust

Set `ZULIP_RUST_MARKDOWN_INLINE_APPLY=1` to run new-style inline match
application in Rust. Restart Django processes after changing the flag. It
defaults to disabled and is independent of inline dispatch and stash scanning.
Legacy `Pattern` instances retain the Python implementation.

Rust checks ancestor exclusions, iterates the existing compiled Python regex,
handles rejected matches, and recursively updates node text and tails before
stashing the result. Text and slice bounds stay Python objects, preserving
Unicode, lone surrogates, string subclasses, and Python indexing errors.
Child traversal uses a snapshot while callbacks and stash lookups remain live.
Exceptions preserve earlier mutations, including an ancestor pushed before a
failed recursive callback and a node stashed before an invalid output slice.

Regex engines, pattern handlers, placeholder creation, and the elements
themselves still require Python. This does not supply a native AST, retire
Python modules, change sanitization, or establish a performance improvement.
Run these tests in all eight independent flag combinations:

```bash
ZULIP_RUST_MARKDOWN_INLINE_APPLY=1 ZULIP_RUST_MARKDOWN_INLINE_PATTERNS=1 \
    ZULIP_RUST_MARKDOWN_INLINE_STASH=1 ./tools/test-backend --parallel 1 \
    zerver.tests.test_markdown_inline_apply \
    zerver.tests.test_markdown_inline_patterns \
    zerver.tests.test_markdown_inline_stash zerver.tests.test_markdown
```

## Traverse inline Markdown trees through Rust

Set `ZULIP_RUST_MARKDOWN_INLINE_TREE=1` to run `InlineProcessor.run` in
Rust. The flag defaults to disabled and is independent of inline pattern
dispatch, match application, and stash scanning. Restart Django processes
after changing it.

The runner resets the stash, copies supplied ancestors, builds the initial
parent map, and processes the live child iterator and pending insertion queue.
It retains the distinct handling of text and tails, `AtomicString` values,
and callbacks selected before recursive processing. Updates to the parent map
and ancestors remain visible to later callbacks. Exceptions leave the same
partial text, tail, parent-map, and stash changes as the reference runner.

Elements, regexes, handlers, ancestor reconstruction, and recursive placeholder
resolution still use Python. This transfers traversal control without creating
a native AST or independent renderer. It does not establish a speedup. Run the
following collections in all sixteen combinations of the four inline flags:

```bash
ZULIP_RUST_MARKDOWN_INLINE_TREE=1 ZULIP_RUST_MARKDOWN_INLINE_APPLY=1 \
    ZULIP_RUST_MARKDOWN_INLINE_PATTERNS=1 ZULIP_RUST_MARKDOWN_INLINE_STASH=1 \
    ./tools/test-backend --parallel 1 zerver.tests.test_markdown_inline_tree \
    zerver.tests.test_markdown_inline_apply \
    zerver.tests.test_markdown_inline_patterns \
    zerver.tests.test_markdown_inline_stash zerver.tests.test_markdown
```

## Run the coordinated inline Markdown engine

Set `ZULIP_RUST_MARKDOWN_INLINE_ENGINE=1` after rebuilding the Python
extension, then restart Django processes. This default-off flag selects one
runtime for inline traversal, pattern dispatch, match application, ancestor
reconstruction, placeholder creation, and recursive placeholder restoration.
Default internal methods call each other directly in Rust, rather than
returning through the individual Python bridge wrappers.

Each callback is still selected at its original point in execution. Only a
bound method belonging to the current processor and matching the original
implementation uses native dispatch. Overrides and callbacks replaced during
rendering stay live, including custom placeholder search, stash access, and
legacy patterns. The runtime preserves Python strings and Element identities;
surrogates, `AtomicString`, shared stashed nodes, text/tail insertion order,
and partial changes before callback errors retain the reference behavior.
After 64 nested native calls, execution delegates to the selected Python
method so Python can enforce its recursion limit without exhausting the native
stack. Exact recursion-limit depth and traceback frames are not preserved.

Unset the engine flag and restart Django to restore the four independently
selected inline bridges. The engine flag does not change their settings.
Regex matching, handlers, Elements, and the surrounding rendering pipeline
still require Python. This is a coordinated migration of execution, not native
tree ownership, an independent renderer, or evidence of a speedup.

Run these tests with the engine alone, with it disabled, and with all 32
combinations of the five inline flags. The profile test requires the default
traversal and mutation methods to execute without entering their Python
implementations.

```bash
ZULIP_RUST_MARKDOWN_INLINE_ENGINE=1 ./tools/test-backend --parallel 1 \
    zerver.tests.test_markdown_inline_engine \
    zerver.tests.test_markdown_inline_tree \
    zerver.tests.test_markdown_inline_apply \
    zerver.tests.test_markdown_inline_patterns \
    zerver.tests.test_markdown_inline_stash zerver.tests.test_markdown \
    zerver.tests.test_scheduled_messages zerver.tests.test_reminders
```

## Run the native inline Markdown planner

Set `ZULIP_RUST_MARKDOWN_NATIVE_INLINE=1` after installing a fresh Python
extension wheel, then restart Django processes. This default-off flag runs a
native tree, text, and placeholder planner for the built-in backtick, emphasis,
strike-through, newline, named-emoji, emoticon, Unicode emoji, and supported
explicit link patterns. Named emoji uses the
current catalog and active realm overrides, including overrides of Unicode
names and `:zulip:`. Unknown names remain text. It preserves the existing Element
objects and their attributes when applying the completed plan.

Rust constructs ordered attributes and atomic display text for new emoji
nodes. The host continues to serialize and escape attributes. Non-ASCII
characters encountered in a colon-name candidate delegate the entire run;
this includes the catalog's non-ASCII aliases. This avoids depending on a
different Unicode word-character database from the running Python version.
Custom emoji helpers, renderer configuration, or catalog values also delegate
before mutation. Catalogs are read on every run, so edits and deactivations
remain visible; their validation and transfer have a cost even for plain text.
This extension does not establish a rendering speedup.

Emoticons use the renderer's current ordered conversions and the sender's
`translate_emoticons` setting. Only the existing ASCII terminal characters
form boundaries; Unicode whitespace is not interchangeable with a space.
Translation inside code remains protected, and emoticons use Unicode emoji
even when a realm emoji overrides the translated name. Custom conversion
keys, replacement escapes, helpers, or unsupported target names delegate
the whole run before mutation.

Unicode emoji uses the reference renderer's emoji grammar, including skin
tones, regional pairs, tag sequences, variation selectors, and ZWJ sequences.
Text-presentation characters remain text unless qualified, and unknown
clusters remain intact. The current codepoint-to-name catalog supplies the
display text. The property tables are generated from Python `regex` version
`2026.9.29`; other versions delegate before mutation. Custom matching patterns,
normalization helpers, or catalog values also delegate. Regenerate or verify
the tables in the provisioned environment without fetching Unicode data:

```bash
python rust-backend/tools/generate_markdown_emoji_data.py --check
```

After updating the reference regex dependency, run the command without
`--check`, review the generated property changes, and repeat the complete
state comparison below before enabling this path.

Before changing the tree, the planner checks the processor, pattern registry,
regular expressions, callbacks, string types, and input depth. Other syntax,
custom handlers, and unsupported input use the existing inline engine or
Python path. Unsupported syntax found after an earlier supported match also
uses that fallback before mutating the tree. The Python renderer remains the
default and the reference for output and metadata.

Run the native planner tests with the flag both off and on, and with the
coordinated inline engine flag on to check fallback between the two paths:

```bash
ZULIP_RUST_MARKDOWN_NATIVE_INLINE=1 ./tools/test-backend --parallel 1 \
    zerver.tests.test_markdown_native_inline zerver.tests.test_markdown \
    zerver.tests.test_markdown_inline_engine
```

### Use the planner from Rust

The `native-markdown` Cargo feature exposes the same planner as
`_zulip_backend::markdown_native_inline`, without linking Python. Both the
`python` and `http` features include it. Callers supply an indexed graph,
ordered patterns, ancestor names, and a guard for unsupported handlers.
`Node.attributes` vector retains attribute order. A named-emoji pattern uses
kind `8` and a `NamedEmoji` catalog with Unicode codepoints and active realm
source URLs; other patterns use `emoji: None`. Kind `9` uses an `Emoticons`
catalog of ordered literal conversions and Unicode names; `emoticons: None`
disables translation. Kind `10` uses `unicode_emoji: Some(UnicodeEmoji { ... })`
with the reference regex version and codepoint-to-name catalog. The type and
`REGEX_VERSION` constant are in `markdown_unicode_emoji`. The guard must have no side
effects on the host renderer and accept only handlers
that would leave both text and renderer state unchanged. A rejected plan
returns `None`; callers must retain their original tree until the entire plan
succeeds. The returned
graph includes temporary and detached stash nodes, so its vector order is
not an HTML serialization order. This interface does not yet provide a
standalone Markdown renderer or migrate another HTTP operation.

Kind `11` uses `links: Some(markdown_links::Policy { ... })` with the live
allowed schemes, urllib authority schemes, file-link setting and optional
realm URL. Other patterns use `links: None`. URL policy rejection leaves the
candidate unconsumed so a later or nested valid link can still be found.

Run the public interface tests without Python or HTTP dependencies:

```bash
cargo test --locked --offline --manifest-path rust-backend/Cargo.toml \
    --no-default-features --features native-markdown
```

### Parse inline link and image syntax from Rust

The `native-markdown` feature also exposes `markdown_link_syntax::parse`
and `parse_image` for the shared link, image, and audio grammar. Pass the
input and the UTF-8 byte offset of its opening `[` or `!`. A successful
result contains ranges for the label, destination, and optional title,
and the byte offset immediately after the link. Nested brackets,
parentheses, angle destinations, Unicode whitespace, and the reference
parser's primary and secondary title quotes retain their existing rules.

`Ok(None)` means no complete match. `Err(ParseError::InvalidEnd)` requires
delegation to the reference handler: some unclosed title inputs cause
Python-Markdown to return a negative end index. Treating this error as a
missing link would change how the rest of the input is processed.

These ranges contain raw Markdown placeholders. `prepare_text` restores
the destination and title from a `TextStash` snapshot and normalizes them
in the reference parser's order. Snapshot element entries with their full
`itertext()` content, including descendant tails and excluding the root's
tail. Pass `None` when the inline treeprocessor is absent; an empty map
instead removes unknown placeholders. Substitution is one pass, preserves
exact identifier strings, and leaves numeric escape placeholders alone.
The destination is stripped after substitution. The title is stripped
before substitution, dequoted, then has each Python whitespace character
replaced by one space without collapsing runs. The label remains raw.
Invalid destination or title ranges return `None`.

The resulting destination is still untrusted. The caller must sanitize
the URL and apply realm-local rewriting before constructing an element.
Image and audio
callers also retain media selection and attachment checks. This is a
preparatory API for native rendering. The native link handler below uses it;
media handlers continue through their existing paths.

The differential compares the raw strings passed to the installed
Python-Markdown parser's unescape callback, along with labels and end
offsets. It records source, reference-module, and probe-binary hashes.
Run it in the provisioned environment using the Cargo-built probe:

```bash
cargo build --locked --offline --manifest-path rust-backend/Cargo.toml \
    --no-default-features --features native-markdown --example link_syntax
for mode in link image; do
    python rust-backend/tests/check_link_syntax_parity.py \
        --probe "${CARGO_TARGET_DIR:-rust-backend/target}/debug/examples/link_syntax" \
        --mode "$mode" --cases 100000 --exhaustive-depth 5 \
        --report "var/link-syntax-$mode-parity.json"
done
```

The text differential also compares the real `getLink` output with absent,
empty, and mixed text/element stashes. It covers restored quotes and
whitespace, nonrecursive replacements, unknown identifiers, nested element
text, and malformed links. It verifies destination/title text, raw labels,
and end offsets, rather than complete rendered HTML or metadata:

```bash
cargo build --locked --offline --manifest-path rust-backend/Cargo.toml \
    --no-default-features --features native-markdown --example link_text
python rust-backend/tests/check_link_text_parity.py \
    --probe "${CARGO_TARGET_DIR:-rust-backend/target}/debug/examples/link_text" \
    --cases 100000 --report var/link-text-parity.json
```

### Snapshot native inline stash text

`markdown_native_inline::snapshot_text_stash(&state.nodes, &state.stash)`
constructs the `TextStash` needed by `prepare_text` directly from the native
arena. Stash identifiers use the planner's decimal format (at least four
digits). Node entries concatenate text and descendant tails in document
order, excluding the stashed root's tail, as ElementTree's `itertext()`
does. Shared children contribute on every occurrence. Plain and atomic
text are copied verbatim; nested placeholders are left for the caller's
next unescape pass.

The snapshot borrows its input and returns `None` for invalid indices,
cycles, more than 100,000 traversal/stash visits, or more than 8 MiB of
combined output text. The pending traversal is bounded by the same work
budget, and deep trees do not use recursion. A caller must delegate its
whole plan on rejection. The native link handler uses this helper before
unescaping destinations; the snapshot itself does not sanitize them.

The differential requires native acceptance for every generated acyclic
arena, including shared children, and compares every stash value with
Python's actual ElementTree traversal. The report records source and
probe hashes. No third-party Python package is needed:

```bash
cargo build --manifest-path rust-backend/Cargo.toml --locked --offline \
    --no-default-features --features native-markdown --example stash_text
python rust-backend/tests/check_stash_text_parity.py \
    --probe "${CARGO_TARGET_DIR:-rust-backend/target}/debug/examples/stash_text" \
    --cases 100000 --seed 962823 --report var/stash-text-parity.json
```

### Render supported explicit links natively

On CPython 3.12, the existing `ZULIP_RUST_MARKDOWN_NATIVE_INLINE` flag also
runs explicit Markdown links inside the native arena. The handler joins the
raw parser, current stash snapshot, destination/title preparation, Zulip's
second unescape, URL policy and local rewriting. It preserves empty titles,
atomic labels, whitespace-only label replacement, attribute order and
message-reply sender/excerpt children. ElementTree still escapes and
serializes the completed tree.

`markdown_links::sanitize` and `prepare` are also available without Python
under `native-markdown`. `Ok(None)` rejects a candidate without consuming
it. `Err(Unsupported)` requires delegation of the entire plan before any
host mutation. Authorities with non-ASCII characters or square brackets
always delegate, retaining Python's Unicode normalization and bracketed IP
validation, including malformed-URL empty results. Unicode paths, queries,
fragments, labels and mail addresses remain supported. Invalid parser ends
and stash resource limits also delegate.
Candidate parsing shares an 8 MiB suffix-scan budget for the entire plan,
including failed openers; exhausting it delegates without changing the host
tree. This bounds repeated scans of inputs containing many unmatched `[`.

The adapter reads allowed schemes, urllib's `uses_netloc`, file policy and
realm URL on every run. Changed helpers, regexes or unsupported configuration
delegate before mutation. The registered inline processor must own the
stash; otherwise links remain guarded host handlers. Other Python versions
keep guarded host links while supported native formatting remains available.
No new flag or default activation is involved. Images, audio, automatic
links, attachment policy and preview metadata remain separate work.

Compare sanitizer and link preparation with the actual checked-in Python
helpers and installed Python-Markdown in the provisioned environment:

```bash
cargo build --locked --offline --manifest-path rust-backend/Cargo.toml \
    --no-default-features --features native-markdown --example links
python rust-backend/tests/check_links_parity.py \
    --probe "${CARGO_TARGET_DIR:-rust-backend/target}/debug/examples/links" \
    --cases 50000 --seed 962824 --report var/links-prepared-parity.json
```

This comparison varies file policy, allowed schemes and stash presence. It
checks URL text, label, title, reply children and byte end offsets, and
requires both native acceptance and delegation for fixed cases. Reports
record source, reference and real Cargo probe hashes. It does not replace
the complete-state comparison below or full rendering-result tests.

### Compare complete native and Python state

The state comparison tool also checks shared element identity, atomic text,
stash order, parent mappings, ancestor state, registry sorting, errors, and
the absence of changes before fallback. Use the provisioned environment
and an installed extension wheel, with all `ZULIP_RUST_*` flags unset:

```bash
python rust-backend/tests/check_native_inline_parity.py \
    --cases 20000 --seed 962817 --report var/native-inline-parity.json
```

Add `--unicode-scalars` to compare all 1,112,064 Unicode scalar values in
isolated Unicode-handler batches, each requiring native acceptance. The
ordinary cases also exercise complete registries, malformed clusters, and
enabled/disabled emoticon translation.

The report records accepted and delegated cases separately, source and
extension hashes, and complete failing inputs and states. Fixed cases require
both native acceptance and fallback, so an extension delegating every input
cannot pass. Generated placeholder inputs use absent or malformed keys;
arbitrary valid keys can create self-referential state in the Python reference.

## Prepare uploaded images and attachment effects natively

With `native-markdown`, `markdown_media::prepare_image` combines the shared
image parser, stash restoration, URL sanitizer, local rewriting, authorized
preview lookup and ordered placeholder-image attributes without Python.
`ImageOutcome` keeps a rejected image distinct from an unsupported plan, and
returns the attachment path to append even when media policy rejects display.
Commit this pending effect in candidate order, preserving duplicates. Set
`has_image` only when an image is returned; preserve existing rendering metadata.
Values are unescaped ElementTree attributes, never ready-to-insert HTML.

Preview keys use the entire raw `/user_uploads/` suffix, including query and
fragment. Attachment paths instead follow `urlsplit(urljoin("/", src))`, including
relative dot-segment resolution, semicolon parameters, Unicode and empty paths.
Absolute local links rewritten to `user_uploads/…` can record an attachment while
failing the image rule's leading-slash requirement. Missing and empty preview
snapshots are separate inputs. Dimensions use decimal strings to preserve
Python's unbounded integers, and empty content types do not add an attribute.

`markdown_media::attachment_path` exposes the same path extraction separately.
It and image preparation require CPython 3.12's standard `urljoin` scheme lists;
future host adapters must guard those lists and all live helpers. Unicode or
bracketed authorities and invalid parser ends return `Err(Unsupported)`, requiring
whole-plan delegation before host mutation. Image rejection is `Ok` with no image
and can still carry an attachment effect. Preview snapshots must contain only
authorized metadata; this API does not establish attachment permissions.

This is a standalone preparatory API, not live handler dispatch. Django still
handles image/audio matching and metadata updates. Native integration must
preserve audio-before-image priority and commit tree and metadata together.
There is no new flag, default change or standalone HTTP operation.

Compare with the actual Python image handler and attachment helper in the
provisioned environment, without Rust feature flags:

```bash
cargo build --locked --offline --manifest-path rust-backend/Cargo.toml \
    --no-default-features --features native-markdown --example media
python rust-backend/tests/check_media_parity.py \
    --probe "${CARGO_TARGET_DIR:-rust-backend/target}/debug/examples/media" \
    --cases 50000 --seed 962826 --report var/media-parity.json
```

The report records actual Cargo probe, source and reference hashes, native
acceptance/delegation, ordered attributes, UTF-8 end offsets and pending
attachment/image state. Cases vary file policy, absent/empty/nested stashes,
preview availability, non-ASCII paths and pre-existing rendering metadata.
This does not establish complete-renderer, media authorization or performance
parity. Audio MIME selection, camo, live-helper guards and atomic planner
integration remain separate work.

### Prepare mention syntax and notification fields natively

The `native-markdown` feature exposes `markdown_mention_syntax::find` and
`markdown_inline_mentions` without Python dependencies. The candidate scanner
returns UTF-8 byte ranges for the complete match and its name, plus the silent
flag. Its boundary check includes the character before the search offset;
invalid byte offsets return `Err(InvalidStart)`. It shares the implementation
used for possible mentions and quote silencing. A syntax match does not resolve
a user, interpret code/quotes, or grant permission to mention a group.

`prepare_user` resolves the reference handler's `name|id` syntax, including
Unicode decimal digits and the regex anchor's final-newline behavior. An
explicit name must exactly equal the resolved user's canonical name. Unknown
users and IDs outside the database integer range return no span. Supply the
current renderer's complete non-ASCII decimal-digit map and live
`sys.get_int_max_str_digits()` value in `UserPolicy`; exceeding that limit
delegates so the reference renderer preserves its exception behavior.

The lookup callback receives `UserReference::Name` or `UserReference::Id` and
must read an authorized snapshot without mutating host state. Preserve the
host's lowercase rules and duplicate-name ordering; this API does not substitute
Rust's Unicode case conversion. Group display names, including translated
system-group names, are also supplied by the caller to `group_mention_span`.

The returned `MentionSpan` contains ordered attributes, atomic text and a pending
`MentionNotification`. The caller must HTML-escape the attribute values and text
when serializing, and commit notification effects only with the complete native
plan. Silent syntax and inactive users/groups suppress notifications; wildcard
names remain case sensitive. A silent or rejected mention must not clear an
earlier notification. There is no new flag, live planner dispatch, HTTP route,
permission lookup or default activation in this preparatory API.

Build and compare offline with the reference checkout:

```bash
cargo build --locked --offline --manifest-path rust-backend/Cargo.toml \
    --no-default-features --features native-markdown \
    --example mention_syntax --example inline_mentions
python rust-backend/tests/check_mention_syntax_parity.py \
    --probe "${CARGO_TARGET_DIR:-rust-backend/target}/debug/examples/mention_syntax" \
    --all-boundaries --cases 100000 --report var/mention-syntax-parity.json
python rust-backend/tests/check_inline_mentions_parity.py \
    --probe "${CARGO_TARGET_DIR:-rust-backend/target}/debug/examples/inline_mentions" \
    --cases 100000 --report var/mention-fields-parity.json
```

Both drivers use only Python's standard library. The syntax oracle reads the
actual regex assignments. The field oracle executes the selected reference
handler definitions with snapshot lookups, capturing ordered attributes, atomic
text and notification effects. It does not run the whole Markdown engine or
exercise database authorization. Reports include source/probe hashes and a
digest of the input and expected output corpus. `--reference-only` computes
that digest without a probe; `--source` (and the field driver's
`--mention-source`) can select a reference checkout on another Python runtime.
Corpora involving decimal digits vary with the runtime's Unicode database.

Native planner integration still requires guarding live helpers and snapshot
ownership, preserving registry priority and quote/code behavior, and committing
tree and notification metadata atomically. Full rendering, notification delivery
and performance parity remain separate work.

## Prepare audio and Camo attributes natively

With `native-markdown`, `markdown_media::prepare_audio` prepares the shared
image/audio syntax, sanitized source, ordered audio attributes and pending
attachment effect without Python. It does not modify a tree or rendering result.
An explicit link title rejects audio before URL processing or attachment
recording. The audio title instead comes from the stripped raw label, preserving
literal formatting and stash placeholders. Rejected audio can still record an
attachment; return `Err(Unsupported)` only for whole-plan delegation.

An absent preview snapshot uses MIME lookup for all sources. A present snapshot
uses authorized audio IDs for sources beginning with `/user_uploads/`, even if
the filename has an audio extension. Lookup uses the full raw suffix, including
query and fragment. Absolute local uploads rewritten to `user_uploads/…` use
MIME lookup instead. Audio never changes `has_image` or other message metadata.
Previews must already be authorized; this API does not grant attachment access.

`MimePolicy` snapshots the initialized CPython 3.12 POSIX strict MIME tables,
encoding suffixes, suffix aliases, `urlparse` parameter schemes and supported
audio types. `mime_type` preserves query/parameter parsing differences between
relative paths and URLs, case-sensitive compression suffixes, dotfiles and data
URLs. Non-ASCII extensions and cyclic/excessive suffix expansion delegate.
Unicode filename stems remain supported. The Rust host MIME database is unused.

`AudioPolicy` snapshots static and current-realm exemptions, the optional public
upload root (ending in `/`), and Camo settings. `is_static_url` retains the
reference netloc, scheme and prefix checks; an apparent static host prefix does
not exempt another host. `camo_url` implements the existing UTF-8 HMAC-SHA1/hex
protocol with the already-cached SHA1 dependency. Empty Camo URI preserves the
URL; enabled Camo without a key delegates. Ordered attributes remain unescaped
ElementTree values, not HTML. Unicode/bracketed authorities delegate.

This is a preparatory API. Live audio/image dispatch still uses Django. A future
adapter must guard the reference helpers, POSIX/Python version, live MIME and
URL tables, settings and authorized previews before committing the whole tree
and rendering state atomically. Preserve audio-before-image registry priority
and duplicate attachment effects across rejected candidates and handlers. No
new flag, default activation or standalone HTTP operation is involved.

Compare the actual Cargo probe with the reference MIME, static, Camo and audio
handlers in the provisioned environment, without Rust feature flags:

```bash
cargo build --locked --offline --manifest-path rust-backend/Cargo.toml \
    --no-default-features --features native-markdown --example media
python rust-backend/tests/check_audio_parity.py \
    --probe "${CARGO_TARGET_DIR:-rust-backend/target}/debug/examples/media" \
    --cases 20000 --seed 962827 --report var/audio-parity.json
```

The report records actual probe, source and reference hashes, native/delegated
counts, UTF-8 end offsets, ordered attributes and the full rendering-result
state. It varies absent/empty/authorized previews, stashes, static and public
upload policy, Camo URI and key lengths, file links and a custom MIME type.
Native acceptance/delegation is mandatory for fixed cases. This is not a
complete-renderer, attachment-authorization or performance comparison.

## Plan native media with pending metadata

`markdown_native_inline::run_with_media` extends the Python-independent arena
planner with audio and image handler kinds 12 and 13. Supply their existing URL
policy through `Pattern.links` and authorized preview/settings snapshots through
`Media`. The registry order controls audio/image selection. The existing `run`
entry point supplies no media snapshot. The guarded Django adapter uses the
separate PyO3 media entry point described below.

The returned `State.attachment_paths` and `State.has_image` are pending effects.
Append paths without deduplication and OR `has_image` with the previous value,
only after the whole plan succeeds. Rejected candidates still record attachment
paths; rescanning from the beginning after each successful substitution can
record the same rejected candidate again. An image followed by accepted audio
can therefore record the image path three times across both handlers. Do not
replace this with a single left-to-right media pass.

Guards, unsupported authorities/parser ends, graph/work limits, the shared
8 MiB candidate-suffix budget, and the separate 8 MiB combined media attribute
and attachment budget discard the whole plan. A late failure returns `None`,
including when an earlier candidate already produced an image or attachment.
Atomic strings and ancestor exclusions bypass their handlers normally.

Adapters must validate live helper identity, URL/MIME tables, settings,
authorized snapshots and resource limits before committing the tree and all
metadata together. This API does not enable live media dispatch, establish
attachment permissions, replace the complete renderer or change an HTTP route.

Compare native graph, stash, parent-map ordering, ancestors, ordered attributes
and the complete rendering result with Python-Markdown's actual inline engine:

```bash
cargo build --locked --offline --manifest-path rust-backend/Cargo.toml \
    --no-default-features --features native-markdown --example media_plan
python rust-backend/tests/check_media_plan_parity.py \
    --probe "${CARGO_TARGET_DIR:-rust-backend/target}/debug/examples/media_plan" \
    --cases 20000 --seed 962828 --report var/media-plan-parity.json
```

The fixed-policy probe uses link/audio/image registries in normal and reversed
media order, absent/empty/authorized previews, atomic/plain text, Unicode labels,
nested links, rejected candidates and existing rendering metadata. Both native
acceptance and delegation are required by fixed cases. The report records source,
reference and actual Cargo binary hashes. Other handlers, arbitrary graph shapes,
live adapter guards, full rendering and performance are outside this comparison.

## Measure Markdown rendering bridges

Use the provisioned development environment and a release wheel to measure
bridge overhead. Once dependencies are cached, build offline and install only
the local wheel:

```bash
CARGO_NET_OFFLINE=true CARGO_BUILD_JOBS=1 CARGO_INCREMENTAL=0 \
    maturin build --release --manifest-path rust-backend/Cargo.toml \
    --out var/markdown-release-wheels
pip install --no-index --no-deps --upgrade --target var/markdown-release-python \
    var/markdown-release-wheels/*.whl
PYTHONPATH="$PWD/var/markdown-release-python:$PWD" \
    python rust-backend/tests/benchmark_markdown_rendering.py \
    > var/markdown-rendering-benchmark.json
```

Run without Rust feature flags in the environment. The script compares the
Python fallback with each of serialization, prettification, inline stash
resolution, inline pattern dispatch, inline match application, inline tree
traversal, the coordinated inline engine, the native inline planner,
postprocessing, and character restoration. It also measures the four separate
inline bridges together and
then enables all ten bridges.
It asserts equality of the complete `MessageRenderingResult` before timing
short, emoji, rich, and long messages without previews or organization data.

Results report median microseconds per conversion and elapsed-time ratios
relative to Python; a ratio above one is slower. `--iterations` and `--repeats`
control sampling. Separate serializer measurements compare the full bridge
with a prebuilt snapshot, which excludes Python snapshot construction and
validation. These measurements do not model database traffic, preview fetches,
concurrent requests, or deployment capacity. Wait for other builds and tests
to finish before measuring, and retain the JSON with its Python version and
extension path.

Use a Maturin wheel for Python imports. A plain Cargo library on macOS may link
a particular `libpython` and is not a substitute for an ABI-compatible wheel.
Keep Cargo registry and Python package caches when reclaiming build space;
compiled outputs can be rebuilt offline, while deleted package caches may
require another download.

### Prepare channel, topic and message links without Python

With `native-markdown`, `markdown_channel_syntax::find` locates channel,
topic and message syntax independently using UTF-8 byte offsets. Supply the
current host Unicode database's complete non-ASCII decimal-digit set for
message IDs. Channel/topic names remain untrimmed; message IDs retain their
original Unicode digits and leading zeroes. Invalid offsets return
`Err(InvalidStart)` and require delegation. An absent match is `Ok(None)`.
The caller controls resumption after rejecting an unresolved candidate and
must preserve registry priority and code/quote handling. Unlike user
mentions, channel links do not allow `[` immediately before the hash.

`markdown_channel_spans::channel_link` now exposes the existing shared field
builder without a Python dependency. Resolve names and topic-history anchors
from an already-authorized snapshot, and reject nonempty topics in
empty-topic-only channels before calling it. The API neither grants access
nor checks that a message ID exists. Message links take precedence over the
optional topic-history `with` operand.

Escape attribute values and text during serialization. Root text and the
optional child tail are atomic Markdown text. For `empty_topic`, append an
`em` child with the host's localized empty-topic fallback as plain text; the
returned tail belongs to that child. Preserve attribute order. These field APIs do not mutate host trees or change live handler dispatch,
rollout flags or HTTP routing.

Build and compare the actual Cargo probe using cached dependencies:

```sh
cargo build --manifest-path rust-backend/Cargo.toml --locked --offline \
  --features native-markdown --example channel_links
python3 rust-backend/tests/check_channel_links_parity.py \
  --probe rust-backend/target/debug/examples/channel_links --cases 100000 \
  --all-boundaries --report /tmp/channel-links-parity.json
```

The standard-library-only oracle extracts the actual channel regexes,
handler bodies, display template and URL encoders from the reference
checkout. It uses snapshot lookup protocols, checks ordered fields and
atomic text, and includes all Unicode scalar boundary characters. It does
not run the complete renderer, live database lookups or channel visibility
checks. `--reference-only` emits a reproducible reference corpus digest
without invoking Cargo.

### Plan channel links with authorized snapshots

`markdown_native_inline::run_with_inputs` accepts `Inputs` containing optional
`Channels` and `Media` snapshots. Existing `run` and `run_with_media` remain
compatible wrappers. Pattern kinds 14, 15 and 16 represent channel, topic and
message links respectively; the supplied registry order controls priority.

Channel names map to visible IDs and the resolved empty-topic-only policy.
Topic-history anchors use exact channel/topic keys; absent anchors omit the
`with` operand. Unknown channels and disallowed nonempty topics are skipped
without consuming later candidates of the same handler. Message IDs remain
unmodified syntax text. Labels and message suffixes are atomic, while the
localized empty-topic `em` child remains plain text as in Python.

The caller must validate the live regexes, lookup methods, authorized data,
realm topic policy, fallback text, encoders and Unicode snapshot before using
these handlers. The API performs no database lookup and grants no access.
The host must commit the complete graph and any pending media metadata
atomically. Unsupported handlers, missing required snapshots, invalid graphs
or resource limits discard the entire plan.

Channel candidate searches share the existing 8 MiB suffix-work budget with
links/media. Channel field and fallback text have an additional 8 MiB combined
budget. Long rejected-candidate inputs therefore delegate safely to Python.
No live adapter, bridge signature, feature flag, deployment default or HTTP
route is changed by this preparatory planner API.

```sh
python3 rust-backend/tests/check_channel_plan_parity.py \
  --probe rust-backend/target/debug/examples/channel_links --cases 20000 \
  --report /tmp/channel-plan-parity.json
```

Run this oracle inside the provisioned development environment with Rust flags
unset. It uses the actual Python inline engine and channel handlers with
snapshot-backed data, comparing graph structure, ordered attributes, atomic
text, stash, parent maps, ancestors and unchanged rendering metadata. The
restricted registry includes code, explicit links, emphasis and line breaks,
with normal/reversed channel priority, absent/populated channel data,
atomic/plain input and excluded ancestors. This is not a complete renderer,
live permission test or deployment benchmark.

### Render channel links through the guarded native inline bridge

After installing a fresh wheel, `ZULIP_RUST_MARKDOWN_NATIVE_INLINE=1` also
runs the channel, topic and message handlers in Rust. The existing flag
remains off by default. Unset it to return the complete inline run to Python.
There is no new HTTP route or independent rollout flag for these handlers.

`native_channels` snapshots only `DbData.stream_names`, cached channel topic
policies and `DbData.topic_info`. It reads the loaded realm policy directly
from the model's fields; a deferred field delegates without fetching it.
Missing cache entries preserve the reference's permissive topic-policy
fallback, while missing visible channel names remain unresolved. Native
rendering does not change channel access or validate message existence.

The adapter guards the original compiled regex objects, channel lookup and
formatting methods, topic-key class, URL encoders and their mutable settings,
realm field descriptor, host Unicode digits and localized empty-topic text.
Custom handlers, nonstandard data, surrogate strings, out-of-range IDs,
more than 100,000 entries or more than one million combined snapshot
characters delegate before any tree/registry/processor mutation. The Unicode
decimal snapshot is cached per interpreter and is created only when needed.
The additional optional PyO3 argument leaves earlier direct calls compatible.

The channel helper snapshot is built after the input graph is validated and
only when channel syntax occurs in its text. The existing atomic commit
applies the planned tree, stash, parents, ancestors and registry together;
channel rendering leaves notification and attachment metadata unchanged.
Media preparation APIs still require their own live adapter.

Run `zerver.tests.test_markdown_native_inline` with coverage for the guard and
state-preservation regressions, then run the Markdown, scheduled-message and
reminder collections with the native flag off and on. The existing
`check_native_inline_parity.py` additionally covers authorized channel
snapshots, inherited topic policy, mixed/shared graphs and prior rendering
metadata. Run it with Rust flags unset and the fresh wheel on `PYTHONPATH`;
it invokes the native planner directly and checks every delegated plan for
unchanged input state before running Python's fallback.

### Plan native mentions with pending notification effects

`markdown_native_inline::run_with_inputs` accepts user and group mention
handlers as kinds 17 and 18 through `Inputs.mentions`. This is an independent
arena API also used by the guarded live adapter described below.
The existing native renderer remains off by default.

`Mentions.data = None` represents a renderer without database context, where
user, group and wildcard syntax remains unresolved. `Inputs.mentions = None`
means no snapshot is available and delegates mention candidates. With database
context, `MentionData` contains a complete authorized user-ID map and raw-name
lookup results for users and groups. Supply the exact host lookup results,
including its lowercase rules and duplicate-name ordering. Store a known
lookup miss as a `None` map value. An absent raw-name
key delegates the entire plan: earlier handlers can introduce syntax that was
not present in the initial snapshot. The planner never performs database reads
or grants mention permissions.

Supply localized group display names and the current interpreter's Unicode
decimal properties and integer conversion limit through `UserPolicy`.
Conversion-limit failures delegate before returning a plan. Exact `name|id`
checks, silent and inactive mentions, case-sensitive wildcards and the given
registry priority are preserved. Mention span text is atomic; attribute order
and text still need the normal serializer's escaping.

On success, union `State.mentions.users` and `.groups` into the existing host
notification sets and OR `.channel` and `.topic` into their existing flags.
Commit these effects together with the graph, stash, parent map, ancestors,
registry and pending media effects. Never clear prior notifications. Missing
snapshots, late guards, unsupported inputs and graph/work/output limits discard
all effects. Candidate scanning shares the existing 8 MiB suffix budget with
links, channels and media; mention text and attributes have a separate 8 MiB
output budget.

```bash
cargo test --manifest-path rust-backend/Cargo.toml --locked --offline \
  --features native-markdown --test native_inline_mentions
cargo build --manifest-path rust-backend/Cargo.toml --locked --offline \
  --features native-markdown --example mention_plan
python rust-backend/tests/check_mention_plan_parity.py \
  --probe rust-backend/target/debug/examples/mention_plan --cases 5000 \
  --report /tmp/mention-plan-parity.json
```

Run the oracle with Rust flags unset and the project's Python-Markdown installed.
It executes the actual reference mention methods, code handler and rendering
result definition extracted from trusted checkout source, together with the
real Python inline engine and configured emphasis/line-break handlers. Raw
candidate snapshots come from the reference regexes. It compares the complete
graph, attribute order, atomic strings, stash, parent-map order, ancestors and
all rendering metadata after merging pending notifications with existing state.
Normal/reversed registry order, missing/empty/populated context, active/inactive
users and groups, excluded ancestors and live digit limits are varied. This is
not full-renderer, live lookup, notification delivery or benchmark coverage.

### Render mentions through the guarded native inline bridge

Rebuild and install the extension before using
`ZULIP_RUST_MARKDOWN_NATIVE_INLINE=1`. The existing default-off flag now
includes user, group and wildcard mentions. Unset it to return the whole
inline run to Python. No new HTTP route, independent flag or default is added.

`native_mentions` takes a read-only snapshot of the renderer's existing
`MentionData`: user IDs, raw-name lookup results including explicit misses,
and localized group display names. Names use the host's lowercase and existing
duplicate-name ordering. It never queries Django, expands visibility or grants
permission to mention a group. Deferred group fields delegate without fetching.
The current interpreter's decimal digits and live integer conversion limit
preserve mixed Unicode IDs and Python's conversion errors.

The adapter guards regex objects, handlers, name/ID/group lookup methods,
wildcard helpers and sets, span/field helpers, system-group translations,
renderer/data types and the integer-limit getter before planning. Snapshots
are needed only for actual syntax, so an ordinary `@` requires no mention
context. Syntax introduced by an earlier handler without a cached raw-name
lookup delegates the complete run. Missing database context leaves even
wildcards unresolved.

Nonstandard data, surrogate strings, IDs outside PostgreSQL's integer range,
more than 100,000 entries or more than one million combined source/output
snapshot characters delegate. Output accounting includes repeated canonical
names under distinct raw aliases, before PyO3 copies those strings. Pending
Rust notification sets are unioned into the existing Python set objects and
wildcard flags are ORed into prior flags only after the complete plan succeeds.
A late failure preserves the graph, registry, processor state and all previous
rendering metadata. HTML escaping remains in the existing serializer.

The optional final PyO3 input carries the authorized mention snapshot and
policy. The plan's final result field carries pending user IDs, group IDs and
two wildcard flags. Reinstall the wheel when updating the Python adapter.

```bash
ZULIP_RUST_MARKDOWN_NATIVE_INLINE=1 ./tools/test-backend \
  zerver.tests.test_markdown_native_inline
python rust-backend/tests/check_native_inline_parity.py --cases 30000 \
  --seed 962833 --report /tmp/mention-live-parity.json
```

Run the differential with Rust flags unset and the fresh wheel on `PYTHONPATH`.
It executes the actual Django handlers and Python-Markdown engine with
snapshot-backed user/group/channel data and compares the complete graph,
ordered attributes, atomic text, stash, parent maps, ancestors, registry and
all rendering metadata. Every delegated plan is checked for unchanged state
before fallback. It varies active/inactive data, absent context, shared graphs,
code/atomic input, channel combinations and unknown syntax. Unit tests also
cover translation, conversion limits, resource guards, existing set identity
and accepted execution without Python mention handlers or lookup callbacks.
This is not a complete standalone renderer, notification-delivery benchmark or
proof of a general speedup; the default remains off.

## Transport native media plans through PyO3

`plan_markdown_native_inline_media` accepts the same graph, registry, guards,
channel and mention inputs as `plan_markdown_native_inline`, plus optional
`media_inputs`. It returns `(inline_result, attachment_paths, has_image)`, or
`None` when the entire plan must be delegated. The existing entry point retains
its signature and six-field result. Both use the same planner implementation.

`media_inputs` is `(realm_host, audio_previews, image_previews, audio_policy)`.
Preview snapshots distinguish `None` (no message data) from an empty collection
(an authorized lookup with no previewable uploads). Audio previews are a set of
exact path suffixes. Image previews map exact suffixes to
`(width, height, content_type)`; dimensions remain decimal strings to preserve
Python integers without an i64 conversion. Query and fragment suffixes remain
part of preview keys, while attachment effects use the parsed path.

`audio_policy` contains `(mime, static_url, external_uri_scheme,
public_upload_root, camo_uri, camo_key)`. `mime` contains, in order, the suffix
map, encoding-extension set, strict MIME map, supported audio MIME set, and
URL parameter-scheme set. These are snapshots of the reference runtime's live
settings and initialized MIME database, rather than the Rust host's defaults.

The caller must first validate the snapshot and guard the live URL, MIME,
static, Camo and parser helpers. This transport performs no database lookups or
permission checks. Its output is pending: append attachment paths in order,
including duplicates and rejected candidates, and OR `has_image` into the
existing rendering result only when committing the complete graph and pending
mention notifications. A late unsupported candidate discards all those effects.

The guarded Django adapter uses this entry point under the existing
`ZULIP_RUST_MARKDOWN_NATIVE_INLINE=1` flag, as described below. The flag remains
off by default; no HTTP route or deployment default changes here.

Using an offline release wheel installed into `var/media-bridge-python`:

```bash
PYTHONPATH="$PWD/var/media-bridge-python:$PWD" \
    python rust-backend/tests/test_native_media_bridge.py
PYTHONPATH="$PWD/var/media-bridge-python:$PWD" \
    python rust-backend/tests/check_media_plan_parity.py \
    --extension var/media-bridge-python/_zulip_backend/_zulip_backend.abi3.so \
    --cases 10000 --report var/media-bridge-parity.json
```

The differential uses the actual extension and Python-Markdown with the
reference link, image and audio handlers. It compares the whole graph, ordered
attributes, atomic text, stash, parent-map order, ancestors and all rendering
metadata across absent, empty and populated previews, reversed media priority
and atomic input. It also retains the existing `--probe` mode for testing the
standalone Cargo example. This is neither complete renderer coverage nor a
performance measurement.

## Render media through the guarded native inline bridge

The existing default-off `ZULIP_RUST_MARKDOWN_NATIVE_INLINE=1` path now includes
Markdown audio and uploaded-image handlers on CPython 3.12 POSIX installations
with local uploads or an already-initialized standard S3 backend. `native_media` validates the already-loaded `DbData`
preview collections and copies the initialized runtime MIME tables. It does
not initialize the MIME database, query attachments or authorize new previews.
Absent data and a present empty preview collection retain distinct behavior.

Parser, URL, attachment helper, Camo, MIME and realm descriptors are guarded.
The shared link-policy validation also applies to audio and images, including
when the link handler is absent from the registry. Static/Camo settings and
MIME mappings are read for each media plan. Changed supported types and strict
MIME mappings therefore take effect without restarting the renderer. Custom
helpers, unsupported runtime versions and uninitialized MIME data delegate the
entire plan to the existing Python engine.

For cloud uploads, the adapter snapshots `public_upload_url_base` from the
existing standard `S3UploadBackend`, after guarding the public-root helpers,
method and field descriptors. It never constructs a backend, invokes its
public-root helper or starts an S3 request. An uninitialized/custom backend,
changed helper, non-string root or root without a trailing slash delegates the
whole plan. The cached public root retains the reference's Camo exemption;
similar URL prefixes outside that root remain proxied.

Snapshot inputs are limited to 100,000 preview entries, 100,000 MIME/policy
entries and one million combined source/output characters. Image dimensions
must be plain integers of at most 2,000 bits; they cross PyO3 as decimal strings.
This conservative bound also avoids CPython's minimum configurable integer
conversion limit. Unusual values, surrogates, unloaded realm fields and
unsupported plans delegate before changing any graph or rendering metadata.

On success, the host applies the complete tree, stash, parent map, ancestors,
registry state and pending mention notifications, then extends the existing
attachment list in place and ORs `has_image`. Attachment order and duplicates
are preserved, including effects from rejected candidates and rescans. A late
failure preserves all previous objects and metadata before Python executes.

Using the cached wheel containing `plan_markdown_native_inline_media`:

```bash
PYTHONPATH="$PWD/var/media-bridge-python:$PWD" \
    ZULIP_RUST_MARKDOWN_NATIVE_INLINE=1 \
    ./tools/test-backend zerver.tests.test_markdown_native_inline
PYTHONPATH="$PWD/var/media-bridge-python:$PWD" \
    python rust-backend/tests/check_native_inline_parity.py \
    --cases 30000 --seed 962834 --report var/media-live-parity.json
```

The actual-extension differential compares the entire graph, attribute order,
atomic text, stash, parent-map order, ancestors, registry and rendering metadata.
It combines media with formatting, channels and mentions and checks that
fallback preserves input state before the reference engine runs. Dedicated
regressions cover live MIME/Camo changes, preview absence, query suffixes,
attachment-list identity, source budgets, parser/helper guards and late failure.
This does not establish a complete Rust renderer, attachment authorization,
notification delivery, HTTP coverage or a performance improvement.

## Render calendar timestamps without Python

`ZULIP_RUST_MARKDOWN_NATIVE_INLINE=1` also handles calendar timestamps with
extended (`2024-01-01T12:34:56Z`) or basic (`20240101T123456Z`) dates and times,
ISO week dates (`2020-W53-7`, `2020W537`), decimal fractions and UTC offsets.
Date, time and offset separators are independent. An omitted weekday means Monday.
Week dates can cross Gregorian years; nonexistent week 53 and dates outside years
1 through 9999 produce escaped source. CPython's separator choice is preserved
even with digit and hyphen separators. For example, `2024-W01-1123` means
Monday at 11:23, while `2024W0112345` means Monday at 23:45.
It preserves the source as atomic display text and normalizes the `datetime`
attribute to UTC with CPython 3.12 microsecond precision. A naive timestamp
means UTC. Invalid calendar values and UTC overflow produce the existing
escaped source. Offset minutes and seconds retain CPython's normalization,
including its treatment of fractional offsets with zero whole seconds.

The adapter guards the timestamp pattern, regex flags, handler methods, datetime
types and element/text constructors. Other runtimes, mixed separators within one
date or clock, and unrecognized syntax retain the Python fallback. A late
unsupported timestamp discards the entire plan, including prior graph, mention
and attachment effects.
Shared nodes can revisit escaped invalid dates and require the existing HTML
handler; that also delegates the whole plan. No HTML is inserted by this
parser; the existing serializer still escapes attributes and display text.
Timestamp scans share an 8 MiB work budget. Exceeding it delegates before any
host mutation, including when earlier timestamps were already planned.

Install a fresh offline release wheel before running the full-state comparison:

```bash
PYTHONPATH="$PWD/var/timestamp-week-python:$PWD" \
    python rust-backend/tests/check_native_inline_parity.py \
    --cases 10000 --timestamp-cases 25000 --week-timestamp-cases 50000 \
    --seed 962838 \
    --report var/timestamp-inline-parity.json
```

The added corpus varies calendar and week dates, separators, offset normalization,
fractions, invalid and unsupported inputs, shared nodes and atomic text. The comparison
includes ordered attributes, stash, parent mappings, ancestors, registry,
HTML stash and all rendering metadata. It is not a benchmark or an exhaustive
test of CPython's ISO parser.

## Plan HTML entities without Python handlers

`ZULIP_RUST_MARKDOWN_NATIVE_INLINE=1` also plans the registered HTML entity
processor in Rust. The supported pattern is Python-Markdown's `ENTITY_RE`:
named ASCII alphanumeric entities, decimal references and hexadecimal references
with a lowercase `x`. Raw spelling is retained, including unknown names and
out-of-range numeric references. Decoding and final serialization continue to
follow the existing renderer. This does not enable raw HTML tags.

`plan_markdown_native_inline_html` returns `(plan, paths, has_image, html_blocks)`.
It takes the same arguments as the media planner plus an optional `html_counter`.
Kind 20 requires a counter; the older entrypoints reject that kind rather than
silently discarding HTML effects. Their signatures and result shapes are
unchanged. A previous wheel without the new entrypoint retains the Python
fallback. The Python-independent `Inputs::html_counter` and `State::html_blocks`
provide the same pending-stash contract to Rust callers.

The adapter verifies the entity expression, flags, handler/unescape methods,
standard HTML stash methods, placeholder constants, unescape regex and host
registry before planning. The current stash counter must be a nonnegative exact
integer no greater than one million. Existing blocks and their list identity
are preserved; the counter need not equal the number of existing blocks.
Entities, graph changes, mentions and attachments commit only after the entire
plan succeeds. A cumulative 8 MiB scan budget bounds repeated entity processing;
unsupported syntax or exhausted budgets retain all original host state.

Using a fresh offline wheel containing the new entrypoint:

```bash
PYTHONPATH="$PWD/var/entity-python:$PWD" \
    ZULIP_RUST_MARKDOWN_NATIVE_INLINE=1 ZULIP_RUST_MARKDOWN_UPLOAD_PATHS=1 \
    ./tools/test-backend zerver.tests.test_markdown_native_inline
PYTHONPATH="$PWD/var/entity-python:$PWD" \
    python rust-backend/tests/test_native_html_bridge.py
PYTHONPATH="$PWD/var/entity-python:$PWD" \
    python rust-backend/tests/check_native_inline_parity.py \
    --cases 10000 --entity-cases 20000 --timestamp-cases 5000 \
    --week-timestamp-cases 5000 --seed 962839 --report var/entity-inline-parity.json
```

The oracle compares the complete graph, ordered attributes, atomic text, inline
stash, parent order, ancestors, registry, HTML stash and all rendering metadata.
It checks original HTML-stash list identity and pre-fallback immutability too.
Entity cases vary counter offsets, shared graphs, excluded ancestors, handler
priority, malformed references, media and mentions. This remains an opt-in
Django bridge; TeX, highlighting, previews and standalone scheduled
message orchestration remain dependencies before Python renderer retirement.

## Match automatic links without Python callbacks

`ZULIP_RUST_MARKDOWN_NATIVE_INLINE=1` also handles `AutoLink` in the native
arena. The matcher uses the live ordered TLDs and scheme expressions, the file
links setting, and CPython 3.12's Unicode 15.0 word class. Its reference data can
be regenerated offline with `tools/generate_markdown_autolink_data.py`.
The adapter checks the complete expression and flags against those settings,
together with the handler, URL helpers, constructors and renderer policy. A
stale cached reference expression or changed helper delegates the whole plan.

The scanner preserves start boundaries, email and bitcoin alternatives, greedy
file paths, lazy URL paths, punctuation and the reference's parenthesis expansion
(the initial group plus six nested expansions). The legacy suffix's `$` may
exclude a final LF after a successful match; this is retained too. A query such
as `https://host?.` has an atomic label ending in `?`; the existing sanitizer
removes the empty query from its `href`. URL sanitization and local upload/narrow
rewrites use the existing Rust implementation. Unsupported Unicode or bracketed
authorities retain reference handling. Schemes with additional regular
expression operators also delegate, rather than being interpreted differently.

`markdown_autolinks::Matcher` has no Python dependency and returns UTF-8 byte
ranges. Up to four compiled snapshots are cached. Compilation and live
configuration sizes are bounded; an 8 MiB cumulative scan budget bounds candidate
searches and repeated substitutions. This is a compatibility boundary, not a
benchmark or a claim of exhaustive URL grammar support.

`plan_markdown_native_inline_autolinks` returns the same four-item result as the
HTML planner and accepts an additional `autolink_spec`: the Unicode version,
ordered TLDs, ordered schemes and file-links flag. Kind 21 requires that snapshot
and the existing link policy. All three older entrypoints retain their signatures
and result shapes and reject the new kind. Old wheels continue to use guarded
Python autolinks. Graph changes, ordered attributes, atomic labels, HTML stash,
notifications and attachment effects commit together after the entire plan
succeeds. No flag, default, HTTP route or Python file retirement changes here.

Using a fresh wheel built with `maturin build --release --locked --offline`
and installed with `pip --no-index --no-deps`:

```bash
PYTHONPATH="$PWD/var/autolink-python:$PWD" \
    ZULIP_RUST_MARKDOWN_NATIVE_INLINE=1 ZULIP_RUST_MARKDOWN_UPLOAD_PATHS=1 \
    ./tools/test-backend zerver.tests.test_markdown_native_inline
PYTHONPATH="$PWD/var/autolink-python:$PWD" \
    python rust-backend/tests/test_native_autolink_bridge.py
PYTHONPATH="$PWD/var/autolink-python:$PWD" \
    python rust-backend/tests/check_native_inline_parity.py \
    --cases 10000 --autolink-cases 20000 --entity-cases 5000 \
    --timestamp-cases 5000 --week-timestamp-cases 5000 --seed 962840 \
    --report var/autolink-inline-parity.json
cargo build --manifest-path rust-backend/Cargo.toml --release --locked --offline \
    --features native-markdown --example autolinks
python rust-backend/tests/check_autolink_parity.py \
    --probe rust-backend/target/release/examples/autolinks \
    --cases 100000 --unicode-scalars --report var/autolink-ranges-parity.json
```

The graph oracle compares all rendering metadata as well as the tree, ordered
attributes, atomic text, inline/HTML stashes, parent order, ancestors and registry.
It checks pre-fallback immutability, shared graphs, reordered/excluded handlers,
file links and combinations with entities, timestamps, mentions and media. The
range oracle compares the actual reference expression, including every Unicode
scalar in four contexts, independently of the sanitizer. TeX, highlighting,
previews and the rest of the renderer and scheduled delivery orchestration remain
before Python renderer retirement.

## Run local KaTeX processes through Rust

`ZULIP_RUST_MARKDOWN_TEX=1` moves the local KaTeX subprocess into
`markdown_tex::run_cli` on Linux. It uses the installed `node` and KaTeX
script, without a shell or additional packages. Both inline and display
rendering retain the same command arguments and HTML output. The native
function accepts encoded bytes and returns `(success, stdout_bytes)`;
Python retains locale encoding, universal-newline decoding and stripping.
Output is decoded even after an unsuccessful exit, preserving decode errors.

The writer and output reader run concurrently to avoid pipe deadlocks.
Broken stdin pipes do not override the child exit status; other process I/O
errors propagate. The PyO3 call releases the GIL, preserves spawn errno and
filename, and discards stderr. Before exec, Linux `close_range` marks all
nonstandard descriptors close-on-exec, and SIGXFSZ is restored along with
Rust's normal SIGPIPE reset. Unsupported process isolation delegates before
KaTeX executes. Additional open descriptors remain unchanged in the parent.

The flag is off by default and independent of the native inline planner.
Other platforms, old wheels, encoding-warning mode and replaced subprocess
helpers use the Python path. `KATEX_SERVER=True` retains its existing HTTP
client, timeout and failure handling. Production bundle selection and missing
script logging also retain their existing behavior. KaTeX itself remains
JavaScript; this is process orchestration, not a native TeX implementation.

Build one release wheel offline and install it into a separate directory:

```bash
maturin build --manifest-path rust-backend/Cargo.toml --release --locked --offline \
    --out var/tex-cli-wheels
python -m pip install --no-index --no-deps --target var/tex-cli-python \
    var/tex-cli-wheels/*.whl
PYTHONPATH="$PWD/var/tex-cli-python:$PWD" \
    python rust-backend/tests/test_tex_bridge.py
PYTHONPATH="$PWD/var/tex-cli-python:$PWD" ZULIP_RUST_MARKDOWN_TEX=1 \
    ./tools/test-backend zerver.tests.test_tex_rust zerver.tests.test_markdown
PYTHONPATH="$PWD/var/tex-cli-python:$PWD" \
    python rust-backend/tests/check_tex_cli_parity.py \
    --katex node_modules/katex/cli.js --cases 200 --seed 962841 \
    --report var/tex-cli-parity.json
```

The differential compares raw output and success against the same installed
KaTeX through Python subprocesses. Dedicated tests also cover inherited
file descriptors, signal restoration, GIL release, large bidirectional
pipes, encoding failures, old-wheel fallback and complete Markdown render
metadata. This does not retire `zerver/lib/tex.py`: the service client,
encoding boundary and raw-LaTeX conversion still need migration. Inline
TeX handler orchestration has a separate opt-in bridge described below.

## Scan inline TeX delimiters through Rust

`ZULIP_RUST_MARKDOWN_TEX_SYNTAX=1` replaces the inline `Tex` pattern's
delimiter scanning with `markdown_tex_syntax::find_tex`. It preserves
Python's word boundaries, first-body-character restrictions, escaped-dollar
alternative and greedy backtracking, including the last repeated capture.
The full legacy Pattern captures and offsets include trailing newlines.
The matcher reuses the generated CPython 3.12 Unicode 15.0 word class.

The PyO3 entrypoint `find_markdown_tex(data, unicode_version)` returns
`((before, body, last_capture, after),)` for a match and `(None,)` for a
missing match. `None` delegates unsupported Unicode versions, surrogate
strings and inputs exceeding 8 MiB. The adapter also delegates other
Python runtimes, changed pattern text or flags and older wheels.

The flag is off by default and independent of `ZULIP_RUST_MARKDOWN_TEX`
and `ZULIP_RUST_MARKDOWN_NATIVE_INLINE`. The native planner recognizes
the wrapper and retains its guarded Python regex for unsupported TeX.
A matching TeX expression still rejects that entire plan before rendering;
the existing inline engine then invokes the renderer once in registry
order. HTML stash writes, error spans, KaTeX service/CLI selection and
all rendering metadata retain their existing behavior. This is a syntax
migration, not full native TeX handling or a Python file retirement.

Build and install one offline release wheel into `var/tex-syntax-python`
using the preceding section's commands with separate wheel/output paths:

```bash
PYTHONPATH="$PWD/var/tex-syntax-python:$PWD" \
    python rust-backend/tests/test_tex_syntax_bridge.py
PYTHONPATH="$PWD/var/tex-syntax-python:$PWD" \
    ZULIP_RUST_MARKDOWN_TEX_SYNTAX=1 ZULIP_RUST_MARKDOWN_NATIVE_INLINE=1 \
    ./tools/test-backend zerver.tests.test_markdown_tex_syntax \
    zerver.tests.test_markdown zerver.tests.test_markdown_native_inline
PYTHONPATH="$PWD/var/tex-syntax-python:$PWD" \
    python rust-backend/tests/check_tex_syntax_parity.py \
    --max-length 8 --cases 100000 --unicode-scalars --seed 962842 \
    --report var/tex-syntax-parity.json
```

Unset Rust feature flags for the differential. It loads the live Python
`Tex` expression and compares all captures against the actual extension,
using exhaustive short strings over six characters, seeded random inputs
and every Unicode scalar in four contexts. It does not render KaTeX,
exhaust all strings or measure performance. Dedicated rendering tests
also check renderer call order, errors, HTML escaping, complete metadata,
pre-fallback immutability and native acceptance of graphs without TeX.

## Orchestrate TeX handlers through Rust

`ZULIP_RUST_MARKDOWN_TEX_HANDLERS=1` independently opts into Rust for inline
TeX result handling and fenced math paragraph rendering. It is off by default
and works with either the service or CLI renderer, regardless of the syntax,
CLI process or native inline planner flags. Older wheels retain both Python
handlers through capability checks.

The inline bridge calls the live `render_tex` at the handler's existing
registry position. Successful output, including an empty string, goes through
the current HTML stash. Failure creates an atomic `tex-error` span whose body
is escaped by ElementTree during serialization. The display bridge splits on
exactly two LF characters, renders paragraphs in order with `is_inline=False`,
escapes unsuccessful paragraphs with Django's existing helper, and joins with
the same separator. Empty paragraphs and surrogate strings are preserved.
Global callbacks and the HTML stash are resolved at the same points as in
Python, including changes made during rendering. Exceptions propagate without
retrying rendering or stash writes. Finding unclosed fences still skips TeX.

These are PyO3 orchestration bridges using Python renderer and tree callbacks,
not a standalone TeX renderer or a performance claim. The native graph planner
continues to delegate matching TeX before any render callback. This avoids an
eager KaTeX execution followed by another execution after a later plan rejection.
Deferred TeX result effects in the standalone planner, the service HTTP client,
raw-LaTeX conversion and complete Markdown rendering still require migration.
No Python file can be retired based on this flag alone.

Build and install a cached wheel into a separate target directory:

```sh
maturin build --manifest-path rust-backend/Cargo.toml --release --locked --offline \
    --out var/tex-handlers-wheels
python -m pip install --no-index --no-deps --target var/tex-handlers-python \
    var/tex-handlers-wheels/*.whl
PYTHONPATH="$PWD/var/tex-handlers-python:$PWD" ZULIP_RUST_MARKDOWN_TEX_HANDLERS=1 \
    ./tools/test-backend zerver.tests.test_markdown_tex_handlers \
    zerver.tests.test_markdown zerver.tests.test_scheduled_messages zerver.tests.test_reminders
PYTHONPATH="$PWD/var/tex-handlers-python:$PWD" DJANGO_SETTINGS_MODULE=zproject.test_settings \
    python rust-backend/tests/check_tex_handlers_parity.py \
    --cases 20000 --seed 962843 --report var/tex-handlers-parity.json
```

The differential compares both live handlers with their Python fallbacks.
It checks result types, atomic error strings, ordered HTML stash contents and
counter values, callback order, renderer exceptions and unchanged list identity.
The corpus uses controlled renderer results rather than invoking KaTeX for each
input. Separate full-render tests and real CLI probes cover composition with
the renderer and all message rendering metadata.

## Convert KaTeX HTML to raw LaTeX through Rust

`ZULIP_RUST_RAW_LATEX=1` independently opts into the PyO3 conversion used by
mobile push content and email HTML. It is off by default. The bridge uses the
existing lxml tree, XPath engine and element factory. It does not parse HTML
in Rust or change the notification format.

It retains the absolute `//span[@class='katex']` selection, including siblings
outside a selected subtree and exact class equality. Each original match uses
the first descendant `application/x-tex` annotation's direct text. Empty text
keeps the existing `$$None$$` representation. Replacements discard math markup,
preserve their tails and escape formula text during lxml serialization. Nested
matches use the original XPath snapshot even after an ancestor is detached.
Missing annotations and missing parents propagate their existing errors; prior
replacements remain applied. Exceptions never trigger a second conversion.
Disabled flags and older wheels use the Python implementation.

Build and install once from cached dependencies:

```sh
maturin build --manifest-path rust-backend/Cargo.toml --release --locked --offline \
    --out var/raw-latex-wheels
python -m pip install --no-index --no-deps --target var/raw-latex-python \
    var/raw-latex-wheels/*.whl
PYTHONPATH="$PWD/var/raw-latex-python:$PWD" ZULIP_RUST_RAW_LATEX=1 \
    ./tools/test-backend zerver.tests.test_raw_latex_rust \
    zerver.tests.test_push_notifications.TestPushNotificationsContent \
    zerver.tests.test_email_notifications
PYTHONPATH="$PWD/var/raw-latex-python:$PWD" DJANGO_SETTINGS_MODULE=zproject.test_settings \
    python rust-backend/tests/check_raw_latex_parity.py \
    --cases 20000 --seed 962844 --report var/raw-latex-parity.json
```

The differential compares serialized trees, original node references and their
parents, element factory order, partial changes and exceptions. It includes
nested math, namespace and class differences, empty annotations, tails and
controlled factory failures. The focused tests also compare real KaTeX fixtures
and mobile push text. This is an orchestration migration, not an independent
HTML renderer or a performance claim; no Python file is retired by this flag.

## Handle KaTeX service responses through Rust

`ZULIP_RUST_KATEX_RESPONSE=1` independently opts into the PyO3 response handler
for `KATEX_SERVER=True`. It is off by default. HTTP still uses `KatexSession`,
the fixed localhost URL, existing form fields, shared secret, explicit proxy
bypass and 0.5-second timeout. Request exceptions and their logging stay in
Python, preserving the handled exception context. This is not a native HTTP
client, and no Python file is retired.

The response bridge decodes successful bodies as strict UTF-8 and applies
Python's Unicode `strip`. Status 400 returns `None` without decoding. Other
statuses log their unstripped decoded body once and return `None`. Status
attributes are read at the same decision points as the reference. Decode,
property and logging failures propagate without retrying the request or response
handler. Older wheels retain Python response handling. Local CLI rendering is
independent of this flag.

```sh
maturin build --manifest-path rust-backend/Cargo.toml --release --locked --offline \
    --out var/katex-response-wheels
python -m pip install --no-index --no-deps --target var/katex-response-python \
    var/katex-response-wheels/*.whl
PYTHONPATH="$PWD/var/katex-response-python:$PWD" ZULIP_RUST_KATEX_RESPONSE=1 \
    ./tools/test-backend zerver.tests.test_katex_response_rust \
    zerver.tests.test_tex_rust zerver.tests.test_markdown_tex_handlers
PYTHONPATH="$PWD/var/katex-response-python:$PWD" DJANGO_SETTINGS_MODULE=zproject.test_settings \
    python rust-backend/tests/check_katex_response_parity.py \
    --cases 20000 --seed 962845 --report var/katex-response-parity.json
```

The offline differential compares complete `render_tex` outcomes with controlled
HTTP responses, request failures and warning calls, including invalid UTF-8,
Unicode whitespace, exact POST arguments and error bodies. It does not contact a
KaTeX server or benchmark the transport.

## Normalize KaTeX CLI output through Rust

The existing `ZULIP_RUST_MARKDOWN_TEX=1` opt-in also uses `finish_katex_cli`
when the installed wheel provides it. Valid UTF-8 stdout is decoded in Rust,
CRLF and CR are converted to LF, and successful output is stripped using
Python's Unicode whitespace rules. Empty successful output remains an empty
string. Unsuccessful output is decoded before returning `None`, preserving
encoding errors rather than hiding them behind the process exit status.

Other codecs and malformed UTF-8 use Python's decoding and normalization to
retain exact codec errors, BOM behavior and surrogate results.
[Input encoding](#select-and-encode-katex-cli-input-through-rust) has independent
capability checks. Older wheels use the existing Python output handling after
a single native process execution. Service response handling and
planner fallback are unchanged. No additional flag or dependency is needed.

```sh
maturin build --manifest-path rust-backend/Cargo.toml --release --locked --offline \
    --out var/katex-output-wheels
python -m pip install --no-index --no-deps --target var/katex-output-python \
    var/katex-output-wheels/*.whl
PYTHONPATH="$PWD/var/katex-output-python:$PWD" ZULIP_RUST_MARKDOWN_TEX=1 \
    ./tools/test-backend zerver.tests.test_katex_output_rust zerver.tests.test_tex_rust
PYTHONPATH="$PWD/var/katex-output-python:$PWD" \
    python rust-backend/tests/check_katex_output_parity.py \
    --cases 20000 --seed 962846 --report var/katex-output-parity.json
```

The offline differential checks every Unicode scalar in a fixed newline context,
random bytes across six codecs and focused codec cases. It compares complete
results and exception arguments. This is not an exhaustive test of all byte
strings or a performance benchmark. The renderer, locale boundary and HTTP
transport still prevent retiring `zerver/lib/tex.py`.

## Orchestrate fenced code highlighting through Rust

`ZULIP_RUST_CODE_HIGHLIGHT=1` opts into PyO3 configuration discovery,
`CodeHilite` construction and language metadata serialization. It is off by
default and independent of the block planner. The first `CodeHiliteExtension`
configuration is cached on the preprocessor, including a missing extension.
Constructor options retain their evaluation order and PHP `startinline=True`.
Language aliases use the current Pygments lexer name; unknown languages retain
the requested name for custom playgrounds.

Pygments tokenization, header parsing, lxml parsing/serialization and HTML escaping
still use Python libraries. Highlight execution and its exception handler stay in
Python to retain the active exception context in logs. Constructor, lexer and HTML
errors propagate at their original boundaries without retry. Both native exports
must be present; an older or partially upgraded wheel uses the Python path before
any work. This does not retire a Python file or replace the complete renderer.

```sh
maturin build --manifest-path rust-backend/Cargo.toml --release --locked --offline \
    --out var/code-highlight-wheels
python -m pip install --no-index --no-deps --target var/code-highlight-python \
    var/code-highlight-wheels/*.whl
PYTHONPATH="$PWD/var/code-highlight-python:$PWD" ZULIP_RUST_CODE_HIGHLIGHT=1 \
    ./tools/test-backend zerver.tests.test_code_highlight_rust zerver.tests.test_markdown
PYTHONPATH="$PWD/var/code-highlight-python:$PWD" DJANGO_SETTINGS_MODULE=zproject.test_settings \
    python rust-backend/tests/check_code_highlight_parity.py \
    --report var/code-highlight-parity.json
```

Use a fresh target directory when installing a newly built wheel. pip leaves an
existing target package unchanged unless explicitly asked to upgrade it. The
seeded offline differential compares real Pygments output, complete exception
arguments and cached configuration, including disabled highlighting, aliases,
Unicode, malformed text and custom languages. It is not exhaustive or a benchmark.

## Parse fenced code headers through Rust

`ZULIP_RUST_CODE_HEADER=1` opts into native first-line header matching in
`CodeHilite._parseHeader`. It is off by default and independent of
`ZULIP_RUST_CODE_HIGHLIGHT`. `markdown_code_header::parse_header` preserves
shebang/path greediness, two-or-more colons, the exact CPython Unicode word
and whitespace classes, and matching single/double quotes around `hl_lines`.
The header stays in the displayed source; only leading/trailing LF characters
are removed, as in the original split/join/strip sequence. CR characters and
all other whitespace are retained.

`parse_fenced_code_header(source, unicode_version)` returns
`(normalized_source, optional_header)`, where the header is
`(language, shebang, optional_highlight_lines)`. `None` delegates before any
mutation when Unicode differs from 15.0.0, UTF-8 conversion fails (including
surrogates), or source exceeds 8 MiB. The adapter also delegates for runtimes
other than CPython 3.12, string subclasses and older wheels without the export.
The optional [header value exports](#normalize-fenced-code-header-values-through-rust)
normalize ASCII language names and bounded ASCII line numbers. Other values
retain Python Unicode lowercase, arbitrary precision, digit limits and custom
helper failures. Language, line-number and highlight-line assignments
retain their original order; a failure does not normalize the source or retry.
Pygments, lxml and exception logging remain in their existing paths.

Using cached dependencies in the provisioned Linux development environment:

```bash
maturin build --manifest-path rust-backend/Cargo.toml --release --locked --offline \
    --out var/code-header-wheels
python -m pip install --no-index --no-deps --target var/code-header-python \
    var/code-header-wheels/*.whl
PYTHONPATH="$PWD/var/code-header-python:$PWD" ZULIP_RUST_CODE_HEADER=1 \
    ./tools/test-backend zerver.tests.test_code_header_rust zerver.tests.test_markdown
PYTHONPATH="$PWD/var/code-header-python:$PWD" DJANGO_SETTINGS_MODULE=zproject.test_settings \
    python rust-backend/tests/check_code_header_parity.py --unicode \
    --report var/code-header-parity.json
```

Use a fresh installation target when changing a wheel; retain previous targets
for fallback checks. The parity oracle reads the original regex from the Python
method's AST, checks all Unicode scalars in three fixed contexts, enumerates
short strings and compares randomized complete highlighter state and errors.
It checks that every supported input actually used the native parser. The
`--mutant-remove-header` option deliberately removes the displayed first line
and must produce differences. This is a compatibility corpus, not exhaustive
string coverage or a performance benchmark. Full renderer and TeX deferred
planner effects still need migration before Python files can be retired.

## Escape fenced code HTML through Rust

The existing default-off `ZULIP_RUST_CODE_HIGHLIGHT=1` also opts into
`escape_fenced_code(source)` when the installed wheel exports it. The native
helper reuses inline code escaping, then escapes double quotes, matching the
original four replacements in order. Apostrophes, line endings, NUL and Unicode
text stay unchanged; existing entities have their ampersand escaped once.
Both disabled highlighting and the existing logged highlighter-error fallback
use this path. There is no additional flag, dependency or HTML parsing step.

The adapter accepts exact strings. String subclasses keep their `replace`
behavior in Python. Invalid UTF-8 conversion (including surrogates) and sources
over 8 MiB return `None` before effects and use the original implementation.
Older wheels fall back independently of their highlighting exports. Native
exceptions propagate without a retry; highlighter exceptions retain the
original Python logging context. Pygments and lxml are still external Python
runtime dependencies; this does not retire the fenced-code module.

With a fresh cached wheel installed into a separate target:

```bash
PYTHONPATH="$PWD/var/code-escape-python:$PWD" ZULIP_RUST_CODE_HIGHLIGHT=1 \
    ./tools/test-backend zerver.tests.test_code_escape_rust \
    zerver.tests.test_code_highlight_rust zerver.tests.test_markdown
PYTHONPATH="$PWD/var/code-escape-python:$PWD" DJANGO_SETTINGS_MODULE=zproject.test_settings \
    python rust-backend/tests/check_code_escape_parity.py --unicode \
    --report var/code-escape-parity.json
```

Build with `maturin build --release --locked --offline` and install with
`pip --no-index --no-deps` as above, retaining earlier wheel targets. The oracle
uses the unchanged Python fallback as its reference, checks every Unicode
scalar in a fixed HTML context, enumerates short strings, randomizes bodies
and exercises both sides of the byte-size limit. It verifies actual native
acceptance and explicit delegation. `--mutant-unescaped-quotes` must report
differences. This is neither exhaustive string testing nor a benchmark.

## Choose unused quote fences through Rust

`ZULIP_RUST_MARKDOWN_FENCES=1` additionally opts `get_unused_fence` into
`get_unused_markdown_fence(content)` when the wheel exports it. This reuses
`reminder_quote::unused_fence_length`, the same native scanner already used by
reminder quote construction. It finds backtick runs of at least three at the
start of a line after zero to three ASCII spaces and returns one more backtick
than the longest such run, with a minimum of three. Only LF starts a new line;
CR, Unicode line separators, tabs and other spaces retain their existing
Python regex behavior. Tildes and inline backticks do not affect the result.

The helper's consumers include quote replies, reminders, moderation reports,
onboarding and GitHub/GitLab webhook content. Their imports and formatting stay
unchanged. The flag remains off by default; older wheels without this export,
string subclasses, surrogate-containing text and input over 8 MiB use the
original regex before any external effects. A native exception propagates
without retrying. There is no new dependency, routing change or Python-file
retirement, and this does not replace `close_unclosed_fences`.

With a fresh cached wheel in `var/unused-fence-python`:

```bash
PYTHONPATH="$PWD/var/unused-fence-python:$PWD" ZULIP_RUST_MARKDOWN_FENCES=1 \
    ./tools/test-backend zerver.tests.test_unused_fence_rust \
    zerver.tests.test_compose_reply zerver.tests.test_message_report zerver.tests.test_reminders
PYTHONPATH="$PWD/var/unused-fence-python:$PWD" DJANGO_SETTINGS_MODULE=zproject.test_settings \
    python rust-backend/tests/check_unused_fence_parity.py --unicode \
    --report var/unused-fence-parity.json
```

Use the locked/offline build and no-index/no-dependency installation commands
above, retaining earlier targets. The oracle compares the original Python
helper with the native scanner, checks actual acceptance and delegation,
enumerates short strings, tests every Unicode scalar in three fixed contexts
and includes both sides of the size limit. `--mutant-short-fence` must report
differences. This is a compatibility corpus rather than exhaustive testing or
a performance benchmark. Deferred TeX, previews and complete renderer/listener
orchestration remain separate migration work.

## Close unclosed quote fences through Rust

`ZULIP_RUST_MARKDOWN_BLOCKS=1` additionally opts `close_unclosed_fences` into
`close_unclosed_markdown_fences(content, default_language=None)` when exported
by the wheel. It composes the existing whitespace normalizer (tab width four)
and block planner, then appends the remaining fences in reverse nesting order.
The original source is preserved byte for byte. CR/CRLF, tabs and stripped
Markdown control characters affect fence detection only. Quotes inherit the
default language, spoilers reset it, and code/math blocks do not nest.

This bypasses construction of Python Markdown handlers for supported inputs.
It does not render code, TeX or spoilers. The existing flag stays off by default.
Older wheels, string subclasses, surrogate-containing source or default
languages, and source over 8 MiB use the Python path before native effects.
Other Unicode defaults use Python's lowercase rules, while the original
default spelling remains part of the plan for Python callbacks.
Native exceptions propagate without retry. No dependencies, HTTP routes or
Python file retirements are added.

Build with the existing `--locked --offline` wheel commands and install with
`--no-index --no-deps` into a fresh `var/close-fences-python` target:

```bash
PYTHONPATH="$PWD/var/close-fences-python:$PWD" ZULIP_RUST_MARKDOWN_BLOCKS=1 \
    ./tools/test-backend zerver.tests.test_close_fences_rust \
    zerver.tests.test_compose_reply zerver.tests.test_markdown
PYTHONPATH="$PWD/var/close-fences-python:$PWD" DJANGO_SETTINGS_MODULE=zproject.test_settings \
    python rust-backend/tests/check_close_fences_parity.py \
    --report var/close-fences-parity.json
```

The seeded differential disables the reference's Rust block/fence/whitespace
bridges. It compares complete strings, verifies acceptance and delegation, and
covers nested fences, default languages, whitespace and size boundaries.
`--mutant-reverse-order` must report differences. This is a compatibility
corpus, not exhaustive testing or a performance benchmark. Whole-graph TeX
preflight, deferred rendering effects and full renderer/listener orchestration
remain separate work.

## Normalize fenced code header values through Rust

The existing default-off `ZULIP_RUST_CODE_HEADER=1` bridge also uses
`normalize_fenced_code_language` and `parse_fenced_highlight_lines` when
available. Rebuild the wheel to enable these exports. ASCII language names
use ASCII lowercase; non-ASCII names retain Python's contextual Unicode
lowercase. Line numbers accept signed ASCII decimal integers and Python's
underscore separators, preserving duplicates, order, zero and negative values.
Invalid accepted syntax produces an empty list, including a valid prefix
followed by an invalid token.

Non-ASCII expressions, numbers outside signed 64-bit range, tokens of 640 or
more bytes, surrogates and expressions above 8 MiB delegate to Python. The token
limit preserves CPython's configurable decimal conversion limit, including long
runs of leading zeros. A replaced `parse_hl_lines` helper always runs in Python.
Missing exports in older wheels delegate independently. Language assignment,
line-number options, highlight options and source assignment retain their
existing order, including partial state on an exception. Native exceptions
propagate without retrying the Python parser.

```bash
ZULIP_RUST_CODE_HEADER=1 ./tools/test-backend zerver.tests.test_code_header_rust
python rust-backend/tests/check_code_header_values_parity.py \
  --report /tmp/code-header-values-parity.json
```

The deterministic oracle compares actual extension results with Python-Markdown,
including short syntax combinations, random signed integers, every Unicode
scalar and surrogate in separator position, and size/digit boundaries. Delegated
cases are counted separately. `--mutant-drop-sign` must reject a deliberately
incorrect sign conversion. The existing header oracle additionally compares
complete highlighter states and exceptions. Neither oracle is a benchmark or an
exhaustive test of all strings. Pygments, lxml and the fallback header methods
remain Python dependencies; this bridge does not retire a Python module.

## Issue KaTeX service requests through Rust

The existing default-off `ZULIP_RUST_KATEX_RESPONSE=1` bridge also uses
`request_katex_service` when available. Request and response exports are checked
independently, so an older wheel retains its existing response bridge and the
Python request path. The request bridge constructs the fixed localhost URL,
form data and proxy override and invokes the live `KatexSession().post` once.
It keeps the existing `OutgoingSession` timeout, HTTP policy and connection
handling; no new HTTP client or dependency is introduced.

Session construction, method lookup, port formatting, inline-mode truthiness,
secret lookup and the request retain Python's evaluation order. Content is
passed without encoding or normalization, including surrogate strings. The
service secret remains in form data, and the fixed localhost request retains
`proxies={"http": ""}` to bypass Smokescreen intentionally. This bridge never
retries a failed request or falls back after a request has begun.

The `try`/`except` and warning calls stay in Python. This preserves the active
handled exception visible to logging callbacks and the request exception as
`__context__` if logging itself fails. Response decoding remains outside the
request catch. CLI selection and native planner delegation are unchanged.
This is request orchestration through the existing Python HTTP client, not a
standalone Rust transport or a Python module retirement.

Build a fresh wheel with `maturin build --release --locked --offline` and install
it into a separate target with `pip install --no-index --no-deps --target`:

```bash
PYTHONPATH="$PWD/var/katex-request-final-python:$PWD" \
    ZULIP_RUST_KATEX_RESPONSE=1 ./tools/test-backend \
    zerver.tests.test_katex_request_rust zerver.tests.test_katex_response_rust
PYTHONPATH="$PWD/var/katex-request-final-python:$PWD" \
    DJANGO_SETTINGS_MODULE=zproject.test_settings \
    python rust-backend/tests/check_katex_response_parity.py \
    --require-request-bridge --seed 962853 --cases 20000 \
    --report /tmp/katex-request-parity.json
```

The offline oracle compares results, exceptions, request arguments and warnings
with the live Python implementation, and requires exactly one native request
call per native case. `--mutant-display` must reject an inverted display-mode
field. Tests additionally cover factory failures, exception context, live
settings, callback evaluation order, partial wheel capabilities and CLI
isolation. Controlled sessions avoid network traffic; this is not an HTTP
transport benchmark or exhaustive input coverage.

## Select and encode KaTeX CLI input through Rust

The default-off `ZULIP_RUST_MARKDOWN_TEX=1` CLI bridge independently checks
`select_katex_encoding` and `encode_katex_input`. The selector honors UTF-8 mode
before reading the live locale encoding. Exact strings with canonical UTF-8
codec names are copied into bytes in Rust. Other codecs, string subclasses and
surrogate errors use the original Python `encode` method. No newline, NUL or
Unicode normalization is performed on stdin.

Encoding selection stays outside the `NotImplementedError` process fallback;
input encoding stays inside it, at the original point before process creation.
An unavailable locale therefore propagates, while an encoder that explicitly
raises `NotImplementedError` retains the existing subprocess fallback. Ordinary
encoding errors propagate without starting or retrying a process. Missing
exports delegate independently, including with wheels that already support
native process creation and output normalization. Linux, encoding-warning and
subprocess callback guards remain in force.

Using a locked offline wheel installed with `--no-index --no-deps` into
`var/katex-input-python`:

```bash
PYTHONPATH="$PWD/var/katex-input-python:$PWD" \
    ZULIP_RUST_MARKDOWN_TEX=1 ./tools/test-backend \
    zerver.tests.test_katex_input_rust zerver.tests.test_katex_output_rust \
    zerver.tests.test_tex_rust
PYTHONPATH="$PWD/var/katex-input-python:$PWD" \
    python rust-backend/tests/check_katex_input_parity.py \
    --unicode-scalars --seed 962854 --cases 20000 \
    --report /tmp/katex-input-parity.json
```

The oracle compares encoded bytes and complete error arguments against Python
for every Unicode code point, including surrogates, in a fixed stdin context,
plus seeded random strings across twelve codecs. `--mutant-newlines` must reject
a deliberately normalized CRLF sequence. Focused tests cover live encoding
selection, independent wheel capabilities, custom encoders and fallback/error
boundaries. These checks do not exhaust all strings or benchmark encoding.
Python codec fallbacks, CLI selection and exception handling remain dependencies;
this bridge does not retire `tex.py` or activate standalone HTTP routes.

## Decode successful KaTeX HTTP responses through Rust

With the existing default-off `ZULIP_RUST_KATEX_RESPONSE=1`, successful
responses containing exact `bytes` with valid UTF-8 now decode and strip in
Rust. The shared Python whitespace predicate includes U+001C through U+001F.
Internal CRLF, CR, NUL and other characters remain unchanged; HTTP responses
do not use the CLI's universal newline conversion. Empty bodies and bodies
larger than 8 MiB keep the original behavior.

Invalid UTF-8 delegates to the original decoder to preserve the complete
`UnicodeDecodeError`. Bytes subclasses, bytearrays and custom content objects
retain their live `decode().strip()` methods, including their exceptions.
Response attributes are read at the same decision points. Error statuses,
request handling, logging, the disabled flag and older wheels keep their
existing behavior. No request or renderer callback is retried.

After an offline wheel build and installation into `var/katex-http-text-python`:

```bash
PYTHONPATH="$PWD/var/katex-http-text-python:$PWD" \
    ./tools/test-backend zerver.tests.test_katex_response_rust
PYTHONPATH="$PWD/var/katex-http-text-python:$PWD" \
    python rust-backend/tests/check_katex_http_text_parity.py \
    --report /tmp/katex-http-text-parity.json
```

The differential checks every Unicode scalar at the boundaries and inside a
fixed body, all byte strings of length zero through two, seeded random bytes,
and size boundaries. It compares exception types and full arguments. The
`--mutant-ascii-strip` control must fail on Unicode whitespace. This is a
finite corpus, not exhaustive string coverage or a performance benchmark.
This computation remains a PyO3 bridge; HTTP transport, renderer orchestration
and Python module retirement remain separate migration work.

## Preflight deferred TeX outcomes without rendering

`markdown_tex_preflight::preflight` is a pure Rust preparation API for the
native inline planner. Pattern kind 22 uses the existing native TeX matcher.
Before exposing renderer arguments, preflight checks the entire graph for
every combination of successful and failed formulas. Each branch keeps its
own error spans, inline stash variants, HTML counter, link destinations and
titles. All branches must agree on the ordered renderer arguments, including
duplicates. A late unsupported URL, guard, or resource limit rejects the
whole preflight without renderer callbacks or host mutations.

The bounds are six formulas, 64 outcome combinations, 100,000 retained graph
items and 8 MiB of retained text. Larger inputs delegate. Guards must be pure
and may run repeatedly. Callers must validate the live handler registry,
CPython/Unicode syntax compatibility, authorized snapshots and HTML stash
semantics before supplying inputs. `calls()` exposes the certified arguments;
`into_plan()` selects an existing branch and fills its reserved raw HTML
blocks. An empty successful rendering is distinct from failure. Selection
does not run handlers, reparse rendered HTML or retry an unsupported plan.
A result-count mismatch is a caller protocol error and must propagate.

This API is **not connected to the Python adapter**. The default and opt-in
native planners still delegate matching TeX before rendering. The prefix
checkpoints below prepare exception-state handling. Applying them to the host
and measuring enumeration cost remain prerequisites for a separate opt-in
adapter; a final-state certificate alone cannot retire any Python module.

With the existing cached Rust dependencies and provisioned Python environment:

```bash
cargo test --manifest-path rust-backend/Cargo.toml --locked --offline \
    --features native-markdown --test tex_preflight
cargo build --manifest-path rust-backend/Cargo.toml --locked --offline \
    --features native-markdown --example tex_preflight
python rust-backend/tests/check_tex_preflight_parity.py \
    --probe rust-backend/target/debug/examples/tex_preflight \
    --seed 962856 --cases 2000 --report /tmp/tex-preflight-parity.json
```

The oracle compares graphs, shared stash and parent identities, atomic text,
ancestors, renderer argument order, raw HTML blocks and counters with the live
Python handlers. It varies registry precedence, nested nodes, tails, atomic
input and mixed normal outcomes. `--mutant-error-class` must reject a changed
error-span class. These finite checks do not cover renderer exceptions or
benchmark the branch enumeration; no runtime activation or speedup is claimed.

## Checkpoint deferred TeX before each renderer call

`TexPreflight::begin()` returns the certified state immediately before the
first renderer call and a single-use `TexPending` continuation. Apply that state
before invoking the renderer with `body()` and `is_inline=True`. On a normal
return, `complete(Some(html))` or `complete(None)` consumes the continuation and
selects the next checkpoint, or the final state with no pending call. Empty
successful HTML remains distinct from failure. Selection neither invokes guards
nor searches Markdown, and renderer HTML stays raw. A continuation cannot be
cloned or completed twice.

If rendering raises, retain the applied state and any changes made by that
callback, drop the continuation and propagate the original exception. Never
complete it, fall back, or render again after an exception. Preflight retains
one checkpoint for each distinct prefix of normal outcomes, under the same
100,000-item and 8 MiB budgets as the final plans. Rejection still occurs before
any renderer effect. Original tails remain present while their inline handlers
run; paragraph text has already been cleared. Detached stash nodes, ancestor
stacks, parent order, preceding entities and successful HTML all reflect the
original callback position.

Checkpoint metadata contains cumulative effects **only up to that call**.
Host integration must preserve arena identities across checkpoints, apply
attachment suffixes once, union mention sets, reconcile raw HTML counters and
preserve existing metadata. It must also guard the live registry and helpers
and account for renderer mutations before replacing subsequent host state.
This pure Rust protocol does not apply host checkpoints or catch exceptions.
It has no Python adapter, new flag, default routing change or Python retirement.

The existing oracle can raise a sentinel exception at every possible callback
position, after mixtures of normal success and failure:

```bash
python rust-backend/tests/check_tex_preflight_parity.py \
    --probe rust-backend/target/debug/examples/tex_preflight \
    --seed 962857 --cases 2000 --checkpoints \
    --report /tmp/tex-checkpoint-parity.json
python rust-backend/tests/check_tex_preflight_parity.py \
    --probe rust-backend/target/debug/examples/tex_preflight \
    --cases 0 --checkpoints --mutant-clear-tail \
    --report /tmp/tex-checkpoint-mutant.json
```

The mutant command must fail. The oracle compares exception arguments, partial
graphs, stash and parent identities, atomic text, ancestors, HTML blocks and
counters, registry sorting and preservation of seeded rendering metadata.
Rust tests also exercise every outcome prefix through six formulas and check
that later attachment and mention effects have not been committed early.
These finite comparisons do not test a live Python commit adapter, arbitrary
callback mutations or production performance.

## Apply cumulative inline checkpoints to Python objects

The internal PyO3 `MarkdownInlineCheckpoint(processor, originals, ordered)`
committer prepares host application separately from renderer execution. Pass
the original Elements in the planner's arena order and the sorted registry
priority snapshot. Construction records the HTML counter and stash length
without changing the graph, registry or rendering metadata.

`apply(plan, paths, has_image, html_blocks)` accepts the existing native inline
plan tuple and cumulative effects from a certified prefix or final state. It
retains original Element and attribute-dictionary identities, creates new
arena Elements once, and reuses those Elements in later trees, stashes and
parent maps. Shared and detached nodes remain shared. Stash and parent-map
dictionaries are initialized on first application and updated in place on
later applications. Atomic strings and dictionary insertion order are restored.
The registry priority list is updated in place only when the plan uses it.

For deferred TeX, pass the state's `ancestor_history` as the optional fifth
argument. Each entry is the final value of a previous traversal's ancestor
list; `plan` contains the current list's value. Repeated checkpoints in the
same traversal update one Python list in place, including nested push/pop.
Moving to another traversal finishes earlier lists and selects a distinct
list, even when its value is equal. References retained by earlier callbacks
therefore observe the same later values and identities as Python-Markdown.
Skipped traversals are included. Histories cannot shrink or rewrite retired
lists, and applications cannot switch between history and legacy mode.
The pure preflight retains histories under its existing shared text/item
budget and separate per-pass limits; ordinary planners do not retain them.

HTML and attachment paths append only their new suffixes, including repeated
paths and empty successful HTML. Mention sets receive new IDs without losing
existing entries; flags preserve previous true values. HTML counters advance
from the captured counter independently of the initial raw-block list length.
Before modifying the host, inconsistent graph indices, stash variants,
shrinking effect prefixes, changed new-node definitions, and unexpected HTML
lengths or counters raise `ValueError`. An error during host mutation preserves
the original exception and partial state and permanently poisons the committer;
another application raises `RuntimeError`. Python reference cycles through the
processor, Elements, ancestor lists or priority snapshot participate in garbage
collection.

This is an **unconnected internal building block**, with no new flag, production
consumer, default activation or Python retirement. Callers must still guard
the live registry, helpers, runtime and snapshots before any effect. They must
certify the entire graph, apply a checkpoint before each original callback,
and propagate renderer exceptions without fallback or retry. It does not guard
renderer mutations to the graph or ancestor lists, preserve identity for
immutable strings (including the strings inside ancestor lists), or bind the
Rust continuation to the Python committer.
Those contracts and enumeration benchmarks remain prerequisites for activation.

The host differential applies whole chains of prefixes and final states to
real Python-Markdown processors. It compares live graphs, shared/stashed node
identities, atomic text, parents, ancestors, HTML and counters with snapshots
taken at the reference renderer's original callback positions. Seeded original
attributes and rendering metadata remain intact. The diagnostic transport omits
the registry-used bit, so that decision is supplied by the reference here and
tested separately by the existing native engine suites. Retained ancestor
aliases are compared at every callback and completion, including their values
and pairwise identities. The probe's optional `history` field exposes retired
lists without changing its original transport.

```bash
PYTHONPATH="$PWD/var/ancestor-history-final-python:$PWD" \
    python -m unittest zerver.tests.test_markdown_inline_checkpoint
cargo build --manifest-path rust-backend/Cargo.toml --locked --offline \
    --release --features native-markdown --example tex_preflight
PYTHONPATH="$PWD/var/ancestor-history-final-python:$PWD" \
    python rust-backend/tests/check_inline_checkpoint_parity.py \
    --probe "$CARGO_TARGET_DIR/release/examples/tex_preflight" \
    --seed 962859 --cases 2000 --report /tmp/ancestor-history-parity.json
PYTHONPATH="$PWD/var/ancestor-history-final-python:$PWD" \
    python rust-backend/tests/check_inline_checkpoint_parity.py \
    --probe "$CARGO_TARGET_DIR/release/examples/tex_preflight" \
    --cases 0 --mutant-duplicate-html --report /tmp/host-checkpoint-mutant.json
PYTHONPATH="$PWD/var/ancestor-history-final-python:$PWD" \
    python rust-backend/tests/check_inline_checkpoint_parity.py \
    --probe "$CARGO_TARGET_DIR/release/examples/tex_preflight" \
    --cases 0 --mutant-replace-ancestors --report /tmp/ancestor-history-mutant.json
```

Both mutant commands must fail. These finite tests do not establish production
renderer integration or performance.

## Locate the KaTeX CLI through Rust

With the existing opt-in `ZULIP_RUST_MARKDOWN_TEX=1`, wheels exporting
`locate_katex_cli` select the packaged production script or development
script in Rust. The resolver calls the live Python path helpers and
returns the original path object after its file check, preserving
platform behavior, exceptions, and evaluation order. Missing scripts
still log once and return without spawning a process. Wheels exporting
`katex_native_cli_allowed` also evaluate the Linux, encoding warning,
and subprocess identity guards in Rust. Guard evaluation still short
circuits in the original order, and replacing either subprocess hook
still selects the Python runner. Older wheels can support either
capability independently. The HTTP service, encoding, and fallback
policies are unchanged.

A targeted check uses the actual Python dispatch and a fresh extension
with controlled settings and storage resolution. It covers the two
paths, missing files, resolver and filesystem errors, and a local
Node.js pipe fixture. From a shell with the Python test dependencies:

```sh
cargo build --manifest-path rust-backend/Cargo.toml --locked --offline --features python --lib
extension_dir=$(mktemp -d)
ln -s "$PWD/rust-backend/target/debug/lib_zulip_backend.so" "$extension_dir/_zulip_backend.so"
PYTHONPATH="$extension_dir:$PWD" python rust-backend/tests/check_katex_cli_location.py -v
```

This Linux check does not require Django services or verify the real
production static-file manifest.

## Transfer deferred TeX checkpoints to Python

The internal `preflight_markdown_tex_checkpoint` export accepts a graph
snapshot and a registry limited to native HTML entities and TeX. It
returns no plan for unsupported or over-budget inputs, before any
callback or host mutation. A returned `MarkdownTexCheckpoint` exposes
the next renderer argument as `body` and transfers a checkpoint once
with `take_checkpoint()`. The tuple and ancestor history are accepted
by the existing `MarkdownInlineCheckpoint.apply` committer.

Only after applying the checkpoint and receiving a normal renderer
result may a caller use `complete(result)`. It consumes one continuation
and returns the next checkpoint. `None` is ordinary failure; an empty
string is a successful rendering. If the renderer raises, the caller
must discard the continuation and propagate the original exception after
preserving its effects. This transport checks ownership and sequencing,
not whether the caller actually applied the checkpoint or ran a callback.

With the same Linux extension setup as the previous section:

```sh
PYTHONPATH="$extension_dir:$PWD" python rust-backend/tests/check_tex_checkpoint_transport.py -v
PYTHONPATH="$extension_dir:$PWD" python -m unittest zerver.tests.test_markdown_inline_checkpoint
```

This interface is not connected to the live renderer or any feature flag.
Live registry and helper guards, immutable object identity, callback
mutation handling, and cost bounds require further verification before
activation. This is not a replacement for the Python renderer.

## Format fenced block quotes through Rust

With `ZULIP_RUST_MARKDOWN_FENCES=1`, a capable extension prefixes every
LF-separated line with `> `. LF, including terminal LF, is the only
separator. Empty lines are still quoted, and CR, NUL, and Unicode are
preserved. The path delegates subclasses, invalid UTF-8 from Python
surrogates, and output larger than 8 MiB without altering the fallback.
Wheels without the formatter and disabled flags retain the Python path.

Using the same Linux extension setup:

```sh
PYTHONPATH="$extension_dir:$PWD" python rust-backend/tests/check_fenced_quote_parity.py -v
```

This check extracts the actual source method and compares opt-in and
old-wheel dispatch with Python. It does not exercise the complete
provisioned Django Markdown stack.

## Assemble fenced spoilers through Rust

On the same opt-in path, a capable extension assembles spoiler fragments
and invokes the current Python placeholder method at each of the original
three call sites. A callback can replace the method for the next call.
The original error types and side effects remain observable. If fragments
are not exact UTF-8 strings or exceed the native join budget, Python joins
the already-collected fragments without invoking any callback again.
A missing export selects the original implementation.

The fenced-quote parity script also tests these contracts, including
all three throwing positions and an invalid fragment discovered at join.

## Preserve untouched Python text identities at TeX checkpoints

The experimental `take_checkpoint_with_text()` path also transfers the
planner's original text/tail provenance bits. Pass them as the optional
`untouched_text` argument of `MarkdownInlineCheckpoint.apply` after the
ancestor history. Only slots that have never been rewritten in the plan
can retain their original Python object; equal text is not evidence of
provenance. A slot still visible at a renderer boundary keeps its exact
string or `AtomicString` identity even when another slot has changed.

The committer rejects an inconsistent slot or a replacement of a
previously protected object before performing any new host writes.
This includes an equal-valued replacement by a callback. This is stale
snapshot detection, not recovery from arbitrary callback mutations:
after a renderer effect, do not switch to the legacy renderer or retry.
The older two-item transport and existing callers without provenance
continue to work. The live renderer and its feature flags are unchanged.

## Detect stale Python registries between TeX checkpoints

An experimental caller can call `protect_registry()` before planning and
before applying any checkpoints. The committer then checks the exact
Python-Markdown registry, its handler mapping, priority list and
entries, and sorted flag before each apply. Its own successful
in-place sort updates the expected order. A later change is rejected
before additional host writes, without undoing the earlier renderer
callback. Callers must not retry or switch the renderer after effects.

This is a structural identity check, not a proof that a mutable handler
or helper has retained its behavior. A live adapter still must certify
the registry semantics, helpers, and callback effects before enabling
the checkpoint path.

## Finalize fenced blocks through Rust

With `ZULIP_RUST_MARKDOWN_FENCES=1`, a wheel exporting
`finish_fenced_block` can run the shared handler completion lifecycle in
Rust. It still looks up the formatter, placeholder, output and pop
callbacks at their original call positions. Python performs the string
join and split so custom objects, lone surrogates, and errors retain
their original behavior. A failure stops at the first unsuccessful
operation and preserves any preceding writes without retry or fallback.
An empty block only pops its handler. Disabled flags and older wheels
run the existing Python implementation.

The fenced-quote parity script exercises the source completion method
with dynamic output replacements and a failure injected at each stage.

## Dispatch fenced block lines through Rust

With `ZULIP_RUST_MARKDOWN_FENCES=1`, a capable wheel can dispatch each
line in the base fenced block handler. It preserves dynamic Python
`rstrip` and rich comparison (including the comparison's truth test).
On a closing marker it calls the live `done` method. Otherwise it resolves
the live append method and only then performs the second `rstrip`,
matching Python's evaluation order. Specialized quote and spoiler
handlers and old wheels keep their existing behavior.

The fenced-quote parity script tests Unicode whitespace, lone surrogates,
dynamic equality and exact error propagation at each dispatch stage.

## Dispatch nested quote and spoiler lines through Rust

Under the same opt-in flag, capable wheels share Rust's closing marker
check across the base, quote, and spoiler handlers. A nested line is
passed unchanged to the live `check_for_new_fence` callback with its
original processor and output. Quotes pass their current
`default_language` keyword; spoilers do not pass that keyword. The
handler resolves the callback and the arguments in Python's original
order and propagates exceptions without repeating an effect. Older wheels
and disabled flags keep the Python handlers.

The fenced-quote parity script exercises both source methods, the
specialized language behavior, and callback failures without starting a
Django test database.

## Dispatch new fenced blocks through Rust

With `ZULIP_RUST_MARKDOWN_FENCES=1`, Rust handles the result from the
original fence parser: it appends an unmatched line unchanged, or
selects the default language, calls the live handler factory, and pushes
the handler. Python overrides of the parser stay on the Python path
before parsing begins, preserving custom return values and exceptions.
Wheels without this export and disabled flags keep the legacy dispatch.

The fenced-quote parity script now runs the actual handler stack with
a real Markdown HTML stash, deterministic code/math formatters, and no
Django services. It checks nested quotes and spoilers, inherited
languages, unfinished fences and validator errors.

## Select fenced block handlers through Rust

Under the same opt-in, a capable extension selects the quote, math,
spoiler, or code handler for an exact Python string or `None`. It keeps
Python Unicode lowercasing for the code language passed to the factory,
including lone surrogates. Factories are resolved at the original call
site, retain their original arguments and exceptions, and run only once.
A string subclass or another custom language object is delegated
before lowercasing; wheels without the new export use Python.

The fenced-quote parity script tests live replacement factories, all
four choices, Unicode lowercasing, and a subclass with an overridden
`lower` method.

## Finish fenced input through Rust

Under the same opt-in, the Rust EOF handler drains the current parser
stack with Python's live `done` methods, then applies the existing
trailing-empty-line compatibility rule. It rereads the stack after
each callback and preserves dynamic length, indexing, comparison,
and append behavior. Any exception stops the sequence without retry.
Disabled flags and older wheels retain the Python path.

The fenced-quote parity script uses the source EOF method in the real
nested handler fixture and checks stack replacement, padding, and errors.

## Dispatch fenced input lines through Rust

`ZULIP_RUST_MARKDOWN_FENCES=1` can also dispatch the unplanned stream of
input lines through the Rust adapter. It advances the Python iterator once
per line and resolves the current handler at index `-1` after each advance.
Line objects are passed unchanged; a handler can replace the stack and an
exception stops dispatch without retry. The existing block planner keeps
priority and its materialized lines still feed the ordinary dispatch if it
declines. Disabled flags and older wheels retain the Python loop.

The parity script covers the real nested Markdown stack, successful and
declined plans, iterator and callback failures, and line lifetime across
iterator advances.

## Validate fenced curl examples through Rust

When the existing fenced Markdown opt-in is enabled and the installed
extension supports this validator, Rust finds the first invalid curl line
in an ordinary list of strings. It preserves the legacy substring rule,
including the optional literal `sS` pair, not separate `s` or `S`.
Python still constructs the exact original rendering error and trims the
original line. Custom containers and strings, lone surrogates encountered
before any error, a replaced regex search, old wheels, and the default
configuration keep the Python validation path.

The parity script compares the source validator across historical flag
and quoting cases, 2,000 seeded input sets, and real Markdown fenced
handler success and failure.

## Measure fenced Markdown parity and performance without Django setup

With the local PyO3 extension importable (prefer an optimized build for
timings), run these from the repository root:

```sh
CARGO_NET_OFFLINE=true cargo build --manifest-path rust-backend/Cargo.toml --locked --release --features python --lib
python rust-backend/tests/check_fenced_quote_parity.py
python rust-backend/tests/benchmark_fenced_markdown.py --iterations 200 --repeats 7
```

The fixture loads the source fenced extension, preprocessor and code
formatter into Python-Markdown. It compares final HTML, stash contents
and validation errors across Python, opt-in Rust and a wheel without the
new entry points. The rendering test covers 88 inputs with and without
Pygments and with and without a realm default. The benchmark interleaves
Python and Rust, checks parity first, and reports median per-conversion
times and Rust/Python ratios. The fixture supplies a realm setting and
a deterministic TeX stub; it does not exercise full Zulip rendering,
production traffic or the actual TeX service.

To isolate the net cost of optional line dispatch and handler adapters,
run the same optimized extension with fixture-only omissions. Each mode
must match Python's HTML and stash before being timed.

```sh
python rust-backend/tests/benchmark_fenced_markdown.py --isolate-exports --samples plain code nested batch \
  --highlight off --iterations 240 --repeats 10 --report fenced-no-highlight.json
python rust-backend/tests/benchmark_fenced_markdown.py --isolate-exports --samples plain code nested batch \
  --highlight on --iterations 240 --repeats 10 --report fenced-with-highlight.json
```

The JSON includes the omitted exports, timing order, and paired ratios
within each repeat. The numerator and denominator are named in each key.
Use a multiple of ten repeats for equal position counts in the five-mode
experiment. The [recorded sample](https://github.com/mlkmhd/zulip-gold/blob/main/rust-backend/tests/fenced_markdown_export_omission_sample.json)
shows a net contribution for both omissions on the batched fixture, but
does not measure individual call cost. Interactions and measurement noise
remain; this is not a production performance or compatibility result.

### Isolate the fenced parser and handler factory

Use `--isolate-parser-factory` instead of `--isolate-exports` to
compare six modes: all Python, all Rust, and four Rust modes with the
line adapters held off. Those four switch the parser and handler factory
independently between Rust and their source Python implementations.
The new-block dispatcher stays Rust in all four: the Python parser
override changes only the fixture's parser namespace and preserves the
original-parser guard.

```sh
python rust-backend/tests/benchmark_fenced_markdown.py --isolate-parser-factory \
  --samples plain code nested batch --highlight both --iterations 240 --repeats 12 \
  --report fenced-parser-factory.json
```

Use a multiple of twelve repeats for equal timing-order position counts.
The [recorded sample](https://github.com/mlkmhd/zulip-gold/blob/main/rust-backend/tests/fenced_markdown_parser_factory_sample.json)
shows no consistent stand-alone parser or factory improvement in the
batch, and a residual Python/Rust gap after both omissions. There is no
basis here to change the production dispatch or defaults.

### Compare one batch planner call with per-line crossings

The fixture can exercise the existing opt-in `plan_markdown_blocks`
route with the per-line Rust adapters disabled, or together with the
rest of the fenced bridge. In the isolated planner-only mode, native
planning runs once per supported input, then Python performs the actual
formatting. A declined plan stays on the Python path.

```sh
python rust-backend/tests/benchmark_fenced_markdown.py --compare-planner \
  --samples plain code nested batch --highlight both --iterations 240 --repeats 8 \
  --report fenced-batch-planner.json
```

The [recorded sample](https://github.com/mlkmhd/zulip-gold/blob/main/rust-backend/tests/fenced_markdown_batch_planner_sample.json)
shows the planner-only mode about 12% faster than scalar Rust without
Pygments and 7% faster with Pygments on the batched fixture. The advantage
over Python on that fixture was smaller (about 2%); plain highlighted
input was slower than Python. The Python formatter and validators
remain active. This is service-free evidence, not end-to-end Zulip
parity or a basis for enabling a default.

### Check planner parity through the actual Zulip Django renderer

The integration check is separate from the service-free fixture. It
compares complete `do_convert` results for fenced Markdown cases in the
repository's Markdown fixtures, plus three targeted inputs, with and
without a persisted synthetic realm. It also requires real KaTeX
output and the realm's default code language. Only the planner varies;
the synthetic rows are rolled back.

The check also invokes the actual message-rendering entry point for five
unsent direct-message examples in the synthetic realm. It compares the
rendered HTML and metadata using persisted users, a recipient group, a
realm linkifier and an alert word. Positive assertions cover the mention,
alert and link outside the fence, and make sure the same content inside
code is not interpreted as active metadata. It does not send messages or
notifications.

Use a **disposable** checkout and cached matching dependencies. In the
snapshot, generate fresh development settings with
`scripts/setup/generate_secrets.py --development`; do not copy actual
settings or credentials. Provide the generated emoji data matching
`generate_sha1sum_emoji` and the KaTeX package version pinned in the lockfile.
Put the locally built optimized extension on `PYTHONPATH`.

Start a disposable PostgreSQL cluster with peer authentication, an
owner-only (0700) Unix socket directory and `listen_addresses=''`.
Create only its `zulip_test` database, load the existing `pgroonga`
extension, and apply the real Django migrations. Use a local settings
module inheriting `zproject.test_settings` and override only the test
database connection to that socket, its port, the peer role and an
empty password. Do not use an existing database or change any host
service or global settings. Stop the cluster after the check.

With that settings module and no Rust flags set, run:

```sh
DJANGO_SETTINGS_MODULE=isolated_test_settings PYTHONPATH="$NATIVE_EXTENSION_DIR:$PWD" \
  python rust-backend/tests/check_fenced_django_parity.py --socket-directory "$PG_SOCKET_DIR"
```

The check refuses non-test settings or a PostgreSQL listener on TCP,
and blocks Python socket connections other than the guarded libpq
connection. It does not exercise the complete Zulip test suite, client
requests, or deployment-level performance.

### Replay fenced block plans in Rust

When `ZULIP_RUST_MARKDOWN_BLOCKS=1`, a wheel with the new replay export
can execute the plan inside Rust for standard fenced preprocessors and
plain output lists. It applies text, open and close operations without
building a Python tuple for every line. Adjacent text lines are appended
in bulk only for standard handlers and exact list destinations. Custom
destinations are appended one line at a time against the live stack.
Python still owns handler creation, content validation, HTML stash,
formatting (including Pygments and TeX), and EOF behavior. Unsupported
input declines before replay effects; callback exceptions propagate
without retry. Old wheels and custom preprocessors retain scalar replay.

The optimized sample comparison uses identical code and disables only
the replay export for the scalar reference:

```sh
python rust-backend/tests/benchmark_fenced_markdown.py --compare-native-replay \
  --samples plain code nested batch long-code --highlight both \
  --iterations 360 --repeats 12 --report fenced-native-replay.json
```

On one local run, the 100-line non-highlighted code sample took about 6%
less time than the old Rust planner with scalar Python replay. Its
highlighted counterpart was effectively unchanged and the mixed batch
took about 1% more time. This is a migration behind the existing flag,
not a general performance improvement or a change to production defaults.

## Render plain reply links for mobile clients through Rust

With `ZULIP_RUST_PLAIN_REPLY_LINKS=1`, the PyO3 extension checks the
existing mobile client names, renders plain reply-link HTML, and selects
the requested HTML fields in one batch for ordinary dictionaries.
This is a delivery-side transformation; stored HTML and web rendering
are unchanged. Exact client names remain case-sensitive and are not
version strings. The transformation preserves search highlights,
entities, multiline excerpts, and HTML outside the matched links.
Missing exports, custom Python types, and strings that cannot be
represented as UTF-8 use the Python implementation. The flag is off by
default and can be removed to roll back.

Test the reference and native paths with a fresh offline extension:

```sh
cargo build --manifest-path rust-backend/Cargo.toml --locked --offline --features python --lib
extension_dir=$(mktemp -d)
ln -s "$PWD/rust-backend/target/debug/lib_zulip_backend.so" "$extension_dir/_zulip_backend.so"
PYTHONPATH="$extension_dir:$PWD" python rust-backend/tests/test_mobile_reply_links.py -v
```

The focused check invokes the actual compatibility module with no Django
services. Use `GetEventsTest.test_message_reply_html_compatibility` in
the backend suite to verify the live delivery and fetch surfaces.

## Plan external Markdown preview URLs through Rust

Set `ZULIP_RUST_MARKDOWN_PREVIEW_URLS=1` to use the PyO3 planners in the
existing preview treeprocessor on CPython 3.12. They select YouTube and
Vimeo video IDs, rewrite GitHub, Wikipedia, and linx.li image sources,
and recognize Dropbox paths and prepare raw file URLs. Dropbox query
duplicates keep their first position and their last nonblank value.
The Django renderer still controls whether previews are enabled, checks
MIME types and attachments, builds the HTML, and queues deferred
metadata requests. The existing URL sanitizer and preview fetchers are
unchanged. The planners never perform I/O.

Older wheels, unavailable exports, and unsupported inputs use the
Python path. These include nonstandard GitHub paths requiring
`urljoin`, bracketed or non-ASCII authorities, Vimeo's Unicode pattern
cases, undecodable form data, and nonstandard Python strings. Leave the
flag unset to use only Python.

With a freshly compiled offline extension on `PYTHONPATH` (see the
previous section), run:

```sh
python rust-backend/tests/test_preview_url_parity.py -v
```

The seeded differential compares the actual Python preview methods,
including the order and arguments of Dropbox's MIME checks. To compare
full HTML and metadata in the project's renderer, use the disposable
Django/PostgreSQL setup described in
[the fenced parity section](#check-planner-parity-through-the-actual-zulip-django-renderer):

```sh
DJANGO_SETTINGS_MODULE=isolated_test_settings PYTHONPATH="$NATIVE_EXTENSION_DIR:$PWD" \
  python rust-backend/tests/check_preview_django_parity.py --socket-directory "$PG_SOCKET_DIR"
```

That check blocks live network access, uses supplied preview metadata,
and rolls back its synthetic realm. It also compares the prior mobile
reply-link migration at the production Tornado event and message-fetch
payload builders, including rich web HTML, Markdown clients, search
matches, and edit history. It does not start a web server.

## Resolve topic link candidates through Rust

With `ZULIP_RUST_MARKDOWN_TOPIC_LINKS=1`, supported topics use the
PyO3 planner to scan the same segments for raw URLs, apply the existing
scheme defaults, and merge them with expanded realm linkifier matches.
Rust selects nonoverlapping candidates in stable priority order and
returns them in topic order. It preserves Python code-point indices,
including the legacy rule that a raw match starts at its segment's
index for overlap purposes even if its text begins later.

Django still loads and caches linkifiers, runs RE2, expands URI
templates, and caches the final result for the request. The planner
checks the active autolink regex and file-link policy before scanning.
If scanning is unsupported, Django finds raw URLs and the Rust
selector can still merge them. When either native export is absent or
its inputs are unsupported, the corresponding Python path is used.
The switch is off by default; removing it rolls back both steps.

Use a fresh cached PyO3 build and the Python dependencies used by the
repository (including `tlds`) for the focused differential check:

```sh
PYTHONPATH="$NATIVE_EXTENSION_DIR:$PWD" \
  python rust-backend/tests/test_topic_link_parity.py -v
```

For application verification, run `MarkdownLinkifierTest` and the
message-event reply regression in a disposable backend database in
both modes. For the native mode, set `ZULIP_RUST_MARKDOWN_TOPIC_LINKS=1`
with the same extension on `PYTHONPATH`. These tests do not follow any
matched URLs.

## Assemble message payload content and annotations through Rust

Set `ZULIP_RUST_MESSAGE_PAYLOAD=1` to use the opt-in PyO3 assembly used
by message fetch, event delivery, and outgoing-webhook formatting.
Rust selects HTML or Markdown for the response, removes internal edit
rendering versions, and applies the empty topic label for older
clients. It also assembles reaction and submessage arrays from Django
rows. The existing lazy reaction query still runs only after message
buckets are initialized. Stable order, shared annotation objects,
repeated message IDs, and the distinct handling of missing reaction
versus submessage targets follow the Python behavior.

Django still chooses and authorizes messages, hydrates users and
recipients, applies sender redaction and avatar policy, and removes
private internal payload fields. The content finalizer receives only
requested rendered content and the fields it needs from already
selected edit history or search matches. It gets a presence marker
rather than the edit-rendering version, and only empty topic placeholders
are needed for legacy substitution. Django keeps the full card, raw
Markdown and identity fields, and applies only the expected content
changes back to that card. The existing plain reply-link
helper runs when requested and retains its separate flag. Neither
cached message HTML nor database contents are changed by the formatter.

The existing shallow-copy and in-place mutation semantics stay the
same for ordinary dictionaries and lists. Old wheels,
custom callbacks and unsupported shapes use the Python path. Native
declines happen before any writing to caller containers; an exception
after the callback has started propagates without retry. The switch is
off by default. The narrower interface adds a small allocation and
copying cost on the opt-in path; it is an input boundary, not a
performance improvement or a new authorization boundary.

With the new extension available on `PYTHONPATH`, run:

```sh
PYTHONPATH="$NATIVE_EXTENSION_DIR:$PWD" \
  python rust-backend/tests/test_message_payload_parity.py -v
```

For actual application checks, run `test_message_dict`, the fetch and
event regressions, and the mocked outgoing-webhook payload test in a
disposable test database with the flag off and on. Include both the
Python and Rust plain reply-link modes while the payload flag is on.
With the extension available, run
`zerver.tests.test_message_payload_boundary_rust` through
`tools/test-backend` to compare exact Basic, Django session, and
web-public spectator responses across the three edit-history policies.
It also checks the projected input allowlist at the real native call,
raw and rendered output, and disabled, older-wheel, and decline paths.

### Assemble cache frames and adapt queued mobile events

With the same `ZULIP_RUST_MESSAGE_PAYLOAD=1` switch, a wheel exporting
`assemble_cached_message_frame` assembles the client-neutral message
dictionary before it is cached or finalized. Rust retains field
insertion order, identifies `/me ` messages and projects the ordered
reaction fields without changing identity of passed-through fields.
Timestamp calculation, realm-scoped topic links, edit JSON decoding,
and stale render lookup and persistence still run once in Django.

The `adapt_mobile_message_events` export traverses an ordinary list of
already-authorized Tornado events. It replaces only rows that need the
existing mobile reply-link compatibility update, copying a nested
message only when needed. Non-mobile clients, plain Markdown message
bodies, unrelated event fields, and queued source objects are not
changed. The existing plain reply-link renderer remains independently
configurable. Eligibility checks can fall back before any list
mutation; callback errors are propagated without a second pass.

Older wheels or unsupported payloads retain the relevant Python path.
The switch is still off by default. Compare both message paths with:

```sh
PYTHONPATH="$NATIVE_EXTENSION_DIR:$PWD" \
  python rust-backend/tests/test_cached_message_parity.py -v
```

In an isolated Django test database, the existing message dictionary,
fetch and event tests and the mocked outgoing-webhook payload test can
exercise all three flag combinations from the previous section.

## Format presence snapshots and events through Rust

Set `ZULIP_RUST_PRESENCE_FORMAT=1` to format already-filtered presence
rows and event details through PyO3. Rust implements the slim and
legacy shapes, missing active/connected times using the join date,
and the inclusive legacy activity cutoff at microsecond precision.
It also finalizes the single-user presence response, where only the
aggregate legacy status changes to `offline` after the existing
threshold and the old client/pushable fields are removed.

Django still selects and authorizes users, queries the database,
manages update cursors and history limits, obtains the current time,
and decides when and to whom events are published. The same preference
and visibility policies apply in either mode.

The datetime bridge accepts ordinary datetime objects using
`timezone.utc`. It limits timestamp conversion to the region where
CPython's float-based conversion cannot round across a second boundary;
the final integer truncation also preserves pre-epoch fractional
seconds. Unsupported dates, unusual rows, replaced callbacks,
nonstandard offsets, and older wheels use the Python implementation.
Unsetting the flag restores the Python path.

With the cached extension on `PYTHONPATH`:

```sh
PYTHONPATH="$NATIVE_EXTENSION_DIR:$PWD" \
  python rust-backend/tests/test_presence_format_parity.py -v
```

Use the ordinary presence tests and legacy/modern presence-event
tests in a disposable test database to verify both modes. Freshly
seeded fixture users should have an older join time to exercise
the legacy idle cases; this is a test fixture requirement, not a
change to production data or settings.

## Format unread snapshots and flag event details through Rust

Set `ZULIP_RUST_UNREAD_PAYLOAD=1` to format already-selected unread
rows through PyO3. The register/bootstrap snapshot groups and orders
one-to-one direct messages, channel topics, and group direct messages.
It keeps the first topic spelling, supports the legacy empty-topic
substitution and `sender_id` alias, preserves mention iteration order,
and uses the existing unmuted result set to calculate the displayed count.
Rust retains CPython's Unicode lowercasing for non-ASCII topic keys.

The message-flag path produces the per-message metadata needed to mark a
message unread on clients. It preserves self-DMs, sorts group recipients
without the current user, and uses the same Python event schema.
Django remains responsible for authorization, row selection, muting and
visibility, database writes, and event dispatch.

The switch is off by default. Unsupported Python object shapes and older
extensions fall back to the original formatter. With the freshly built
extension available:

```sh
PYTHONPATH="$NATIVE_EXTENSION_DIR:$PWD" \
  python -m unittest discover -s rust-backend/tests -p test_unread_payload_parity.py
./tools/test-backend zerver.tests.test_event_system.EventsEndpointTest.test_events_register_unread_topic_compatibility \
  zerver.tests.test_message_flags.GetUnreadMsgsTest zerver.tests.test_message_flags.MarkUnreadTest
```

## Assemble recent direct-message conversations through Rust

Set `ZULIP_RUST_RECENT_CONVERSATIONS=1` to assemble the intermediate
conversation state from the eligible recent/pinned rows and to format the
final `recent_private_conversations` register or browser bootstrap field.
Rust excludes the current user from each participant set (self-DMs have
no other participants), reads the pin from the current user's subscription,
and sorts the final conversation list by descending message ID, preserving
insertion order for equal IDs. It does not move pinned rows out of their
existing recency order.

Django still selects the current user's visible messages, applies the
recent-message window, loads older pinned conversations and applies event
updates. Neither authorization nor the API contract changes. The switch is
off by default, with Python fallback for older wheels and unusual object
shapes.

```sh
PYTHONPATH="$NATIVE_EXTENSION_DIR:$PWD" \
  python -m unittest discover -s rust-backend/tests -p test_recent_conversations_parity.py
./tools/test-backend zerver.tests.test_direct_message_groups.DirectMessageConversationPinTest
```

## Finalize register user lists and legacy client fields through Rust

Set `ZULIP_RUST_REGISTER_STATE=1` to process already-selected register
and browser bootstrap rows. Rust partitions the user directory by activity
and stably sorts both lists by user ID, dropping only the redundant
`is_active` field on the original rows.

Legacy clients still inherit null per-channel notification settings from
the user's defaults, or `false` for spectators. Explicit preferences are
unchanged. Legacy clients continue to receive the existing fallback name
for empty topics in `user_topics`; modern clients receive the original
nullable settings and names. Django still selects the visible rows and
decides which compatibility paths run. These formatters do not modify
persisted settings. Older wheels and unusual inputs use Python; the switch
is off by default.

```sh
PYTHONPATH="$NATIVE_EXTENSION_DIR:$PWD" \
  python -m unittest discover -s rust-backend/tests -p test_register_state_parity.py
./tools/test-backend zerver.tests.test_event_system.EventsEndpointTest.test_events_register_legacy_subscription_and_topic_fields
```

## Assemble channel subscriptions and calculate activity through Rust

Set `ZULIP_RUST_CHANNEL_PAYLOAD=1` to format the existing 41-field
channel subscription payload for register, GET subscriptions and
unsubscribe state replay. The Rust formatter uses the same explicit
field allowlist and insertion order, retains nullable notification
preferences and existing group objects, and computes legacy
`in_home_view` as the inverse of `is_muted`.

The same switch moves the weekly activity calculation shared by
channel response shapes to Rust. It preserves the seven- and
28-day age boundaries, positive nonzero floor, Python integer
division and half-even significant-digit rounding. Django still
chooses the visible channels, queries traffic, checks authorization,
reads the current time and augments subscribers. The default stays
Python; an old wheel or unsupported object shape falls back.

```sh
PYTHONPATH="$NATIVE_EXTENSION_DIR:$PWD" \
  python -m unittest discover -s rust-backend/tests -p test_channel_payload_parity.py
./tools/test-backend zerver.tests.test_subs.SubscriptionAPITest.test_subscriptions_list_keeps_muting_compatibility_and_weekly_traffic \
  zerver.tests.test_subs.StreamTrafficTest
```

### Batch the personal subscription response through Rust

Set `ZULIP_RUST_OWN_SUBSCRIPTIONS=1` to batch the 41-field active
subscription projection and its sorted order for the existing Django
GET/HEAD handler. Rust also plans which of those channels use partial
rosters. Django passes the threshold resolved in that request process
and the exact counts from its existing stream read. Django still
authenticates, selects all rows, authorizes and reads subscriber IDs,
and adds the roster fields to the response. The register path and the
default stay unchanged; missing exports and unsupported input use
the existing Python projection.

In the existing private Django fixture and loopback-only namespace,
build the locked Python extension and run:

```sh
ZULIP_RUST_OWN_SUBSCRIPTIONS=1 PYTHONPATH="$NATIVE_EXTENSION_DIR:$PWD" \
  "$venv/bin/python" "$checkout/rust-backend/tests/check_own_subscriptions_django_parity.py" \
  --fixture-root "$fixture_root"
```

This checks both signed sessions and Basic Auth with the default option,
`false`, `true` and `partial`, and an old extension or a declined batch.
This is not a standalone HTTP route and does not expand its coverage.

## Calculate thumbnail readiness through Rust

Set `ZULIP_RUST_THUMBNAIL_STATUS=1` to check the format metadata for the
existing Django thumbnail-status GET/HEAD handler. Django still authorizes
the attachment, fetches its metadata, resolves its MIME policy, and supplies
the permitted output formats. Rust checks whether the still, animated and
orientation-specific results are present. It reads no image data or storage
and does not change thumbnail generation, settings or routes. Older wheels
and unsupported historical rows use the existing Python calculation.

After building the locked Python extension, run from the existing
private Django fixture in its loopback-only namespace:

```sh
ZULIP_RUST_THUMBNAIL_STATUS=1 PYTHONPATH="$NATIVE_EXTENSION_DIR:$PWD" \
  "$venv/bin/python" "$checkout/rust-backend/tests/check_thumbnail_status_django_parity.py" \
  --fixture-root "$fixture_root"
```

This is a default-off in-process bridge, not an independent HTTP route.

### Batch transcoded preview selection through Rust

Set `ZULIP_RUST_TRANSCODE_SELECTION=1` to select transcoded thumbnail
metadata in one optional batch for manifests with at least four image rows.
Rust receives only existing eligible metadata and selects the largest actual
pixel area in one pass. Equal areas choose the last stored variant, as in
Python's stable sort. Django still owns queries and locking, MIME policy,
thumbnail paths, and enqueuing pending images. No image decoding moves.

An older extension or an unsupported row makes Django use the original
selection for the entire batch. Smaller manifests and the default flag-off
path use Python. The focused backend tests compare the manifest, fallback,
and rendered HTML (including a four-image TIFF message):

```sh
for flag in 0 1; do
    ZULIP_RUST_TRANSCODE_SELECTION=$flag ./tools/test-backend \
        zerver.tests.test_thumbnail.TestStoreThumbnail.test_transcoded_manifest_parity \
        zerver.tests.test_markdown_thumbnail.MarkdownThumbnailTest.test_batched_transcoded_preview_rendering
done
```

The local benchmark showed a speedup for metadata selection in larger
batches. Selected-row manifest timing excluded SQL and does not estimate
request or production latency.

### Plan missing thumbnail variants in the Django worker

Set `ZULIP_RUST_THUMBNAIL_PLAN=1` on the Django thumbnail worker to plan
the missing positions in its configured output list through Rust, when at
least two variants are already stored. The worker keeps the same format
objects, order, options, still/animated rules, and portrait transcode.
Python still owns MIME policy, locking, decoding, encoding, and storage.

An older extension, a small metadata list, or an unsupported row uses the
existing Django planner. The flag is independent of readiness and preview
selection and defaults off; restart the worker with it unset to roll back.

```sh
for flag in 0 1; do
    ZULIP_RUST_THUMBNAIL_PLAN=$flag ./tools/test-backend \
        zerver.tests.test_thumbnail.TestStoreThumbnail.test_worker_plan_parity \
        zerver.tests.test_thumbnail.TestStoreThumbnail.test_worker_resume_generates_only_missing_output
done
```

The retry fixture loses one of three ordinary PNG derivative metadata rows,
then verifies that Django writes only the missing variant and that a second
retry writes none. Local timings compare metadata planning only, not
storage, encoding, or worker throughput.

### Match stored thumbnail variants through Rust

Set `ZULIP_RUST_THUMBNAIL_STORED_VARIANT=1` to compare a Django-parsed
request format with already-selected stored metadata. For two or more
metadata rows, Rust reports whether the exact width, height, extension,
and animation variant is present. It validates the whole list before
returning, so a match cannot hide unsupported later metadata.

Django retains attachment access, current and legacy format policy,
Accept negotiation, on-demand generation, paths, and responses. Small
lists, older extensions, and unsupported metadata use the Python path.
The flag defaults off and is independent of the worker and preview flags.

```sh
for flag in 0 1; do
    ZULIP_RUST_THUMBNAIL_STORED_VARIANT=$flag ./tools/test-backend \
        zerver.tests.test_thumbnail.TestThumbnailRetrieval.test_native_stored_variant_probe \
        zerver.tests.test_thumbnail.TestThumbnailRetrieval.test_native_stored_variant_retrieval \
        zerver.tests.test_thumbnail.TestThumbnailRetrieval.test_get_thumbnail
done
```

The private tests compare the returned file and MIME type and keep
Django's closest-format and generation paths. Local microbenchmarks
measure metadata matching, not request or storage latency.

## Assemble user settings and preference catalogs through Rust

Set `ZULIP_RUST_SETTINGS_PAYLOAD=1` to build the personal and realm
default settings objects used by register and browser bootstrap. Rust
reads only the existing ordered Django property schema, preserves Python
values, and assembles the emoji choice and notification sound catalogs.
The sound catalog still includes only plain `.ogg` basenames, excludes
hidden and production-hashed variants, and sorts the result.

Django continues to select the user or spectator defaults and eligible
realm, read the static directory, canonicalize timezone aliases,
translate policy enums, and decide which setting changes are permitted.
The switch is off by default. An older extension or unsupported object
shape falls back to Python.

```sh
PYTHONPATH="$NATIVE_EXTENSION_DIR:$PWD" \
  python -m unittest discover -s rust-backend/tests -p test_settings_payload_parity.py
./tools/test-backend zerver.tests.test_event_system.EventsEndpointTest.test_register_separates_personal_and_realm_settings_payloads
```

## Classify, fold and merge Tornado event queues through Rust

Set `ZULIP_RUST_EVENT_QUEUE=1` to classify queued events, fold later
message IDs into an existing compatible flag event, and weave pending
virtual events into the real queue. The opt-in preserves event order,
event and list identity, timestamps, and duplicate-ID behavior for
restored queues. Unusual Python values and older wheels use the Python
implementation.

Tornado still selects recipients and queues, assigns IDs, decides which
flag events can collapse, prunes acknowledged events, and controls
delivery. Internal-data removal before a client receives an event
continues to run in Python regardless of the flag.

```sh
PYTHONPATH="$NATIVE_EXTENSION_DIR:$PWD" \
  python -m unittest discover -s rust-backend/tests -p test_event_queue_parity.py
./tools/test-backend zerver.tests.test_event_queue.EventQueueTest \
  zerver.tests.test_event_system.GetEventsTest
```

## Assemble user group memberships and responses through Rust

Set `ZULIP_RUST_GROUP_PAYLOAD=1` to bucket and sort already-selected
membership rows, and build the ordered user-group payload used by
GET user groups, register and browser bootstrap. The same membership
helper serves channel settings. It preserves empty groups, direct
membership and subgroup arrays, system names, and the distinction
between a named setting group ID and its expanded unnamed member data.

Django still decides who may list groups, which realm and deactivated
groups are included, and which membership rows to read. This is a
prerequisite for the standalone route, not an independent route.
The switch is off by default; an older extension or unsupported shape
uses the Python implementation.

```sh
PYTHONPATH="$NATIVE_EXTENSION_DIR:$PWD" \
  python -m unittest discover -s rust-backend/tests -p test_group_payload_parity.py
./tools/test-backend zerver.tests.test_user_groups.UserGroupAPITestCase.test_user_group_get
```

### Compare the standalone user-group database query locally

The HTTP feature now builds a Rust query for the list data, but the listener
does not use it. Django still authenticates and authorizes the current
endpoint. Rust loads the selected realm's named groups and their settings,
then reads membership in a second query. It retains the Django filter for
active users, uses bigint group IDs, sorts membership and group arrays, and
preserves both a named setting's ID and the inline form for unnamed settings.

Use the [owner-private PostgreSQL fixture](#rehearse-saved-snippet-read-routing-and-rollback-locally)
with fresh synthetic records. From its project directory, with the fixture's
Django settings and socket environment selected, generate the reference
using Django with all Rust bridges off:

```sh
PYTHONPATH="$PWD" "$venv/bin/python" \
  "$checkout/rust-backend/tests/seed_user_group_listing.py" --fixture-root "$fixture_root"
```

The seeder modifies only the disposable synthetic database. It stores four
Django list responses and 30 request cases from the real Basic and session
Django endpoints. These cover both realms, guest rejection before parameter
validation, the default and explicit filter, the last duplicate value, and
the normal error and ignored-parameter shapes. The reference contains
synthetic IDs and responses, not credentials or cookies. Run the Rust
comparison on that same database:

```sh
ZULIP_GROUP_FIXTURE_ROOT="$fixture_root" ISOLATED_PG_PORT="$port" \
  ISOLATED_PG_ROLE="$ISOLATED_PG_ROLE" ISOLATED_PG_SOCKET="$ISOLATED_PG_SOCKET" \
  CARGO_NET_OFFLINE=true cargo test --manifest-path "$checkout/rust-backend/Cargo.toml" \
  --locked --features http --test group_listing_postgres -- --ignored
```

The Rust test uses only the private Unix socket in a read-only transaction.
The new operation takes a principal that has already passed the existing
authentication and realm checks. It reads the role for that user in that
realm and applies the existing non-guest restriction before validation.
This lower-level check does not exercise Rust HTTP authentication. The
separate direct pilot below covers the next layer.

### Verify the direct user-group read pilot locally

The Rust listener only attaches GET/HEAD for `/api/v1/user_groups` and
`/json/user_groups` when `ZULIP_RUST_USER_GROUP_READ_PILOT=1`, with both the
database and signed Django session settings present. The switch is off by
default. The binary still binds only to loopback. The versioned proxy
templates below are not included by default; Django remains the served
fallback.

Build the HTTP binary offline and use the same owner-private database fixture
as above. Run its setup and the commands below inside a [private network
namespace with only loopback](#verify-a-browser-session-across-the-three-read-routes).
Keep the fixture's Django settings and socket environment, with no Rust
environment variables other than the test binary:

```sh
CARGO_NET_OFFLINE=true cargo build --manifest-path "$checkout/rust-backend/Cargo.toml" \
  --locked --all-features --lib --bin zulip-http
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" \
  "$checkout/rust-backend/tests/check_user_groups_direct.py" \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv"
```

The script compares 24 direct Rust responses with real Django/uWSGI via a
disposable nginx left on Django. It covers Basic and signed session reads,
authorized users in two organizations, guest/localized errors, normal
parameter errors, unknown parameters, and empty HEAD bodies. It also checks
that the default Rust listener has no group route, that the pilot cannot
start without the database/session configuration, and that OPTIONS is not
part of the direct Rust pilot. This test does not exercise switching the
proxy to Rust, service accounting for this endpoint, or rollback.

### Rehearse opt-in user-group routing and rollback locally

In the same disposable fixture, first generate the group references above.
Then run the direct comparison command with
`--with-proxy --openssl "$openssl"`. It loads the versioned
`user-groups-map.conf` and `user-groups-routes.conf` templates only into the
private proxy. They select GET/HEAD on the two exact list paths on
`zulip.testserver` (with optional port 8880). Nonmatching hosts, other
methods and group detail routes stay with Django.

Run the combined accounting check in the same isolated namespace:

```sh
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" \
  "$checkout/rust-backend/tests/check_read_proxy_activity.py" --user-groups \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv" \
  --openssl "$openssl" --redis-server "$redis_server" \
  --rabbitmq-server "$rabbitmq_server" --epmd "$epmd"
```

The private HTTPS rehearsal covers Django default, Rust opt-in, Rust
stopped, and explicitly restored Django. It compares 30 cases in each
serving state and validates matched upstreams, including the excluded
routes. There is no automatic fallback: requests still mapped to the
stopped Rust process receive 502. In the rehearsal, removing the private
route include, testing the config and reloading nginx restored Django while
Rust stayed stopped. The combined check observes one Redis charge and one
RabbitMQ activity event per normal GET/HEAD, and neither when Rust is down.

This verifies a local standalone operation, not a deployment. Production
ingress, credentials and role privileges, load, and service failure behavior
outside the tested Rust stop are not covered. Do not add the templates to
live nginx based on this rehearsal alone.

### Compare group member and subgroup reads before routing

The same reference and Rust PostgreSQL test in the
[group database section](#compare-the-standalone-user-group-database-query-locally)
also exercise two unrouted operations, the group members and group subgroups
reads. Both use the existing post-authentication non-guest check and typed
JSON boolean contract. They verify the named group belongs to the caller's
organization and is not deactivated before looking up its members or subgroups.

The references include 62 real Django Basic and session cases. The private
graph has direct members, a nested group reached by two branches, an inactive
member, and empty groups. Rust compares the same member and subgroup IDs
without assuming a wire order, since the Django comparisons treat both
arrays as unordered. Invalid query values, guest precedence, other-realm
and unnamed groups, and ordinary bot reads are covered as well.

The PostgreSQL check does not exercise the HTTP layer. The separate
direct pilot below covers transport. The single-user membership check
has an additional target-user visibility policy and is not included.

### Verify the direct user-group detail read pilot locally

The separate, default-off `ZULIP_RUST_USER_GROUP_DETAIL_READ_PILOT=1`
switch attaches only GET/HEAD for numeric
`/api/v1/user_groups/{id}/members`, `/api/v1/user_groups/{id}/subgroups`,
and their `/json/` counterparts to the loopback service. It requires
database and signed Django session settings. The list and detail pilot
flags are independent. The versioned proxy templates below are not included
by default.

From the same private namespace and synthetic database as the list pilot,
generate the reference fixtures and build the HTTP binary as above, then run:

```sh
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" \
  "$checkout/rust-backend/tests/check_user_group_details_direct.py" \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv"
```

The check compares 98 direct responses with Django/uWSGI, including
16 empty HEAD responses. It covers the two authenticated surfaces,
standard group and parameter errors, French error messages, ordinary
numeric IDs including leading zeroes and a missing ID beyond bigint, and
the separate startup and opt-in guards. The comparison sorts ID arrays
whose existing Django tests treat order as unspecified. Other methods
still go to Django in the proxy; the direct pilot offers only GET/HEAD.

The direct check alone does not exercise the proxy, request accounting,
or rollback. The separate rehearsal below covers those layers.

### Rehearse opt-in group detail routing and rollback locally

After preparing the same disposable fixture, add
`--with-proxy --openssl "$openssl"` to the direct detail command. In this
mode, only the private proxy loads the versioned
`user-group-details-map.conf` and `user-group-details-routes.conf`. They select
GET/HEAD for the numeric member and subgroup list paths on
`zulip.testserver` (with optional port 8880). Other hosts, OPTIONS, POST and
the single-user membership endpoint are not forwarded to Rust.

The exact method map is also checked separately in a diagnostic nginx that
returns 204 without any upstream. No POST is sent to Django or Rust. The
private TLS parity check compares 66 cases per serving state. All eight
mapped GET/HEAD outage checks receive the expected 502 with Rust stopped.
Removing the private route include, checking the config and reloading nginx
restores Django while Rust remains stopped. There is no automatic fallback.

Run the combined accounting check in the same isolated namespace:

```sh
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" \
  "$checkout/rust-backend/tests/check_read_proxy_activity.py" --user-group-details \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv" \
  --openssl "$openssl" --redis-server "$redis_server" \
  --rabbitmq-server "$rabbitmq_server" --epmd "$epmd"
```

For 24 normal reads across Django, Rust and restored Django, it verifies one
Redis charge and one RabbitMQ activity event per request. The two Rust-down
requests in that check have neither side effect. Production ingress,
credentials, application privileges, load and other failure modes are not
covered. No host or default configuration is changed.

### Compare the unrouted self-profile projection locally

The self-profile projection reads only the active account from the trusted
authenticated user and organization IDs. It keeps the account role and bot
fields, timestamp precision, own delivery email, and typed custom profile
values. The newest message ID comes from that account's user-message rows,
or is `-1` when there are none. The projection cannot select another target
user. Django still supplies the resolved avatar URL and trusted bot registry.
Rust classifies the avatar source and bot flag, reads aliases from the same
trusted tzdata source, and rejects an avatar resolved for another account,
organization, version or source. It does not perform storage lookups,
compute salted avatar paths or load deployment credentials.

With the private PostgreSQL fixture and Rust bridges disabled, generate
the synthetic references from the fixture's project directory and compare:

```sh
PYTHONPATH="$PWD" "$venv/bin/python" \
  "$checkout/rust-backend/tests/seed_own_profile.py" --fixture-root "$fixture_root"
ZULIP_OWN_PROFILE_FIXTURE_ROOT="$fixture_root" ISOLATED_PG_PORT="$port" \
  ISOLATED_PG_ROLE="$ISOLATED_PG_ROLE" ISOLATED_PG_SOCKET="$ISOLATED_PG_SOCKET" \
  CARGO_NET_OFFLINE=true cargo test --manifest-path "$checkout/rust-backend/Cargo.toml" \
  --locked --features http --test own_profile_postgres -- --ignored
```

The Rust test uses a read-only transaction and compares 11 actual Django
Basic/session responses including ordinary owners, admins, members, guests,
bots and a separate organization. It also verifies that mismatched
organization IDs do not return rows, and rejects mismatched avatar
presentation. The test also checks the entire alias map from the installed
tzdata source and 12 Django avatar and system-bot decisions without login
or storage calls. No HTTP adapter was added. Django still handles the route
and must remain the trusted avatar URL provider before an adapter can opt in.

### Format authenticated self-profile responses through Rust

With a locally built Python extension, `ZULIP_RUST_OWN_PROFILE_PAYLOAD=1`
selects the same Rust response composer inside Django for `GET /users/me`.
It is off by default. Authentication, the account snapshot, database
connection, time zone lookup and avatar URL stay in Django; Rust does not
obtain an avatar salt or call storage. The existing Django formatter is
used if the extension or function is unavailable or the helper does not
support the supplied values. No Rust listener or proxy route is added.

After generating the disposable PostgreSQL references above, expose the
matching locally built Python extension on the fixture's Python path and run:

```sh
PYTHONPATH="$native_extension_dir:$PWD" "$venv/bin/python" \
  "$checkout/rust-backend/tests/check_own_profile_bridge.py" --fixture-root "$fixture_root"
```

This compares 11 real Django Basic/session GETs with the flag off, with the
native formatter selected, and with a forced fallback to Django. It asserts
that enabled requests cannot silently pass by using the old formatter.

### Rehearse one user's group membership read and rollback

`ZULIP_RUST_USER_GROUP_MEMBERSHIP_READ_PILOT=1` registers GET and HEAD for
`/api/v1/user_groups/{user_group_id}/members/{user_id}` and its `/json/`
alias on the loopback listener only. This option is off by default and
requires the existing database and signed Django session configuration.
It does not enable or depend on the separate list or group detail pilots.

Django's non-guest restriction still precedes boolean parameter validation.
The named group is checked before the active target user in the same
organization. The output is a single direct or recursive membership
boolean; Rust does not load other users' profiles. Bots can be targets or
API actors under the existing policy, and HEAD has no body.

After preparing the disposable group fixture above, run these two checks
in the same private loopback-only namespace:

```sh
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" \
  "$checkout/rust-backend/tests/check_user_group_membership.py" \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv" \
  --openssl "$openssl"

ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" \
  "$checkout/rust-backend/tests/check_read_proxy_activity.py" --user-group-membership \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv" \
  --openssl "$openssl" --redis-server "$redis_server" \
  --rabbitmq-server "$rabbitmq_server" --epmd "$epmd"
```

The versioned staging map selects only the two numeric paths and the
synthetic host `zulip.testserver` (optional port 8880). OPTIONS and other
hosts stay in Django. A separate nginx with no upstream verifies the map
excludes POST. If Rust stops, selected requests return 502; removing the
route include and reloading nginx restores Django even while Rust is down.
There is no automatic failover. These tests do not validate production
ingress, privileges or load. Neither templates nor the listener change
the deployment default.

### Rehearse channel ID lookup and rollback

`ZULIP_RUST_CHANNEL_ID_READ_PILOT=1` adds the loopback-only GET and HEAD
for `/api/v1/get_stream_id` and `/json/get_stream_id` with a configured
database and signed Django sessions. Django remains the default. The lookup
uses PostgreSQL's case-insensitive name comparison and the same realm,
active-channel and content-access checks as the Django view. A channel
administrator without content access still cannot look up a private channel.

Use the same disposable group fixture above. Run the checks inside its
private loopback namespace:

```sh
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" "$checkout/rust-backend/tests/check_channel_id.py" \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv" \
  --openssl "$openssl"
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" \
  "$checkout/rust-backend/tests/check_read_proxy_activity.py" --channel-id \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv" \
  --openssl "$openssl" --redis-server "$redis_server" \
  --rabbitmq-server "$rabbitmq_server" --epmd "$epmd"
```

The staging map selects exactly these two read paths on the synthetic host.
OPTIONS and other hosts stay in Django; POST is checked in a separate nginx
with no upstream. If Rust stops, the selected requests return 502 until the
route include is removed and nginx is reloaded. There is no automatic
failover. Production routing, ingress and load are not covered by this test.

### Rehearse authenticated API topic history and rollback

Only the loopback `GET` and `HEAD` for
`/api/v1/users/me/{stream_id}/topics` are eligible, with Basic Auth and
existing channel-content access. The browser `/json/` alias, including
anonymous web-public access, remains in Django. Public history is grouped
over channel messages, while restricted history uses only the caller's
user-message rows. Archived channels are readable when the caller has the
same content access Django requires.

Generate a runtime lowercase table with the Python interpreter used by
the corresponding Django deployment. Regenerate it if that interpreter's
Unicode version changes. From the provisioned checkout:

```sh
./manage.py shell -c 'from pathlib import Path; from zerver.management.commands.export_rust_alert_word_cache import lowercase_tables, publish_json; publish_json(Path("var/rust-channel-topics-lowercase.json"), lowercase_tables())'
export ZULIP_RUST_CHANNEL_TOPICS_LOWERCASE_FILE="$PWD/var/rust-channel-topics-lowercase.json"
```

This setting registers the direct read with a configured database. Keep it
unset to leave the direct route disabled. The separate nginx opt-in uses
`channel-topics-map.conf` and `channel-topics-routes.conf` in
`rust-backend/staging/nginx/`; neither is installed by default.

Use the same private disposable fixture with `WEB_PUBLIC_STREAMS_ENABLED = True`
in its test-only Django settings to exercise the anonymous control.
The first check seeds synthetic topic history in the fixture; the second
uses its public test channel. Run them in the private loopback namespace:

```sh
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" "$checkout/rust-backend/tests/check_channel_topics.py" \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv" \
  --openssl "$openssl"
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" \
  "$checkout/rust-backend/tests/check_read_proxy_activity.py" --channel-topics \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv" \
  --openssl "$openssl" --redis-server "$redis_server" \
  --rabbitmq-server "$rabbitmq_server" --epmd "$epmd"
```

Removing the route include and reloading nginx restores Django while Rust
is offline. There is no automatic failover. Production ingress and load
were not tested; deployment routing remains unchanged.

### Format visible topic history inside Django

Set `ZULIP_RUST_TOPIC_HISTORY=1` to reuse the Rust topic-history
formatter after Django admits the request and runs its existing query.
Basic, signed web sessions, and web-public spectators still use their
existing Django authorization, site and organization settings, and
channel-history rules. Only the visible topics and their newest message
IDs reach the formatter.

The shared formatter keeps the newest spelling of each case-insensitive
topic and substitutes the legacy empty-topic label only after grouping.
This preserves a distinct literal "general chat" topic. Non-ASCII
casing uses the running Python interpreter. Unsupported row types or
non-unique or non-descending IDs, older wheels, and an unset flag retain
the original Python path. The option is off by default and does not
route the JSON alias to the independent listener.

With the extension on `PYTHONPATH` and a disposable Django test
database:

```sh
for enabled in 0 1; do
    ZULIP_RUST_TOPIC_HISTORY=$enabled ./tools/test-backend \
        zerver.tests.test_message_topics.TopicHistoryTest \
        zerver.tests.test_topic_history_rust.TopicHistoryRustTest
done
```

For 256 synthetic rows already read from the database, the formatting
stage including FFI was about 1.5–1.6 times faster. SQL, authentication,
and end-to-end HTTP were not benchmarked. Independent coverage is
unchanged.

### Rehearse topic renames with the topic-history pilot

Use the [same private topic fixture](#rehearse-authenticated-api-topic-history-and-rollback),
adding `--workflow` to the call to `check_channel_topics.py`. The harness
creates a fresh synthetic channel and posts three messages through Django.

A Django session renames a two-message topic while Rust serves the opt-in
Basic API history. Both list surfaces must show the committed name, message
anchor, and order, while a separate topic remains unchanged. HEAD stays
empty. The browser's history and write requests stay on Django.

While Rust is stopped, the opted-in API reads return 502, but the session
can still rename the topic and read the new state. Removing the private
nginx include restores that same history to the API without restarting
Rust. This does not change route configuration, defaults, or independent
HTTP coverage.

### Convert emoji code points through Rust

Set `ZULIP_RUST_EMOJI_CODEPOINTS=1` to remove the emoji presentation
selector and convert already selected emoji text to or from its
hexadecimal code points in Rust. Markdown, notification preview text,
and the standalone emoji build helper keep their existing behavior.
Missing methods and values the extension declines, including legacy
numeric spellings and lone surrogates, use the original Python helpers.

The flag defaults to off. Unset it and restart the worker to return to
the Python helpers. With a locally built extension, test both modes:

```bash
ZULIP_RUST_EMOJI_CODEPOINTS=0 ./tools/test-backend zerver.tests.test_emoji_codepoints_rust
ZULIP_RUST_EMOJI_CODEPOINTS=1 ./tools/test-backend zerver.tests.test_emoji_codepoints_rust
```

### Normalize decoded import message bodies through Rust

Set `ZULIP_RUST_IMPORT_BODY=1` before starting a Django import to remove
NUL characters from already decoded message text and apply the existing
Unicode code point cutoff. The helper preserves whitespace and Python
slice behavior when the limit is shorter than the truncation suffix.
Import parsing, topic names, rendered content, storage, and normal
message sending are unchanged.

The flag defaults to off. An absent or older extension and values the
helper declines use the original Python implementation. With a locally
built extension, test both modes without importing any real records:

```bash
for enabled in 0 1; do
    ZULIP_RUST_IMPORT_BODY=$enabled ./tools/test-backend zerver.tests.test_import_body_rust
done
```

### Rank eligible digest topics through Rust

Set `ZULIP_RUST_DIGEST_RANK=1` before starting Django to rank topics already
selected for a digest. Rust receives only ordered identity labels and two
numeric scores. It returns positions in the original list, preserving
ties and repeats in the diversity pass and the existing four-candidate
limit on the length pass. Django still selects eligible topics and
handles rendering and delivery.

The switch defaults to off. An absent or older extension, a different
limit, or unsupported ranking inputs leave both passes on Python. In
an isolated environment with the current extension built, test both
modes without sending email:

```bash
for enabled in 0 1; do
    ZULIP_RUST_DIGEST_RANK=$enabled ./tools/test-backend zerver.tests.test_digest_rank_rust
done
```

### Format selected topic title text through Rust

Set `ZULIP_RUST_TOPIC_TEXT=1` before starting Django to apply the existing
topic code point cutoff and recognize the literal resolution marker
(`✔ `) through Rust. The helper removes at most one marker, preserves
the original string when unchanged, and does not select or rename topics.

The switch defaults to off. Each helper falls back to Python if the
extension is absent, its method is missing, the Python punctuation
differs, or the input is not supported. With the current extension built,
test both modes in an isolated Django environment:

```bash
for enabled in 0 1; do
    ZULIP_RUST_TOPIC_TEXT=$enabled ./tools/test-backend zerver.tests.test_topic_text_rust
done
```

### Size and clip prepared quote-and-reply text through Rust

Set `ZULIP_RUST_COMPOSE_QUOTE=1` before starting Django to allocate the
existing quote and reply length budget and clip the quote at the existing
Unicode code point limit. If clipping leaves fences open, the retry loop
calls the existing Python fence closer. The parser, translated templates,
quote context, links, and delivery remain in Python.

The switch defaults to off. Missing extension methods, unsupported
inputs, or a different Python minimum, suffix, or fence closer retain
the corresponding Python path. Test both modes using the current
extension and synthetic composition inputs:

```bash
for enabled in 0 1; do
    ZULIP_RUST_COMPOSE_QUOTE=$enabled ./tools/test-backend \
        zerver.tests.test_compose_quote_rust zerver.tests.test_compose_reply
done
```

### Decorate prepared email plaintext through Rust

Set `ZULIP_RUST_EMAIL_PLAIN_PREFIX=1` before starting Django to add quote
prefixes and a sender header to already processed notification plaintext.
Only LF splits quoted lines. Empty and trailing lines keep their prefix,
and CR characters are left in place. Python still controls spoiler
concealment, links, HTML, sender and recipient selection, and delivery.

The switch defaults to off. Missing extension methods and unsupported
strings fall back independently. Test both modes without sending mail:

```bash
for enabled in 0 1; do
    ZULIP_RUST_EMAIL_PLAIN_PREFIX=$enabled ./tools/test-backend zerver.tests.test_email_plain_prefix_rust
done
```

### Format prepared onboarding text through Rust

Set `ZULIP_RUST_ONBOARDING_TEXT=1` before starting Django to apply the
existing single-newline cleanup and format a custom welcome quote from
its translated introduction, chosen fence, and body. Blank lines and
list boundaries are preserved. Django still selects mentions, the
translation and fence, and handles all onboarding actions and delivery.

The switch defaults to off. Missing extension methods or unsupported
strings fall back independently. Test both modes without sending messages:

```bash
for enabled in 0 1; do
    ZULIP_RUST_ONBOARDING_TEXT=$enabled ./tools/test-backend zerver.tests.test_onboarding_text_rust
done
```

### Project a prepared private unread arrival through Rust

Set `ZULIP_RUST_PRIVATE_UNREAD_ARRIVAL=1` before starting Django to project
the display roster of an already selected private message to a direct
or group unread record. Rust receives only the account ID and roster IDs.
Group records keep sorted IDs and duplicates. Django still writes the
unread records and handles mentions, channels, visibility and muting.

This switch is off by default and independent of unread replay and
payload formatting. An absent or older extension or unsupported roster
keeps the Python projection. In isolated Django, test both modes:

```bash
for enabled in 0 1; do
    ZULIP_RUST_PRIVATE_UNREAD_ARRIVAL=$enabled ./tools/test-backend zerver.tests.test_unread_replay_rust
done
```

### Plan display text for prepared silent mentions through Rust

Set `ZULIP_RUST_DISPLAY_MENTIONS=1` before starting Django to plan the
order and comma joining of already constructed silent mention strings.
The presorted caller keeps its supplied order. Python still selects
users, builds mention syntax, and renders the locale's joining template.

The switch defaults to off. An absent or older extension, an empty list,
or unsupported text retains the Python path. Test both modes without
sending messages:

```bash
for enabled in 0 1; do
    ZULIP_RUST_DISPLAY_MENTIONS=$enabled ./tools/test-backend zerver.tests.test_display_mentions_rust
done
```

### Extract selected poll and todo text through Rust

Set `ZULIP_RUST_WIDGET_TEXT=1` before starting Django to extract titles
and ordered rows from already selected poll and todo plaintext.
Python still selects the widget type, creates and serializes its
dataclasses, validates inputs, and handles persistence and delivery.

The switch defaults to off. A missing extension or symbol, a string
subclass, or a string with lone surrogates keeps the original Python
parser. Test both modes without sending messages or saving widgets:

```bash
for enabled in 0 1; do
    ZULIP_RUST_WIDGET_TEXT=$enabled ./tools/test-backend zerver.tests.test_widget_text_rust
done
```

### Read prepared language catalog metadata through Rust

Set `ZULIP_RUST_LOCALE_CATALOG=1` before starting Django to look up a
display name or collect available language codes from an already loaded
catalog. Python still loads the catalog, applies the availability
threshold, logs missing names, and owns request language selection.

The switch defaults to off. A missing extension or symbol, or an
unsupported input, keeps the original Python lookup or projection.
Test both modes in an isolated environment:

```bash
for enabled in 0 1; do
    ZULIP_RUST_LOCALE_CATALOG=$enabled ./tools/test-backend zerver.tests.test_locale_catalog_rust
done
```

### Format prepared analytics client chart series through Rust

Set `ZULIP_RUST_ANALYTICS_CLIENT_SERIES=1` before starting Django to
rename and sum already computed client chart arrays. The helper keeps
first-seen order and copies arrays before returning them. Python still
selects the statistic, queries values, and controls visibility.

The switch defaults to off. An absent or older extension, unsupported
input, or a sum outside Rust's integer range uses the original Python
implementation. Test both modes with synthetic chart arrays:

```bash
for enabled in 0 1; do
    ZULIP_RUST_ANALYTICS_CLIENT_SERIES=$enabled ./tools/test-backend analytics.tests.test_client_series_rust
done
```

### Order prepared analytics chart labels through Rust

Set `ZULIP_RUST_ANALYTICS_CHART_ORDER=1` before starting Django to
order labels by totals or combine realm and user label rankings.
Rust receives only already computed series and returns their existing
label objects. Queries, chart selection and client aliases stay
unchanged. This switch is independent of the client series switch.

The switch defaults to off. Missing extension methods or unsupported
inputs keep Python's original ordering and error behavior. Test both
modes with synthetic arrays:

```bash
for enabled in 0 1; do
    ZULIP_RUST_ANALYTICS_CHART_ORDER=$enabled ./tools/test-backend analytics.tests.test_chart_order_rust
done
```

### Parse supplied timezone alias text through Rust

Set `ZULIP_RUST_TIMEZONE_ALIASES=1` before starting Django to parse
each already read alias line and look up an alias in the cached map.
Python still opens the timezone data, preserves stream and cache
behavior, and controls all timezone settings and conversions.

The switch defaults to off. Missing extension methods or unsupported
input keep Python's original parsing or lookup. Exercise both modes
with supplied synthetic lines, without changing timezone data:

```bash
for enabled in 0 1; do
    ZULIP_RUST_TIMEZONE_ALIASES=$enabled ./tools/test-backend zerver.tests.test_timezone_aliases_rust
done
```

### Normalize whitespace in extracted description text through Rust

Set `ZULIP_RUST_DESCRIPTION_TEXT=1` before starting Django to collapse
whitespace in text Python has already extracted for a description.
Python still parses HTML, selects and separates elements, enforces the
existing length cutoff, and escapes the result.

The switch defaults to off. A missing extension or symbol, or an
unsupported string, keeps Python's original split and join. Test both
modes using synthetic descriptions:

```bash
for enabled in 0 1; do
    ZULIP_RUST_DESCRIPTION_TEXT=$enabled ./tools/test-backend zerver.tests.test_description_text_rust
done
```

### Plan prepared analytics chart time buckets through Rust

Set `ZULIP_RUST_ANALYTICS_TIME_BUCKETS=1` before starting Django to
plan hourly or daily buckets from already selected built-in UTC dates
in chart reads. Python retains range selection, datetime creation and
fallbacks. The shared Python generator and database population command
are unchanged.

The switch defaults to off. A missing extension method, unsupported
input, or overflow keeps Python's original time range and exceptions.
Test both modes with synthetic boundaries:

```bash
for enabled in 0 1; do
    ZULIP_RUST_ANALYTICS_TIME_BUCKETS=$enabled ./tools/test-backend analytics.tests.test_time_buckets_rust
done
```

### Join prepared Slack import text pieces through Rust

Set `ZULIP_RUST_SLACK_IMPORT_PIECES=1` before starting Django to trim,
drop empty pieces and join already formatted Slack import text. One or
two newlines are preserved, as chosen by the Python caller. Parsing,
block selection and markup formatting stay in Python.

The switch defaults to off. A missing native method or unsupported
input keeps the same Python strip and join. Test both modes without
importing records:

```bash
for enabled in 0 1; do
    ZULIP_RUST_SLACK_IMPORT_PIECES=$enabled ./tools/test-backend zerver.tests.test_slack_import_pieces_rust
done
```

### Format prepared plaintext digest lists through Rust

Set `ZULIP_RUST_PLAIN_DISPLAY_LIST=1` before starting Django to format
already selected plain names in the plaintext digest. The HTML
template and its existing filter stay unchanged.

The switch defaults to off. A missing method or unsupported input,
including markup and string subclasses, uses the existing Python
formatter. Test synthetic rendering without sending email:

```bash
for enabled in 0 1; do
    ZULIP_RUST_PLAIN_DISPLAY_LIST=$enabled ./tools/test-backend zerver.tests.test_plain_display_list_rust
done
```

### Format a prepared request duration through Rust

Set `ZULIP_RUST_DURATION_TEXT=1` before starting Django to format an
already computed duration with the existing seconds or milliseconds
suffix. Clock sampling, logger destinations and slow-request decisions
stay in Python.

The switch defaults to off. A missing method or an unsupported value,
including nonfinite or very large durations, uses the original Python
formatter. Test both modes with synthetic durations:

```bash
for enabled in 0 1; do
    ZULIP_RUST_DURATION_TEXT=$enabled ./tools/test-backend zerver.tests.test_duration_text_rust
done
```

### Format prepared mobile push text through Rust

Set `ZULIP_RUST_PUSH_TEXT=1` on a Django worker to prefix extracted
quote lines and apply the existing 200-code-point plaintext cutoff in
Rust. Django still extracts HTML text, selects language and recipients,
and owns the APNs/GCM payloads, encryption, and delivery. Either step
falls back individually to Python if the extension declines or does
not support it.

The flag defaults to off. Unset it and restart the worker to restore
the Python text helpers. To check both flags without sending pushes:

```bash
ZULIP_RUST_PUSH_TEXT=0 ./tools/test-backend zerver.tests.test_push_text_rust
ZULIP_RUST_PUSH_TEXT=1 ./tools/test-backend zerver.tests.test_push_text_rust
```

### Rehearse authenticated user status reads and rollback

`ZULIP_RUST_USER_STATUS_READ_PILOT=1` adds loopback-only GET and HEAD
for `/api/v1/users/{user_id}/status` and `/json/users/{user_id}/status`
with a configured database and signed Django sessions. Django stays the
default. The read uses the same realm, human-target and active-account
checks as Django. It preserves the Everyone/Members visibility setting
and reuses the shared-recipient check and Rust status formatter. No
status fields are returned for a target outside the caller's visibility.

Use a fresh disposable synthetic fixture in the private loopback
namespace. The first command seeds the ordinary status and recipient
cases; the second reuses the synthetic identities for accounting:

```sh
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" "$checkout/rust-backend/tests/check_user_status_read.py" \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv" \
  --openssl "$openssl"
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" \
  "$checkout/rust-backend/tests/check_read_proxy_activity.py" --user-status \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv" \
  --openssl "$openssl" --redis-server "$redis_server" \
  --rabbitmq-server "$rabbitmq_server" --epmd "$epmd"
```

The separate staging map and route include select only these two numeric
read paths on the test host. Status updates and OPTIONS stay in Django;
the POST selection check runs without forwarding to either application.
If Rust stops, remove the route include and reload nginx to restore
Django. There is no automatic failover. Live ingress and production load
were not tested and deployment routing is unchanged.

### Batch visible statuses for registration

With the existing default-off `ZULIP_RUST_USER_STATUS=1`, initial
registration can format its visible user statuses in one Rust call.
Django applies the same organization, active-account and audience
filters first. Rust reads only the selected user IDs, presence-enabled
booleans and status fields. The single-user GET and status updates do
not change.

An older extension still uses the established single-row formatter.
A declined batch formats the same rows one at a time without rerunning
the authorization or database query. The unset flag still uses Python.

In a disposable Django database with the extension on `PYTHONPATH`:

```sh
for enabled in 0 1; do
    ZULIP_RUST_USER_STATUS=$enabled ./tools/test-backend \
        zerver.tests.test_user_status.UserStatusTest \
        zerver.tests.test_user_status_batch_rust.UserStatusBatchRustTest
done
```

For 256 already-selected synthetic rows, status formatting through
the Rust batch was about 1.8–2.6 times faster than calling the existing
Rust formatter for each row, and about 1.1–1.3 times faster than the
Python formatter. These are formatting measurements, not database or
whole registration timings. Independent HTTP coverage does not change.

### Replay selected user status events through Rust

Set `ZULIP_RUST_USER_STATUS_REPLAY=1` on a Django worker to replay
received status edits during registration through the Rust row reducer.
Django still selects the status row and owns the outer map. Visibility,
database queries, event ordering, and persistent writes are unchanged.
An older extension or a declined row uses the existing Python replay.

The flag defaults to off and is separate from status reads and writes.
Unset it and restart the worker to restore Python replay. With the
locally built extension, run both flag states in an isolated database:

```bash
ZULIP_RUST_USER_STATUS_REPLAY=0 ./tools/test-backend zerver.tests.test_user_status_batch_rust
ZULIP_RUST_USER_STATUS_REPLAY=1 ./tools/test-backend zerver.tests.test_user_status_batch_rust
```

### Persist an existing user status through Rust

The separate, default-off `ZULIP_RUST_USER_STATUS_PERSISTENCE=1` option
writes an existing status with one parameterized PostgreSQL UPDATE
on Django's transaction connection. Django retains authentication,
target selection, emoji and presence settings, timestamps, and
post-commit event recipients. An omitted field is not written;
explicit empty text and clearing an emoji still work.

Django still creates a first status and handles creation races.
An older extension, unsupported values or a model save hook uses
the original ORM path. SQL errors propagate without an ORM retry.
The Rust formatter and independent read routes do not change.

In a disposable Django database with the extension on `PYTHONPATH`:

```sh
for enabled in 0 1; do
    ZULIP_RUST_USER_STATUS_PERSISTENCE=$enabled ./tools/test-backend \
        zerver.tests.test_user_status.UserStatusTest \
        zerver.tests.test_user_status_persistence_rust.UserStatusPersistenceRustTest
done
```

For an existing synthetic status the update used one status-table SQL
statement rather than the ORM's locking SELECT and UPDATE. Initial
creation can do an extra no-match UPDATE before the unchanged ORM
creation. A separate local two-connection check preserved concurrent
text and emoji edits for first and existing statuses in both modes.
Statement counts are not HTTP latency measurements. Independent coverage
does not change.

### Rehearse status writes, reads, and independent rollback

This private rehearsal combines the two separate opt-ins without
changing either default. It compares a Django-only cycle with Django
Basic and signed-session writes using native persistence and Rust
GET/HEAD reads through the checked-in nginx map. The same synthetic user
sets text and an emoji, clears the emoji, and then clears the text.
A guest without access cannot read the status or receive its notice.
The private RabbitMQ queue must receive exactly one post-commit notice
per successful write, with the Django-selected recipients and fields.

With the Rust listener stopped, reads return 502 and the signed-session
Django writer can still commit. Removing the read-only nginx include
and reloading restores reads of that exact saved status. A separate
private Django restart without native persistence then reads the same
row and writes through the ORM, using the same session fixture. This
does not test Tornado's delivery to a browser or a live rollout.

From a fresh disposable Django fixture in its loopback-only namespace
with one Tornado queue, use the freshly built Rust listener and copy
the matching extension into the fixture as `_zulip_backend.so`:

```sh
ZULIP_RUST_HTTP_BIN=/absolute/path/to/zulip-http \
    ./.venv/bin/python rust-backend/tests/check_user_status_workflow.py \
    --fixture-root /absolute/path/to/disposable-fixture \
    --native /absolute/path/to/disposable-fixture/native/_zulip_backend.so \
    --nginx /usr/sbin/nginx --uwsgi /absolute/path/to/uwsgi \
    --venv /absolute/path/to/venv --openssl /usr/bin/openssl \
    --rabbitmq-server /usr/lib/rabbitmq/bin/rabbitmq-server --epmd /usr/bin/epmd
```

### Persist channel folder reordering through Rust

The opt-in, default-off `ZULIP_RUST_CHANNEL_FOLDER_ORDER=1` writes a
validated multi-folder order in one PostgreSQL statement on Django's
transaction connection. Django still checks administrator access and
the submitted order, selects only that organization's folders, and
publishes the original reorder event after commit. Archived folders
still receive their positions; duplicate IDs keep their last position,
and obsolete extra IDs do not write rows.

With the extension disabled or older, an empty or single-folder
organization, a different database/router, or a model save hook,
Django keeps its existing row-by-row behavior. If an attempted native
batch misses any previously selected row, it rolls back and is not
retried through the ORM. The HTTP routes and defaults do not change.

```sh
for enabled in 0 1; do
    ZULIP_RUST_CHANNEL_FOLDER_ORDER=$enabled ./tools/test-backend \
        zerver.tests.test_channel_folders.ReorderChannelFolderTest \
        zerver.tests.test_channel_folder_order_rust.ChannelFolderOrderRustTest \
        zerver.tests.test_events.ChannelFolderActionTest
done
```

In a synthetic eight-folder organization, both the Basic and session
reorders used one folder-table UPDATE instead of eight. The visible and
archived-inclusive lists, event audience and payload, and ORM fallback
matched. This is a statement count, not an HTTP latency measurement.
Independent HTTP coverage does not change.

### Rehearse a single channel subscription read and rollback

`ZULIP_RUST_SUBSCRIPTION_STATUS_READ_PILOT=1` enables only the loopback
GET and HEAD for `/api/v1/users/{user_id}/subscriptions/{stream_id}` and
its `/json/` alias, with a database and signed Django sessions. Django
remains the default. The target user must be active and visible to the
caller; bot targets are supported just as in Django. Only after the
target check does the route check active-channel metadata access,
including administrator and configured group access. It returns one
boolean, not a member list. Subscription changes are not routed to Rust.

From a fresh disposable fixture in its private loopback namespace:

```sh
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" "$checkout/rust-backend/tests/check_subscription_status.py" \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv" \
  --openssl "$openssl"
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" \
  "$checkout/rust-backend/tests/check_read_proxy_activity.py" --subscription-status \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv" \
  --openssl "$openssl" --redis-server "$redis_server" \
  --rabbitmq-server "$rabbitmq_server" --epmd "$epmd"
```

The first check prepares the synthetic users, channels, and subscriptions.
The second checks normal quota and activity for the same fixture.
The separate staging map and include match only the numeric read paths
on the test host. If Rust is stopped, removing the route include and
reloading nginx restores Django. There is no automatic failover. Neither
live ingress nor production load was tested.

### Rehearse subscribed-channel IDs and rollback

`ZULIP_RUST_SUBSCRIBED_CHANNELS_READ_PILOT=1` enables only loopback GET
and HEAD for `/api/v1/users/{user_id}/channels` and its `/json/` alias.
It requires a database and signed Django sessions. Django remains the
default. Rust applies the same target and channel metadata visibility
as the single-subscription read, but preserves archived channels when
their subscriptions are still active, as Django's list does. It returns
channel IDs only, in increasing order; OpenAPI does not prescribe order.

From a fresh disposable fixture in its private loopback namespace:

```sh
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" "$checkout/rust-backend/tests/check_subscribed_channels.py" \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv" \
  --openssl "$openssl"
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" \
  "$checkout/rust-backend/tests/check_read_proxy_activity.py" --subscribed-channels \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv" \
  --openssl "$openssl" --redis-server "$redis_server" \
  --rabbitmq-server "$rabbitmq_server" --epmd "$epmd"
```

The staging map and route include select only these numeric GET and HEAD
paths on the test host. Requests fail with 502 if Rust stops rather than
falling back automatically. Remove the route include and reload nginx to
restore Django. Deployment routing, live ingress and production load
were not changed or tested.

### Rehearse authenticated channel rosters and rollback

`ZULIP_RUST_CHANNEL_MEMBERS_READ_PILOT=1` enables only loopback GET and HEAD
for `/api/v1/streams/{stream_id}/members` and its `/json/` alias.
It requires a database and signed Django sessions. The caller must first
have channel metadata access and then pass Django's separate subscriber
check. Archived channels are supported, and both subscriptions and their
users must be active. The result contains only user IDs in increasing
order; OpenAPI does not prescribe order. Django remains the default.

From a fresh disposable fixture in its private loopback namespace:

```sh
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" "$checkout/rust-backend/tests/check_channel_members.py" \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv" \
  --openssl "$openssl"
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" \
  "$checkout/rust-backend/tests/check_read_proxy_activity.py" --channel-members \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv" \
  --openssl "$openssl" --redis-server "$redis_server" \
  --rabbitmq-server "$rabbitmq_server" --epmd "$epmd"
```

The separate staging map selects the numeric read paths only. Removing
the route include and reloading nginx restores Django while Rust is
stopped. There is no automatic failover, anonymous access, or change
to deployment routing. Live ingress and production load were not tested.

### Rehearse authenticated channel details and rollback

`ZULIP_RUST_CHANNEL_DETAILS_READ_PILOT=1` adds loopback-only GET and HEAD
for `/api/v1/streams/{stream_id}` and its `/json/` alias, with a database
and signed Django sessions. Django remains the default. The read keeps
the existing channel metadata permissions, including for archived channels.
It selects the documented projection rather than the full model. Anonymous
group settings, icon URLs, legacy posting and topic policy, and 28-day
traffic use the existing Django response contract.

From a fresh disposable fixture in its private loopback namespace:

```sh
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" "$checkout/rust-backend/tests/check_channel_details.py" \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv" \
  --openssl "$openssl"
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" \
  "$checkout/rust-backend/tests/check_read_proxy_activity.py" --channel-details \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv" \
  --openssl "$openssl" --redis-server "$redis_server" \
  --rabbitmq-server "$rabbitmq_server" --epmd "$epmd"
```

PATCH and DELETE remain on Django, as do the separate picture and email
address routes. No write requests are forwarded by the diagnostic map
check. Removing the route include and reloading nginx restores Django
while Rust is offline; there is no automatic failover. Live ingress,
production load and deployment routing are not part of this pilot.

### Rehearse authenticated message read receipts and rollback

`ZULIP_RUST_READ_RECEIPTS_READ_PILOT=1` adds loopback-only GET and HEAD
for `/api/v1/messages/{message_id}/read_receipts` and its `/json/` alias
using the existing Basic and signed Django session authentication. Django
remains the default. Rust reads the message access rule, organization's live
read-receipt setting, and each reader's sharing setting in one database
statement. It checks the applicable channel or direct-message access before
returning any IDs. The sender, inactive users, and users muted in either
direction remain excluded. OpenAPI does not prescribe an order for the IDs.

From a fresh disposable fixture in its private loopback namespace:

```sh
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" "$checkout/rust-backend/tests/check_read_receipts.py" \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv" \
  --openssl "$openssl"
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" \
  "$checkout/rust-backend/tests/check_read_proxy_activity.py" --read-receipts \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv" \
  --openssl "$openssl" --redis-server "$redis_server" \
  --rabbitmq-server "$rabbitmq_server" --epmd "$epmd"
```

The exact host/path/method map leaves writes and other message
routes on Django. Removing the read-receipts include and reloading nginx
restores Django while Rust is stopped. There is no automatic failover.
Live ingress, production load and deployment routing were not tested.

### Rehearse authenticated channel listings and rollback

`ZULIP_RUST_CHANNEL_LIST_READ_PILOT=1` adds loopback-only GET and HEAD for
`/api/v1/streams` and its `/json/streams` alias. The existing channel
projection is shared with the detail read. The list keeps the Django
visibility rules for metadata, content and active subscriptions, including
archive filtering and the default-channel marker. The owner-subscription
option reads the authenticated bot's recorded owner only. If an old bot
has no owner, that option returns the same generic error as Django and
never returns a partial list. The all-metadata options still take priority.

From a fresh disposable fixture in its private loopback namespace:

```sh
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" "$checkout/rust-backend/tests/check_channel_list.py" \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv" \
  --openssl "$openssl"
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" \
  "$checkout/rust-backend/tests/check_read_proxy_activity.py" --channel-list \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv" \
  --openssl "$openssl" --redis-server "$redis_server" \
  --rabbitmq-server "$rabbitmq_server" --epmd "$epmd"
```

The test uses Django's usual JSON error handling inside the disposable
uWSGI process for the ownerless-bot comparisons. The startup language for
typed-parameter errors is checked on a cold French request. The whitelist
does not match channel details, writes or other hosts. Remove the route
include and reload nginx to restore Django while Rust is stopped. There is
no automatic failover. Django remains the default. Live ingress and
production load were not tested.

### Rehearse administrator export-consent reads and rollback

`ZULIP_RUST_EXPORT_CONSENTS_READ_PILOT=1` adds loopback-only GET and HEAD
for `/api/v1/export/realm/consents` and its `/json/` alias.
The live owner or administrator role and same-organization records
are checked in one PostgreSQL statement. The response carries only
the documented numeric user ID, consent flag, and email visibility
setting for active users who are not bots. It never reads email
addresses or exported content. Non-administrators get Django's
existing error.

From a fresh disposable fixture in its private loopback namespace:

```sh
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" "$checkout/rust-backend/tests/check_export_consents.py" \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv" \
  --openssl "$openssl"
ZULIP_RUST_HTTP_BIN="$checkout/rust-backend/target/debug/zulip-http" \
  PYTHONPATH="$PWD" "$venv/bin/python" \
  "$checkout/rust-backend/tests/check_read_proxy_activity.py" --export-consents \
  --fixture-root "$fixture_root" --nginx "$nginx" --uwsgi "$uwsgi" --venv "$venv" \
  --openssl "$openssl" --redis-server "$redis_server" \
  --rabbitmq-server "$rabbitmq_server" --epmd "$epmd"
```

Listing, creating, downloading or deleting exports is not part of this
pilot. This pilot does not change anyone's settings or consent. Remove the
route include and reload nginx to restore Django while Rust is stopped.
There is no automatic failover. Django remains the default; live ingress
and production load were not tested.

### Format message search highlights through Rust

Set `ZULIP_RUST_SEARCH_HIGHLIGHTS=1` on the Django worker to opt into
one batch projection of content and topic highlights for `GET /messages`
and `GET /messages/matches_narrow`, for both API and web requests. Django
still runs the original search, visibility checks, and SQL for PostgreSQL
text search or PGroonga. Rust only formats selected rows and their
character offsets. Django still applies the existing legacy mobile link
formatting and assembles the rest of the response.

The setting defaults to off. If the extension is absent or too old, or
if a range or row shape is unsupported, Django formats the whole batch
with the original Python implementation. Unset the flag and restart
the Django worker to return to Python without a routing change.

With the locally built extension on your Python path, run the targeted
Django tests in an isolated test database:

```bash
./tools/test-backend zerver.tests.test_search_highlights_rust
```

The differential test checks both HTTP handlers and both authentication
styles, and exercises PGroonga when enabled in the test configuration.
This bridge does not add independently served HTTP operations.

### Assemble user-directory cards through Rust

Set `ZULIP_RUST_USER_DIRECTORY=1` on the Django worker to assemble an
entire batch of already prepared user cards in Rust. This includes both
directory listings and single-user responses that use the same formatter.
Django still selects visible users and placeholders, checks delivery email
visibility, resolves the avatar and its omission, and sets date and
timezone disclosure. Rust assembles those prepared fields, preserving
absent versus null fields and the existing order.

The setting defaults to off. A missing or older extension, or an
unsupported row, uses the original Python formatter for the entire
batch. Unset the setting and restart the Django worker to return to
Python. There is no HTTP routing change.

With a locally built extension on the Python path, run
the directory differential tests against an isolated test database:

```bash
./tools/test-backend zerver.tests.test_user_directory_rust
```

This bridge does not add independently served HTTP operations.

### Replay scheduled-message and reminder state through Rust

Set `ZULIP_RUST_SCHEDULE_REPLAY=1` on the Django worker to reconcile the owner's already selected
scheduled-message and reminder events when constructing register state.
Rust applies add, update, and remove for scheduled messages, and add and
remove for reminders. Stable delivery order, unchanged edits, and row
and list identity follow the Python reference.

Django still selects the account, rows, events, and event types. The
bridge does not create, edit, remove, or deliver a scheduled database
row, or change HTTP routing. The default is off. Unsupported input or
an older extension uses the reference without a partial native update.

Run the Django reconciliation tests against an isolated test database,
with the locally built extension on your Python path:

```bash
./tools/test-backend zerver.tests.test_schedule_replay_rust
./tools/test-backend zerver.tests.test_events.ScheduledMessagesEventsTest zerver.tests.test_events.RemindersEventsTest
```

This bridge does not add independently served HTTP operations.

### Replay drafts, saved snippets, and navigation views through Rust

Set `ZULIP_RUST_PERSONAL_REPLAY=1` on the Django worker to reconcile
already selected personal-list events during register. Rust applies
the existing add, update, and remove operations to drafts, saved
snippets, and navigation views. Existing draft IDs, first-match
updates and removals, insertion order, and navigation in-place updates
retain their Python semantics.

Django still decides which user's records and events to load and
which event types to apply. Rust receives only one prepared list
and its event. It does not create or write database records, and
does not change the HTTP routes. The flag defaults to off. Unsupported
rows and older extensions use the unchanged Python reducer.

Run the differential tests in an isolated test database with the built
extension on the Python path:

```bash
./tools/test-backend zerver.tests.test_personal_replay_rust
```

This bridge changes no independent HTTP counts.

### Replay channel-folder changes through Rust

Set `ZULIP_RUST_CHANNEL_FOLDER_REPLAY=1` before starting Django to reconcile the
already-selected channel-folder add, update, and reorder events during
registration. The bridge preserves the order of equal IDs, updates
existing cards in place, and applies the requested positions. Django still
selects the organization and events, owns permissions and writes, and
handles HTTP. The default, an older extension, or a rejected input uses the
unchanged Python reducer.

In an isolated Zulip test environment with the Python extension built and
importable, run both paths:

```bash
for enabled in 0 1; do
    ZULIP_RUST_CHANNEL_FOLDER_REPLAY=$enabled ./tools/test-backend \
        zerver.tests.test_channel_folder_replay_rust \
        zerver.tests.test_events.ChannelFolderActionTest \
        zerver.tests.test_channel_folders.ChannelFolderCreationTest \
        zerver.tests.test_channel_folders.GetChannelFoldersTest \
        zerver.tests.test_channel_folders.UpdateChannelFoldersTest \
        zerver.tests.test_channel_folders.ReorderChannelFolderTest
done
```

This changes neither the default route nor independent HTTP coverage.

### Reconcile unread register state through Rust

Set `ZULIP_RUST_UNREAD_REPLAY=1` before starting Django. The optional extension applies
read and unread flags, deletions, and channel or topic moves to the
current account's already-selected unread state during registration.
It preserves channel, direct-message and group-message buckets,
mentions, and iteration order. Django still owns event selection,
authorization, the database and mute policy for newly arriving messages.
The original Python path handles an older extension or unsupported input.

In an isolated Zulip test environment with the Python extension built
and importable, exercise both flags independently:

```bash
for payload in 0 1; do
    for replay in 0 1; do
        ZULIP_RUST_UNREAD_PAYLOAD=$payload ZULIP_RUST_UNREAD_REPLAY=$replay \
            ./tools/test-backend \
            zerver.tests.test_unread_replay_rust \
            zerver.tests.test_events.NormalActionsTest.test_update_read_flag_removes_unread_msg_ids \
            zerver.tests.test_events.NormalActionsTest.test_stream_update_message_events \
            zerver.tests.test_events.NormalActionsTest.test_do_delete_message_stream \
            zerver.tests.test_events.NormalActionsTest.test_do_delete_message_personal \
            zerver.tests.test_message_flags.GetUnreadMsgsTest
    done
done
```

This is in-process register reconciliation, not a new HTTP route.

### Persist a direct-message pin through Rust

The optional, default-off `ZULIP_RUST_DM_PIN=1` uses one PostgreSQL
statement for the existing-conversation lookup, owner subscription lock,
and a changed pin value on Django's current transaction. Django still
authenticates, validates the request, and sends the same owner-only
event after commit. It never creates a conversation for a pin request
and an unchanged pin sends no event.

An older extension, nondefault write routing, model save hooks, or IDs
that do not fit the native storage type use the original ORM path.
Once the native statement is attempted, a database error is not
retried through the ORM. This does not expose a new HTTP route.

With the matching extension importable in a disposable fixture:

```sh
for enabled in 0 1; do
    ZULIP_RUST_DM_PIN=$enabled ./tools/test-backend \
        zerver.tests.test_direct_message_groups.DirectMessageConversationPinTest \
        zerver.tests.test_direct_message_pin_rust.DirectMessagePinRustTest
done
```

The synthetic changed and unchanged requests used one domain statement,
down from four and three respectively. Across three local rehearsals with
five paired batches each, median action-time speedups varied from about
1.3 to 2.6 times for changes and 1.2 to 2.3 times for no-ops. This includes
the Python bridge and database transaction, not HTTP or broker latency.
Independent HTTP coverage is unchanged.

### Replay recent direct-message conversations through Rust

Set `ZULIP_RUST_RECENT_CONVERSATION_REPLAY=1` before starting Django to
apply selected message-arrival and pin/unpin events during registration.
The reducer groups the caller's existing participant IDs without self,
keeps conversation order and existing pins when a new message arrives,
and updates the pin in place only for a known conversation. Django still
selects visible messages and events and queries the current rows again
after a deletion. No message content, write, or HTTP path moves to Rust.

This switch is independent of `ZULIP_RUST_RECENT_CONVERSATIONS`. Older
extensions and unsupported inputs use the unchanged Python reducer. Run
in an isolated test environment with the extension built and importable:

```bash
for assemble in 0 1; do
    for replay in 0 1; do
        ZULIP_RUST_RECENT_CONVERSATIONS=$assemble ZULIP_RUST_RECENT_CONVERSATION_REPLAY=$replay \
            ./tools/test-backend \
            zerver.tests.test_recent_conversation_replay_rust \
            zerver.tests.test_events.NormalActionsTest.test_pm_send_message_events_via_direct_message_group \
            zerver.tests.test_events.NormalActionsTest.test_direct_message_group_send_message_events \
            zerver.tests.test_events.NormalActionsTest.test_pinned_direct_message_conversation_events \
            zerver.tests.test_direct_message_groups.DirectMessageConversationPinTest
    done
done
```

This does not change the independent HTTP inventory.

### Prune stale custom profile fields during registration

Set `ZULIP_RUST_REGISTER_PROFILE_PRUNE=1` on a Django worker to prune
obsolete custom profile field values from the already-selected user cards
when a profile-fields event is replayed during registration. Django retains
the event, profile visibility, field metadata, queries, and writes. The
native reducer preserves card identity and the order of remaining fields.
An older extension or a declined snapshot uses the original Python replay.

The flag defaults to off and is independent of the other register flags.
Unset it and restart the worker to restore Python replay. Test both flag
states with the locally built extension and an isolated test database:

```bash
ZULIP_RUST_REGISTER_PROFILE_PRUNE=0 ./tools/test-backend zerver.tests.test_register_rust_replay
ZULIP_RUST_REGISTER_PROFILE_PRUNE=1 ./tools/test-backend zerver.tests.test_register_rust_replay
```

### Replay selected user-group rosters through Rust

Set `ZULIP_RUST_GROUP_ROSTER_REPLAY=1` to apply queued member and subgroup
ID list edits to user-group cards already selected by Django during
registration. Appends preserve the existing list and its stable ordering;
removals replace it with the same sorted set difference as Python. An
unsupported list or older extension returns just that card to Python.
Visibility, permissions, group membership writes and event order stay in
Django.

The flag defaults to off. Unset it and restart the worker to restore
the Python roster replay. Test both flags with the local extension and
an isolated test database:

```bash
ZULIP_RUST_GROUP_ROSTER_REPLAY=0 ./tools/test-backend zerver.tests.test_user_group_roster_replay_rust
ZULIP_RUST_GROUP_ROSTER_REPLAY=1 ./tools/test-backend zerver.tests.test_user_group_roster_replay_rust
```

### Verify combined register replay and its rollback

Run this in an isolated Zulip test environment with the current Python
extension built and importable. The two tests use Django Basic and
session requests and a private test database. Real actions enqueue
draft, snippet, navigation, scheduled-message, reminder, folder,
direct-message, pin, and read-flag updates while the view is between
its initial snapshot and queue read. Only the queue transport is
stubbed; Tornado's event queue and Django's access checks, database
queries, and response processing run in the test process.

The raced response must match a fresh snapshot apart from the expected
queue cursor, and must not contain another account's private changes.
Each invocation starts a new process. The final invocation verifies
rollback by leaving the flags unset before process startup:

```bash
run_register_replay() (
    for flag in \
        ZULIP_RUST_PERSONAL_REPLAY ZULIP_RUST_SCHEDULE_REPLAY \
        ZULIP_RUST_CHANNEL_FOLDER_REPLAY ZULIP_RUST_UNREAD_REPLAY \
        ZULIP_RUST_RECENT_CONVERSATION_REPLAY \
        ZULIP_RUST_UNREAD_PAYLOAD ZULIP_RUST_RECENT_CONVERSATIONS; do
        if [ "$1" = 1 ]; then
            export "$flag=1"
        else
            unset "$flag"
        fi
    done
    ./tools/test-backend zerver.tests.test_register_rust_replay
)
run_register_replay 0 && run_register_replay 1 && run_register_replay 0
```

An older extension that supports projections but not replay continues
to use Python for replay. This verifies an in-process fallback, not a
production restart, network-proxy cutover, or independent Rust route.

### Label message receipt flags in batches through Rust

Set `ZULIP_RUST_USER_MESSAGE_FLAGS=1` before starting Django to label
already selected receipt masks in message send events and message list
responses. Django supplies its ordered public names and withholds
internal flag positions. Only identifiers and numeric masks reach the
extension; recipients, message selection and content, stored flags,
historical defaults, and spectator behavior are unchanged. This switch
is off by default and an older extension or unsupported batch uses the
original Python loops.

With the current extension built in an isolated test environment:

```bash
for enabled in 0 1; do
    ZULIP_RUST_USER_MESSAGE_`ZULIP_RUST_USER_MESSAGE_FLAGS=1`S=$enabled ./tools/test-backend \
        zerver.tests.test_user_message_flags_rust \
        zerver.tests.test_message_send.DoSendMessagesTest.test_events_for_several_messages \
        zerver.tests.test_message_fetch.GetOldMessagesTest.test_include_history
done
```

This changes no standalone HTTP ownership.

### Plan message-edit receipt changes through Rust

Set `ZULIP_RUST_MESSAGE_EDIT_FLAGS=1` before starting Django to
calculate which rendering-derived receipt bits change after a message
edit or embedded-content rewrite. Only receipt IDs, numeric masks and
the membership sets that Django already calculated enter the bridge.
Django decides who and what to edit and saves only the same changed
receipts in the same order. Read, starred, private and any other bits
are unchanged. Older extensions and unsupported batches use the original
Python path.

With the extension built in an isolated test environment, run both modes:

```bash
for enabled in 0 1; do
    ZULIP_RUST_MESSAGE_EDIT_`ZULIP_RUST_MESSAGE_EDIT_FLAGS=1`S=$enabled ./tools/test-backend \
        zerver.tests.test_message_edit_flags_rust \
        zerver.tests.test_alert_words.AlertWordTests.test_alert_flags \
        zerver.tests.test_message_edit.EditMessageTest.test_remove_topic_wildcard_mention \
        zerver.tests.test_events.NormalActionsTest.test_stream_update_message_events
done
```

The default and independent HTTP ownership are unchanged.

### Calculate initial message receipt masks through Rust

Set `ZULIP_RUST_MESSAGE_SEND_MASKS=1` before starting Django to calculate
initial read, mention and alert bits for recipients already selected
by Django. Rust gets ordered IDs, numeric masks, and ordinary membership
sets. No further recipients can be selected. Python still owns the
baseline channel/direct-message bits, which idle receipts can be
omitted, row objects, database writes, and event dispatch.

This switch is independent of the edit planner and public label switch.
Older extensions and unsupported batches use the Python calculation.
With the current extension built in an isolated test environment:

```bash
for enabled in 0 1; do
    ZULIP_RUST_MESSAGE_SEND_MASKS=$enabled ./tools/test-backend \
        zerver.tests.test_message_send_masks_rust \
        zerver.tests.test_message_send.DoSendMessagesTest.test_events_for_several_messages \
        zerver.tests.test_message_send.StreamMessagesTest.test_topic_wildcard_mentioned_flag \
        zerver.tests.test_soft_deactivation.SoftDeactivationMessageTest.test_reactivate_user_if_soft_deactivated
done
```

Default behavior and independent HTTP ownership are unchanged.

### Verify the combined message receipt lifecycle

Use an isolated Zulip test environment with the current extension
built and importable. The test runs an authenticated private channel
through Basic auth and a group direct message through a Django
session. It verifies stored receipt bits, public send/edit event flags,
and byte-identical authorized fetches before and after editing. Read
and starred bits survive, the private storage bit is not exposed, and
a former subscriber no longer sees the message or gets its edit event.

Run each independent startup combination, then a fresh process with
all three switches unset:

```bash
for send in 0 1; do
    for edit in 0 1; do
        for labels in 0 1; do
            ZULIP_RUST_MESSAGE_SEND_MASKS=$send \
                ZULIP_RUST_MESSAGE_EDIT_FLAGS=$edit \
                ZULIP_RUST_USER_MESSAGE_FLAGS=$labels \
                ./tools/test-backend zerver.tests.test_message_receipt_lifecycle_rust || exit 1
        done
    done
done
(
    unset ZULIP_RUST_MESSAGE_SEND_MASKS ZULIP_RUST_MESSAGE_EDIT_FLAGS ZULIP_RUST_USER_MESSAGE_FLAGS
    ./tools/test-backend zerver.tests.test_message_receipt_lifecycle_rust
)
```

A missing symbol in an older extension leaves its stage on Python.
The test uses Django's in-process HTTP client and synthetic database,
not a deployed listener or a production rollback.

### Plan visible message windows through Rust

Set `ZULIP_RUST_MESSAGE_WINDOW=1` before starting Django
to locate the anchor and requested window in rows already selected,
sorted and filtered for visible history by Django. The Rust planner
reads only message IDs, finds the stable boundaries, and returns
positions. Django slices the original rows and calculates the
completeness and history indicators as before. Permission checks,
explicit message-ID fetches and SQL are unchanged.

The switch is off by default. An older extension or an unsupported
input still uses the original Python page assembly.

In an isolated test environment with the extension built:

```bash
for enabled in 0 1; do
    ZULIP_RUST_MESSAGE_WINDOW=$enabled ./tools/test-backend \
        zerver.tests.test_message_window_rust \
        zerver.tests.test_message_fetch.PostProcessTest \
        zerver.tests.test_message_fetch.GetOldMessagesTest.test_get_visible_messages_with_anchor \
        zerver.tests.test_message_fetch.GetOldMessagesTest.test_use_first_unread_anchor_with_some_unread_messages
done
```

Independent HTTP ownership does not change.

### Opt-in fixed time display text

`ZULIP_RUST_TIME_DISPLAY=1` enables the two literal formatting steps in
`zerver.lib.timestamp`. They take the original Python ISO string or the
already-validated sign, hours, and minutes. Timezone selection, conversion,
minute validation, and ICU locale formatting are unchanged. Python remains the
default and retains its original formatting if the extension or either symbol is
missing, or if a native helper declines its input.

### Opt-in log display text

`ZULIP_RUST_LOG_DISPLAY=1` enables two literal calculations in
`zerver.lib.logging_util`: the four-codepoint fallback level prefix and the
standard format template with or without an already-selected PID placeholder.
The level map, Python logging formatter, record data and decoration, and
handlers are unchanged. Python is the default, and the original formatting
still runs if the extension or either symbol is missing or declines.

### Opt-in migration diagnostic text

`ZULIP_RUST_MIGRATION_DISPLAY=1` enables two cosmetics in
`zerver.lib.migration_status`: removing the existing narrowly defined SGR
spans from already-buffered output and formatting an already-selected
badge and title. Migration commands, data reads, traversal, state selection,
and squashed-title construction still run in Python. The original formatting
is the default and remains the fallback if either native symbol is missing
or declines.

### Opt-in API documentation type labels

`ZULIP_RUST_API_TYPE_DISPLAY=1` enables literal union, array, and nullable type
label assembly for the API documentation tables. Python retains schema
traversal, validation and error behavior, and all markup and escaping. Python is
the default and the fallback if the extension or an individual helper is missing
or declines an already-prepared value.

### Opt-in analytics subgroup projection

`ZULIP_RUST_ANALYTICS_SUBGROUP_PROJECTION=1` fills chart arrays for already
selected subgroup labels and prepared UTC time keys. Django retains chart
queries, grouping and eligible-label selection; the unchanged client alias
step runs afterward. The default and unsupported-value fallback is the
original Python fill loop. No time conversion or scope decision runs in Rust.

### Opt-in documentation tab text

`ZULIP_RUST_TABBED_TEXT=1` enables LF joining and the original outer Python
whitespace trimming for already-selected documentation tab lines, and LF
joining of already-formatted blocks. Python keeps directive parsing, tab
selection, labels, callbacks, templates, and Markdown behavior. Python is
the default and the fallback for unavailable or declined native helpers.

### Opt-in prepared commit display values

`ZULIP_RUST_COMMIT_DISPLAY=1` enables short SHA and first-LF subject extraction
at the existing display row, and ranking of already-extracted display names.
Python keeps the original field access order, URL lookup, row template,
committer extraction, and all notification behavior. Original Python runs
by default and when either native helper is unavailable or declines.

### Opt-in API return documentation indentation

`ZULIP_RUST_API_RETURN_INDENT=1` optionally replaces LF characters with the
original Python-prepared newline and spaces in a return-value description.
Python retains indentation choice, field selection, Markdown and markup. The
original replacement remains the default and handles unavailable or declined
native inputs.

### Opt-in API documentation enum examples

`ZULIP_RUST_API_ENUM_DISPLAY=1` optionally comma-joins already-formatted enum
examples in the API documentation argument and object tables. Enum selection,
JSON serialization, templates, and Markdown remain in Python. The original
Python join remains the default and the fallback for missing or declined
native input.

### Opt-in Slack import field display

`ZULIP_RUST_SLACK_FIELD_DISPLAY=1` optionally assembles a two-column section
table after the existing Python cleanup, and LF-joins prepared attachment field
rows with no trimming. Python retains field selection, cleanup, markup
decisions, and import behavior. The original assembly is the default and the
fallback for unavailable or declined native helpers.

### Opt-in Git people display text

`ZULIP_RUST_GIT_DISPLAY_PEOPLE=1` optionally joins already-extracted assignee
labels and formats already-ranked committer labels and counts. The single-name
path, selection, ranking, surrounding templates, URLs, and notification
behavior stay in Python. The original display formatting is the default and
handles unavailable or declined native inputs.

### Opt-in chart timestamp display integers

`ZULIP_RUST_CHART_TIMESTAMP_DISPLAY=1` optionally truncates already calculated
Python chart timestamp floats toward zero for display. Python retains timestamp
calculation, timezone and bucket handling, and response serialization. The
original Python conversion is the default and handles unavailable or declined
native values.

### Opt-in saved-snippet display rows

`ZULIP_RUST_SAVED_SNIPPET_DISPLAY=1` optionally assembles the existing ordered
saved-snippet dictionaries for the legacy Django GET after the same query.
Python still prepares the original values, including the integer timestamp, and
handles the response. Events, bootstrap and writes keep their original Python
formatting. The switch defaults off; absent, older, or declining native
implementations use Python.

### Opt-in navigation view display rows

`ZULIP_RUST_NAVIGATION_VIEW_DISPLAY=1` optionally assembles the existing navigation
view dictionaries for the legacy Django GET after the same query. Fragment,
pin and name values are untouched. The switch defaults off; an absent, older
or declining extension falls back to Python. Bootstrap, events and writes
retain their existing Python formatting.

### Opt-in Gong duration and topic display text

`ZULIP_RUST_GONG_DISPLAY=1` optionally formats supplied duration seconds
and joins the already selected topic display labels. Python still checks
inputs, chooses and sorts topics, and owns time, URL, message and webhook
handling. The switch defaults off; absent, older, or declining native
implementations use the original Python formatting.

### Opt-in channel folder display ordering

`ZULIP_RUST_CHANNEL_FOLDER_DISPLAY=1` optionally plans stable ordering
after Python selects channel folders and builds the display rows. Realm and
spectator views retain their existing sort keys and original DTO objects.
Python handles absent, older, declining or malformed native results. This is
off by default and does not participate in folder writes or reordering.

### Opt-in Jotform display value joining

`ZULIP_RUST_JOTFORM_VALUE_DISPLAY=1` optionally joins selected list items
and ordered dictionary values with a space. Python still handles scalar
strings, selects fields, parses input, and handles files and delivery.
The switch defaults off; an absent, older, or declining extension uses the
original Python joins.

### Opt-in Rocket.Chat display name text

`ZULIP_RUST_ROCKETCHAT_DISPLAY_NAME=1` optionally clips a name
after Python chooses it and formats its existing suffix, or adds the literal
team prefix after Python chooses the existing team branch. Python retains
selection, legacy formatting dispatch, and the original fallback for absent,
older or declining extensions. The switch defaults off and does not change
data import, routing or storage.

### Opt-in OpenProject duration wording

`ZULIP_RUST_OPENPROJECT_DURATION_DISPLAY=1` optionally formats
prepared duration counts and joins the display parts. Python retains the
existing parser, field order, integer conversions, and singular/plural unit
selection. The switch defaults off; absent, older or declining extensions
use the original formatting. Webhook inputs and delivery are unchanged.

### Opt-in integration catalog display

`ZULIP_RUST_INTEGRATION_CATALOG_DISPLAY=1` optionally rounds the
already aggregated integration count and assembles selected category tuples
for documentation display. Return-category routing, catalog eligibility,
templates and URL handling retain their existing Python paths. Default is
off; an absent, older, declining or incompatible extension uses Python.

### Opt-in activity date cell display

`ZULIP_RUST_ACTIVITY_DATE_DISPLAY=1` optionally spells the fixed
calendar day or calendar minute after Python performs its original date
operations. It does not choose a timezone, convert time, or change how
missing values display. It defaults off, with Python fallback for older,
absent or declining extensions and custom date types.
