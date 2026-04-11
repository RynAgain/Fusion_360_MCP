# Fusion 360 MCP Agent

> AI agent system that designs, manipulates, and operates Fusion 360 through Claude.

<!-- badges -->

---

## Features

- **Browser-based chat interface** -- Flask + Socket.IO web app with a responsive Tailwind CSS UI
- **Claude AI agent with 27 MCP tools** for controlling Fusion 360 programmatically
- **Screenshot capture** -- Claude can "see" the Fusion 360 viewport via `take_screenshot`
- **Dynamic script execution** -- Claude writes and runs Fusion 360 Python scripts on the fly
- **Comprehensive skill document** -- curated F360 API reference injected into the system prompt
- **Conversation persistence** -- chat history saved to disk and loadable across sessions
- **Simulation mode** -- full development and testing workflow without a running Fusion 360 instance

---

## Architecture Overview

The system is composed of six layers connected over WebSocket and TCP:

```
Browser  <-->  Flask / Socket.IO  <-->  Claude Agent  <-->  MCP Tools
                                                              |
                                                        FusionBridge
                                                              |
                                                           TCP:9876
                                                              |
                                                     F360 Add-in Server
                                                              |
                                                       Fusion 360 API
```

- The **browser** sends user messages over Socket.IO.
- **Flask** routes the message to the **Claude Agent** (Anthropic API with tool use).
- Claude reasons, then invokes **MCP tools** registered on the server.
- Tools call the **FusionBridge**, which forwards commands over **TCP** to the Fusion 360 add-in.
- The **add-in** executes commands against the Fusion 360 API and returns results.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design document.

---

## Quick Start

```bash
# 1. Clone the repository
git clone <repo-url> && cd Fusion_360_MCP

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set your Anthropic API key
#    (via the web UI settings panel or config/config.json)

# 4. Launch the server
python main.py

# 5. Open in your browser
#    http://localhost:5000

# 6. Start chatting with Claude about 3D design!
```

---

## Fusion 360 Add-in Installation

1. Copy the `fusion_addin/` folder into your Fusion 360 AddIns directory:

   | OS      | Path                                                                                 |
   |---------|--------------------------------------------------------------------------------------|
   | Windows | `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\`                                |
   | macOS   | `~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/`             |

2. Restart Fusion 360.
3. Open **Scripts and Add-Ins** (Shift+S), switch to the **Add-Ins** tab, and enable **Fusion360MCP**.
4. The add-in starts a TCP server on **port 9876** that the bridge connects to.

---

## Available MCP Tools

| Category         | Tools                                                                   |
|------------------|-------------------------------------------------------------------------|
| Document         | `get_document_info`, `save_document`                                    |
| Creation         | `create_cylinder`, `create_box`, `create_sphere`                        |
| Sketching        | `create_sketch`, `add_sketch_line`, `add_sketch_circle`, `add_sketch_rectangle`, `add_sketch_arc` |
| Features         | `extrude`, `revolve`, `add_fillet`, `add_chamfer`                       |
| Body Operations  | `mirror_body`, `create_component`, `apply_material`                     |
| Query            | `get_body_list`, `get_timeline`                                         |
| Utility          | `undo`, `redo`, `set_parameter`                                         |
| Export           | `export_stl`, `export_step`, `export_f3d`                              |
| Vision           | `take_screenshot`                                                       |
| Scripting        | `execute_script`                                                        |

---

## Development

```bash
# Run the full test suite (210 tests, simulation mode -- no Fusion 360 needed)
python -m pytest
```

### Project Structure

```
Fusion_360_MCP/
  main.py                 # Entry point
  requirements.txt        # Python dependencies
  ai/                     # Claude client, conversation manager, system prompt
  config/                 # Settings module + config.json
  data/conversations/     # Persisted chat history
  docs/                   # ARCHITECTURE.md, F360_SKILL.md
  fusion/                 # FusionBridge (TCP client to add-in)
  fusion_addin/           # Fusion 360 add-in (TCP server)
  mcp/                    # MCP tool server and tool definitions
  tests/                  # Pytest test suite
  web/                    # Flask app, Socket.IO events, routes, templates, static
```

---

## Configuration

- Settings are stored in `config/config.json` (auto-created on first run).
- All settings can be changed at runtime via the **web UI settings panel**.
- Key settings:

  | Setting          | Description                                    |
  |------------------|------------------------------------------------|
  | `api_key`        | Anthropic API key                              |
  | `model`          | Claude model to use (e.g. `claude-sonnet-4-20250514`) |
  | `system_prompt`  | System prompt (auto-populated with F360 skill) |
  | `simulation_mode`| Run without a live Fusion 360 connection       |

---

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) -- system architecture and design decisions
- [`docs/F360_SKILL.md`](docs/F360_SKILL.md) -- Fusion 360 skill reference injected into the AI agent
- [`FEATURES.md`](FEATURES.md) -- feature roadmap and version history

---

## License

See `LICENSE` file.
