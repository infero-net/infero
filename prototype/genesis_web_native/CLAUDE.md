# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Genesis (Infero) v0.1 — a local-first digital life engine. Split-screen web app: chat console (left) + visual canvas & living UI (right). The AI can execute JavaScript in the browser via `/browser exec` blocks and self-loop via `/self_continue` or pause via `/call_for_human`.

## Architecture

**Single file, zero build step:**

- **`index.html`** — The entire frontend: UI, state management, IndexedDB storage, SSE streaming, JS execution, and the system prompt. All in one self-contained SPA.
- **`models.json`** — Model and provider configuration, loaded from remote (`https://infero.net/genesis/models.json`).
- **`i18n.json`** — UI string translations (zh/en).

**Data flow:** Browser → LLM API (SSE) → streamed back to browser. Provider/endpoint configurable in settings (Gemini, OpenAI, Anthropic, DeepSeek, OpenRouter, custom).

**Storage:** All data lives in browser IndexedDB (`GenesisDB`, object store `beings`). No server-side state.

**Core loop (BIS architecture):**
- `perceive()` — formats environment context + user input
- `infer()` — calls LLM, streams response, extracts `/browser exec` code blocks
- `act(B)` — executes extracted JS, writes result to consciousness (15s timeout)
- `loop()` — orchestrates the cycle; continues on `/self_continue`, stops on `/call_for_human`

**Device Relay (`../device_relay/relay.py`):**
- WebSocket relay that connects the browser to external devices (macOS, Linux, Windows shells, iOS shortcuts)
- HTTP endpoints for pairing; tokens persisted to `tokens.json`
- `agent.py` — Python agent script delivered to devices on pairing; loaded at relay startup (restart relay to pick up `agent.py` changes)
- Two independent instances: prod (HTTP 8082 / WS 8083) and dev (HTTP 8087 / WS 8088)

## Environments

| Env | URL | Branch | API Relay | Device Relay |
|-----|-----|--------|-----------|--------------|
| Prod | `infero.net/genesis` | `main` | `:8080` | `:8082/:8083` |
| Dev | `dev.infero.net/genesis` | `dev` | `:8084` | `:8087/:8088` |

Server: `ubuntu@3.114.3.152` (infero.net, hosts both prod and dev), key: `~/.ssh/ec2_tokyo_2023.pem`

## Deployment

See **`DEPLOY.md`** (gitignored, local only) for full deployment commands, service list, and one-click scripts. If deployment methods change, update `DEPLOY.md` accordingly.

Also works from GitHub Pages, Vercel, or local `file://` (no device relay needed).

## Symmetric Logic (keep in sync manually)

`index.html` (JS) and `device_relay/agent.py` (Python) both implement the same core loop, encryption, exec-block parsing, and LLM payload construction. **Any logic change in one file likely needs to be mirrored in the other.** Always check both when modifying shared behaviour.

## Key Conventions

- The canvas is Retina-aware: AI-generated JS must **never** set `canvas.width`/`canvas.height` directly.
- `#html-div` overlays canvas with transparent background. AI can place interactive HTML elements there. Content is auto-saved/restored via snapshots.
- AI output must end with `/call_for_human` or `/self_continue` — this drives the autonomous execution loop.
- The system prompt is defined inline in `SYSTEM_INSTRUCTION` at the top of `index.html`.
- Settings (model, provider, token, vision mode) stored in `localStorage.genesis_settings`.
- Context compression triggers at 300k tokens, saves trimmed middle to IndexedDB.
- Anthropic cache uses up to 4 floor-aligned breakpoints in the user content array (stable cache positions).
- `agent.py` is loaded once at relay startup — changes require relay restart to take effect.
- **`index.html` must contain no Chinese text.** All UI strings, comments, default-skill instructions, and any inline prose must be English. Translations live in `i18n.json`.
