# Project Knowledge

This file stores durable project context so future work can update known facts instead of repeating full discovery.

Last full external research refresh: `2026-04-21 UTC`

Update rule:
- Re-validate external facts when MAX docs, official SDKs, or user-account protocol behavior change.
- Prefer appending a new dated section over rewriting history.

## [2026-04-21] MAX Ecosystem Snapshot
- **Decision:** Treat MAX integration as two separate surfaces: `official partner platform` and `unofficial user-account / WebSocket`.
- **Reason:** Publicly documented official capabilities and reverse-engineered user capabilities are materially different and should not be mixed in planning.
- **Context:** Research was done against official MAX developer docs, official GitHub org repositories, public GitHub reverse-engineering projects, and issue trackers current on `2026-04-21 UTC`.

## [2026-04-21] Official MAX Platform
- **Decision:** Consider the official public API to be the partner Bot API / Mini Apps / Channels platform, not a public user-account API.
- **Reason:** Official docs publicly expose bot and partner workflows on `dev.max.ru` and `platform-api.max.ru`, but no public official user-account WebSocket API was confirmed.
- **Context:**
  - Official docs root: `https://dev.max.ru/docs`
  - API docs root: `https://dev.max.ru/docs-api`
  - Long polling: `https://dev.max.ru/docs-api/methods/GET/updates`
  - Webhook subscriptions: `https://dev.max.ru/docs-api/methods/POST/subscriptions`
  - Subscription listing: `https://dev.max.ru/docs-api/methods/GET/subscriptions`
  - Message send: `https://dev.max.ru/docs-api/methods/POST/messages`
  - Upload flow: `https://dev.max.ru/docs-api/methods/POST/uploads`

## [2026-04-21] Official Access Restrictions
- **Decision:** Do not assume the official MAX platform is universally available for all users or use cases.
- **Reason:** Official docs state partner access is available to legal entities and individual entrepreneurs who are residents of the Russian Federation.
- **Context:**
  - Docs note partner onboarding restrictions on `dev.max.ru/docs`
  - Relevant partner connection page: `https://dev.max.ru/docs/maxbusiness/connection`
  - This means the official platform is not a drop-in replacement for user-account automation in `maxBridge`.

## [2026-04-21] Official GitHub Assets
- **Decision:** Track official GitHub repositories as the canonical signal for Bot API and UI ecosystem changes.
- **Reason:** The official `max-messenger` organization has public repos that reflect supported SDK and UI layers.
- **Context:**
  - `https://github.com/max-messenger/max-bot-api-client-ts`
    - TypeScript Bot API SDK
    - Observed on `2026-04-21`: `111★`, `36 forks`, updated `2026-04-17`
  - `https://github.com/max-messenger/max-bot-api-client-go`
    - Go Bot API SDK
    - Observed on `2026-04-21`: `75★`, `53 forks`, updated `2026-04-20`
  - `https://github.com/max-messenger/max-ui`
    - UI library for MAX ecosystem
    - Observed on `2026-04-21`: `46★`, `17 forks`, updated `2026-04-20`

## [2026-04-21] Unofficial User-Account Ecosystem
- **Decision:** Use the unofficial ecosystem as a research/reference layer, not as a trusted dependency layer without isolation.
- **Reason:** The strongest user-account integrations are reverse-engineered and subject to breakage when auth, protocol, or media flows change.
- **Context:**
  - `https://github.com/nsdkinx/vkmax`
    - Python user client for MAX / OneMe
    - Includes protocol notes and opcode docs
    - Observed on `2026-04-21`: `43★`, `14 forks`, updated `2026-04-09`
  - `https://github.com/MaxTeamAPI/PyMax`
    - Async Python wrapper for internal MAX user API
    - Strong recent interest, very young project
    - Observed on `2026-04-21`: `65★`, updated `2026-04-20`
  - `https://github.com/Ladvix/WebMax`
    - Async Python library for WebSocket MAX integration / userbots
    - Observed on `2026-04-21`: `31★`, `4 forks`, updated `2026-04-09`
  - `https://github.com/Aist/max2tg`
    - Practical MAX -> Telegram bridge with reply-back support
    - Useful architectural reference for `maxBridge`
    - Observed on `2026-04-21`: `23★`, `9 forks`, updated `2026-04-20`
  - `https://github.com/MrCatchParkington/go-max-client`
    - Unofficial Go WebSocket client with QR auth, media, groups, channels
    - Observed on `2026-04-21`: `3★`, updated `2026-04-18`
  - `https://github.com/weristvlad/max-fuck`
    - Deep reverse-engineering repo with QR, SMS, opcode map, APK notes
    - Useful as research, high production risk
    - Observed on `2026-04-21`: updated `2026-03-27`
  - `https://github.com/mochensky/max-user-api`
    - Early-stage Python user API library
    - README explicitly marks it unstable and experimental

