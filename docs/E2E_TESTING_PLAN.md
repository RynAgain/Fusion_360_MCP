# E2E Testing Plan -- SSH to Mac for Live Fusion 360 Testing

## Overview

The Fusion 360 MCP application runs as a client-server system:
- **Fusion 360 Addin** (`fusion_addin/addin_server.py`) runs inside Fusion 360 on macOS
- **MCP Server + AI Client** (`main.py`) runs on any machine with Python 3.10+
- They communicate over a TCP socket (default port `12345`)

This plan enables running the full stack with a real Fusion 360 instance on a Mac
accessible via SSH from the Windows development machine.

---

## Prerequisites

### On the Mac (Fusion 360 Host)
1. **Fusion 360** installed and running
2. **Python 3.10+** installed (ships with macOS or via Homebrew)
3. **SSH enabled**: System Settings > General > Sharing > Remote Login = ON
4. **Network reachable** from the Windows dev machine (same LAN/VPN)
5. **Addin installed**: Copy `fusion_addin/` to Fusion 360's addins directory:
   ```
   ~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/Fusion360MCP/
   ```

### On Windows (Dev Machine)
1. **OpenSSH client** (built into Windows 10/11)
2. **GitHub CLI** (`gh`) authenticated (for pushing code)
3. **Python 3.10+ venv** with project deps installed
4. **SSH key pair** generated and public key deployed to Mac

---

## Phase 1 -- Network Discovery & SSH Setup

### 1.1 Find the Mac on the network
```cmd
:: From Windows, scan the local subnet (adjust range for your network)
arp -a
:: Or use the Mac's hostname directly:
ping <mac-hostname>.local
```

### 1.2 Generate SSH key (if not already done)
```cmd
ssh-keygen -t ed25519 -C "kryasatt@windows-dev"
:: Accept defaults, key lands at %USERPROFILE%\.ssh\id_ed25519
```

### 1.3 Deploy key to Mac
```cmd
:: Copy public key to Mac's authorized_keys
type %USERPROFILE%\.ssh\id_ed25519.pub | ssh <user>@<mac-ip> "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

### 1.4 Test SSH connectivity
```cmd
ssh <user>@<mac-ip> "echo SSH OK && uname -a"
```

### 1.5 SSH config shortcut (optional)
Add to `%USERPROFILE%\.ssh\config`:
```
Host mac-fusion
    HostName <mac-ip-or-hostname.local>
    User <username>
    IdentityFile ~/.ssh/id_ed25519
    ForwardAgent yes
```
Then: `ssh mac-fusion`

---

## Phase 2 -- Deploy Code to Mac

### 2.1 Clone repo on Mac (first time)
```cmd
ssh mac-fusion "cd ~/Projects && git clone https://github.com/RynAgain/Fusion_360_MCP.git"
```

### 2.2 Pull latest changes (subsequent runs)
```cmd
ssh mac-fusion "cd ~/Projects/Fusion_360_MCP && git fetch origin && git checkout feature/v1.9.0-improvements && git pull"
```

### 2.3 Install Python dependencies on Mac
```cmd
ssh mac-fusion "cd ~/Projects/Fusion_360_MCP && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
```

### 2.4 Install/update addin
```cmd
ssh mac-fusion "cd ~/Projects/Fusion_360_MCP && python3 scripts/install_addin.py"
```
Or manually symlink:
```cmd
ssh mac-fusion "ln -sfn ~/Projects/Fusion_360_MCP/fusion_addin ~/Library/Application\ Support/Autodesk/Autodesk\ Fusion\ 360/API/AddIns/Fusion360MCP"
```

---

## Phase 3 -- Port Forwarding & Connection

The addin listens on `127.0.0.1:12345` inside the Mac. Two options:

### Option A -- SSH Tunnel (Recommended, Secure)
From Windows, create a tunnel that maps local port 12345 to the Mac's port 12345:
```cmd
ssh -N -L 12345:127.0.0.1:12345 mac-fusion
```
Then the MCP server on Windows connects to `localhost:12345` as if Fusion were local.

### Option B -- Direct Network (Requires addin bind to 0.0.0.0)
Modify `addin_server.py` line `self._sock.bind(("127.0.0.1", 12345))` to
`self._sock.bind(("0.0.0.0", 12345))`, then connect from Windows using the Mac's IP.
**Not recommended** -- opens the socket to the entire network with no encryption.

---

## Phase 4 -- Running E2E Tests

### 4.1 Start Fusion 360 + Addin on Mac
1. Open Fusion 360 on the Mac
2. Go to Utilities > Add-ins > Fusion360MCP > Run
3. The addin starts listening on port 12345

### 4.2 Open SSH Tunnel (from Windows)
```cmd
:: In a dedicated terminal -- leave running
ssh -N -L 12345:127.0.0.1:12345 mac-fusion
```

### 4.3 Run the MCP server locally on Windows
```cmd
python main.py
```
The bridge connects to `localhost:12345` which tunnels to the Mac.

### 4.4 Manual E2E smoke test sequence
Use the web UI or API to send these commands in order:

| # | Command | Expected Result |
|---|---------|-----------------|
| 1 | `get_document_info` | Returns active document name, units |
| 2 | `create_box length=50 width=30 height=20` | Box body appears in Fusion |
| 3 | `get_body_list` | Shows the new box body |
| 4 | `set_parameter name=length value=100` | Parameter updates or creates |
| 5 | `create_sketch plane=XY` | New sketch on XY plane |
| 6 | `add_sketch_circle sketch_name=Sketch1 center_x=0 center_y=0 radius=25` | Circle in sketch |
| 7 | `extrude sketch_name=Sketch1 distance=40` | Cylinder from circle |
| 8 | `take_screenshot` | Returns base64 PNG of viewport |
| 9 | `undo count=3` | Reverts last 3 operations |
| 10 | `export_stl filename=test_export.stl` | STL file created on Mac |

### 4.5 Automated E2E test script (future)
Create `tests/test_e2e_live.py`:
```python
"""
E2E tests that require a live Fusion 360 connection.
Skip automatically if no connection available.

Run with: pytest tests/test_e2e_live.py -v --e2e
"""
import pytest
from fusion.bridge import FusionBridge

