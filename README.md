# BingX → Telegram copy-trading bot (MVP scaffold)

Watches the master trader's BingX account (READ-ONLY key) and mirrors his new trades to followers who
linked a trade-only API key through the Telegram bot.

```
BingX master account ──WS──▶ run_listener ──▶ MasterEvent (DB queue)
                                                   │
                       run_executor  ◀─────────────┘
                          │  sizing + risk per follower, concurrent fan-out
                          ▼
                 follower BingX accounts  ──▶ CopyOrder rows + Telegram notifications
run_bot: onboarding (/connect), /status, /pause, /sizing, /maxtrade, /maxlev, /disconnect
Django admin: followers, events, copy log, GLOBAL KILL SWITCH (System state)
```

## Layout

- `engine/` pure Python (sizing, risk, fill classification). No Django/network. Unit-tested.
- `exchange/` BingX REST client, request signing, WebSocket stream, message parsing.
- `copier/` Django app: models, admin, services (listener, executor, bot, notify), management commands.
- `tests/` `python -m unittest discover -s tests -t .`

## Run it (dev)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in ENCRYPTION_KEYS, TELEGRAM_BOT_TOKEN, master read-only key
set -a; source .env; set +a
python manage.py makemigrations copier && python manage.py migrate
python manage.py createsuperuser
python manage.py run_listener   # terminal 1
python manage.py run_executor   # terminal 2
python manage.py run_bot        # terminal 3
```

Production: `docker compose up -d` on a VPS with a STATIC IP (followers whitelist it on their API keys).

## Debugging (VS Code)

`.vscode/launch.json` has launch configs for the tests and each process (they read `.env`).

1. Leave `DB_NAME` unset to use local sqlite, and set `DRY_RUN=1`: reads hit BingX, but NO orders/leverage changes are sent.
2. Link a test account with the bot (`/connect`), then run `python manage.py inject_event` (refuses unless DRY_RUN=1)
   while `run_executor` is running. Check `CopyOrder` rows in the admin and the executor logs.
3. Set `LOG_RAW_STREAM=1` and run `run_listener` on the real (read-only) master key to see raw stream payloads.

## Design decisions

- **Hedge mode required** (master and followers): `positionSide` makes open-vs-close unambiguous.
- **Only fully FILLED master orders are copied** (partial fills and limit-order lifecycle: not yet).
- **At-most-once delivery** per (event, follower): a missed copy is safer than a duplicate. Timeouts become
  `UNKNOWN` and need reconciliation.
- **Stale opens are not copied** (`MAX_OPEN_EVENT_AGE_SECONDS`); closes always are.
- **Master's post-fill position/leverage come from REST** right after each fill (adds latency, but is the truth).
- API keys are Fernet-encrypted at rest; the bot deletes the messages containing them; the admin never shows them.

## MUST DO before real money

1. Verify every `# VERIFY` in `exchange/bingx.py` and the field names in `exchange/parsing.py` against BingX's
   current docs. Run the listener with `LOG_RAW_STREAM=1` on the brother's account and check real payloads.
2. Test end-to-end on BingX's demo (VST) environment, then with tiny amounts and 2-3 trusted followers.
3. Confirm the followers' `set_leverage` / margin-mode behaviour and minimum order sizes on a real account.

## Known gaps / TODO (roughly in priority order)

- Reconciliation job: resolve `UNKNOWN` copies by client order id; detect follower/master position drift.
- Admin alerts (Telegram message to you) when the listener fails to record a fill or the stream is down.
- Reject API keys that have withdrawal permission (find and add BingX's permission-check endpoint).
- Master TP/SL and limit orders, partial fills, master adding to / reducing positions in one-way mode.
- Follower risk: daily loss limit, max total exposure; automatic pause on repeated failures.
- Move key entry from chat to a Telegram Mini App / HTTPS form; per-follower rate limiting; audit log.
- Legal: terms of service, risk disclosure, and local regulatory advice before charging for this.
