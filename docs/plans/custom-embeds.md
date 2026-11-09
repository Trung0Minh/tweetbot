# Plan: Custom Discord embeds for forwarded posts

## Goal

Stop relying on the FXTwitter link preview (which crops multi-image posts into a 2×2 grid).
The bot builds its own embeds so every image shows uncropped, stacked vertically, in ONE message.

User decisions (final, do not revisit):

- Applies to **every** delivered post (layout "B").
- Layout B: embed 1 = text only (no image); embeds 2..N = one image each.
- Posts with a video or animated GIF, quote posts, and pure reposts **fall back** to the
  current output (`[Tweeted](https://fxtwitter.com/<user>/status/<id>)`), unchanged.
- Images are referenced by URL from `pbs.twimg.com`. No downloading or re-uploading media.

Target message (no ping → `content` is empty/None; ping → `content` is `<@&ROLE_ID>` only):

```
[embed 1] author "@username" (links to post) · description = post text · timestamp = created_at
[embed 2] image 1
[embed 3] image 2
...
```

Discord merges embeds that share the same `url` into a cropped grid. Image embeds therefore
must NOT set `url` (and must not share the text embed's url). Only embed 1 links to the post.

## Rule changes (docs)

This intentionally overrides these fixed decisions in `AGENTS.md` / `IMPLEMENTATION_PLAN.md`:
"Output: raw X link only", "No custom embeds", "Do not copy post text", "custom embed rendering"
in the scope-control list. Update those files to describe the new behavior (keep the
"no media download/proxy" rule). Update `README.md` output section and the sentence about
FXTwitter previews. Embed Links permission is now required (README already lists it).

## Task 1 — X data (models + Twikit parsing)

Files: `app/models.py`, `app/twikit_service.py`, `tests/test_twikit_service.py`, `tests/conftest.py`.

1. Add to `XPost` (with defaults so existing constructors keep working):
   - `text: str = ""`
   - `image_urls: tuple[str, ...] = ()`
   - `has_video: bool = False` (true if any media is `video` or `animated_gif`)
2. In `TwikitXService.get_recent_posts`, fill them. Keep all Twikit-specific parsing in
   `twikit_service.py` (small private helper functions are fine).
   - **Media gotcha:** Twikit 2.3.3 `Tweet.media` reads `legacy.entities.media`, which X fills
     with only the FIRST media item. Read `tweet._data["legacy"]["extended_entities"]["media"]`
     first, fall back to `legacy.entities.media`. For each item: `type` and `media_url_https`.
     Photo URL → append `?name=large` (full image, up to 2048px).
   - Text: `tweet.full_text` (handles long "note" tweets). Remove the trailing t.co media link
     (each media item's `url` field) from the text and strip whitespace. Leave other t.co links.
     Unescape HTML entities (`&amp;` etc.) with `html.unescape`.
   - **Quote gotcha:** the legacy `is_quote_status` flag can be absent; also check
     `quoted_status_result`. Use `tweet._data["legacy"].get("is_quote_status")` or presence of
     `quoted_status_result`. Quote filtering behavior (quotes forwarded) is unchanged.
   - Missing/malformed media data for one post must not fail the whole timeline: treat as no
     media. Existing `XServiceError` handling for truly invalid timeline data stays.
3. Extend `post_factory` in `tests/conftest.py` to accept the new fields.

Done when: tests cover 4-photo extended_entities, entities-only fallback, video → `has_video`,
media t.co link stripped from text, note-tweet text, quote detection true/false, and existing tests pass.

## Task 2 — Delivery (embeds)

Files: `app/delivery.py`, `tests/test_delivery.py`.

1. Add a pure function (testable without Discord I/O), e.g.
   `build_message(post, ping_role_id) -> tuple[str | None, list[discord.Embed] | None]`
   (content, embeds). `embeds is None` means fallback link mode.
2. Fallback (content = current `[Tweeted](fxtwitter...)` with optional ping prefix, no embeds) when
   `post.has_video or post.is_quote or post.is_repost`.
3. Otherwise:
   - Embed 1: `description` = text truncated to 4096 chars (append `…` when cut),
     author name `@username` with author `url` = `post.url`-style link
     `https://x.com/<username>/status/<id>`, author icon = avatar (only if it passes the existing
     `pbs.twimg.com`/`abs.twimg.com` check), `timestamp` = `created_at`. If text is empty, keep
     embed 1 anyway (author line only).
   - Embeds 2..: `discord.Embed()` with `set_image(url=...)`, no `url`, max 4 images
     (Discord limit is 10 embeds; X allows 4 photos).
   - Content: `<@&ROLE_ID>` when ping is set, else `None`.
4. `DiscordSender.send` passes `embeds=` (when not None) to `webhook.send`. Keep webhook
   username/avatar, allowed_mentions, webhook caching and `NotFound` handling unchanged.
5. No color, footer, fields or other decoration.

Done when: tests cover text-only post (1 embed), 4 images (5 embeds, image embeds have no url),
ping content, video fallback, quote fallback, repost fallback, long text truncation,
non-X avatar dropped; existing delivery tests updated and passing.

## Task 3 — Docs

Files: `AGENTS.md`, `IMPLEMENTATION_PLAN.md`, `README.md`. Update as described in "Rule changes".
No code. Keep edits minimal and accurate to the implemented behavior.

## Out of scope

No DB/schema changes (post data flows in memory from fetch to send in the same cycle).
No media download, no video embedding, no rendering of the quoted post, no new commands/config.

## Verification

- `pytest` full suite passes.
- Manual: follow an account in a test channel, confirm a 4-image post renders as 5 stacked
  uncropped embeds and a video post still shows the FXTwitter link.

## Task 4 — Expand t.co links in post text (follow-up)

Files: `app/twikit_service.py` (`_text_and_media`), `tests/test_twikit_service.py`.

- Replace each non-media t.co link in the text with its real URL (`expanded_url`).
- Source of the url → expanded_url mapping: for long "note" posts use
  `_data["note_tweet"]["note_tweet_results"]["result"]["entity_set"]["urls"]`; otherwise
  `_data["legacy"]["entities"]["urls"]`. Each entry has `url` and `expanded_url`.
- Media t.co links are still removed (unchanged). Unknown/malformed url entities: leave the
  text as-is for that link; never fail the timeline.
- Done when: tests cover a normal post and a note post with an expanded link, a media link
  still removed, and the full suite + ruff pass.