@pytest.fixture(scope="session")
def bridge():
    b = FusionBridge()
    try:
        result = b.connect()
        if result.get("error"):
            pytest.skip("No live Fusion 360 connection")
    except Exception:
        pytest.skip("No live Fusion 360 connection")
    yield b
    b.disconnect()

class TestE2ESmoke:
    def test_document_info(self, bridge):
        result = bridge.execute("get_document_info", {})
        assert "document_name" in result

    def test_create_and_list_body(self, bridge):
        bridge.execute("create_box", {"length": 50, "width": 30, "height": 20})
        bodies = bridge.execute("get_body_list", {})
        assert len(bodies.get("bodies", [])) > 0

    def test_set_parameter_creates_new(self, bridge):
        result = bridge.execute("set_parameter", {
            "name": "test_e2e_param",
            "value": "42 mm",
        })
        assert result.get("created") or result.get("success")

    def test_screenshot(self, bridge):
        result = bridge.execute("take_screenshot", {})
        assert result.get("image") or result.get("screenshot")

    def test_undo(self, bridge):
        result = bridge.execute("undo", {"count": 1})
        assert not result.get("error")
```

---

## Phase 5 -- Continuous Integration (Future)

### 5.1 Self-hosted GitHub Actions runner on Mac
```bash
# On the Mac
cd ~/actions-runner
./config.sh --url https://github.com/RynAgain/Fusion_360_MCP --token <TOKEN>
./run.sh
```
Label the runner `macos-fusion` and use it in CI:
```yaml
jobs:
  e2e:
    runs-on: self-hosted
    steps:
      - uses: actions/checkout@v4
      - run: python -m pytest tests/test_e2e_live.py -v --e2e
```

### 5.2 Headless considerations
Fusion 360 requires a display. On macOS, it cannot run fully headless.
Options:
- Keep a logged-in Mac with Fusion 360 always open (dedicated test machine)
- Use VNC/Screen Sharing to keep the GUI session alive even without physical display
- Schedule test runs during off-hours

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `Connection refused` on port 12345 | Addin not running. Check Fusion 360 > Add-ins panel. |
| SSH tunnel drops | Add `ServerAliveInterval 60` to SSH config. Use `autossh` for auto-reconnect. |
| Auth token mismatch | Delete `data/.fusion_mcp_token` on both machines, restart addin. The token file is written by the addin and read by the bridge. |
| Slow response from Fusion 360 | Large models take time. Increase `timeout` in bridge config. |
| `Permission denied` SSH | Check `~/.ssh/authorized_keys` on Mac, verify file permissions (`chmod 700 ~/.ssh`, `chmod 600 ~/.ssh/authorized_keys`). |
| Mac firewall blocking | System Settings > Network > Firewall > allow SSH (port 22). Port 12345 does not need to be open if using SSH tunnel. |

---

## Quick Reference Commands

```cmd
:: === From Windows ===

:: SSH into Mac
ssh mac-fusion

:: Open tunnel (leave running in background terminal)
ssh -N -L 12345:127.0.0.1:12345 mac-fusion

:: Deploy latest code
ssh mac-fusion "cd ~/Projects/Fusion_360_MCP && git pull origin feature/v1.9.0-improvements"

:: Run unit tests on Mac
ssh mac-fusion "cd ~/Projects/Fusion_360_MCP && source .venv/bin/activate && python -m pytest tests/ -x -q"

:: Run E2E tests from Windows (with tunnel active)
python -m pytest tests/test_e2e_live.py -v --e2e

:: Push code from Windows
"C:\Program Files\GitHub CLI\gh.exe" auth status
git push origin feature/v1.9.0-improvements
```
