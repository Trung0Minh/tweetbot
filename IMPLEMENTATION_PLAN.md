# Discord X Feed Bot — Implementation Plan

## 1. Project Goal

Build a lightweight Discord bot that continuously monitors selected public X/Twitter accounts and posts links to newly published posts into configured Discord channels.

The bot is intentionally small in scope.

Core flow:

```text
Discord admin configures account(s)
        ↓
Bot polls X periodically
        ↓
New post detected
        ↓
Apply reply/repost rules
        ↓
Optionally ping a Discord role
        ↓
Send raw X post URL to configured channel
```

The bot must persist configuration and delivery state so restarts do not resend old posts.

---

## 2. Final Scope

Version 1 must support:

- Tracking multiple X accounts.
- Tracking the same X account in multiple Discord channels or servers.
- Periodic polling for new posts.
- Sending raw X post links to Discord.
- Optional repost forwarding.
- Optional Discord role ping.
- Ignoring replies.
- Treating quote posts as normal posts.
- Persistent SQLite storage.
- Deduplication.
- Restart-safe behavior.
- Slash-command management.
- Deployment on a Google Cloud Compute Engine VM.
- Automatic startup using `systemd`.

Version 1 must NOT include:

- Web dashboard.
- Official X API.
- Tweet posting.
- Likes, follows, DMs, bookmarks, or other X write actions.
- Keyword filters.
- Translation.
- Analytics.
- RSS.
- Custom rich embeds.
- Docker.
- Message-prefix commands.
- Complex caching.
- Microservices.

---

## 3. Technology Stack

Use:

- Python 3.12+
- `discord.py`
- `twikit`
- `aiosqlite`
- `python-dotenv`
- `asyncio`
- built-in `logging`
- SQLite

Deployment target:

- Google Cloud Compute Engine
- `e2-micro`
- Ubuntu
- standard persistent disk
- Python virtual environment
- `systemd`

Do not use Docker.

---

## 4. Discord Slash Commands

Implement exactly these four commands for v1:

### `/follow`

Add one or more X accounts to a Discord channel.

Parameters:

| Parameter | Type | Required | Default | Meaning |
|---|---|---:|---|---|
| `feed-source` | string | yes | — | One or more X handles, comma-separated |
| `channel` | Discord text channel | yes | — | Destination channel |
| `reposts` | boolean | no | `false` | Whether pure reposts/retweets are forwarded |
| `ping` | Discord role | no | none | Role to mention when a new post is forwarded |

Examples:

```text
/follow feed-source:@ufotable channel:#twitter-feed
```

```text
/follow feed-source:@ufotable,@MAPPA_Info channel:#anime-news reposts:true ping:@AnimeNews
```

Input normalization:

- Trim whitespace.
- Accept handles with or without `@`.
- Normalize usernames consistently.
- Remove duplicate handles case-insensitively.
- Reject empty entries.
- Resolve each account on X before saving it.

If multiple handles are supplied and some fail, use partial success.

Example response:

```text
Added:
@ufotable
@MAPPA_Info

Failed:
@invalid_name — account not found
```

Do not roll back successful accounts because another account failed.

If the same X account is already followed in the same Discord channel, `/follow` must UPDATE that existing subscription instead of creating a duplicate.

Example:

```text
/follow feed-source:@ufotable channel:#news reposts:true ping:@AnimeNews
```

updates the existing `@ufotable → #news` subscription.

When a feed is first added:

- Initialize the latest seen post.
- Do not send existing historical posts.
- Only posts published after the feed is added should be eligible for forwarding.

---

### `/unfollow`

Remove one or more account subscriptions from a channel.

Parameters:

| Parameter | Type | Required |
|---|---|---:|
| `feed-source` | string | yes |
| `channel` | Discord text channel | yes |

Example:

```text
/unfollow feed-source:@ufotable,@MAPPA_Info channel:#twitter-feed
```

Only remove matching subscriptions from the specified channel.

Do not remove subscriptions for the same X account in other channels or servers.

Use partial success if some supplied handles are not currently followed.

---

### `/follows`

Show subscriptions configured in the current Discord server.

Optional parameter:

| Parameter | Type | Required |
|---|---|---:|
| `channel` | Discord text channel | no |

Behavior:

- Without `channel`: show all feeds in the current server.
- With `channel`: show only subscriptions for that channel.

Example output:

```text
#twitter-feed

@ufotable
Reposts: No
Ping: @AnimeNews

@MAPPA_Info
Reposts: Yes
Ping: None
```

Prefer an ephemeral command response.

---

### `/status`

Show simple operational status.

Example:

```text
X watcher: Running
Tracked X accounts: 8
Discord subscriptions: 12
Polling interval: 60 seconds
Last successful poll: 21:42:13
```

This command is primarily for diagnostics.

---

## 5. Discord Permissions

Commands that modify configuration:

```text
/follow
/unfollow
```

must only be usable by members with either:

- Administrator
- Manage Server / Manage Guild

Read-only commands:

```text
/follows
/status
```

may be available to everyone.

Before saving a subscription, verify that the bot can use the selected destination channel.

At minimum check:

- channel exists
- bot can view channel
- bot can send messages

Do not require embed permission because the bot sends only raw links.

---

## 6. Post Forwarding Rules

The bot forwards:

- Normal posts: YES
- Quote posts: YES
- Replies: NO
- Pure reposts/retweets: controlled by the subscription's `reposts` option

Rules:

```text
normal post                 → send
quote post                  → send
reply                       → skip
pure repost + reposts=true  → send
pure repost + reposts=false → skip
```

The `reposts` default is `false`.

Replies are always ignored in v1 and have no command option.

---

## 7. Discord Message Format

Keep output intentionally simple.

Without role ping:

```text
https://x.com/username/status/123456789
```

With role ping:

```text
<@&ROLE_ID>
https://x.com/username/status/123456789
```

Do not create a custom Discord embed.

Do not copy tweet text into the Discord message.

Do not download or re-upload tweet media.

Let Discord/X handle link previews naturally.

---

## 8. X Integration Architecture

Do not let Twikit-specific objects leak into the rest of the application.

Define a small abstraction:

```python
class XService:
    async def resolve_user(self, username: str) -> XUser:
        ...

    async def get_recent_posts(self, user_id: str) -> list[XPost]:
        ...
```

Suggested internal models:

```python
@dataclass
class XUser:
    id: str
    username: str
```

```python
@dataclass
class XPost:
    id: str
    username: str
    created_at: datetime
    is_reply: bool
    is_repost: bool
    is_quote: bool

    @property
    def url(self) -> str:
        return f"https://x.com/{self.username}/status/{self.id}"
```

Create the implementation:

```text
TwikitXService
```

Responsibilities:

- authenticate to X
- resolve username → stable X user ID
- fetch recent posts
- normalize Twikit objects into `XPost`
- expose clean exceptions to callers

The rest of the bot must depend on `XService`, not directly on Twikit.

This is important because Twikit relies on X's web/internal APIs and may need replacement later.

Twikit currently exposes operations such as `get_user_by_screen_name` and `get_user_tweets`. Its published rate-limit reference lists `get_user_tweets[tweet_type="Tweets"]` separately, so polling frequency must remain configurable rather than aggressive.

---

## 9. X Authentication

Use a dedicated X account for the bot.

Do not use the user's primary X account unless explicitly desired later.

Authentication requirements:

- login once
- persist/reuse cookies or session state
- avoid logging in on every polling cycle
- store authentication files outside source control

Environment/config should support:

```env
X_USERNAME=
X_EMAIL=
X_PASSWORD=
X_COOKIES_PATH=data/x_cookies.json
```

Prefer loading a valid saved cookie/session first.

Only perform credential login if required.

Never print credentials or cookies in logs.

---

## 10. Polling Strategy

Use one central polling task.

Default:

```env
POLL_INTERVAL_SECONDS=60
```

The interval must be configurable through `.env`.

Do NOT create one infinite task per Discord subscription.

Instead:

```text
load unique tracked X users
        ↓
fetch each X account once
        ↓
detect unseen posts
        ↓
fan out each new post to all subscriptions
```

Example:

```text
@ufotable
├── Server A → #news
├── Server A → #sakuga
└── Server B → #twitter
```

`@ufotable` must be fetched once per polling cycle, not three times.

Pseudo-flow:

```python
while running:
    tracked_users = await db.get_tracked_users()

    for user in tracked_users:
        try:
            posts = await x_service.get_recent_posts(user.x_user_id)
            await process_user_posts(user, posts)
        except Exception:
            log_exception_for_user()
            continue

    await sleep_until_next_cycle()
```

---

## 11. Detecting New Posts

Each tracked account has persistent watcher state.

Store:

```text
last_seen_post_id
```

On each poll:

1. Fetch a small recent batch.
2. Find the previously stored `last_seen_post_id`.
3. Collect newer posts.
4. Sort them from oldest to newest.
5. Process them in chronological order.
6. Advance watcher state after processing.

Example:

API returns:

```text
105
104
103
102
```

Stored state:

```text
102
```

Delivery order must be:

```text
103
104
105
```

not newest-first.

If no `last_seen_post_id` exists because this is the initial `/follow`, initialize it from the newest current post and send nothing.

Do not assume numeric comparison alone is sufficient for every retrieval edge case. Prefer locating the saved ID in the returned recent history when possible.

---

## 12. Handling Gaps

Polling can fail temporarily.

If the account publishes multiple posts between successful checks, the next successful poll should forward every unseen post available in the fetched recent batch.

Fetch enough recent items to tolerate normal downtime.

Do not fetch the full account history.

If the previous marker is no longer present in the recent batch:

- log a warning
- process only clearly unseen items if this can be determined safely
- never intentionally spam old historical posts
- avoid resetting the account to an ancient timeline position

Correctness priority:

```text
avoid duplicates/spam > recover every possible missed historical post
```

---

## 13. Database

Use SQLite through `aiosqlite`.

Suggested database path:

```env
DATABASE_PATH=data/bot.db
```

### `subscriptions`

```sql
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,

    x_user_id TEXT NOT NULL,
    x_username TEXT NOT NULL,

    include_reposts INTEGER NOT NULL DEFAULT 0,
    ping_role_id INTEGER,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    UNIQUE(guild_id, channel_id, x_user_id)
);
```

### `tracked_users`

```sql
CREATE TABLE IF NOT EXISTS tracked_users (
    x_user_id TEXT PRIMARY KEY,
    x_username TEXT NOT NULL,

    last_seen_post_id TEXT,
    last_checked_at TEXT,
    last_successful_poll_at TEXT
);
```

### `sent_posts`

```sql
CREATE TABLE IF NOT EXISTS sent_posts (
    subscription_id INTEGER NOT NULL,
    post_id TEXT NOT NULL,
    sent_at TEXT NOT NULL,

    PRIMARY KEY(subscription_id, post_id),

    FOREIGN KEY(subscription_id)
        REFERENCES subscriptions(id)
        ON DELETE CASCADE
);
```

Enable foreign keys:

```sql
PRAGMA foreign_keys = ON;
```

Consider WAL mode:

```sql
PRAGMA journal_mode = WAL;
```

---

## 14. Why Both `last_seen_post_id` and `sent_posts` Exist

They solve different problems.

`last_seen_post_id`:

- efficient watcher cursor
- avoids reprocessing the same timeline repeatedly

`sent_posts`:

- delivery-level deduplication
- protects against restart or partial failure
- allows one post to be delivered independently to multiple subscriptions

Uniqueness is:

```text
subscription_id + post_id
```

A post sent to channel A must still be allowed to be sent to channel B.

---

## 15. Delivery Transaction Behavior

For each new post:

1. Load subscriptions for that X user.
2. Apply per-subscription repost rules.
3. Skip replies globally.
4. Check `sent_posts`.
5. Send to Discord.
6. Only after Discord confirms success, insert `sent_posts`.

Pseudo-flow:

```python
for subscription in subscriptions:
    if post.is_reply:
        continue

    if post.is_repost and not subscription.include_reposts:
        continue

    if await db.was_sent(subscription.id, post.id):
        continue

    await discord_sender.send(subscription, post)

    await db.mark_sent(subscription.id, post.id)
```

