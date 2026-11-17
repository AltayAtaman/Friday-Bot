"""
Friday Remote AI Bridge - Windows System Tray Daemon (.pyw)
Runs silently in the Windows taskbar notification area (System Tray).
- Native Antigravity session persistence (--conversation) + rolling history.
- Real-time stream-json parsing for live tool/reasoning logs & exact token usage.
- /model with interactive Telegram inline buttons to switch models on the fly.
- /usage to inspect session, all-time, and per-model token usage.
- /clear to reset conversation memory, /remember to add persistent rules.
"""

import os
import sys
import html
import time
import json
import socket
import atexit
import asyncio
import threading
import subprocess
import platform
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from friday_config import (
    get_agy_exe,
    get_env_secret,
    get_owner_name,
    get_python_exe,
    get_repos_root,
    is_chat_authorized,
)

from PIL import Image
import pystray
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

SKILL_DIR = Path(__file__).parent.parent
LOG_FILE = SKILL_DIR / "friday.log"
PERSONA_PATH = SKILL_DIR / "persona.md"
HISTORY_FILE = SKILL_DIR / "chat_history.json"
STATE_FILE = SKILL_DIR / "session_state.json"
USAGE_FILE = SKILL_DIR / "usage_stats.json"
ICON_PATH = SKILL_DIR / "friday_icon.png"
TASKS_FILE = SKILL_DIR / "tasks.json"
REPOS_ROOT = get_repos_root()
VIEWER_SCRIPT = Path(__file__).parent / "terminal_viewer.py"
OWNER_NAME = get_owner_name()


def get_active_cwd() -> str:
    """Return the active working directory from SESSION_STATE, defaulting to REPOS_ROOT."""
    cwd = SESSION_STATE.get("active_cwd")
    if cwd and Path(cwd).is_dir():
        return cwd
    return str(REPOS_ROOT) if REPOS_ROOT.is_dir() else os.getcwd()
AGY_EXE = get_agy_exe()
PYTHON_EXE = get_python_exe()

DEFAULT_MODELS = [
    ("gemini-4-hydrogen-high", "Gemini 4 Hydrogen (High)"),
    ("gemini-4-hydrogen-medium", "Gemini 4 Hydrogen (Medium)"),
    ("gemini-4-hydrogen-low", "Gemini 4 Hydrogen (Low)"),
    ("gemini-4-argon-high", "Gemini 4 Argon (High)"),
    ("gemini-4-argon-medium", "Gemini 4 Argon (Medium)"),
    ("gemini-3.8-flash-high", "Gemini 3.8 Flash (High)"),
    ("gemini-3.8-flash-low", "Gemini 3.8 Flash (Low)"),
    ("claude-opus-5-5-high", "Claude Opus 5.5 (High)"),
    ("claude-sonnet-5-5-high", "Claude Sonnet 5.5 (High)"),
    ("gpt-oss-120b-medium", "GPT-OSS 120B (Medium)"),
]

_activity_lock = threading.Lock()
_last_log_line: str = ""
_last_log_at: Optional[float] = None


def _record_activity(text: str) -> None:
    """Record the latest non-empty log line and wall-clock timestamp."""
    global _last_log_line, _last_log_at
    cleaned = " ".join(text.strip().splitlines()).strip()
    if not cleaned:
        return
    with _activity_lock:
        _last_log_line = cleaned
        _last_log_at = time.time()


