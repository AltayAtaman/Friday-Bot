"""
Friday Live Terminal Viewer
Renders the Quantum Telemetry HUD and streams real-time agent output.
Closing this window does NOT stop the background Friday agent.
"""

import os
import sys
import json
import time
import psutil
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from friday_config import get_bot_username, get_telegram_username

BOT_USERNAME = get_bot_username()
OWNER_HANDLE = get_telegram_username()

# Set console title
if sys.platform == "win32":
    import ctypes
    ctypes.windll.kernel32.SetConsoleTitleW("F.R.I.D.A.Y. // Quantum Telemetry Terminal")

SKILL_DIR = Path(__file__).parent.parent
LOG_FILE = SKILL_DIR / "friday.log"
STATE_FILE = SKILL_DIR / "session_state.json"
USAGE_FILE = SKILL_DIR / "usage_stats.json"

console = Console(width=96)

def read_json_safe(path: Path, default: dict) -> dict:
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return default

def get_telemetry_gauges() -> Table:
    """Query live CPU, RAM, SSD, active model, and token usage metrics."""
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

    state = read_json_safe(STATE_FILE, {})
    usage = read_json_safe(USAGE_FILE, {})
    active_model = state.get("active_model", "gemini-4-hydrogen-high").upper()
    tot_tokens = usage.get("total_tokens", 0)
    tot_reqs = usage.get("total_requests", 0)

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
    t2.append(f"{active_model} [🟢]\n", style="bold bright_green")

    t2.append("📡 BOT GATEWAY:  ", style="bold gold1")
    t2.append(f"{BOT_USERNAME}\n", style="bold white")

    t2.append("📊 TOKEN USAGE:  ", style="bold bright_magenta")
    t2.append(f"{tot_tokens:,} Tokens ({tot_reqs} Reqs)\n", style="bold white")

    t2.append("📶 CARRIER:      ", style="bold bright_cyan")
    t2.append(f"▂▄▆█ 100% [@{OWNER_HANDLE}]", style="bold green")

    grid.add_row(t1, t2)
    return grid

def print_startup_hud():
    """Print the complete Stark Industries Quantum Telemetry HUD."""
    banner = Text(r"""
  ███████╗██████╗ ██╗██████╗  █████╗ ██╗   ██╗   ░█▀█░█▀▀░█░█
  ██╔════╝██╔══██╗██║██╔══██╗██╔══██╗╚██╗ ██╔╝   ░█▀█░█░█░░█░
  █████╗  ██████╔╝██║██║  ██║███████║ ╚████╔╝    ░▀░▀░▀▀▀░░▀░
  ██╔══╝  ██╔══██╗██║██║  ██║██╔══██║  ╚██╔╝     QUANTUM CORE v4.5
  ██║     ██║  ██║██║██████╔╝██║  ██║   ██║      STARK OS // HYPERLINK
  ╚═╝     ╚═╝  ╚═╝╚═╝╚═════╝ ╚═╝  ╚═╝   ╚═╝      [TELEGRAM AUTONOMOUS LINK]
""", style="bold bright_cyan")
    console.print(banner)

    gauges = get_telemetry_gauges()
    console.print(Panel(gauges, title="[bold bright_cyan]◈ LIVE HARDWARE & TELEMETRY GAUGES ◈[/]", border_style="cyan"))

    wave = Text("◈───/\\╱\\/\\───[ STARK-QUANTUM CARRIER ACTIVE ]───/\\╱\\/\\───◈", style="bold bright_blue")
    console.print(wave, justify="center")
    console.print("[dim cyan]Streaming live agent telemetry from background daemon. (Closing this window will NOT stop Friday)[/]\n", justify="center")

def format_log_line(line: str):
    """Format and syntax-highlight log entries."""
    s = line.rstrip("\r\n")
    if not s:
        return

    if "Incoming prompt:" in s:
        prompt = s.split("Incoming prompt:", 1)[-1].strip()
        time_part = s[:8]
        comm_text = Text()
        comm_text.append("SPECTRUM: [ ▂▃▅▆▇█▇▆▅▃▂ ]  TRANSMISSION: SECURE\n", style="bold bright_magenta")
        comm_text.append(f"\"{prompt}\"", style="bold bright_white")
        console.print()
        console.print(Panel(comm_text, title=f"[bold gold1]📥 INCOMING COMM: @{OWNER_HANDLE} [{time_part}][/]", border_style="gold1", box=box.ROUNDED))
    elif "Incoming interaction:" in s or "Generated response" in s:
        return
    elif "Agent finished" in s:
        console.print("[bold bright_green]✔ [AGENT EXECUTION COMPLETE][/]\n")
    elif "📊 [USAGE]" in s:
        console.print(f"[bold bright_magenta]  {s}[/]")
    elif s.startswith("### "):
        console.print(f"[bold bright_cyan]⚡ {s[4:]}[/]")
    elif "> 💡" in s:
        console.print(f"[bold bright_green]  💡 {s.split('> 💡', 1)[-1].strip()}[/]")
    elif "> 🧠" in s:
        console.print(f"[bold bright_magenta]  🧠 {s.split('> 🧠', 1)[-1].strip()}[/]")
    elif "● Ran (" in s or "Start-Process" in s:
        console.print(f"[bold yellow]  ⚙ [TOOL DISPATCH] {s}[/]")
    elif "Command exited" in s:
        console.print(f"[bold green]  ✔ {s}[/]")
    elif "Error" in s or "failed" in s.lower() or "[ERROR]" in s:
        console.print(f"[bold red]  ❌ {s}[/]")
    else:
        console.print(f"[dim white]  │ {s}[/]")

def follow_log():
    """Stream existing log tail, then follow new lines live."""
    if not LOG_FILE.exists():
        LOG_FILE.touch()

    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
        for line in lines[-25:]:
            format_log_line(line)

        while True:
            line = f.readline()
            if line:
                format_log_line(line)
            else:
                time.sleep(0.2)

def main():
    print_startup_hud()
    try:
        follow_log()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
