# Friday Remote AI Bridge

A Telegram-to-workstation AI bridge and Windows system tray daemon for [Antigravity CLI (`agy`)](https://antigravity.google/), paired with a local Windows Desktop MCP server, live telemetry HUD, voice handling, and scheduled workstation checks.

> ⚠️ **Security & Prompt Injection Warning:**
> - **Full Workstation Access:** Friday invokes `agy` with `--dangerously-skip-permissions` (bypassing interactive CLI confirmation) and can run PowerShell (`/run`), switch workspaces, capture screenshots, and control mouse/keyboard input via `desktop_mcp.py`.
> - **Indirect Prompt Injection Risk:** If Friday reads untrusted external text (e.g. incoming emails via `scripts/mail_scout.py`, web/repo feeds via `scripts/search_ai_gems.py`, or arbitrary pages), malicious text inside that content could attempt to trigger unintended shell or desktop actions. Do not point Friday unattended at untrusted inputs, keep explicit `/confirm` rules active in `persona.md`, and audit any automated `tasks.json` prompts carefully.
> - **Mandatory Auth:** Always set `TELEGRAM_CHAT_ID` to your numeric Telegram user ID — authorization fails closed when `TELEGRAM_CHAT_ID` is unset. Keep `FRIDAY_MCP_HOST` bound to `127.0.0.1`.

---

## Components

- **`scripts/tray_bridge.pyw`**: Windows System Tray daemon with Telegram bot polling, native `agy --conversation` persistence, `/model` switching, `/usage` tracking, `/repo` workspace switching, voice message transcription/handling, and a periodic `tasks.json` scheduler (`git_dirty`, `system_health`, `http_check`, `ai_prompt`).
- **`scripts/bridge.py`**: Interactive console Telegram bridge with the Rich Quantum Telemetry HUD and rolling `chat_history.json` context.
- **`scripts/desktop_mcp.py`**: Local Streamable HTTP MCP server (`127.0.0.1:8765`) providing interactive desktop window, screenshot, URL/app launch, mouse, and keyboard tools.
- **`scripts/terminal_viewer.py`**: Standalone live HUD log follower (`F.R.I.D.A.Y. // Quantum Telemetry Terminal`).
- **`scripts/mail_scout.py`**: Optional read-only Gmail IMAP scout for job application updates.
- **`scripts/search_ai_gems.py`**: Optional GitHub, Hacker News, and Hugging Face scout.
- **`scripts/friday_config.py`**: Shared `.env` / environment / Windows registry config loader and fail-closed Telegram chat ID guard.

---

## Setup (Windows)

1. **Install dependencies:**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
2. **Configure environment & persona:**
   ```powershell
   Copy-Item .env.example .env
   Copy-Item persona.example.md persona.md
   ```
   Edit `.env` and fill in at minimum:
   - `TELEGRAM_BOT_TOKEN` (from [@BotFather](https://t.me/BotFather))
   - `TELEGRAM_CHAT_ID` (your numeric Telegram user ID, e.g. from `@userinfobot`)
   - Optional: `OWNER_NAME`, `TELEGRAM_USERNAME`, `TELEGRAM_BOT_USERNAME`, `FRIDAY_REPOS_ROOT`, `AGY_EXE`, `GEMINI_API_KEY`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`
3. **Run:**
   - **Console mode:**
     ```powershell
     python scripts/bridge.py
     ```
   - **Windows System Tray daemon (background):**
     ```powershell
     pythonw scripts/tray_bridge.pyw
     ```
   - **Live HUD viewer (optional):**
     ```powershell
     python scripts/terminal_viewer.py
     ```

## License

[MIT](LICENSE)
