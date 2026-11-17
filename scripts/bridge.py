"""
Friday Remote AI Agent Bridge for Antigravity
Routes incoming Telegram prompts directly into your local Antigravity terminal session and responds via AI.
Equipped with Stark-OS Quantum Telemetry HUD, real-time hardware gauges, and persistent multi-turn memory.
"""

import os
import sys
import json
import socket
import atexit
import asyncio
import subprocess
import platform
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from friday_config import (
    get_agy_exe,
    get_bot_username,
    get_env_secret,
    get_owner_name,
    get_telegram_username,
    is_chat_authorized,
)

import psutil
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

SKILL_DIR = Path(__file__).parent.parent
LOG_FILE = SKILL_DIR / "friday.log"
PERSONA_PATH = SKILL_DIR / "persona.md"
HISTORY_FILE = SKILL_DIR / "chat_history.json"
AGY_EXE = get_agy_exe()
OWNER_NAME = get_owner_name()
OWNER_HANDLE = get_telegram_username()
BOT_USERNAME = get_bot_username()

# Terminal Console initialized with full TrueColor support
console = Console(width=96)

# Setup file logger for persistence
file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))

logger = logging.getLogger("Friday")
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)

BOT_TOKEN = get_env_secret("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_ID = get_env_secret("TELEGRAM_CHAT_ID")

if not BOT_TOKEN or not ALLOWED_CHAT_ID:
    console.print("[bold red]❌ ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env, environment, or registry.[/]")
    sys.exit(1)

def load_persona() -> str:
    if PERSONA_PATH.exists():
        try:
            return PERSONA_PATH.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error reading persona.md: {e}"
    return "Friday: Autonomous Antigravity Assistant."

def load_history() -> List[Dict[str, str]]:
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            logger.error(f"Error loading chat history: {e}")
    return []

def save_history(history: List[Dict[str, str]]) -> None:
    try:
        trimmed = history[-20:]
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(trimmed, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error saving chat history: {e}")

CONVERSATION_HISTORY = load_history()

def get_telemetry_gauges() -> Table:
    """Query live CPU, RAM, and SSD metrics to build futuristic telemetry bars."""
    cpu_pct = psutil.cpu_percent(interval=0.1)
    cpu_cores = psutil.cpu_count(logical=True)
    per_cpu = psutil.cpu_percent(interval=0.1, percpu=True)
    
    bars = [" ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    sparkline = "".join(bars[min(int(p / 12.5), 7)] for p in per_cpu[:16])

    mem = psutil.virtual_memory()
    ram_bar_len = 16
    filled_ram = int((mem.percent / 100) * ram_bar_len)
    ram_bar = "█" * filled_ram + "░" * (ram_bar_len - filled_ram)

    disk = psutil.disk_usage("C:\\")
    filled_disk = int((disk.percent / 100) * 16)
    disk_bar = "█" * filled_disk + "░" * (16 - filled_disk)

    grid = Table(box=box.DOUBLE_EDGE, border_style="bright_blue", expand=True, show_header=False)
    grid.add_column("Hardware", ratio=1)
    grid.add_column("Network & Bot", ratio=1)

    t1 = Text()
    t1.append("⚡ CPU SPECTRUM: ", style="bold cyan")
    t1.append(f"[{sparkline}] ", style="bold bright_green")
    t1.append(f"{cpu_pct}% ({cpu_cores} Cores)\n", style="bold white")

    t1.append("🧠 RAM ALLOC:   ", style="bold magenta")
    t1.append(f"[{ram_bar}] ", style="bold bright_magenta")
    t1.append(f"{mem.percent}% ({mem.used/(1024**3):.1f}/{mem.total/(1024**3):.1f} GB)\n", style="bold white")

    t1.append("💾 SSD STORAGE: ", style="bold yellow")
    t1.append(f"[{disk_bar}] ", style="bold bright_yellow")
    t1.append(f"{disk.percent}% ({disk.used/(1024**3):.0f}/{disk.total/(1024**3):.0f} GB)", style="bold white")

    t2 = Text()
    t2.append("🛰  NEURAL CORE:  ", style="bold cyan")
    t2.append("GEMINI 3.8 FLASH [ONLINE 🟢]\n", style="bold bright_green")

    t2.append("📡 BOT GATEWAY:  ", style="bold gold1")
    t2.append(f"{BOT_USERNAME}\n", style="bold white")

    t2.append("👤 SECURE LINK:  ", style="bold bright_magenta")
    t2.append(f"@{OWNER_HANDLE} [ID: {ALLOWED_CHAT_ID}]\n", style="bold white")

    t2.append("📶 CARRIER:      ", style="bold bright_cyan")
    t2.append(f"▂▄▆█ [{len(CONVERSATION_HISTORY)//2} Turns in Memory]", style="bold green")

    grid.add_row(t1, t2)
    return grid

def print_startup_hud():
    """Print the complete Stark Industries Quantum Telemetry HUD."""
    console.print()
    banner = Text(r"""
  ███████╗██████╗ ██╗██████╗  █████╗ ██╗   ██╗   ░█▀█░█▀▀░█░█
  ██╔════╝██╔══██╗██║██╔══██╗██╔══██╗╚██╗ ██╔╝   ░█▀█░█░█░░█░
  █████╗  ██████╔╝██║██║  ██║███████║ ╚████╔╝    ░▀░▀░▀▀▀░░▀░
  ██╔══╝  ██╔══██╗██║██║  ██║██╔══██║  ╚██╔╝     QUANTUM CORE v4.5
  ██║     ██║  ██║██║██████╔╝██║  ██║   ██║      STARK OS // HYPERLINK
  ╚═╝     ╚═╝  ╚═╝╚═╝╚═════╝ ╚═╝  ╚═╝   ╚═╝      [PERSISTENT MEMORY ENGINE]
""", style="bold bright_cyan")
    console.print(banner)

    gauges = get_telemetry_gauges()
    console.print(Panel(gauges, title="[bold bright_cyan]◈ LIVE HARDWARE & TELEMETRY GAUGES ◈[/]", border_style="cyan"))

    wave = Text("◈───/\\╱\\/\\───[ STARK-QUANTUM CARRIER ACTIVE ]───/\\╱\\/\\───◈", style="bold bright_blue")
    console.print(wave, justify="center")
    console.print("[dim cyan]Listening with persistent memory tracking across turns.[/]\n", justify="center")

def format_agent_log_line(line: str) -> None:
    """Format Antigravity agent output lines with futuristic cyber highlighting."""
    stripped = line.rstrip("\r\n")
    if not stripped:
        return

    if stripped.startswith("### "):
        console.print(f"[bold bright_cyan]⚡ {stripped[4:]}[/]")
    elif "> 💡" in stripped:
        console.print(f"[bold bright_green]  💡 {stripped.split('> 💡', 1)[-1].strip()}[/]")
    elif "> 🧠" in stripped:
        console.print(f"[bold bright_magenta]  🧠 {stripped.split('> 🧠', 1)[-1].strip()}[/]")
    elif "● Ran (" in stripped or "Start-Process" in stripped:
        console.print(f"[bold yellow]  ⚙ [TOOL DISPATCH] {stripped}[/]")
    elif "Command exited with code" in stripped:
        console.print(f"[bold green]  ✔ {stripped}[/]")
    elif "Error" in stripped or "failed" in stripped.lower():
        console.print(f"[bold red]  ❌ {stripped}[/]")
    else:
        console.print(f"[dim white]  │ {stripped}[/]")

def is_authorized(update: Update) -> bool:
    chat_id = str(update.effective_chat.id) if update.effective_chat else "None"
    return is_chat_authorized(chat_id, ALLOWED_CHAT_ID, logger)

def generate_ai_response(user_prompt: str) -> str:
    """Run Antigravity CLI in full agent mode with multi-turn memory context."""
    persona = load_persona()
    
    # Format rolling conversation history
    history_lines = []
    if CONVERSATION_HISTORY:
        for turn in CONVERSATION_HISTORY[-10:]:
            role = OWNER_NAME if turn.get("role") == "user" else "Friday"
            history_lines.append(f"{role}: {turn.get('text', '')}")
    
    history_context = "\n".join(history_lines) if history_lines else "None (Fresh conversation)"

    combined_prompt = (
        f"You are Friday, {OWNER_NAME}'s remote AI workstation assistant.\n"
        f"Persona & System Directives:\n{persona}\n\n"
        f"=== CORE INTERACTION RULES ===\n"
        f"1. CONTEXT MEMORY: You have full access to our ongoing conversation history below. Always use it to understand follow-ups, jokes, references, and pronouns.\n"
        f"2. ZERO BOILERPLATE: Never output robotic startup menus (like 'Systems online, Boss... Available commands...') unless explicitly asked.\n"
        f"3. TONE: Witty, concise, brilliant, and confident like Tony Stark's F.R.I.D.A.Y.\n"
        f"4. ACTIONS: If the user asks you to do something on this PC (play sounds, open apps/browser, check files, run commands), execute them directly with your tools without asking for confirmation.\n\n"
        f"=== RECENT CONVERSATION HISTORY ===\n"
        f"{history_context}\n\n"
        f"=== NEW MESSAGE FROM {OWNER_NAME.upper()} ===\n"
        f"{OWNER_NAME}: {user_prompt}\n\n"
        f"Friday's Direct Response:"
    )

    console.print()
    console.print(Panel(
        f"[bold bright_green]⚡ EXECUTING ANTIGRAVITY AUTONOMOUS AGENT...[/]\n[dim white]Memory Context: {len(CONVERSATION_HISTORY)//2} previous turns[/]",
        title="[bold bright_cyan]◈ LIVE AGENT NEURAL STREAM ◈[/]",
        border_style="bright_cyan"
    ))

    # 1. Try agy CLI in full agent mode with live output streaming
    if os.path.exists(AGY_EXE):
        try:
            cmd = [
                AGY_EXE,
                "--dangerously-skip-permissions",
                "-p",
                combined_prompt
            ]
            
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1
            )

            collected_lines = []
            for line in iter(proc.stdout.readline, ""):
                format_agent_log_line(line)
                collected_lines.append(line)

            proc.stdout.close()
            proc.wait(timeout=120)

            console.print("[bold bright_green]✔ [AGENT EXECUTION COMPLETE][/]\n")

            full_text = "".join(collected_lines).strip()
            if full_text:
                CONVERSATION_HISTORY.append({"role": "user", "text": user_prompt})
                CONVERSATION_HISTORY.append({"role": "friday", "text": full_text})
                save_history(CONVERSATION_HISTORY)
                return full_text
        except Exception as e:
            logger.error(f"agy execution error: {e}", exc_info=True)
            console.print(f"[bold red]❌ Agent execution error: {e}[/]")

    # 2. Fallback to google.genai if GEMINI_API_KEY is available
    api_key = get_env_secret("GEMINI_API_KEY") or get_env_secret("GOOGLE_API_KEY")
    if api_key:
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=user_prompt,
                config={"system_instruction": f"{persona}\n\nRecent History:\n{history_context}"}
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

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    welcome = (
        "⚡ <b>FRIDAY QUANTUM MEMORY ONLINE</b>\n\n"
        "Your PC workstation terminal is listening with full multi-turn conversation memory.\n\n"
        "<b>Available Commands:</b>\n"
        "• <b>Send any message</b> — Full context tracking & autonomous tool execution.\n"
        "• <code>/clear</code> — Wipe active chat memory.\n"
        "• <code>/remember &lt;info&gt;</code> — Add custom system rule to persistent memory.\n"
        "• <code>/memory</code> — View current memory and active system rules.\n"
        "• <code>/run &lt;cmd&gt;</code> — Run PowerShell commands directly.\n"
        "• <code>/status</code> — Inspect system vitals."
    )
    await update.message.reply_text(welcome, parse_mode="HTML")

async def clear_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wipe active chat memory."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    global CONVERSATION_HISTORY
    count = len(CONVERSATION_HISTORY)
    CONVERSATION_HISTORY = []
    save_history([])
    
    logger.info(f"Chat memory cleared ({count} turns wiped).")
    await update.message.reply_text(
        f"🧹 <b>Conversation memory wiped clean.</b> ({count // 2} previous exchanges cleared).\n\n"
        f"Standing by with a fresh slate, {OWNER_NAME}.",
        parse_mode="HTML"
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
            parse_mode="HTML"
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
            parse_mode="HTML"
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
    persona_content = load_persona()
    
    notes_section = ""
    if "## 3. Persistent Memory & Notes" in persona_content:
        notes_section = persona_content.split("## 3. Persistent Memory & Notes", 1)[-1].strip()

    reply = (
        f"🧠 <b>Friday Memory Status:</b>\n\n"
        f"• <b>Active Chat Turns:</b> {turn_count} turns retained in memory\n\n"
        f"<b>Persistent System Rules & Notes:</b>\n<pre>{notes_section or 'No custom notes'}</pre>\n\n"
        f"💡 <i>Use <code>/clear</code> to wipe chat history, or <code>/remember &lt;info&gt;</code> to add rules.</i>"
    )
    await update.message.reply_text(reply, parse_mode="HTML")

async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    mem = psutil.virtual_memory()
    cpu_pct = psutil.cpu_percent(interval=0.1)
    status_text = (
        f"🖥️ <b>Workstation Status:</b>\n"
        f"• <b>Host:</b> <code>{platform.node()}</code>\n"
        f"• <b>CPU:</b> {cpu_pct}%\n"
        f"• <b>RAM:</b> {mem.percent}% ({mem.used/(1024**3):.1f}/{mem.total/(1024**3):.1f} GB)\n"
        f"• <b>Workspace:</b> <code>{os.getcwd()}</code>\n"
        f"• <b>Memory:</b> {len(CONVERSATION_HISTORY) // 2} active chat turns\n"
        f"• <b>Engine:</b> Antigravity Agent Tool Execution (Live) 🟢\n"
        f"• <b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await update.message.reply_text(status_text, parse_mode="HTML")

async def run_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    if not context.args:
        await update.message.reply_text("Usage: <code>/run &lt;command&gt;</code>", parse_mode="HTML")
        return

    cmd = " ".join(context.args)
    logger.info(f"Executing remote command: {cmd}")
    console.print(Panel(f"[bold yellow]{cmd}[/]", title="[bold gold1]⚙ MANUAL /RUN DISPATCH[/]", border_style="gold1"))

    await update.message.reply_text(f"⚙️ <i>Executing on PC:</i> <code>{cmd}</code>...", parse_mode="HTML")

    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True,
            text=True,
            timeout=60
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
    except subprocess.TimeoutExpired:
        await update.message.reply_text("⏱️ <i>Execution timed out (60s).</i>", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ <i>Execution error:</i> {e}", parse_mode="HTML")

async def prompt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles incoming natural language prompts from Telegram and executes via Antigravity Agent."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized device.")
        return

    prompt = update.message.text
    if not prompt:
        return

    time_str = datetime.now().strftime("%H:%M:%S")
    username = update.effective_user.username or OWNER_HANDLE
    logger.info(f"Incoming prompt: {prompt}")

    # Render Glowing Holographic Incoming Comm Card
    comm_text = Text()
    comm_text.append("SPECTRUM: [ ▂▃▅▆▇█▇▆▅▃▂ ]  TRANSMISSION: SECURE\n", style="bold bright_magenta")
    comm_text.append(f"\"{prompt}\"", style="bold bright_white")

    comm_panel = Panel(
        comm_text,
        title=f"[bold gold1]📥 INCOMING COMM: @{username} [{time_str}][/]",
        border_style="gold1",
        box=box.ROUNDED
    )
    console.print()
    console.print(comm_panel)

    # Indicate typing to Telegram
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception as e:
        logger.warning(f"Could not send typing action: {e}")

    # Generate response asynchronously with live terminal stream
    try:
        response_text = await asyncio.to_thread(generate_ai_response, prompt)
    except Exception as e:
        logger.error(f"Error during agent execution: {e}", exc_info=True)
        response_text = f"⚠️ Execution error: {e}"

    # Render Glowing Response Card
    console.print(Panel(
        f"[italic bright_white]{response_text}[/]",
        title="[bold bright_cyan]🤖 FRIDAY TRANSMISSION TO TELEGRAM[/]",
        border_style="bright_blue",
        box=box.ROUNDED
    ))

    # Deliver reply chunks safely to Telegram
    for i in range(0, max(len(response_text), 1), 4000):
        chunk = response_text[i:i + 4000]
        try:
            await update.message.reply_text(chunk)
        except Exception as e:
            logger.error(f"Failed to send reply chunk: {e}", exc_info=True)
            await update.message.reply_text(chunk, parse_mode=None)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error causing updates."""
    logger.error("Exception while handling an update:", exc_info=context.error)

def ensure_desktop_mcp() -> None:
    """Start the local Friday Desktop MCP server if not already listening."""
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
        proc = subprocess.Popen(
            [sys.executable, str(mcp_script)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        atexit.register(lambda: proc.poll() is None and proc.terminate())
        logger.info(f"Started Desktop MCP (PID {proc.pid}) on http://{host}:{port}/mcp")
    except Exception as e:
        logger.warning(f"Failed to start Desktop MCP: {e}")


def main():
    print_startup_hud()
    ensure_desktop_mcp()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler(["start", "help"], start_handler))
    app.add_handler(CommandHandler("status", status_handler))
    app.add_handler(CommandHandler("run", run_handler))
    app.add_handler(CommandHandler(["clear", "reset"], clear_handler))
    app.add_handler(CommandHandler(["system", "remember", "instruct"], system_prompt_handler))
    app.add_handler(CommandHandler("memory", memory_handler))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), prompt_handler))

    app.run_polling(drop_pending_updates=False)

if __name__ == "__main__":
    main()
