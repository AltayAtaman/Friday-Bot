---
name: friday-remote
description: >-
  Launches and manages Friday, a personal remote AI assistant bridge connecting Telegram to your local workstation.
  Use when asked to start Friday, connect to Telegram, or manage remote workstation tasks.
---

# Friday Remote AI Bridge

This skill integrates your Antigravity environment with a Telegram bot, turning any terminal into a remote workstation agent.

## Components
- **Instructions & Runbook**: [`SKILL.md`](./SKILL.md)
- **Persona & Memory Template**: [`persona.example.md`](./persona.example.md) (copy to `persona.md`)
- **System Tray Daemon**: [`scripts/tray_bridge.pyw`](./scripts/tray_bridge.pyw)
- **Console Bridge Script**: [`scripts/bridge.py`](./scripts/bridge.py)
- **Desktop MCP Server**: [`scripts/desktop_mcp.py`](./scripts/desktop_mcp.py)

## How to Launch
To run the interactive console bridge:
```powershell
python scripts/bridge.py
```

Or run the Windows system tray daemon in the background:
```powershell
pythonw scripts/tray_bridge.pyw
```

## How to Customize Friday's Persona
Copy `persona.example.md` to `persona.md` and edit:
- Friday's tone, identity, and style
- Project memory and context
- Available command definitions

Changes take effect on the next fresh conversation or via `/clear` on Telegram.