## [2026-04-21] Auth Reality For User-Account Integrations
- **Decision:** Treat `QR-first auth` as the default strategy for web-like unofficial clients.
- **Reason:** Public evidence indicates that phone/SMS login for `WEB`-style flows is restricted or no longer reliable, while QR remains the stable path for web-like sessions.
- **Context:**
  - `vkmax` issues indicate web login changed toward QR and away from old phone-based flow:
    - `https://github.com/nsdkinx/vkmax/issues/23`
    - `https://github.com/nsdkinx/vkmax/issues/24`
    - `https://github.com/nsdkinx/vkmax/issues/27`
  - `PyMax` README explicitly separates auth by `device_type`:
    - `DESKTOP` for phone-number-based auth
    - `WEB` for QR-style auth
  - `max-fuck` README also documents QR-first behavior and a separate SMS path via a different transport.

## [2026-04-21] Protocol Volatility
- **Decision:** Isolate MAX transport logic behind a compatibility layer and versioned assumptions.
- **Reason:** Reverse-engineered user-account protocol is volatile and may change without notice.
- **Context:**
  - `vkmax` publishes protocol/opcode docs but maintainers also discuss native clients moving toward a custom binary protocol:
    - `https://github.com/nsdkinx/vkmax/blob/main/docs/protocol.md`
    - `https://github.com/nsdkinx/vkmax/issues/27`
  - `max-fuck` README explicitly warns that the server may update the protocol at any time and disconnect on validation errors.

## [2026-04-21] Confirmed Integration Pain Points
- **Decision:** Assume auth, reconnect, media, and docs drift are the main failure domains for MAX integrations.
- **Reason:** These are repeatedly confirmed across official Bot SDK issue trackers and unofficial user-account projects.
- **Context:**
  - Reconnect / stuck polling / network recovery problems:
    - Official TS SDK:
      - `https://github.com/max-messenger/max-bot-api-client-ts/issues/238`
      - `https://github.com/max-messenger/max-bot-api-client-ts/issues/240`
      - `https://github.com/max-messenger/max-bot-api-client-ts/issues/224`
    - Unofficial user client:
      - `https://github.com/nsdkinx/vkmax/issues/19`
  - Channel vs webhook / update delivery edge cases:
    - `https://github.com/max-messenger/max-bot-api-client-ts/issues/237`
  - Media upload / attachment readiness problems:
    - `https://github.com/max-messenger/max-bot-api-client-ts/issues/226`
    - `https://github.com/max-messenger/max-bot-api-client-ts/issues/231`
    - `https://github.com/max-messenger/max-bot-api-client-ts/issues/182`
  - Docs and SDK/runtime mismatch:
    - `https://github.com/max-messenger/max-bot-api-client-ts/issues/219`
    - `https://github.com/max-messenger/max-bot-api-client-ts/issues/221`
    - `https://github.com/max-messenger/max-bot-api-client-ts/issues/225`
    - `https://github.com/max-messenger/max-bot-api-client-ts/issues/243`
  - Fingerprinting / anti-bot sensitivity signals:
    - `https://github.com/nsdkinx/vkmax/issues/20`

## [2026-04-21] What Was NOT Reliably Confirmed
- **Decision:** Do not state VPN blocking as a confirmed MAX policy without new evidence.
- **Reason:** Public sources reviewed on this date showed regional and fingerprint-related friction, but not strong enough proof of a formal anti-VPN policy.
- **Context:**
  - Confirmed: partner access restrictions, auth churn for `WEB`, protocol volatility, reconnect/media problems
  - Not confirmed strongly enough: explicit VPN-ban policy and a complete public matrix of phone-region acceptance for user-account auth