class _ActivityTrackingHandler(logging.Handler):
    """Mirror logger events into the in-memory latest-log snapshot."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _record_activity(self.format(record))
        except Exception:
            pass


# Setup persistent file logging
file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
activity_handler = _ActivityTrackingHandler()
activity_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))

logger = logging.getLogger("FridayTray")
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(activity_handler)

BOT_TOKEN = get_env_secret("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_ID = get_env_secret("TELEGRAM_CHAT_ID")

if not BOT_TOKEN or not ALLOWED_CHAT_ID:
    logger.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required in .env, environment, or registry.")
    sys.exit(1)

def load_json(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading {path.name}: {e}")
    return default

def save_json(path: Path, data: Any) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error saving {path.name}: {e}")

def load_state() -> Dict[str, Any]:
    state = load_json(STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("active_model", "gemini-4-hydrogen-high")
    state.setdefault("conversation_id", None)
    return state

def save_state(state: Dict[str, Any]) -> None:
    save_json(STATE_FILE, state)

def load_usage() -> Dict[str, Any]:
    usage = load_json(USAGE_FILE, {})
    if not isinstance(usage, dict):
        usage = {}
    defaults = {
        "total_requests": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_thinking_tokens": 0,
        "total_cache_read_tokens": 0,
        "total_tokens": 0,
        "total_duration_seconds": 0.0,
        "session_requests": 0,
        "session_tokens": 0,
        "by_model": {},
    }
    for k, v in defaults.items():
        usage.setdefault(k, v)
    return usage

def record_usage(model_id: str, usage_data: Dict[str, Any], duration: float) -> None:
    stats = load_usage()
    in_tok = int(usage_data.get("input_tokens", 0))
    out_tok = int(usage_data.get("output_tokens", 0))
    think_tok = int(usage_data.get("thinking_tokens", 0))
    cache_tok = int(usage_data.get("cache_read_tokens", 0))
    tot_tok = int(usage_data.get("total_tokens", in_tok + out_tok + think_tok))

    stats["total_requests"] += 1
    stats["total_input_tokens"] += in_tok
    stats["total_output_tokens"] += out_tok
    stats["total_thinking_tokens"] += think_tok
    stats["total_cache_read_tokens"] += cache_tok
    stats["total_tokens"] += tot_tok
    stats["total_duration_seconds"] += float(duration)
    stats["session_requests"] += 1
    stats["session_tokens"] += tot_tok

    by_model = stats.setdefault("by_model", {})
    m_stat = by_model.setdefault(model_id, {"requests": 0, "tokens": 0, "input": 0, "output": 0})
    m_stat["requests"] += 1
    m_stat["tokens"] += tot_tok
    m_stat["input"] += in_tok
    m_stat["output"] += out_tok

    save_json(USAGE_FILE, stats)

def load_persona() -> str:
    if PERSONA_PATH.exists():
        try:
            return PERSONA_PATH.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error reading persona.md: {e}"
    return "Friday: Autonomous Antigravity Assistant."

def load_history() -> List[Dict[str, str]]:
    data = load_json(HISTORY_FILE, [])
    return data if isinstance(data, list) else []

def save_history(history: List[Dict[str, str]]) -> None:
    save_json(HISTORY_FILE, history[-20:])

SESSION_STATE = load_state()
CONVERSATION_HISTORY = load_history()

def fetch_available_models() -> List[tuple]:
    """Query `agy models` dynamically, falling back to DEFAULT_MODELS."""
    if os.path.exists(AGY_EXE):
        try:
            res = subprocess.run(
                [AGY_EXE, "models"],
                capture_output=True,
                text=True,
                timeout=10,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            models = []
            for line in res.stdout.splitlines():
                line = line.strip()
                if "\t" in line:
                    m_id, m_name = line.split("\t", 1)
                    models.append((m_id.strip(), m_name.strip()))
            if models:
                return models
        except Exception as e:
            logger.warning(f"Failed to fetch dynamic agy models: {e}")
    return DEFAULT_MODELS

def get_model_display_name(model_id: str) -> str:
    for m_id, m_name in DEFAULT_MODELS:
        if m_id == model_id:
            return m_name
    return model_id

def is_authorized(update: Update) -> bool:
    chat_id = str(update.effective_chat.id) if update.effective_chat else "None"
    return is_chat_authorized(chat_id, ALLOWED_CHAT_ID, logger)

def open_terminal(icon=None, item=None):
    """Launch the Quantum Terminal Viewer in a new interactive console window."""
    try:
        logger.info(f"Launching terminal viewer: {VIEWER_SCRIPT}")
        subprocess.Popen(
            [PYTHON_EXE, str(VIEWER_SCRIPT)],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            cwd=get_active_cwd(),
        )
    except Exception as e:
        logger.error(f"Failed to open terminal viewer: {e}", exc_info=True)

def open_logs(icon=None, item=None):
    """Open friday.log in default text editor."""
    try:
        os.startfile(str(LOG_FILE))
    except Exception as e:
        logger.error(f"Failed to open log file: {e}", exc_info=True)

def quit_app(icon=None, item=None):
    """Gracefully stop Friday tray daemon and exit."""
    logger.info("Friday daemon shutting down via tray command.")
    try:
        if icon:
            icon.stop()
    except Exception:
        pass
    os._exit(0)

def write_log_line(text: str) -> None:
    """Append raw streaming line directly to friday.log for live terminal display."""
    _record_activity(text)
    with open(LOG_FILE, "a", encoding="utf-8", errors="replace") as f:
        f.write(text if text.endswith("\n") else text + "\n")
        f.flush()

def clean_for_telegram(raw_text: str) -> str:
    """Strip intermediate narration, finding, and reasoning blocks so Telegram only gets the final answer."""
    cleaned_lines = []
    for line in raw_text.splitlines():
        s = line.strip()
        if s.startswith("### ") and s.endswith("..."):
            continue
        if s.startswith("> 💡") or s.startswith("> 🧠"):
            continue
        cleaned_lines.append(line)
    result = "\n".join(cleaned_lines).strip()
    return result or raw_text.strip()

_proc_lock = threading.Lock()
_active_proc: Optional[subprocess.Popen] = None
_task_started_at: Optional[float] = None
_stop_requested = False


def _format_duration(seconds: float) -> str:
    """Format elapsed seconds compactly as e.g. '4s', '2m 15s', or '1h 3m'."""
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    mins, secs = divmod(total, 60)
    if mins < 60:
        return f"{mins}m {secs}s"
    hours, mins = divmod(mins, 60)
    return f"{hours}h {mins}m"


def _read_tail_log_line() -> tuple[str, Optional[float]]:
    """Read the last non-empty line and modification time from LOG_FILE."""
    if not LOG_FILE.exists():
        return "", None
    try:
        mtime = LOG_FILE.stat().st_mtime
        size = LOG_FILE.stat().st_size
        with open(LOG_FILE, "rb") as f:
            if size > 8192:
                f.seek(-8192, os.SEEK_END)
            tail = f.read().decode("utf-8", errors="replace")
        for line in reversed(tail.splitlines()):
            stripped = line.strip()
            if stripped:
                return stripped, mtime
        return "", mtime
    except Exception:
        return "", None


def _get_latest_log_snapshot() -> Dict[str, Any]:
    """Return active process state, task elapsed duration, and latest log message with timing."""
    now = time.time()
    with _proc_lock:
        proc = _active_proc
        started_at = _task_started_at
        running = proc is not None and proc.poll() is None
        pid = proc.pid if running and proc is not None else None
        task_elapsed = max(0.0, now - started_at) if (running and started_at is not None) else None

    with _activity_lock:
        log_line = _last_log_line
        log_at = _last_log_at

    if not log_line or log_at is None:
        tail_line, tail_mtime = _read_tail_log_line()
        if tail_line:
            log_line = tail_line
            log_at = tail_mtime

    age_seconds = max(0.0, now - log_at) if log_at is not None else None
    timestamp = datetime.fromtimestamp(log_at).strftime("%Y-%m-%d %H:%M:%S") if log_at is not None else None
    return {
        "running": running,
        "pid": pid,
        "task_elapsed": task_elapsed,
        "log_line": log_line,
        "log_at": log_at,
        "age_seconds": age_seconds,
        "timestamp": timestamp,
    }


def generate_ai_response(user_prompt: str) -> str:
    """Run Antigravity CLI with stream-json parsing, native conversation continuation, and usage tracking."""
    global _active_proc, _task_started_at, _stop_requested
    active_model = SESSION_STATE.get("active_model", "gemini-4-hydrogen-high")
    conv_id = SESSION_STATE.get("conversation_id")
    persona = load_persona()

    # If starting a fresh conversation, include persona & rules; if resuming, send prompt directly
    if not conv_id:
        full_prompt = (
            f"You are Friday, {OWNER_NAME}'s remote AI workstation assistant.\n"
            f"Persona & System Directives:\n{persona}\n\n"
            f"=== CORE INTERACTION RULES ===\n"
            f"1. TONE: Witty, concise, brilliant, and confident like Tony Stark's F.R.I.D.A.Y.\n"
            f"2. ZERO BOILERPLATE: Never output robotic startup menus unless explicitly asked.\n"
            f"3. ACTIONS: If asked to do something on this PC (play sounds, open apps/browser, check files, run scripts), execute directly with your tools without asking for confirmation.\n\n"
            f"{OWNER_NAME}: {user_prompt}"
        )
    else:
        full_prompt = user_prompt

    if os.path.exists(AGY_EXE):
        try:
            cmd = [
                AGY_EXE,
                "--model", active_model,
                "--dangerously-skip-permissions",
                "--output-format", "stream-json",
            ]
            if conv_id:
                cmd.extend(["--conversation", conv_id])
            cmd.extend(["-p", full_prompt])

            proc = subprocess.Popen(
                cmd,
                cwd=get_active_cwd(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            with _proc_lock:
                _active_proc = proc
                _task_started_at = time.time()
                _stop_requested = False

            final_response = ""
            delta_buffer = ""
            new_conv_id = conv_id
            step_texts: Dict[int, str] = {}
            last_agent_step = -1

            for raw_line in iter(proc.stdout.readline, ""):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    write_log_line(line)
                    continue

                evt_type = evt.get("event")
                if evt.get("conversation_id"):
                    new_conv_id = evt["conversation_id"]

                if evt_type == "step_update":
                    su = evt.get("step_update", {})
                    stype = su.get("step_type")
                    state = su.get("state")
                    step_idx = su.get("step_index", 0)

                    # Log any internal thinking deltas strictly to terminal log
                    t_delta = su.get("thinking_delta", "")
                    if t_delta:
                        for t_line in t_delta.splitlines():
                            if t_line.strip():
                                write_log_line(f"> 🧠 [THINKING] {t_line.strip()}")

                    if stype == "agent_response":
                        delta = su.get("text_delta", "")
                        if delta:
                            step_texts[step_idx] = step_texts.get(step_idx, "") + delta
                            last_agent_step = step_idx
                            delta_buffer += delta
                            while "\n" in delta_buffer:
                                complete_line, delta_buffer = delta_buffer.split("\n", 1)
                                if complete_line.strip():
                                    write_log_line(complete_line)

                    elif stype == "tool":
                        tool_name = su.get("tool_name", "tool")
                        t_info = su.get("tool_info", {})
                        if state == "ACTIVE":
                            params = json.dumps(t_info.get("parameters", {}), ensure_ascii=False)[:140]
                            write_log_line(f"● Ran ({tool_name}: {params})")
                        elif state == "DONE":
                            dur = su.get("duration_seconds", 0.0)
                            write_log_line(f"Command exited ({tool_name}) in {dur:.2f}s")

                elif evt_type == "result":
                    res = evt.get("result", {})
                    if delta_buffer.strip():
                        write_log_line(delta_buffer.strip())
                        delta_buffer = ""

                    raw_final = step_texts.get(last_agent_step, "").strip()
                    if not raw_final:
                        raw_final = (res.get("response") or "").strip()

                    final_response = clean_for_telegram(raw_final)
                    usage_data = res.get("usage", {})
                    dur = res.get("duration_seconds", 0.0)

                    if new_conv_id:
                        SESSION_STATE["conversation_id"] = new_conv_id
                        save_state(SESSION_STATE)

                    if usage_data:
                        record_usage(active_model, usage_data, dur)
                        toks = usage_data.get("total_tokens", 0)
                        write_log_line(f"📊 [USAGE] Model: {active_model} | Tokens: {toks:,} | Duration: {dur:.2f}s")

            proc.stdout.close()
            proc.wait(timeout=180)

            with _proc_lock:
                if _active_proc is proc:
                    _active_proc = None
                    _task_started_at = None
                was_stopped = _stop_requested
                _stop_requested = False

            if was_stopped:
                write_log_line("🛑 [STOPPED] Task aborted by user via /stop.")
                return "🛑 Stopped."

            if final_response:
                CONVERSATION_HISTORY.append({"role": "user", "text": user_prompt})
                CONVERSATION_HISTORY.append({"role": "friday", "text": final_response})
                save_history(CONVERSATION_HISTORY)
                return final_response

        except Exception as e:
            with _proc_lock:
                _active_proc = None
                _task_started_at = None
                was_stopped = _stop_requested
                _stop_requested = False
            if was_stopped:
                write_log_line("🛑 [STOPPED] Task aborted by user via /stop.")
                return "🛑 Stopped."
            logger.error(f"agy execution error: {e}", exc_info=True)

    # Fallback to google.genai if available
    api_key = get_env_secret("GEMINI_API_KEY") or get_env_secret("GOOGLE_API_KEY")
    if api_key:
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=user_prompt,
                config={"system_instruction": persona},
            )
            if resp.text:
                out = resp.text.strip()
                CONVERSATION_HISTORY.append({"role": "user", "text": user_prompt})
                CONVERSATION_HISTORY.append({"role": "friday", "text": out})
                save_history(CONVERSATION_HISTORY)
                return out
        except Exception as e:
            logger.error(f"google.genai error: {e}", exc_info=True)

    return "⚠️ Friday could not reach the Antigravity agent engine."

def _markdown_to_telegram_html(text: str) -> str:
    """Convert common Markdown (headings, bold, italic, code, links, bullets) to Telegram-safe HTML."""
    import html as _html
    import re as _re

    code_blocks: List[str] = []
    inline_codes: List[str] = []

    def _save_block(m: _re.Match) -> str:
        idx = len(code_blocks)
        code_blocks.append(f"<pre><code>{_html.escape(m.group(1).strip())}</code></pre>")
        return f"\x00CB{idx}\x00"

    def _save_inline(m: _re.Match) -> str:
        idx = len(inline_codes)
        inline_codes.append(f"<code>{_html.escape(m.group(1))}</code>")
        return f"\x00IC{idx}\x00"

    s = _re.sub(r"```(?:\w+)?\n?([\s\S]*?)```", _save_block, text or "")
    s = _re.sub(r"`([^`\n]+)`", _save_inline, s)
    s = _html.escape(s)

    def _replace_link(m: _re.Match) -> str:
        label, url = m.group(1), m.group(2).strip()
        if url.startswith(("http://", "https://")):
            return f'<a href="{url}">{label}</a>'
        return f"<code>{label}</code>"

    s = _re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", _replace_link, s)
    s = _re.sub(r"^[ \t]*#{1,6}[ \t]+(.+)$", r"<b>\1</b>", s, flags=_re.MULTILINE)
    s = _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = _re.sub(r"__(.+?)__", r"<b>\1</b>", s)
    s = _re.sub(r"^[ \t]*[-*][ \t]+", "• ", s, flags=_re.MULTILINE)
    s = _re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", s)

    for idx, block in enumerate(code_blocks):
        s = s.replace(f"\x00CB{idx}\x00", block)
    for idx, code in enumerate(inline_codes):
        s = s.replace(f"\x00IC{idx}\x00", code)
    return s


def _split_safe_chunks(text: str, max_len: int = 3500, is_html: bool = False) -> List[str]:
    """Split text into bounded chunks on clean boundaries without slicing HTML tags or entities."""
    import re as _re

    if not text:
        return [""]
    chunks: List[str] = []
    pos = 0
    n = len(text)
    limit_margin = 160 if is_html else 0
    step_limit = max(500, max_len - limit_margin)

    while pos < n:
        if n - pos <= step_limit:
            chunks.append(text[pos:])
            break
        end = pos + step_limit
        nl = text.rfind("\n", pos, end)
        if nl > pos + 400:
            end = nl + 1
        elif is_html:
            # Avoid cutting inside an unclosed <tag...> or &entity;
            lt = text.rfind("<", pos, end)
            gt = text.rfind(">", pos, end)
            if lt > gt and lt > pos:
                end = lt
            else:
                amp = text.rfind("&", max(pos, end - 12), end)
                if amp > pos and ";" not in text[amp:end]:
                    end = amp
        chunks.append(text[pos:end])
        pos = end

    if not is_html or len(chunks) <= 1:
        return chunks

    # Balance Telegram HTML tags across chunk boundaries so no chunk has dangling open tags
    tag_re = _re.compile(r"<(/?)([a-zA-Z0-9]+)(?:\s[^>]*)?>")
    balanced: List[str] = []
    carry_open: List[tuple] = []
    for raw_chunk in chunks:
        prefix = "".join(open_tag for _, open_tag in carry_open)
        stack = list(carry_open)
        for m in tag_re.finditer(raw_chunk):
            is_close, name = m.group(1) == "/", m.group(2).lower()
            if not is_close:
                stack.append((name, m.group(0)))
            else:
                for idx in range(len(stack) - 1, -1, -1):
                    if stack[idx][0] == name:
                        stack.pop(idx)
                        break
        suffix = "".join(f"</{name}>" for name, _ in reversed(stack))
        balanced.append(f"{prefix}{raw_chunk}{suffix}")
        carry_open = stack
    return balanced


async def _send_formatted_reply(reply_fn, raw_text: str) -> None:
    """Chunk long replies on newline boundaries and send as Telegram HTML with plain-text fallback."""
    for chunk in _split_safe_chunks(raw_text or "", max_len=3500, is_html=False):
        try:
            await reply_fn(_markdown_to_telegram_html(chunk), parse_mode="HTML", disable_web_page_preview=True)
        except Exception as e:
            logger.warning(f"HTML reply fallback to plain text: {e}")
            await reply_fn(chunk, parse_mode=None)


async def _send_html_chunked(send_fn, html_text: str) -> None:
    """Send pre-formatted Telegram HTML in tag-balanced chunks with plain-text fallback."""
    import html as _html
    import re as _re

    for chunk in _split_safe_chunks(html_text or "", max_len=3500, is_html=True):
        if not chunk.strip():
            continue
        try:
            await send_fn(chunk, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as e:
            logger.warning(f"Pre-formatted HTML chunk fallback to plain text: {e}")
            plain = _html.unescape(_re.sub(r"<[^>]+>", "", chunk))
            if plain.strip():
                await send_fn(plain, parse_mode=None)


async def prompt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming Telegram prompt."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    prompt = update.message.text
    if not prompt:
        return

    logger.info(f"Incoming prompt: {prompt}")

    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception:
        pass

    try:
        response_text = await asyncio.to_thread(generate_ai_response, prompt)
    except Exception as e:
        logger.error(f"Error during agent execution: {e}", exc_info=True)
        response_text = f"⚠️ Execution error: {e}"

    logger.info(f"Agent finished ({len(response_text)} chars)")
    await _send_formatted_reply(update.message.reply_text, response_text)

async def model_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View or switch the active LLM model via command or interactive inline buttons."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    models = await asyncio.to_thread(fetch_available_models)
    current = SESSION_STATE.get("active_model", "gemini-4-hydrogen-high")

    # Direct switch: /model <model_id>
    if context.args:
        target = context.args[0].strip()
        matched = next(((m_id, m_name) for m_id, m_name in models if m_id.lower() == target.lower()), None)
        if not matched:
            matched = next(((m_id, m_name) for m_id, m_name in models if target.lower() in m_id.lower()), None)
        if matched:
            SESSION_STATE["active_model"] = matched[0]
            save_state(SESSION_STATE)
            logger.info(f"Model switched via command to: {matched[0]}")
            await update.message.reply_text(
                f"🧠 <b>Active model switched:</b>\n• <code>{matched[1]}</code> (<code>{matched[0]}</code>)",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                f"⚠️ Unknown model: <code>{target}</code>\nUse <code>/model</code> without arguments to pick from the list.",
                parse_mode="HTML",
            )
        return

    # Interactive Inline Keyboard
    keyboard = []
    for m_id, m_name in models:
        prefix = "✅ " if m_id == current else ""
        keyboard.append([InlineKeyboardButton(f"{prefix}{m_name}", callback_data=f"set_model:{m_id}")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"🧠 <b>Model Selection</b>\n\n"
        f"• <b>Current Model:</b> <code>{get_model_display_name(current)}</code>\n\n"
        f"Tap a model below to switch immediately:",
        reply_markup=reply_markup,
        parse_mode="HTML",
    )

async def model_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard tap for model switching."""
    query = update.callback_query
    if not query:
        return
    if not is_authorized(update):
        await query.answer("⛔ Unauthorized.", show_alert=True)
        return

    data = query.data or ""
    if data.startswith("set_model:"):
        new_model = data.split("set_model:", 1)[1]
        SESSION_STATE["active_model"] = new_model
        save_state(SESSION_STATE)
        logger.info(f"Model switched via inline button to: {new_model}")

        models = await asyncio.to_thread(fetch_available_models)
        display_name = next((n for i, n in models if i == new_model), new_model)

        keyboard = []
        for m_id, m_name in models:
            prefix = "✅ " if m_id == new_model else ""
            keyboard.append([InlineKeyboardButton(f"{prefix}{m_name}", callback_data=f"set_model:{m_id}")])

        await query.answer(f"Switched to {display_name}")
        await query.edit_message_text(
            f"🧠 <b>Model Selection</b>\n\n"
            f"• <b>Active Model:</b> <code>{display_name}</code> (<code>{new_model}</code>)\n\n"
            f"<i>All subsequent prompts will use this model.</i>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )

def format_reset_countdown(iso_str: str) -> str:
    """Convert UTC ISO timestamp into human-readable countdown + local time."""
    try:
        reset_utc = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now_utc = datetime.now(timezone.utc)
        delta = reset_utc - now_utc
        total_secs = int(delta.total_seconds())
        if total_secs <= 0:
            return "resetting soon"
        days, rem = divmod(total_secs, 86400)
        hours, rem = divmod(rem, 3600)
        mins, _ = divmod(rem, 60)
        local_dt = reset_utc.astimezone()
        if days > 0:
            return f"in {days}d {hours}h ({local_dt.strftime('%d %b %H:%M')})"
        elif hours > 0:
            return f"in {hours}h {mins}m ({local_dt.strftime('%H:%M')})"
        else:
            return f"in {mins}m ({local_dt.strftime('%H:%M')})"
    except Exception:
        return iso_str

def fetch_antigravity_limits() -> List[Dict[str, str]]:
    """Query `agy -p /usage` to retrieve live Antigravity quota limits."""
    if not os.path.exists(AGY_EXE):
        return []
    try:
        res = subprocess.run(
            [AGY_EXE, "-p", "/usage"],
            capture_output=True,
            text=True,
            timeout=20,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        entries = []
        for line in res.stdout.splitlines():
            parts = [p.strip() for p in line.split("\t")]
            if len(parts) >= 4:
                entries.append({
                    "category": parts[0],
                    "label": parts[1],
                    "pct": parts[2],
                    "reset": parts[3],
                })
        return entries
    except Exception as e:
        logger.error(f"Failed to fetch Antigravity limits: {e}")
        return []

async def usage_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Report live Antigravity account limits and local token usage telemetry."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    if context.args and context.args[0].lower() == "reset":
        save_json(USAGE_FILE, {})
        await update.message.reply_text("🧹 <b>Local token usage counters reset.</b>", parse_mode="HTML")
        return

    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception:
        pass

    limits = await asyncio.to_thread(fetch_antigravity_limits)
    stats = load_usage()
    current_model = SESSION_STATE.get("active_model", "gemini-4-hydrogen-high")

    lines = ["⚡ <b>Antigravity Account Limits</b>\n"]

    if limits:
        grouped: Dict[str, List[Dict[str, str]]] = {}
        for item in limits:
            grouped.setdefault(item["category"], []).append(item)

        for cat, items in grouped.items():
            lines.append(f"🔹 <b>{cat}</b>")
            for it in items:
                pct_num = int(it["pct"].rstrip("%")) if it["pct"].rstrip("%").isdigit() else 0
                filled = round(pct_num / 10)
                bar = "█" * filled + "░" * (10 - filled)
                badge = "🟢" if pct_num >= 50 else ("🟡" if pct_num >= 20 else "🔴")
                short_label = it["label"].replace("Remaining", "").replace(cat, "").strip() or it["label"]
                countdown = format_reset_countdown(it["reset"])
                lines.append(f"• {badge} <b>{short_label}:</b> <code>{bar} {it['pct']}</code>\n  <i>Resets {countdown}</i>")
            lines.append("")
    else:
        lines.append("<i>⚠️ Could not fetch live Antigravity account limits.</i>\n")

    total_reqs = stats["total_requests"]
    avg_dur = (stats["total_duration_seconds"] / total_reqs) if total_reqs else 0.0

    lines.extend([
        "📊 <b>Local Bridge Telemetry</b>",
        f"• <b>Active Model:</b> <code>{get_model_display_name(current_model)}</code>",
        f"• <b>Session:</b> {stats['session_requests']} reqs | {stats['session_tokens']:,} tokens",
        f"• <b>All-Time:</b> {total_reqs} reqs | {stats['total_tokens']:,} tokens (avg {avg_dur:.1f}s)",
    ])

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def clear_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wipe active chat memory and start a new Antigravity session."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    global CONVERSATION_HISTORY
    count = len(CONVERSATION_HISTORY)
    CONVERSATION_HISTORY = []
    save_history([])

    SESSION_STATE["conversation_id"] = None
    save_state(SESSION_STATE)

    stats = load_usage()
    stats["session_requests"] = 0
    stats["session_tokens"] = 0
    save_json(USAGE_FILE, stats)

    logger.info(f"Chat memory & native session cleared ({count} turns wiped).")
    await update.message.reply_text(
        f"🧹 <b>Conversation memory & session wiped clean.</b> ({count // 2} previous exchanges cleared).\n\n"
        f"Standing by with a fresh slate, {OWNER_NAME}.",
        parse_mode="HTML",
    )

async def system_prompt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add persistent system instruction or note into persona.md."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    if not context.args:
        await update.message.reply_text(
            "<b>Usage:</b> <code>/system &lt;instruction or info&gt;</code> or <code>/remember &lt;note&gt;</code>\n\n"
            "<i>Example:</i> <code>/remember Always answer in Turkish when I ask about news.</code>",
            parse_mode="HTML",
        )
        return

    new_rule = " ".join(context.args)
    try:
        with open(PERSONA_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n- {new_rule}")
        logger.info(f"Added persistent system rule: {new_rule}")
        await update.message.reply_text(
            f"🧠 <b>System rule logged into persistent memory:</b>\n\n"
            f"<code>• {new_rule}</code>\n\n"
            f"<i>This instruction will persist across all sessions and reboots.</i>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Failed to save system rule: {e}")
        await update.message.reply_text(f"⚠️ Error saving system rule: {e}")

async def memory_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inspect current active memory and persistent rules."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    turn_count = len(CONVERSATION_HISTORY) // 2
    conv_id = SESSION_STATE.get("conversation_id") or "None"
    persona_content = load_persona()

    notes_section = ""
    if "## 3. Persistent Memory & Notes" in persona_content:
        notes_section = persona_content.split("## 3. Persistent Memory & Notes", 1)[-1].strip()

    reply = (
        f"🧠 <b>Friday Memory Status:</b>\n\n"
        f"• <b>Active Chat Turns:</b> {turn_count} turns retained\n"
        f"• <b>Native Session ID:</b> <code>{conv_id}</code>\n\n"
        f"<b>Persistent System Rules & Notes:</b>\n<pre>{notes_section or 'No custom notes'}</pre>\n\n"
        f"💡 <i>Use <code>/clear</code> to wipe chat history, or <code>/remember &lt;info&gt;</code> to add rules.</i>"
    )
    await update.message.reply_text(reply, parse_mode="HTML")

async def run_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    if not context.args:
        await update.message.reply_text("Usage: <code>/run &lt;command&gt;</code>", parse_mode="HTML")
        return

    cmd = " ".join(context.args)
    logger.info(f"Executing remote command: {cmd}")
    await update.message.reply_text(f"⚙️ <i>Executing on PC:</i> <code>{cmd}</code>...", parse_mode="HTML")

    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            cwd=get_active_cwd(),
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()

        resp = []
        if stdout:
            resp.append(f"<b>Output:</b>\n<pre>{stdout[:3500]}</pre>")
        if stderr:
            resp.append(f"⚠️ <b>Stderr:</b>\n<pre>{stderr[:1000]}</pre>")
        if not stdout and not stderr:
            resp.append("<i>Command finished with exit code 0 and no output.</i>")

        await update.message.reply_text("\n\n".join(resp), parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ <i>Execution error:</i> {e}", parse_mode="HTML")

def _format_pulse_message() -> str:
    """Build concise HTML summary showing running state, timing, and single latest log line."""
    snap = _get_latest_log_snapshot()
    if snap["running"]:
        elapsed = _format_duration(snap["task_elapsed"] or 0.0)
        state_str = f"🟢 Running (PID <code>{snap['pid']}</code> · {elapsed} elapsed)"
    else:
        state_str = "⚪ Idle (no active task)"

    if snap["timestamp"] and snap["age_seconds"] is not None:
        age_str = _format_duration(snap["age_seconds"])
        timing_str = f"<code>{snap['timestamp']}</code> ({age_str} ago)"
    else:
        timing_str = "<i>No log timestamp recorded</i>"

    raw_line = snap["log_line"] or "(no log entries yet)"
    if len(raw_line) > 800:
        raw_line = raw_line[:797] + "..."
    safe_line = html.escape(raw_line)

    return (
        f"🩺 <b>Agent Pulse</b>\n"
        f"• <b>State:</b> {state_str}\n"
        f"• <b>Last Log:</b> {timing_str}\n"
        f"• <b>Latest Output:</b>\n<pre>{safe_line}</pre>"
    )


async def pulse_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Report agent running status, elapsed timing, and the single latest log entry."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    await update.message.reply_text(_format_pulse_message(), parse_mode="HTML")


async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    current_model = SESSION_STATE.get("active_model", "gemini-4-hydrogen-high")
    snap = _get_latest_log_snapshot()
    agent_state = (
        f"Running ({_format_duration(snap['task_elapsed'] or 0.0)} elapsed)"
        if snap["running"]
        else "Idle"
    )
    if snap["age_seconds"] is not None and snap["log_line"]:
        preview = snap["log_line"][:120] + ("..." if len(snap["log_line"]) > 120 else "")
        last_log_summary = f"{_format_duration(snap['age_seconds'])} ago — <code>{html.escape(preview)}</code>"
    else:
        last_log_summary = "None"

    status_text = (
        f"🖥️ <b>Workstation Status:</b>\n"
        f"• <b>Host:</b> <code>{platform.node()}</code>\n"
        f"• <b>OS:</b> {platform.system()} {platform.release()}\n"
        f"• <b>Workspace:</b> <code>{html.escape(get_active_cwd())}</code>\n"
        f"• <b>Model:</b> <code>{get_model_display_name(current_model)}</code>\n"
        f"• <b>Agent:</b> {agent_state}\n"
        f"• <b>Last Log:</b> {last_log_summary}\n"
        f"• <b>Memory:</b> {len(CONVERSATION_HISTORY) // 2} active chat turns\n"
        f"• <b>Engine:</b> Antigravity Tray Daemon (Live) 🟢\n"
        f"• <b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await update.message.reply_text(status_text, parse_mode="HTML")

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    welcome = (
        "⚡ <b>FRIDAY QUANTUM MEMORY ONLINE</b>\n\n"
        "Active in your Windows System Tray with native session tracking.\n\n"
        "<b>Available Commands:</b>\n"
        "• <b>Send any message</b> — Full context tracking & autonomous tool execution.\n"
        "• <code>/menu</code> — Open interactive quick-action buttons.\n"
        "• <code>/ss</code> — Capture & send a desktop screenshot.\n"
        "• <code>/model</code> — Switch AI model via interactive selector.\n"
        "• <code>/usage</code> — View token usage & latency telemetry.\n"
        "• <code>/clear</code> — Wipe active chat memory & start fresh.\n"
        "• <code>/remember &lt;info&gt;</code> — Add custom system rule to persistent memory.\n"
        "• <code>/memory</code> — View current memory and active system rules.\n"
        "• <code>/run &lt;cmd&gt;</code> — Run PowerShell commands directly.\n"
        "• <code>/pulse</code> (or <code>/log</code>) — Latest agent log &amp; timing.\n"
        "• <code>/status</code> — Inspect system vitals.\n"
        "• <code>/restart</code> — Restart Friday & Desktop MCP."
    )
    await update.message.reply_text(welcome, parse_mode="HTML")


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show an interactive quick-action button menu."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    keyboard = [
        [
            InlineKeyboardButton("📊 Status", callback_data="menu:status"),
            InlineKeyboardButton("🩺 Latest Log", callback_data="menu:pulse"),
        ],
        [
            InlineKeyboardButton("📸 Screenshot", callback_data="menu:ss"),
            InlineKeyboardButton("📁 Switch Repo", callback_data="menu:repo"),
        ],
        [
            InlineKeyboardButton("⏰ Tasks", callback_data="menu:tasks"),
            InlineKeyboardButton("🤖 Model", callback_data="menu:model"),
        ],
        [
            InlineKeyboardButton("📈 Usage", callback_data="menu:usage"),
            InlineKeyboardButton("🧠 Memory", callback_data="menu:memory"),
        ],
        [
            InlineKeyboardButton("🧹 Clear Chat", callback_data="menu:clear"),
            InlineKeyboardButton("🔄 Restart Friday", callback_data="menu:restart"),
        ],
    ]
    await update.message.reply_text(
        "🎛️ <b>F.R.I.D.A.Y. Control Panel</b>\nTap an action below (or use Telegram's <b>☰ Menu</b>):",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


async def menu_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dispatch inline menu button taps to the corresponding command handler."""
    query = update.callback_query
    if not is_authorized(update):
        await query.answer("⛔ Unauthorized.", show_alert=True)
        return

    await query.answer()
    action = (query.data or "").split(":", 1)[1]
    from types import SimpleNamespace

    proxy_update = SimpleNamespace(
        message=query.message,
        effective_user=update.effective_user,
        effective_chat=update.effective_chat,
    )
    dispatch = {
        "status": status_handler,
        "pulse": pulse_handler,
        "ss": screenshot_handler,
        "repo": repo_handler,
        "tasks": tasks_handler,
        "model": model_handler,
        "usage": usage_handler,
        "memory": memory_handler,
        "clear": clear_handler,
        "restart": restart_handler,
    }
    if handler := dispatch.get(action):
        await handler(proxy_update, context)


async def _register_bot_commands(app) -> None:
    """Populate Telegram's native '/' command menu."""
    commands = [
        BotCommand("menu", "Interactive quick-action button panel"),
        BotCommand("stop", "Stop the currently running task"),
        BotCommand("confirm", "Explicitly confirm a pending sensitive action"),
        BotCommand("repo", "Switch active project folder in workspace"),
        BotCommand("tasks", "View, toggle, or run periodic tasks"),
        BotCommand("ss", "Capture & send desktop screenshot"),
        BotCommand("computer_standby", "Turn displays off (keep Friday awake)"),
        BotCommand("computer_wake", "Wake displays from standby"),
        BotCommand("status", "System vitals & active workspace"),
        BotCommand("pulse", "Latest agent log & timing"),
        BotCommand("model", "Switch AI model"),
        BotCommand("usage", "Token usage & latency telemetry"),
        BotCommand("memory", "View persistent rules & history"),
        BotCommand("remember", "Add persistent rule (/remember <rule>)"),
        BotCommand("clear", "Reset active chat session"),
        BotCommand("run", "Run PowerShell command (/run <cmd>)"),
        BotCommand("restart", "Restart Friday & Desktop MCP"),
        BotCommand("help", "Show command list"),
    ]
    try:
        await app.bot.set_my_commands(commands)
    except Exception as e:
        logger.warning(f"Failed to register Telegram bot commands: {e}")

    notify_flag = SKILL_DIR / "restart_notify.flag"
    if notify_flag.exists():
        try:
            notify_flag.unlink(missing_ok=True)
            if ALLOWED_CHAT_ID:
                await app.bot.send_message(
                    chat_id=int(ALLOWED_CHAT_ID),
                    text="🟢 Friday & Desktop MCP are back online.",
                )
        except Exception as e:
            logger.warning(f"Failed to send post-restart notification: {e}")

    asyncio.create_task(_scheduler_loop(app))


def _list_workspace_repos() -> List[Path]:
    """List REPOS_ROOT and its subdirectories."""
    items: List[Path] = []
    if REPOS_ROOT.is_dir():
        items.append(REPOS_ROOT)
        for child in sorted(REPOS_ROOT.iterdir(), key=lambda p: p.name.lower()):
            if child.is_dir() and not child.name.startswith("."):
                items.append(child)
    return items[:16]


async def repo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switch active working directory between workspace projects."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    current = get_active_cwd()
    repos = _list_workspace_repos()
    keyboard = []
    for r in repos:
        label = f"{REPOS_ROOT.name} (root)" if r == REPOS_ROOT else r.name
        prefix = "✅ " if str(r).lower() == current.lower() else "📁 "
        keyboard.append([InlineKeyboardButton(f"{prefix}{label}", callback_data=f"repo:{r.name if r != REPOS_ROOT else '__root__'}")])

    await update.message.reply_text(
        f"📁 <b>Active Workspace:</b> <code>{current}</code>\nSelect a project:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


async def repo_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle repo switcher button selection."""
    query = update.callback_query
    if not is_authorized(update):
        await query.answer("⛔ Unauthorized.", show_alert=True)
        return

    await query.answer()
    target = (query.data or "").split(":", 1)[1]
    new_path = REPOS_ROOT if target == "__root__" else REPOS_ROOT / target
    if not new_path.is_dir():
        await query.edit_message_text(f"❌ Folder not found: <code>{new_path}</code>", parse_mode="HTML")
        return

    SESSION_STATE["active_cwd"] = str(new_path)
    save_state(SESSION_STATE)
    await query.edit_message_text(
        f"✅ Active workspace switched to:\n<code>{new_path}</code>",
        parse_mode="HTML",
    )


def load_tasks() -> List[Dict[str, Any]]:
    """Load periodic tasks from tasks.json."""
    if TASKS_FILE.exists():
        try:
            data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception as e:
            logger.error(f"Error loading tasks.json: {e}")
    return []


def save_tasks(tasks: List[Dict[str, Any]]) -> None:
    """Persist periodic tasks to tasks.json."""
    try:
        TASKS_FILE.write_text(json.dumps(tasks, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.error(f"Error saving tasks.json: {e}")


def _run_periodic_task_sync(task: Dict[str, Any]) -> Optional[str]:
    """Execute a periodic task synchronously. Returns message text or None if silent."""
    ttype = task.get("type", "")
    alert_only = bool(task.get("alert_only", False))

    if ttype == "git_dirty":
        dirty = []
        if REPOS_ROOT.is_dir():
            for child in sorted(REPOS_ROOT.iterdir(), key=lambda p: p.name.lower()):
                if (child / ".git").exists():
                    res = subprocess.run(
                        ["git", "status", "--porcelain"],
                        cwd=str(child),
                        capture_output=True,
                        text=True,
                        timeout=10,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
                    if lines:
                        dirty.append(f"• <b>{child.name}</b>: {len(lines)} uncommitted change(s)")
        if dirty:
            return "🔔 <b>Git Repo Guardian</b>\n" + "\n".join(dirty)
        return None if alert_only else f"✅ <b>Git Repo Guardian</b>: All repos in {html.escape(str(REPOS_ROOT))} are clean."

    if ttype == "system_health":
        import psutil
        ram = psutil.virtual_memory().percent
        free_gb = psutil.disk_usage("C:\\").free / (1024 ** 3)
        cpu = psutil.cpu_percent(interval=0.5)
        issues = []
        if ram > 90:
            issues.append(f"• RAM high: <b>{ram:.0f}%</b>")
        if free_gb < 15:
            issues.append(f"• C: free space low: <b>{free_gb:.1f} GB</b>")
        if issues:
            return "⚠️ <b>Workstation Health Alert</b>\n" + "\n".join(issues)
        return None if alert_only else f"🖥️ <b>Workstation Health</b>: CPU {cpu:.0f}% | RAM {ram:.0f}% | C: {free_gb:.1f} GB free"

    if ttype == "http_check":
        import urllib.request
        url = task.get("url", "")
        if not url:
            return None
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                code = resp.getcode()
                if code >= 400:
                    return f"⚠️ <b>HTTP Alert</b>: <code>{url}</code> returned {code}"
                return None if alert_only else f"✅ <code>{url}</code> is UP ({code})"
        except Exception as e:
            return f"🚨 <b>HTTP Down</b>: <code>{url}</code> ({e})"

    if ttype == "ai_prompt":
        prompt = task.get("prompt", "").strip()
        if not prompt:
            return None
        out = generate_ai_response(prompt)
        # Prefix with __MD__ so caller chunks raw Markdown before converting to HTML.
        return f"__MD__🤖 **{task.get('name', task.get('id', 'Scheduled Task'))}**\n\n{out}"

    return f"⚠️ Unknown task type: {ttype}"


def _is_task_due(task: Dict[str, Any], now_dt: datetime) -> bool:
    """Return True if an enabled periodic task is due to run at now_dt (local time)."""
    last_run = float(task.get("last_run", 0))
    sched_time = task.get("schedule_time")
    if sched_time:
        try:
            hh, mm = (int(x) for x in str(sched_time).split(":", 1))
            scheduled_today = now_dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if now_dt < scheduled_today:
                return False
            if last_run <= 0:
                return True
            last_dt = datetime.fromtimestamp(last_run)
            interval_days = max(int(task.get("interval_days", 1)), 1)
            return (now_dt.date() - last_dt.date()).days >= interval_days
        except Exception as e:
            logger.warning(f"Invalid schedule_time '{sched_time}' on task {task.get('id')}: {e}")

    interval_sec = max(int(task.get("interval_minutes", 60)), 1) * 60
    return (now_dt.timestamp() - last_run) >= interval_sec


async def _scheduler_loop(app) -> None:
    """Background loop checking tasks.json every 30s and running due periodic tasks."""
    logger.info("Periodic task scheduler started.")
    while True:
        try:
            await asyncio.sleep(30)
            tasks = load_tasks()
            now_dt = datetime.now()
            now = now_dt.timestamp()
            for task in tasks:
                if not task.get("enabled", False):
                    continue
                if _is_task_due(task, now_dt):
                    task["last_run"] = now
                    save_tasks(tasks)
                    try:
                        msg = await asyncio.to_thread(_run_periodic_task_sync, task)
                        if msg and ALLOWED_CHAT_ID:
                            send_fn = lambda text, **kw: app.bot.send_message(chat_id=int(ALLOWED_CHAT_ID), text=text, **kw)
                            if msg.startswith("__MD__"):
                                await _send_formatted_reply(send_fn, msg[6:])
                            else:
                                await _send_html_chunked(send_fn, msg)
                    except Exception as task_err:
                        logger.error(f"Scheduled task '{task.get('id')}' failed: {task_err}", exc_info=True)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Scheduler loop error: {e}", exc_info=True)


def _build_tasks_keyboard(tasks: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    keyboard = []
    for t in tasks:
        tid = t.get("id", "")
        name = t.get("name", tid)
        enabled = t.get("enabled", False)
        icon = "🟢" if enabled else "⏸️"
        if t.get("schedule_time"):
            days = int(t.get("interval_days", 1))
            cadence = f"@{t['schedule_time']}" if days <= 1 else f"@{t['schedule_time']}/{days}d"
        else:
            cadence = f"{t.get('interval_minutes', 60)}m"
        keyboard.append([
            InlineKeyboardButton(f"{icon} {name} ({cadence})", callback_data=f"task:toggle:{tid}"),
            InlineKeyboardButton("⚡ Run", callback_data=f"task:run:{tid}"),
        ])
    return InlineKeyboardMarkup(keyboard)


async def tasks_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display periodic tasks with toggle and run-now buttons."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    tasks = load_tasks()
    if not tasks:
        await update.message.reply_text("📭 No tasks configured in <code>tasks.json</code>.", parse_mode="HTML")
        return

    await update.message.reply_text(
        "⏰ <b>Periodic Tasks</b>\nTap a task to toggle On/Off, or <b>⚡ Run</b> to execute now:",
        reply_markup=_build_tasks_keyboard(tasks),
        parse_mode="HTML",
    )


async def tasks_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle periodic task toggle and run-now callbacks."""
    query = update.callback_query
    if not is_authorized(update):
        await query.answer("⛔ Unauthorized.", show_alert=True)
        return

    parts = (query.data or "").split(":", 2)
    if len(parts) != 3:
        await query.answer()
        return
    _, action, tid = parts
    tasks = load_tasks()
    target = next((t for t in tasks if t.get("id") == tid), None)
    if not target:
        await query.answer("Task not found.", show_alert=True)
        return

    if action == "toggle":
        target["enabled"] = not target.get("enabled", False)
        save_tasks(tasks)
        state_lbl = "enabled" if target["enabled"] else "paused"
        await query.answer(f"{target.get('name', tid)} {state_lbl}.")
        await query.edit_message_reply_markup(reply_markup=_build_tasks_keyboard(tasks))
        return

    if action == "run":
        await query.answer(f"Running {target.get('name', tid)}...")
        target["last_run"] = datetime.now().timestamp()
        save_tasks(tasks)
        temp_task = dict(target, alert_only=False)
        msg = await asyncio.to_thread(_run_periodic_task_sync, temp_task)
        if msg:
            if msg.startswith("__MD__"):
                await _send_formatted_reply(query.message.reply_text, msg[6:])
            else:
                await _send_html_chunked(query.message.reply_text, msg)

def _is_workstation_locked() -> bool:
    """Return True if the Windows input desktop is inaccessible (e.g., Lock Screen)."""
    import ctypes

    user32 = ctypes.windll.user32
    hdesk = user32.OpenInputDesktop(0, False, 0x0001)
    if not hdesk:
        return True
    user32.CloseDesktop(hdesk)
    return False


def _keep_system_awake():
    """Prevent Windows S0 Modern Standby from suspending the system while allowing displays to sleep."""
    import ctypes

    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)


def _standby_displays():
    """Disconnect external monitor (PC screen only) without triggering Windows S0 Modern Standby."""
    _keep_system_awake()
    subprocess.run(
        ["DisplaySwitch.exe", "/internal"],
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=10,
    )


def _extend_displays():
    """Re-enable external monitor in extended mode and wake displays."""
    subprocess.run(
        ["DisplaySwitch.exe", "/extend"],
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=10,
    )
    _wake_display()


def _wake_display():
    """Wake the Windows display from monitor standby before capturing a screenshot."""
    import ctypes
    import time

    # Tell Windows the display/system is needed and send a 1px mouse jiggle + monitor-on signal
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000002 | 0x00000001)
    ctypes.windll.user32.mouse_event(0x0001, 1, 0, 0, 0)
    ctypes.windll.user32.mouse_event(0x0001, -1, 0, 0, 0)
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x0112, 0xF170, -1, 0x0002, 500, None)
    time.sleep(1.5)


async def computer_standby_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switch to internal screen only (external monitor standby) while keeping Friday awake."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    await asyncio.to_thread(_standby_displays)
    await update.message.reply_text("🌙 External monitor turned off (`DisplaySwitch /internal`; Friday staying awake).")


async def computer_wake_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Re-enable external monitor in extended mode and wake displays."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    await asyncio.to_thread(_extend_displays)
    await update.message.reply_text("☀️ External monitor re-enabled (`DisplaySwitch /extend`).")


async def screenshot_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Capture a desktop screenshot and send it directly to Telegram."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    if _is_workstation_locked():
        await update.message.reply_text("🔒 Workstation is currently locked — unlock the PC to capture a screenshot.")
        return

    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="upload_photo")
    except Exception:
        pass

    def _capture_png() -> bytes:
        import io
        from PIL import ImageGrab

        _wake_display()
        img = ImageGrab.grab(all_screens=True)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    try:
        png_bytes = await asyncio.to_thread(_capture_png)
        ts = datetime.now().strftime("%H:%M:%S")
        await update.message.reply_photo(
            photo=png_bytes,
            caption=f"📸 Desktop snapshot ({ts})",
        )
    except Exception as e:
        logger.error(f"Screenshot error: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Screenshot failed: {e}")


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Download a Telegram voice/audio note, transcribe or route to agy, and reply."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    media = update.message.voice or update.message.audio
    if not media:
        return

    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception:
        pass

    voice_dir = SKILL_DIR / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    ext = ".ogg" if update.message.voice else ".mp3"
    voice_path = voice_dir / f"voice_{int(datetime.now().timestamp())}{ext}"

    try:
        tg_file = await context.bot.get_file(media.file_id)
        await tg_file.download_to_drive(custom_path=str(voice_path))
    except Exception as e:
        logger.error(f"Voice download error: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Failed to download voice note: {e}")
        return

    prompt_to_run: Optional[str] = None
    api_key = get_env_secret("GEMINI_API_KEY") or get_env_secret("GOOGLE_API_KEY")
    if api_key:
        def _transcribe() -> Optional[str]:
            from google import genai
            client = genai.Client(api_key=api_key)
            uploaded = client.files.upload(file=str(voice_path))
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    "Transcribe this voice message verbatim in its original language (Turkish or English). Return only the transcript.",
                    uploaded,
                ],
            )
            return (resp.text or "").strip() or None

        try:
            transcript = await asyncio.to_thread(_transcribe)
            if transcript:
                await update.message.reply_text(f"🎙️ <i>Heard:</i> {transcript}", parse_mode="HTML")
                prompt_to_run = transcript
        except Exception as e:
            logger.warning(f"Voice transcription fallback to agy: {e}")

    if not prompt_to_run:
        prompt_to_run = (
            f"{OWNER_NAME} sent a voice message saved at: {voice_path}\n"
            "Listen to this audio file using read_file and respond or execute the request directly."
        )

    try:
        response_text = await asyncio.to_thread(generate_ai_response, prompt_to_run)
    except Exception as e:
        logger.error(f"Error during voice agent execution: {e}", exc_info=True)
        response_text = f"⚠️ Execution error: {e}"

    await _send_formatted_reply(update.message.reply_text, response_text)


