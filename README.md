# Discord X Feed Bot

A small Discord bot that monitors configured public X accounts and forwards raw links to new posts. It ignores replies, optionally includes pure reposts, forwards quote posts, and persists delivery state in SQLite so restarts do not resend posts.

Twikit uses unofficial X web/internal APIs. X can change these APIs without notice, so authentication or fetching may occasionally require a Twikit update.

## Features

- Multiple X accounts, Discord servers, and channels
- One X timeline fetch per unique tracked account per polling cycle
- Optional role ping and per-subscription repost setting
- X account display name and avatar through reusable Discord webhooks
- Persistent cursors, per-subscription deduplication, and no historical backfill
- Failure isolation between X accounts and Discord destinations
- Google Compute Engine and `systemd` deployment without Docker

## Slash commands

- `/follow feed-source channel [reposts] [ping]` adds or updates comma-separated handles. Requires Administrator or Manage Server.
- `/unfollow feed-source channel` removes matching subscriptions from one channel. Requires Administrator or Manage Server.
- `/follows [channel]` lists feeds in the current server.
- `/status` shows watcher, account, subscription, interval, and last-poll status.

The bot sends only a labeled link:

```text
[Tweeted](https://twitter.com/username/status/POST_ID)
```

or, when a role is configured:

```text
<@&ROLE_ID>
[Tweeted](https://twitter.com/username/status/POST_ID)
```

## Discord setup

1. Create an application and bot at the Discord Developer Portal.
2. Invite it with the `bot` and `applications.commands` scopes.
3. Grant View Channels, Send Messages, Embed Links, and Manage Webhooks in destination channels.
4. Put the bot token in `.env`; never commit that file.

The bot creates one reusable webhook per destination channel. Each forwarded message contains only a `Tweeted` link, while the webhook uses the source X account's display name and avatar. Discord generates the normal link preview.

## Local installation

Python 3.12 or newer is required.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

For development:

```bash
pip install -r requirements-dev.txt
pytest
ruff check .
ruff format --check .
```

## Configuration

```env
DISCORD_TOKEN=
POLL_INTERVAL_SECONDS=60
DATABASE_PATH=data/bot.db
X_USERNAME=
X_EMAIL=
X_PASSWORD=
X_COOKIES_PATH=data/x_cookies.json
LOG_LEVEL=INFO
```

Use a dedicated X account. Twikit loads the cookie file when it exists and otherwise logs in with the supplied username, email, and password, then saves cookies for reuse. Keep `.env` and the cookie file readable only by the bot user.

### X browser-session fallback

X or Cloudflare may block Twikit's automated credential-login flow even when the account details are correct. If that happens, log in to the dedicated account normally in a browser and create `data/x_cookies.json` from that authenticated X session. Twikit expects one JSON object that maps cookie names to values, not a browser-export array:

```json
{
  "auth_token": "BROWSER_COOKIE_VALUE",
  "ct0": "BROWSER_COOKIE_VALUE"
}
```

Use the complete `x.com` cookie set when available. Never commit, print, or share this file. Restrict it to the bot user with `chmod 600 data/x_cookies.json`, then start the bot again. A successful cookie validation skips credential login.

This project currently includes temporary compatibility patches for X's changed transaction bundle format and user profiles whose description metadata omits an empty `urls` field. Twikit 2.3.3 cannot handle either response shape on its own. Remove `app/twikit_compat.py` and its installation calls after an official Twikit release includes both upstream fixes.

## Google Compute Engine

Create an Ubuntu LTS `e2-micro` VM in a currently eligible Google Cloud Free Tier region with a standard persistent disk. The bot needs outbound access to Discord and X, but no inbound application port. Restrict SSH as appropriate.

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
git clone <repo-url> discord-x-feed-bot
cd discord-x-feed-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
python main.py
```

Verify Discord connection, X authentication, `/follow`, new-post delivery, and restart deduplication before enabling `systemd`.

## systemd

Copy `deploy/discord-x-feed.service` to `/etc/systemd/system/discord-x-feed.service`. Replace every `BOT_USER` with the dedicated non-root Linux user and adjust the working directory if needed.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now discord-x-feed
systemctl status discord-x-feed
journalctl -u discord-x-feed -f
```