## [2026-04-21] Implications For maxBridge
- **Decision:** Keep user-account integration, but treat it as a volatile adapter rather than a stable platform contract.
- **Reason:** Official MAX APIs do not publicly replace the current `maxBridge` use case, but the unofficial user-account surface is too unstable to embed directly into business logic without isolation.
- **Context:**
  - `maxBridge` should move toward:
    - `QR-first` auth for web-style flow
    - Configurable `device_type`, `app_version`, and user-agent assumptions
    - Strong reconnect/watchdog logic
    - Media pipeline isolation with per-type fallback/retry behavior
    - Protocol capability checks and versioned opcode assumptions
    - Continuous monitoring of official and unofficial upstreams before major changes

## [2026-04-21] Error Notifications To Telegram
- **Decision:** Start Telegram notifications before account connection and mirror `ERROR` / `CRITICAL` logs from the `maxbridge` logger tree into the Telegram channel.
- **Reason:** Startup failures such as expired MAX auth tokens were previously logged before Telegram forwarder startup, so operators could miss them entirely.
- **Context:**
  - `TelegramForwarder` now exposes a dedicated alert path for system errors, separate from MAX message forwarding.
  - `MaxBridgeDaemon.start()` starts Telegram first, attaches a logging handler, and only then runs `connect_all()`.
  - Unhandled asyncio loop exceptions and top-level daemon exceptions are now logged through `maxbridge.main`, so they can also reach Telegram.
  - Forwarder self-errors are excluded from Telegram log mirroring to avoid recursion loops.

## [2026-04-21] QR Login Can Escalate To MAX Password Challenge
- **Decision:** Treat `passwordChallenge` after `LOGIN_BY_QR` as a normal second step of auth, not as an unknown protocol failure.
- **Reason:** Current MAX web-style auth may require the account password after QR scan before returning `tokenAttrs.LOGIN.token`.
- **Context:**
  - Observed live in `maxBridge` on `2026-04-21`: QR flow returned payload keys `tokenAttrs`, `passwordChallenge` and no ready login token.
  - Confirmed against current public implementations:
    - `PyMax` handles `passwordChallenge` after QR completion and then submits opcode `115` (`AUTH_LOGIN_CHECK_PASSWORD`).
    - `max-fuck` also maps password auth to opcode `115` for QR/SMS second-factor completion.
  - `maxBridge` now:
    - detects `passwordChallenge` explicitly
    - requests MAX password in Telegram personal chat
    - attempts to delete the entered password message and the prompt after use
    - refuses secret input in channel chats and instructs the operator to continue in DM

## [2026-08-05] TUI QR Login Handles MAX Password Challenge
- **Decision:** Route TUI QR completion through the shared auth flow and treat `passwordChallenge` as an interactive second step.
- **Reason:** The TUI previously extracted the login token directly, swallowed the resulting password-challenge exception, and returned to the sessions screen without explaining the failure.
- **Behavior:**
  - after QR scan, the TUI replaces the QR with a masked MAX password field
  - the password is submitted through opcode `115` and is never logged
  - the session is saved only after MAX returns a login token
  - QR expiry, password rejection, and other completion failures remain visible until the operator dismisses the screen
  - the password prompt and final error fit in a standard `80x24` terminal

## [2026-04-21] Best External References For Future Updates
- **Decision:** Refresh these sources first before doing any new deep research.
- **Reason:** They are the highest-signal public sources found during the `2026-04-21` research pass.
- **Context:**
  - Official docs:
    - `https://dev.max.ru/docs`
    - `https://dev.max.ru/docs-api`
  - Official GitHub:
    - `https://github.com/max-messenger/max-bot-api-client-ts`
    - `https://github.com/max-messenger/max-bot-api-client-go`
    - `https://github.com/max-messenger/max-ui`
  - Unofficial high-signal repos:
    - `https://github.com/nsdkinx/vkmax`
    - `https://github.com/MaxTeamAPI/PyMax`
    - `https://github.com/Aist/max2tg`
    - `https://github.com/Ladvix/WebMax`
    - `https://github.com/MrCatchParkington/go-max-client`
    - `https://github.com/weristvlad/max-fuck`