async def confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Explicitly confirm a pending sensitive desktop/messaging action."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception:
        pass

    extra = " ".join(context.args).strip() if context.args else ""
    confirm_prompt = "[EXPLICIT CONFIRMATION VIA /confirm]" + (f" Note: {extra}" if extra else " Execute the pending action now.")
    try:
        response_text = await asyncio.to_thread(generate_ai_response, confirm_prompt)
    except Exception as e:
        logger.error(f"Error during /confirm execution: {e}", exc_info=True)
        response_text = f"⚠️ Execution error: {e}"

    await _send_formatted_reply(update.message.reply_text, response_text)


async def stop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Abort the currently running agy task mid-execution."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    global _active_proc, _task_started_at, _stop_requested
    with _proc_lock:
        proc = _active_proc
        if proc is not None and proc.poll() is None:
            _stop_requested = True
        else:
            proc = None

    if proc is None:
        await update.message.reply_text("ℹ️ No task is currently running.")
        return

    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=5,
        )
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

    await update.message.reply_text("🛑 Stopping current task...")


async def restart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restart Friday System Tray Bridge and Desktop MCP."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    await update.message.reply_text("🔄 Restarting Friday & Desktop MCP...")
    threading.Timer(0.8, perform_restart).start()


def run_telegram_loop():
    """Run Telegram bot polling inside dedicated thread loop."""
    try:
        _keep_system_awake()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        app = ApplicationBuilder().token(BOT_TOKEN).concurrent_updates(True).post_init(_register_bot_commands).build()
        app.add_handler(CommandHandler(["start", "help"], start_handler))
        app.add_handler(CommandHandler("menu", menu_handler))
        app.add_handler(CommandHandler(["stop", "cancel"], stop_handler))
        app.add_handler(CommandHandler("confirm", confirm_handler))
        app.add_handler(CommandHandler("repo", repo_handler))
        app.add_handler(CommandHandler("tasks", tasks_handler))
        app.add_handler(CommandHandler("status", status_handler))
        app.add_handler(CommandHandler(["pulse", "log"], pulse_handler))
        app.add_handler(CommandHandler("ss", screenshot_handler))
        app.add_handler(CommandHandler("screenshot", screenshot_handler))
        app.add_handler(CommandHandler("computer_standby", computer_standby_handler))
        app.add_handler(CommandHandler("computer_wake", computer_wake_handler))
        app.add_handler(CommandHandler("restart", restart_handler))
        app.add_handler(CommandHandler("run", run_handler))
        app.add_handler(CommandHandler("model", model_handler))
        app.add_handler(CallbackQueryHandler(menu_callback_handler, pattern="^menu:"))
        app.add_handler(CallbackQueryHandler(repo_callback_handler, pattern="^repo:"))
        app.add_handler(CallbackQueryHandler(tasks_callback_handler, pattern="^task:"))
        app.add_handler(CallbackQueryHandler(model_callback_handler))
        app.add_handler(CommandHandler("usage", usage_handler))
        app.add_handler(CommandHandler(["clear", "reset"], clear_handler))
        app.add_handler(CommandHandler(["system", "remember", "instruct"], system_prompt_handler))
        app.add_handler(CommandHandler("memory", memory_handler))
        app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, voice_handler))
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), prompt_handler))

        logger.info("Telegram background polling started with /model, /usage, and native sessions.")
        app.run_polling(drop_pending_updates=False, stop_signals=None)
    except Exception as e:
        logger.error(f"Fatal error in Telegram loop: {e}", exc_info=True)