Never mark a post as delivered before the Discord send succeeds.

---

## 16. Advancing Watcher State

Do not allow one broken Discord channel to permanently block the entire X account watcher.

After the new batch has been considered, watcher state may advance even if an individual Discord subscription failed to deliver.

The failed subscription must remain absent from `sent_posts`, allowing controlled retry logic later if implemented.

For v1, delivery retry may happen on the next poll only if the post is still inside the fetched recent window.

Log failed deliveries clearly.

Avoid infinite blocking caused by:

- deleted channel
- missing permissions
- removed bot
- invalid role

---

## 17. `/follow` Detailed Behavior

For each supplied username:

1. Normalize the handle.
2. Resolve it using `XService`.
3. Obtain stable X user ID and canonical username.
4. Verify destination Discord channel.
5. Check for existing subscription.
6. Insert or update the subscription.
7. Ensure `tracked_users` contains this X user.
8. If this X user has never been tracked:
   - fetch current recent posts
   - save newest post as `last_seen_post_id`
   - send nothing
9. Return success/failure for that handle.

Important edge case:

If `@foo` is already tracked in another channel, do NOT reset the global watcher cursor when adding a second subscription.

The new subscription should start from the moment it is added.

To guarantee the new channel does not receive older unseen posts that existed before its creation, record an appropriate subscription creation boundary or initialize sent/delivery behavior accordingly.

Recommended implementation:

- add `start_after_post_id` to each subscription, OR
- store creation time and skip posts older than that boundary.

Preferred explicit schema addition:

```sql
ALTER TABLE subscriptions
ADD COLUMN start_after_post_id TEXT;
```

When creating a new subscription:

- set `start_after_post_id` to the newest current post for that account
- do not deliver that post
- only future posts are eligible for that subscription

This avoids historical backfill when the same X account was already globally tracked.

---

## 18. `/unfollow` Detailed Behavior

For each supplied username:

1. Normalize handle.
2. Match the account against subscriptions in:
   - current guild
   - specified channel
3. Delete the matching subscription.
4. Report success or not-followed status.

After deletion:

- if no subscriptions anywhere reference that X user anymore, delete it from `tracked_users`
- otherwise keep global watcher state

Do not affect other subscriptions.

---

## 19. Username Changes

Store both:

```text
x_user_id
x_username
```

Use stable X user ID internally whenever possible.

If X reports a changed canonical username:

- update cached `x_username`
- continue using the same stable user ID
- generate future URLs using the current username returned by the X adapter

Do not require users to recreate the subscription because of a username change.

---

## 20. Suspended / Deleted / Temporarily Unavailable Accounts

If an account fetch fails:

- do not delete the subscription
- log the error
- continue processing other accounts
- retry on a future polling cycle

One failing X account must never kill the watcher.

---

## 21. X Rate Limits / Temporary Blocking

Twikit is not the official X API.

Expect:

- endpoint changes
- authentication failures
- temporary rate limits
- X-side behavior changes

Requirements:

- catch rate-limit-related errors
- back off rather than tight-loop retry
- never retry repeatedly within milliseconds/seconds
- preserve bot process health
- log enough information to diagnose failures
- keep polling interval configurable

Do not build aggressive scraping behavior.

---

## 22. Logging

Use Python `logging`.

Recommended events:

```text
Bot starting
Database initialized
Discord connected
Slash commands synced
X session loaded
Watcher started
Polling @username
Detected N new posts for @username
Skipped reply
Skipped repost
Sent post <id> to guild/channel
Failed sending post <id>
X fetch failed for @username
Rate limited
Feed added
Feed updated
Feed removed
Bot shutting down
```

Never log:

- Discord token
- X password
- X cookies
- full secret environment values

---

## 23. Configuration

`.env.example`:

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

Configuration should be loaded once into a typed settings object or dataclass.

Fail fast during startup if required configuration is missing.

---

## 24. Suggested Project Structure

Keep the codebase small but separated by responsibility.