## [2026-04-21] Official Bot API Endpoint Inventory
- **Decision:** Treat the following HTTP routes as the current high-value official surface for bot-side MAX integrations.
- **Reason:** Official SDK source on `2026-04-21` maps these routes directly against `https://platform-api.max.ru`.
- **Context:**
  - Bot profile:
    - `GET /me`
    - `PATCH /me`
  - Chats:
    - `GET /chats`
    - `GET /chats/{chat_id}`
    - `GET /chats/{chat_link}`
    - `PATCH /chats/{chat_id}`
    - `GET /chats/{chat_id}/members`
    - `POST /chats/{chat_id}/members`
    - `DELETE /chats/{chat_id}/members`
    - `GET /chats/{chat_id}/members/me`
    - `DELETE /chats/{chat_id}/members/me`
    - `GET /chats/{chat_id}/members/admins`
    - `GET /chats/{chat_id}/pin`
    - `PUT /chats/{chat_id}/pin`
    - `DELETE /chats/{chat_id}/pin`
    - `POST /chats/{chat_id}/actions`
  - Messages:
    - `GET /messages`
    - `GET /messages/{message_id}`
    - `POST /messages`
    - `PUT /messages`
    - `DELETE /messages`
    - `POST /answers`
  - Delivery:
    - `GET /updates`
  - Upload bootstrap:
    - `POST /uploads`

## [2026-04-21] Endpoint Status Inside maxBridge
- **Decision:** Do not replace the current unofficial user WebSocket endpoint, but do keep it configurable.
- **Reason:** On `2026-04-21 UTC`, three independent signals still matched:
  - live `maxBridge` token login succeeded against `wss://ws-api.oneme.ru/websocket`
  - current unofficial libraries `vkmax` and `PyMax` still target `wss://ws-api.oneme.ru/websocket`
  - official Bot SDK still targets `https://platform-api.max.ru` for bot HTTP, not for user-account login
- **Context:**
  - Current unofficial user-account surface:
    - WebSocket: `wss://ws-api.oneme.ru/websocket`
    - Origin: `https://web.max.ru`
  - Current official bot surface:
    - Base URL: `https://platform-api.max.ru`
  - Project implication:
    - keep the working user-account endpoint defaults
    - move transport and fingerprint assumptions to config so they can be refreshed without code edits

## [2026-04-21] Telegram Control Plane And PM2 Operations
- **Decision:** Run `maxBridge` under `pm2` and use a dedicated Telegram control bot as the operator control plane.
- **Reason:** MAX auth can expire silently, and operational recovery has to work without SSHing into the box or watching local logs continuously.
- **Context:**
  - Current bot commands implemented in `maxBridge`:
    - `/auth`
      - requests a fresh MAX QR code
      - prefers Telegram photo delivery of the QR
      - falls back to text QR / auth link if photo upload fails
    - `/status`
      - returns a human-readable health snapshot for daemon, Telegram, bridge, auth, errors, and attachment recovery
    - `/update`
      - returns a short runtime/message/error summary
  - Personal chat support is required:
    - operational commands are accepted from configured `allowed_chat_ids`
    - secret input such as MAX password is only accepted in personal chat, never in channel/group chats
  - QR re-auth flow details:
    - when MAX token/session becomes invalid, the control bot can request a new QR automatically or manually
    - if MAX returns `passwordChallenge` after QR scan, the operator enters the MAX password in Telegram DM
    - `maxBridge` attempts to delete both the password prompt and the operator reply after use
  - Runtime/deploy files now used:
    - `ecosystem.config.cjs` for PM2 process definition
    - `package.json` for `pm2:prod`, `pm2:restart`, `pm2:status`, `pm2:logs`, `pm2:save`
    - `deploy/maxbridge-pm2.user.service` for systemd user autostart of PM2 state
  - Repository hygiene:
    - bot token / chat IDs remain in `data/telegram.json`, which stays ignored by Git
    - local artifacts `.codex` and `.playwright-mcp/` should remain ignored

## [2026-08-13] Single Telegram Poller And Supervisor Ownership
- **Decision:** systemd and PM2 are mutually exclusive daemon owners. Manual `nohup` is
  permitted only when both are stopped; TUI and Commander do not own a poller.