def run_tray():
    """Start the Windows System Tray Icon."""
    try:
        image = Image.open(ICON_PATH) if ICON_PATH.exists() else Image.new("RGBA", (64, 64), color=(0, 240, 255, 255))

        menu = pystray.Menu(
            pystray.MenuItem("🖥️ Open Live Terminal", open_terminal, default=True),
            pystray.MenuItem("📜 View Log File", open_logs),
            pystray.MenuItem("🟢 Status: Online", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🔄 Restart Friday", lambda icon, item: perform_restart()),
            pystray.MenuItem("❌ Quit Friday", quit_app),
        )

        icon = pystray.Icon(
            "FridayAssistant",
            image,
            "F.R.I.D.A.Y. - Autonomous AI Assistant",
            menu,
        )

        logger.info("System Tray icon initialized. Entering message loop.")
        icon.run()
    except Exception as e:
        logger.error(f"Fatal error in System Tray run: {e}", exc_info=True)

_mcp_proc: Optional[subprocess.Popen] = None
PID_FILE = SKILL_DIR / "tray.pid"


def _stop_desktop_mcp() -> None:
    """Terminate the Desktop MCP child process (and any stale listener on its port)."""
    global _mcp_proc
    if _mcp_proc is not None and _mcp_proc.poll() is None:
        try:
            _mcp_proc.terminate()
            _mcp_proc.wait(timeout=2)
        except Exception:
            try:
                _mcp_proc.kill()
            except Exception:
                pass
    _mcp_proc = None
    port = int(os.environ.get("FRIDAY_MCP_PORT", "8765"))
    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}",
            ],
            capture_output=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