The service restarts after failures and starts automatically after a VM reboot.

## Automatic deployment from GitHub

The workflow in `.github/workflows/deploy.yml` runs tests on pull requests and pushes. A successful push to `main` deploys to the Compute Engine VM over SSH, installs production dependencies, restarts `discord-x-feed`, and verifies that the service remains active. If the new version fails, `deploy/update.sh` restores the previous Git revision and restarts it.

### One-time VM setup

Complete the normal VM and `systemd` setup first. The deployment checkout must be on the `main` branch, have no tracked local edits, and contain an executable update script:

```bash
cd /home/BOT_USER/discord-x-feed-bot
chmod +x deploy/update.sh
```

Allow only this service restart without a password. Run `sudo visudo -f /etc/sudoers.d/discord-x-feed-deploy` and add:

```text
BOT_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart discord-x-feed
```

Generate a dedicated deployment key on a trusted computer, without replacing your normal SSH key:

```bash
ssh-keygen -t ed25519 -f github-actions-tweetbot -C github-actions-tweetbot
```

Append `github-actions-tweetbot.pub` to the VM user's `~/.ssh/authorized_keys`. Store the private key only in GitHub Actions, then delete the local private-key copy after confirming deployment works.

The VM itself must also be able to run `git fetch origin main`. Public repositories need no additional GitHub credential. For a private repository, configure a separate read-only GitHub deploy key on the VM and use the repository's SSH clone URL; do not reuse the GitHub Actions-to-VM private key.

### GitHub configuration

In the GitHub repository, open **Settings → Secrets and variables → Actions** and create these repository secrets:

- `GCE_HOST`: VM external IP or DNS name.
- `GCE_USER`: dedicated non-root VM user.
- `GCE_SSH_PRIVATE_KEY`: complete contents of `github-actions-tweetbot`.
- `GCE_SSH_KNOWN_HOSTS`: trusted SSH host-key line for the VM.
- `GCE_SSH_PORT`: optional; defaults to `22`.

Create these repository variables:

- `AUTO_DEPLOY_ENABLED`: set to `true` only after all VM secrets are configured.
- `GCE_DEPLOY_PATH`: absolute checkout path, for example `/home/BOT_USER/discord-x-feed-bot`.
- `GCE_SERVICE_NAME`: optional; defaults to `discord-x-feed`.

Obtain the known-hosts line from a trusted connection and verify its fingerprint against the VM before saving it:

```bash
ssh-keyscan -H VM_EXTERNAL_IP
```

The workflow uses strict host-key checking and never stores `.env`, Discord credentials, or X cookies in GitHub. Those files remain only on the VM. For additional safety, configure the GitHub `production` environment with required reviewers and protect the `main` branch so the quality job must pass before merging.

## Troubleshooting

- Commands missing: confirm the invite includes `applications.commands`, then restart and check the sync log.
- `/follow` fails: verify the X handle is public and the Twikit session is valid.
- No Discord message: verify View Channel, Send Messages, Embed Links, and Manage Webhooks permissions in the selected channel.
- X reports that credential login was blocked: do not retry repeatedly. Use the browser-session cookie fallback described above; this does not by itself mean the username or password is wrong.
- A saved X session is invalid: replace only the local cookie file with a fresh browser session. Never paste cookie contents into logs or issues.
- Rate limits or temporary X errors: keep the default 60-second interval or increase it. The watcher retries on later cycles without crashing other feeds.
- Service problems: inspect `systemctl status discord-x-feed` and `journalctl -u discord-x-feed -f`.
