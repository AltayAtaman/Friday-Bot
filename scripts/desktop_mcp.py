"""
Friday Local Desktop & Browser MCP Server (Streamable HTTP).

Run this inside your interactive Windows session (e.g. Windows Terminal or via bridge.py)
so tools execute on your real desktop rather than agy's isolated runner.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import shlex
import shutil
import subprocess
import time
import webbrowser
from pathlib import Path
from typing import Literal

import psutil
from mcp.server.mcpserver import MCPServer

mcp = MCPServer(
    name="friday-desktop",
    instructions=(
        "Local desktop and browser control tools running in the workstation owner's "
        "interactive Windows session. Always ask for explicit permission before using "
        "take_screenshot, mouse_click, type_text, or hotkey on personal/messaging apps."
    ),
)

_BROWSER_PATHS = {
    "firefox": [
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Mozilla Firefox\firefox.exe"),
    ],
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ],
    "edge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
}


def _find_browser(browser: str) -> str | None:
    """Resolve executable path for a named browser."""
    exe_names = {"firefox": "firefox.exe", "chrome": "chrome.exe", "edge": "msedge.exe"}
    if exe_name := exe_names.get(browser):
        if found := shutil.which(exe_name):
            return found
    for candidate in _BROWSER_PATHS.get(browser, []):
        if Path(candidate).exists():
            return candidate
    return None


_PROTECTED_GAMES = {"valorant-win64-shipping.exe", "valorant.exe"}


def _game_safety_block() -> str | None:
    """Return a block reason if a protected anti-cheat game is currently running."""
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "").lower()
        if name in _PROTECTED_GAMES:
            return (
                f"Blocked by Vanguard safety lock: '{proc.info['name']}' is running. "
                "Desktop input/focus tools are disabled to prevent anti-cheat flags or alt-tabs."
            )
    return None


@mcp.tool()
def open_url(
    url: str,
    browser: Literal["default", "firefox", "chrome", "edge"] = "default",
) -> str:
    """Open a URL in the live desktop browser."""
    if blocked := _game_safety_block():
        return blocked
    if not url.startswith(("http://", "https://")):
        return f"Rejected URL (must start with http:// or https://): {url}"

    if browser == "default":
        webbrowser.open(url, new=2)
        return f"Opened in default browser: {url}"

    exe = _find_browser(browser)
    if not exe:
        return f"Browser executable not found for '{browser}'."

    subprocess.Popen([exe, url], close_fds=True)
    return f"Opened in {browser} ({exe}): {url}"


@mcp.tool()
def launch_app(target: str, args: str = "") -> str:
    """Launch a Windows application, executable, or file on the live desktop."""
    resolved = shutil.which(target) or target
    arg_list = shlex.split(args, posix=False) if args.strip() else []

    if arg_list:
        subprocess.Popen([resolved, *arg_list], close_fds=True)
        return f"Launched: {resolved} {' '.join(arg_list)}"

    if Path(resolved).exists() or shutil.which(target):
        os.startfile(resolved)  # noqa: S606 - intentional local desktop launcher
        return f"Launched: {resolved}"

    return f"Target not found: {target}"


_user32 = ctypes.windll.user32
_KEYEVENTF_KEYUP = 0x0002
_SW_RESTORE = 9

_VK_MAP = {
    "play_pause": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "stop": 0xB2,
    "mute": 0xAD,
    "volume_down": 0xAE,
    "volume_up": 0xAF,
    "space": 0x20,
    "enter": 0x0D,
    "tab": 0x09,
    "escape": 0x1B,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
}


def _enum_visible_windows() -> list[tuple[int, str]]:
    """Return list of (hwnd, title) for visible top-level windows."""
    results: list[tuple[int, str]] = []
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd: int, _lparam: int) -> bool:
        if _user32.IsWindowVisible(hwnd):
            length = _user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                _user32.GetWindowTextW(hwnd, buf, length + 1)
                if buf.value.strip():
                    results.append((hwnd, buf.value))
        return True

    _user32.EnumWindows(enum_proc(callback), 0)
    return results


def _tap_vk(vk: int) -> None:
    """Press and release a Windows virtual-key code."""
    _user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.03)
    _user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)


@mcp.tool()
def list_windows() -> str:
    """List visible desktop application windows and their titles."""
    windows = _enum_visible_windows()
    if not windows:
        return "No titled visible windows found."
    return "\n".join(f"[{hwnd}] {title}" for hwnd, title in windows)


@mcp.tool()
def focus_window(title_contains: str) -> str:
    """Bring the first visible window whose title matches a substring to the foreground."""
    if blocked := _game_safety_block():
        return blocked
    needle = title_contains.lower()
    for hwnd, title in _enum_visible_windows():
        if needle in title.lower():
            if _user32.IsIconic(hwnd):
                _user32.ShowWindow(hwnd, _SW_RESTORE)
            _user32.SetForegroundWindow(hwnd)
            return f"Focused window [{hwnd}]: {title}"
    return f"No visible window found matching: {title_contains}"


@mcp.tool()
def media_control(
    action: Literal[
        "play_pause", "next", "previous", "stop", "mute", "volume_up", "volume_down"
    ] = "play_pause",
) -> str:
    """Send a Windows media or volume key event (works for YouTube, Spotify, etc.)."""
    if blocked := _game_safety_block():
        return blocked
    vk = _VK_MAP[action]
    _tap_vk(vk)
    return f"Sent media key: {action}"


@mcp.tool()
def send_keys(key: str) -> str:
    """Send a named key ('space', 'enter', 'tab', 'escape', 'left', 'right', 'up', 'down') or single character (e.g. 'k', 'f', 'm') to the active window."""
    if blocked := _game_safety_block():
        return blocked
    normalized = key.strip().lower()
    if normalized in _VK_MAP:
        _tap_vk(_VK_MAP[normalized])
        return f"Sent key: {normalized}"
    if len(key) == 1:
        vk_scan = _user32.VkKeyScanW(ord(key))
        if vk_scan == -1:
            return f"Unsupported character: {key}"
        _tap_vk(vk_scan & 0xFF)
        return f"Sent key: {key}"
    return f"Unknown key '{key}'. Use a single character or one of: {', '.join(_VK_MAP)}"


@mcp.tool()
def take_screenshot() -> str:
    """Capture a full-desktop screenshot and return the saved PNG file path."""
    if blocked := _game_safety_block():
        return blocked
    from PIL import ImageGrab

    out_dir = Path(__file__).resolve().parent.parent / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"screen_{int(time.time())}.png"
    img = ImageGrab.grab(all_screens=True)
    img.save(out_path, format="PNG")
    return str(out_path)


@mcp.tool()
def get_screen_size() -> str:
    """Return the primary screen width and height in pixels."""
    w = _user32.GetSystemMetrics(0)
    h = _user32.GetSystemMetrics(1)
    return f"{w}x{h}"


@mcp.tool()
def mouse_click(
    x: int,
    y: int,
    button: Literal["left", "right"] = "left",
    double: bool = False,
) -> str:
    """Move cursor to (x, y) and click. Always ask the user for permission first."""
    if blocked := _game_safety_block():
        return blocked
    _user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    down, up = (0x0002, 0x0004) if button == "left" else (0x0008, 0x0010)
    clicks = 2 if double else 1
    for _ in range(clicks):
        _user32.mouse_event(down, 0, 0, 0, 0)
        time.sleep(0.03)
        _user32.mouse_event(up, 0, 0, 0, 0)
        if double:
            time.sleep(0.06)
    return f"Clicked {button}{' (double)' if double else ''} at ({x}, {y})"


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUT_UNION)]


@mcp.tool()
def type_text(text: str, press_enter: bool = False) -> str:
    """Type Unicode text into the active window. Always ask the user for permission first."""
    if blocked := _game_safety_block():
        return blocked
    _INPUT_KEYBOARD = 1
    _KEYEVENTF_UNICODE = 0x0004
    _KEYEVENTF_KEYUP_FLAG = 0x0002
    extra = ctypes.pointer(ctypes.c_ulong(0))

    for ch in text:
        code = ord(ch)
        down = _INPUT(_INPUT_KEYBOARD, _INPUT_UNION(ki=_KEYBDINPUT(0, code, _KEYEVENTF_UNICODE, 0, extra)))
        up = _INPUT(_INPUT_KEYBOARD, _INPUT_UNION(ki=_KEYBDINPUT(0, code, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP_FLAG, 0, extra)))
        arr = (_INPUT * 2)(down, up)
        _user32.SendInput(2, arr, ctypes.sizeof(_INPUT))
        time.sleep(0.01)

    if press_enter:
        time.sleep(0.05)
        _tap_vk(0x0D)
    return f"Typed {len(text)} char(s){' + Enter' if press_enter else ''}"


@mcp.tool()
def hotkey(keys: list[str]) -> str:
    """Press a key combination like ['ctrl', 'f'] or ['alt', 'tab']. Always ask the user for permission first."""
    if blocked := _game_safety_block():
        return blocked
    mod_map = {
        "ctrl": 0x11,
        "control": 0x11,
        "alt": 0x12,
        "shift": 0x10,
        "win": 0x5B,
        **_VK_MAP,
    }
    vks: list[int] = []
    for k in keys:
        norm = k.strip().lower()
        if norm in mod_map:
            vks.append(mod_map[norm])
        elif len(norm) == 1:
            scan = _user32.VkKeyScanW(ord(norm))
            if scan == -1:
                return f"Unsupported key in hotkey: {k}"
            vks.append(scan & 0xFF)
        else:
            return f"Unknown key in hotkey: {k}"

    for vk in vks:
        _user32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.02)
    for vk in reversed(vks):
        _user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)
        time.sleep(0.02)
    return f"Sent hotkey: {'+'.join(keys)}"


@mcp.tool()
def restart_friday() -> str:
    """Restart Friday System Tray Bridge and Desktop MCP to apply code or config edits."""
    import sys

    skill_dir = Path(__file__).resolve().parent.parent
    tray_script = Path(__file__).resolve().parent / "tray_bridge.pyw"
    pid_file = skill_dir / "tray.pid"
    mcp_pid = os.getpid()
    try:
        (skill_dir / "restart_notify.flag").write_text("1", encoding="utf-8")
    except Exception:
        pass

    helper = (
        "import os, time, subprocess, pathlib; "
        "time.sleep(1.5); "
        f"pid_file = pathlib.Path(r'{pid_file}'); "
        "pids = [int(pid_file.read_text().strip())] if pid_file.exists() else []; "
        f"pids.append({mcp_pid}); "
        "[subprocess.run(['taskkill', '/PID', str(p), '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for p in pids]; "
        "time.sleep(0.5); "
        "flags = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0); "
        f"subprocess.Popen([r'{sys.executable}', r'{tray_script}'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)"
    )
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(
        [sys.executable, "-c", helper],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )
    return "Scheduled Friday & Desktop MCP restart in ~1.5s."


# Optional Playwright tools (active when `playwright` is installed)
try:
    from playwright.async_api import async_playwright

    _pw_state: dict[str, object] = {"pw": None, "browser": None, "page": None}

    async def _ensure_page():
        if _pw_state["page"] is not None:
            return _pw_state["page"]
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=False)
        page = await browser.new_page()
        _pw_state.update({"pw": pw, "browser": browser, "page": page})
        return page

    @mcp.tool()
    async def browser_goto(url: str) -> str:
        """Navigate the persistent visible Playwright browser to a URL."""
        page = await _ensure_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return f"Navigated to {page.url} | Title: {await page.title()}"

    @mcp.tool()
    async def browser_click(selector: str) -> str:
        """Click an element matching a CSS or text selector in the Playwright browser."""
        page = await _ensure_page()
        await page.click(selector, timeout=10000)
        return f"Clicked: {selector}"

    @mcp.tool()
    async def browser_fill(selector: str, value: str) -> str:
        """Fill an input matching a selector in the Playwright browser."""
        page = await _ensure_page()
        await page.fill(selector, value, timeout=10000)
        return f"Filled: {selector}"

    @mcp.tool()
    async def browser_text(max_chars: int = 4000) -> str:
        """Return visible body text from the current Playwright page."""
        page = await _ensure_page()
        text = await page.inner_text("body")
        return text[:max_chars]

except ImportError:
    pass


if __name__ == "__main__":
    host = os.environ.get("FRIDAY_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("FRIDAY_MCP_PORT", "8765"))
    print(f"Friday Desktop MCP listening on http://{host}:{port}/mcp", flush=True)
    mcp.run(
        transport="streamable-http",
        host=host,
        port=port,
        streamable_http_path="/mcp",
    )