def perform_restart() -> None:
    """Cleanly stop Desktop MCP, spawn a fresh tray_bridge.pyw, and exit."""
    logger.info("Restarting Friday System Tray Bridge...")
    try:
        (SKILL_DIR / "restart_notify.flag").write_text("1", encoding="utf-8")
    except Exception:
        pass
    _stop_desktop_mcp()
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
    except Exception as e:
        logger.error(f"Failed to respawn tray_bridge: {e}")
        return
    os._exit(0)


def ensure_desktop_mcp() -> None:
    """Start the local Friday Desktop MCP server if not already listening."""
    global _mcp_proc
    host = os.environ.get("FRIDAY_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("FRIDAY_MCP_PORT", "8765"))
    mcp_script = Path(__file__).parent / "desktop_mcp.py"
    if not mcp_script.exists():
        return

    try:
        with socket.create_connection((host, port), timeout=0.5):
            logger.info(f"Desktop MCP already listening on {host}:{port}")
            return
    except OSError:
        pass

    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        _mcp_proc = subprocess.Popen(
            [sys.executable, str(mcp_script)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        atexit.register(_stop_desktop_mcp)
        logger.info(f"Started Desktop MCP (PID {_mcp_proc.pid}) on http://{host}:{port}/mcp")
    except Exception as e:
        logger.warning(f"Failed to start Desktop MCP: {e}")


def main():
    logger.info("Starting Friday System Tray Bridge...")
    try:
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass
    ensure_desktop_mcp()

    bot_thread = threading.Thread(target=run_telegram_loop, daemon=True)
    bot_thread.start()

    run_tray()

if __name__ == "__main__":
    main()