- **PID invariant:** `daemon.pid_file` / `MAXBRIDGE_DAEMON_PID_FILE` is the application
  lock and is separate from supervisor-internal `PM2_HOME` state. systemd uses
  `/run/maxbridge/maxbridge.pid`; PM2 uses the repo-local data directory.
- **Polling invariant:** Telegram ownership is keyed only by bot identity and is local to
  one host and Linux network namespace. The lease carries no filesystem state or public
  identity material.
- **Conflict invariant:** Telegram HTTP 409 latches `external_conflict`, degrades only the
  control plane, and requires an operator-triggered restart after the external consumer is
  stopped. MAX runtime and forwarding stay available; no automatic reacquisition occurs.

## [2026-04-21] Delayed Attachment Recovery Strategy
- **Decision:** Treat delayed attachment readiness as a first-class bridge concern and process MAX `opcode 136` separately from normal `opcode 128` messages.
- **Reason:** Some MAX messages arrive before media becomes usable; without a second recovery path the bridge can forward text while silently losing the attachment.
- **Context:**
  - New runtime behavior:
    - router now handles both `Opcode.INCOMING_MESSAGE (128)` and `Opcode.UPLOAD_COMPLETE (136)`
    - if `136` already contains a complete `message`, it is transformed and published immediately
    - otherwise `maxBridge` extracts `chatId/messageId`, refetches recent history, and republishes the completed message if found
  - Diagnostics:
    - unresolved recovery attempts are no longer silent
    - sanitized payload shape is logged by `maxbridge.handlers.attachment`
    - runtime stats track:
      - delayed attachment notifications seen
      - successful reconciliations
      - unresolved attachment recoveries
    - `/status` exposes these counters in a dedicated `📎 Вложения` section
  - Important limitation:
    - a live production payload sample for every `136` variant has still not been captured
    - current implementation is intentionally defensive and designed to fail visible, not invisible

## [2026-04-21] Service Message Formatting For Telegram
- **Decision:** Humanize MAX `CONTROL`-only events before forwarding them to Telegram.
- **Reason:** Raw fallbacks like `📎 CONTROL` are operator-hostile and hide what actually happened in the chat.
- **Context:**
  - For `CONTROL` attachments without user text, `maxBridge` now emits a readable sentence like:
    - `Пользователь совершил(а) действие в <чат>: звонок`
  - Known events such as join/leave/call/pin are mapped to readable Russian labels.
  - Unknown control events fall back to normalized event names rather than the raw word `CONTROL`.

## [2026-05-17] Telegram Multi-Attachment Forwarding
- **Decision:** Forward every downloadable MAX attachment to Telegram instead of sending only the first media item and falling back to text placeholders.
- **Reason:** Operators were seeing placeholder messages such as `📎 UNSUPPORTED`, repeated `🖼 Фото`, or `🎬 Видео (236с)` instead of the actual attached files, especially when a MAX message contained several documents/media items.
- **Context:**
  - `TelegramForwarder` now collects all downloadable attachments from the message and forwarded-message payload.
  - `PHOTO` and direct `VIDEO` URLs are sent directly; `VIDEO`, `FILE`, `AUDIO`, and unknown document-like attachments with a `fileId` use the MAX download API before upload to Telegram.
  - The original message caption is attached only to the first successfully sent Telegram file to avoid duplicate captions.
  - If MAX provides attachment metadata without usable URL/file ID, maxBridge logs a warning with sanitized attachment keys so future payload variants can be supported without exposing secrets.

## [2026-05-20] MAX Video Download Payload Shape
- **Decision:** Use `videoId` rather than `fileId` for MAX opcode `83` video downloads and parse format URLs from the response payload.
- **Reason:** Production video attachments contained `_type=VIDEO`, numeric `videoId`, `token`, `thumbnail`, and no direct video URL. Sending opcode `83` with `fileId` produced no downloadable URL, causing Telegram alerts like `Не удалось отправить вложения: VIDEO`.
- **Context:**
  - Confirmed from live IPC history and `vkmax`: `DOWNLOAD_VIDEO` payload is `{chatId, messageId, videoId}`.
  - `DOWNLOAD_FILE` still uses `{chatId, messageId, fileId}`.
  - Video download responses can contain format keys instead of `payload.url`; ignore `cache` and `EXTERNAL`, then use the first concrete format URL.