```text
discord-x-feed-bot/
├── main.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── AGENTS.md
│
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── logging_config.py
│   │
│   ├── discord_bot.py
│   ├── commands.py
│   │
│   ├── models.py
│   │
│   ├── db.py
│   │
│   ├── x_service.py
│   ├── twikit_service.py
│   │
│   ├── watcher.py
│   └── delivery.py
│
├── tests/
│   ├── test_username_parser.py
│   ├── test_filters.py
│   ├── test_deduplication.py
│   └── test_watcher.py
│
├── deploy/
│   └── discord-x-feed.service
│
└── data/
    └── .gitkeep
```

Do not split every class into a separate file.

Do not introduce repository/service/factory abstractions unless they clearly simplify the code.

---

## 25. Dependencies

Keep dependencies minimal.

Example `requirements.txt`:

```text
discord.py
twikit
aiosqlite
python-dotenv
```

A testing dependency such as `pytest` may be added for development.

Pin versions after implementation has been tested successfully.

---

## 26. `.gitignore`

At minimum:

```gitignore
.env

__pycache__/
*.py[cod]

.venv/
venv/

.pytest_cache/

data/*.db
data/*.sqlite
data/*.sqlite3
data/x_cookies.json

*.log
```

Keep:

```text
data/.gitkeep
```

---

## 27. Startup Sequence

Expected application startup:

```text
load environment
        ↓
configure logging
        ↓
open/init SQLite
        ↓
initialize X service
        ↓
restore/load X session
        ↓
initialize Discord bot
        ↓
connect to Discord
        ↓
sync slash commands
        ↓
start watcher
```

Run locally with:

```bash
python main.py
```

---

## 28. Graceful Shutdown

Handle:

- `SIGINT`
- `SIGTERM`

Shutdown sequence:

```text
signal received
    ↓
stop scheduling new polls
    ↓
allow active operation to exit safely
    ↓
close X client if necessary
    ↓
close SQLite connection
    ↓
close Discord client
    ↓
exit
```

This matters because Google Cloud/systemd may stop or restart the process.

---

## 29. Tests

At minimum implement tests for the following logic.

### Username parsing

Input:

```text
@foo, bar,@baz
```

Expected normalized result:

```text
foo
bar
baz
```

---

### Duplicate usernames

Input:

```text
foo,@foo,FOO
```

Expected:

```text
foo
```

only once.

---

### Repost filtering

```text
include_reposts = false
post.is_repost = true
```

Expected:

```text
skip
```

---

### Quote handling

```text
post.is_quote = true
post.is_reply = false
```

Expected:

```text
send
```

---

### Reply handling

```text
post.is_reply = true
```

Expected:

```text
skip
```

regardless of repost settings.

---

### Chronological delivery

API returns:

```text
103
102
101
```

Expected delivery:

```text
101
102
103
```

---

### Delivery deduplication

Same:

```text
subscription_id + post_id
```

must never create two successful sends.

---

### Multi-channel fanout

One X fetch should be able to produce messages for multiple subscriptions without performing multiple X fetches.

---

### Restart safety

Existing `sent_posts` and watcher state must prevent duplicate notifications after process restart.

---

### New subscription boundary

If an X account is already tracked globally and a second channel subscribes later:

- old posts must NOT be sent to the new channel
- only posts after that subscription was created are eligible

---

## 30. Google Cloud Deployment

Target Google Cloud Compute Engine.

Current Google Cloud Free Tier documentation provides an eligible `e2-micro` VM allowance in specific US regions plus standard persistent disk allowance. Use an eligible free-tier region when creating the VM.

Recommended VM:

```text
Machine type: e2-micro
OS: Ubuntu LTS
Disk: standard persistent disk
Region: an eligible Google Cloud Free Tier region
```

Do not expose any application port publicly because this bot does not need an inbound HTTP server.

Firewall:

- SSH only as needed
- no custom application port

The VM only needs outbound access to:

- Discord
- X/Twitter

---

## 31. VM Installation

Typical deployment flow:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

Clone project:

```bash
git clone <repo-url>
cd discord-x-feed-bot
```

Create environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create configuration:

```bash
cp .env.example .env
nano .env
```

Run manually first:

```bash
python main.py
```

Verify:

- Discord bot connects
- commands appear
- X authentication works
- `/follow` succeeds
- new posts are forwarded
- restart does not duplicate posts

Only after manual verification configure `systemd`.

---

## 32. systemd

Create:

```text
/etc/systemd/system/discord-x-feed.service
```

Example:

```ini
[Unit]
Description=Discord X Feed Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=BOT_USER
WorkingDirectory=/home/BOT_USER/discord-x-feed-bot
ExecStart=/home/BOT_USER/discord-x-feed-bot/.venv/bin/python main.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Replace `BOT_USER` correctly during deployment.

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now discord-x-feed
```

Status:

```bash
systemctl status discord-x-feed
```

Logs:

```bash
journalctl -u discord-x-feed -f
```

The bot must automatically start after VM reboot.

---

## 33. Security

Requirements:

- Never commit `.env`.
- Never commit X cookie/session files.
- Never commit Discord token.
- Prefer a dedicated X account.
- Prefer a dedicated non-root Linux user for the bot.
- Keep file permissions restrictive for `.env` and cookie files.
- Do not expose an HTTP server just to keep the bot alive.
- Do not run the application as root.

---

## 34. README Requirements

The generated `README.md` should include:

1. What the bot does.
2. Feature list.
3. Slash commands.
4. Local installation.
5. Discord bot/application setup.
6. Required Discord permissions.
7. X/Twikit authentication setup.
8. `.env` configuration.
9. Running locally.
10. Google Cloud deployment.
11. `systemd` management.
12. Troubleshooting.
13. Important note that Twikit uses unofficial X web/internal APIs and may break when X changes behavior.

Keep README practical rather than verbose.

---

## 35. Implementation Order

Implement in this order.

### Phase 1 — Foundation

- project structure
- configuration
- logging
- SQLite initialization
- internal models

### Phase 2 — X Adapter

- Twikit authentication/session persistence
- username resolution
- recent-post retrieval
- normalize normal/reply/repost/quote state
- build canonical post URL

Test this independently before Discord integration.

### Phase 3 — Discord Commands

Implement:

- `/follow`
- `/unfollow`
- `/follows`
- `/status`

Implement permissions and input parsing.

### Phase 4 — Watcher

- unique-user polling
- cursor handling
- chronological ordering
- reply filtering
- fanout to subscriptions

### Phase 5 — Delivery Safety

- `sent_posts`
- restart safety
- partial failures
- new-subscription boundary behavior

### Phase 6 — Tests

Implement the tests defined above.

### Phase 7 — Deployment

- README deployment section
- systemd unit
- Google Cloud VM instructions

---

## 36. Acceptance Test

The product is complete when this scenario works end-to-end.

Admin executes:

```text
/follow
feed-source: @ufotable,@MAPPA_Info
channel: #twitter-feed
reposts: false
ping: @AnimeNews
```

Bot confirms both accounts.

No old posts are sent.

Later `@ufotable` publishes a normal post:

```text
https://x.com/ufotable/status/123456789
```

Within approximately one polling interval, Discord receives:

```text
@AnimeNews
https://x.com/ufotable/status/123456789
```

If the bot restarts:

- that post is NOT sent again

If `@ufotable` replies to another user:

- nothing is sent

If `@ufotable` posts a quote:

- the quote post link is sent

If `@ufotable` makes a pure repost while:

```text
reposts = false
```

- nothing is sent

After updating the subscription using:

```text
/follow feed-source:@ufotable channel:#twitter-feed reposts:true ping:@AnimeNews
```

future pure reposts are also forwarded.

The same X account may simultaneously feed another channel without duplicate X fetching.

---

## 37. Definition of Done

Version 1 is done when:

- all four slash commands work
- the watcher runs continuously
- new posts reach Discord
- replies are ignored
- repost preference works
- quote posts work
- optional role ping works
- multiple accounts work
- multiple channels/servers work
- one account is fetched once per poll cycle
- SQLite state survives restart
- duplicate sends are prevented
- adding a feed never backfills old posts
- failures for one account/channel do not crash the bot
- secrets are not committed
- bot runs automatically under `systemd` on Google Cloud
- tests for core logic pass

Do not add features beyond this scope without explicit instruction.
