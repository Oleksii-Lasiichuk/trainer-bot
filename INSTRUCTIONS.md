# Trainer-bot — Self-host setup guide

Target audience: a developer who has never used Telegram bots, intervals.icu or Groq. This should take under 30 minutes end to end.

You will need:
- A Telegram account
- An intervals.icu account with Garmin Connect sync enabled
- A Groq account (free, no credit card)
- Docker (or a Python 3.11+ environment)

---

## Section 1 — Create a Telegram bot

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot`. Follow the prompts to pick a display name and a username ending in `bot`.
3. BotFather returns a token in the form `123456789:AAH...`. Copy it. This is your `TELEGRAM_BOT_TOKEN`.
4. Optional polish: `/setdescription`, `/setabouttext`, `/setcommands`. A suggested command list:
   ```
   start - Greet and show help
   help - Usage + examples
   setkey - Connect your intervals.icu account
   whoami - Show stored athlete ID
   reset - Clear conversation history
   ping - Check intervals.icu connectivity
   ```
5. If you want the bot to see group messages, `/setprivacy` → `Disable`. For DM-only usage the default is fine.

## Section 2 — Get your intervals.icu API key

1. Go to <https://intervals.icu> and log in.
2. Click your avatar → **Settings**.
3. Scroll to **Developer Settings** near the bottom.
4. Click **Generate API key** and copy it.
5. Your **Athlete ID** is on the same page — format `i123456`. Copy it too.
6. **Important:** confirm Garmin sync is active: Settings → **Integrations** → Garmin should say "Connected". If not, reconnect there. Intervals.icu is the bot's single source of truth for Garmin data — if Garmin isn't syncing, the bot won't see your data.

## Section 3 — Get a Groq API key

1. Go to <https://console.groq.com> and sign up (Google OAuth works; no credit card required).
2. In the console click **API Keys** → **Create API Key**. Copy the key — it looks like `gsk_...`. This is your `GROQ_API_KEY`.
3. Free-tier limits (at the time of writing):
   - `llama-3.3-70b-versatile` — 30 RPM / 6 000 TPM / 1 000 requests per day. Best quality.
   - `llama-3.1-8b-instant` — higher daily allowance (14 400 RPD / 500 000 TPD). Used as automatic fallback.

## Section 4 — Find your Telegram user ID (for whitelisting)

Running a bot without a whitelist means anyone who discovers it can DM it and burn your Groq quota. Get your numeric user ID:

1. In Telegram, start a chat with **@userinfobot** (or **@getmyid_bot**).
2. It replies with your numeric ID. Put it in `ALLOWED_TELEGRAM_USER_IDS` (comma-separated if multiple).

## Section 5 — Run locally with Docker (recommended)

1. Install Docker Desktop (macOS/Windows) or Docker Engine (Linux).
2. Clone the repo:
   ```bash
   git clone https://github.com/<your-fork>/trainer-bot.git
   cd trainer-bot
   ```
3. Prepare environment:
   ```bash
   cp .env.example .env
   # edit .env and paste: TELEGRAM_BOT_TOKEN, GROQ_API_KEY, ALLOWED_TELEGRAM_USER_IDS
   ```
4. Start:
   ```bash
   docker compose up -d
   ```
5. Tail logs — you should see `bot.ready` and `bot.start_polling`:
   ```bash
   docker compose logs -f
   ```
6. In Telegram, find your bot by username. Send:
   ```
   /start
   /setkey
   i123456           ← your intervals.icu athlete ID
   your-intervals-key ← your intervals.icu API key
   ```
7. Delete the messages that contained your API key (hygiene).
8. Try:
   ```
   what was my last activity?
   how did I sleep this week?
   should I run hard tomorrow given my load?
   ```

## Section 5b — Run without Docker (development)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in secrets
python -m trainer_bot
```

## Section 6 — Deploy for free

### Option A — Oracle Cloud Free Tier (truly free forever)

1. Sign up at <https://cloud.oracle.com>. A credit card is required for identity verification only; always-free resources are never billed.
2. Create Compute → Instance → shape **VM.Standard.A1.Flex** (ARM, e.g. 1 vCPU + 6 GB RAM — well within free tier).
3. Pick Ubuntu 22.04. Save the generated SSH key.
4. SSH in, install Docker:
   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER   # logout/login after this
   ```
5. Clone the repo, prepare `.env`, run `docker compose up -d`. Done.

### Option B — Fly.io (easier, small always-free allowance)

1. Install flyctl: `curl -L https://fly.io/install.sh | sh`
2. `fly auth signup`
3. From the project root:
   ```bash
   fly launch --copy-config --no-deploy   # adjust fly.toml if needed
   fly secrets set TELEGRAM_BOT_TOKEN=... GROQ_API_KEY=... ALLOWED_TELEGRAM_USER_IDS=...
   fly volumes create trainer_data --size 1 --region waw
   fly deploy
   ```
   Decline the offer to provision Postgres — SQLite on the mounted volume is enough.

### Option C — Railway

1. Sign up at <https://railway.app> and complete the one-time $5 verification.
2. New Project → Deploy from GitHub → pick your fork.
3. In the service's **Variables** tab, add `TELEGRAM_BOT_TOKEN`, `GROQ_API_KEY`, `ALLOWED_TELEGRAM_USER_IDS`.
4. Attach a volume at `/app/data` (Settings → Volumes).
5. Redeploy.

## Section 7 — Troubleshooting

- **`/ping` returns 401** → intervals.icu key is wrong or revoked. Regenerate on intervals.icu and re-run `/setkey`.
- **Bot doesn't respond** → check `docker compose logs -f`. Common causes: wrong `TELEGRAM_BOT_TOKEN`, or `ALLOWED_TELEGRAM_USER_IDS` is set and doesn't include your own ID.
- **"Rate limit exceeded" on Groq** → 1 000 daily requests on the 70B used up. Wait until midnight UTC, or add billing on console.groq.com. The bot auto-falls-back to the 8B model on persistent 429, which has a larger daily allowance.
- **Most wellness fields are null** → Garmin hasn't pushed them to intervals.icu, or your device doesn't record that metric. Open intervals.icu → Settings → Integrations → Garmin and force a sync.
- **SQLite "database is locked"** → typically only happens if two processes mount the same file. Make sure only one `docker compose` stack is running.
- **Bot can see old conversation after restart** → good; history is on the mounted `data/` volume. If you want a fresh slate, run `/reset`.

## Section 8 — Security checklist

- Never commit `.env` — it's in `.gitignore`, keep it there.
- Rotate the Groq key if your fork is public and you pushed it by accident.
- Always set `ALLOWED_TELEGRAM_USER_IDS`. If left empty, anyone who discovers your bot's username can chat and burn your quota.
- After running `/setkey`, delete the Telegram messages that contained the API key.
- If you share your server, consider running the bot under an unprivileged user (the Docker image already does).
