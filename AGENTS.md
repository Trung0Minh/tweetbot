# AGENTS.md

## Project

You are implementing a small Discord bot that monitors configured public X/Twitter accounts and forwards links to new posts into Discord channels.

Read `IMPLEMENTATION_PLAN.md` completely before changing code.

The implementation plan is the source of truth.

## Core Product Rule

Keep the project intentionally simple.

The core job is only:

```text
monitor X account
→ detect new post
→ apply filters
→ send post embeds (or a link fallback) to Discord
```

Do not turn this into a general social-media platform.

## Fixed Decisions

Do not revisit these choices unless explicitly instructed:

- Language: Python 3.12+
- Discord library: `discord.py`
- X integration: Twikit
- Database: SQLite via `aiosqlite`
- Hosting: Google Cloud Compute Engine
- Process management: `systemd`
- No Docker
- Poll interval default: 60 seconds
- Replies: always ignored
- Quote posts: forwarded
- Pure reposts: configurable per subscription
- Reposts default: off
- Output: custom embeds (post text + stacked images); video, quote and repost posts fall back to a `[Tweeted](fxtwitter link)` link
- Optional Discord role ping
- No historical backfill after `/follow`
- Persistent deduplication across restarts
- One X fetch per unique tracked account per polling cycle
- A failed account/channel must not crash the entire watcher

## Slash Commands

Only these commands belong in v1:

```text
/follow
/unfollow
/follows
/status
```

Do not add aliases, prefix commands, dashboards, or extra admin commands.

## `/follow`

Options:

```text
feed-source : string, required
channel     : Discord text channel, required
reposts     : boolean, optional, default false
ping        : Discord role, optional
```

`feed-source` accepts comma-separated handles.

Calling `/follow` again for an existing account/channel pair updates the existing configuration.

Multiple handles use partial success.

## Permissions

Only Administrator or Manage Guild users may use:

```text
/follow
/unfollow
```

## Architecture

Keep Twikit behind an `XService` abstraction.

Application logic must not depend directly on raw Twikit models.

Keep Discord commands, X fetching, persistence, watcher logic, and message delivery separated enough to test independently.

Do not over-engineer with unnecessary repositories, factories, dependency-injection frameworks, message buses, microservices, or generic plugin systems.

## Database

Persist:

- subscriptions
- tracked-user watcher cursor
- per-subscription sent-post deduplication
- a new-subscription boundary so adding a channel does not backfill old posts

Never rely only on in-memory state for correctness.

## Correctness Priorities

In order:

1. Never spam historical posts.
2. Never duplicate successful deliveries.
3. Never crash the whole watcher because one source/destination fails.
4. Preserve chronological ordering of multiple new posts.
5. Minimize X requests by fetching each unique account once per polling cycle.
6. Keep the code easy to understand.

## X/Twikit

Twikit uses unofficial X web/internal APIs.

Assume X can change behavior.

Therefore:

- persist/reuse session cookies
- avoid logging in every poll
- handle rate limits and temporary failures
- avoid aggressive retries
- keep polling configurable
- isolate Twikit-specific parsing

Do not implement any X write action.

## Discord Output

Text and image posts: embed 1 holds the author line (linking to the post), the post text and the timestamp; embeds 2..N hold one image each (max 4), referenced by `pbs.twimg.com` URL and without a `url` so Discord does not merge them into a cropped grid. With a ping, the message content is `<@&ROLE_ID>`, otherwise empty.

Posts with a video or GIF, quote posts and pure reposts fall back to `[Tweeted](https://fxtwitter.com/username/status/POST_ID)` (prefixed by `<@&ROLE_ID>` and a newline when pinging).

Do not download or re-upload media.

Do not add colors, footers or fields to embeds.

## Development Workflow

Implement in small phases.

After each phase:

- run tests
- fix failures
- verify no regression
- keep code formatted and readable

Before declaring completion:

- run the full test suite
- review `.gitignore`
- verify no secret is committed
- verify restart behavior
- verify new-subscription no-backfill behavior
- verify systemd deployment files
- verify README instructions match actual commands

## Testing

Do not mock every internal function.

Prioritize tests around observable behavior:

- username parsing
- duplicate handles
- reply/repost/quote filtering
- chronological ordering
- fanout
- deduplication
- restart safety
- new-subscription boundary
- partial failure isolation

Twikit network tests may be separated from deterministic unit tests.

## Secrets

Never commit or print:

- Discord bot token
- X password
- X cookies
- `.env`

Use `.env.example` with blank values only.

## Scope Control

If something is not required by `IMPLEMENTATION_PLAN.md`, do not add it merely because it might be useful.

In particular do not add:

- dashboard
- RSS
- translation
- keyword filtering
- analytics
- official X API
- Docker
- Redis
- PostgreSQL
- web server
- health-check HTTP endpoint
- media proxying

The best implementation is the smallest reliable implementation that satisfies the plan.
