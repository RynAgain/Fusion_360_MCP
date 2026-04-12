# Artifex360

> **AI-powered design intelligence for Fusion 360.**

An autonomous AI agent system that designs, manipulates, and operates Fusion 360 through Claude.

<!-- badges -->

---

## Features

- **Browser-based chat interface** -- Flask + Socket.IO web app with a responsive Tailwind CSS UI
- **Claude AI agent with 38 MCP tools** for controlling Fusion 360 programmatically
- **7 CAD-specific operating modes** -- Full, Sketch, Modeling, Assembly, Analysis, Export, Scripting
- **Context condensation** -- unlimited-length design sessions via automatic conversation summarization
- **Tool repetition detection** -- prevents stuck agent loops by detecting identical and similar calls
- **Design checkpoints** -- save/restore design states with F360 timeline rollback
- **Task decomposition** -- structured multi-step design plans with progress tracking
- **Hierarchical rules system** -- global, project-level, and mode-specific custom instructions
- **Screenshot capture** -- Claude can "see" the Fusion 360 viewport via `take_screenshot`
- **Dynamic script execution** -- Claude writes and runs Fusion 360 Python scripts on the fly
- **Comprehensive skill document** -- curated F360 API reference injected into the system prompt
- **Conversation persistence** -- chat history saved to disk and loadable across sessions
- **Simulation mode** -- full development and testing workflow without a running Fusion 360 instance
- **Dark/light theme switching** -- CSS variable-based theming
- **Design history timeline visualization** -- auto-refreshes after geometry operations
- **Multi-document support** -- open, switch, create, and close Fusion 360 documents
- **Secure API key storage** -- base64 obfuscation + environment variable priority
- **Agent verification loop** -- pre/post state comparison for reliable tool execution
- **Error classification and auto-recovery** -- auto-undo on geometry failures, enriched error payloads
- **6 geometric query tools** -- detailed body, sketch, face, distance, component, and design inspection
- **Multi-provider support** -- Anthropic Claude + Ollama (local LLMs)
- **Autonomous action protocol** -- agent never stalls; every turn produces tool calls, not just text
- **Auto-continue mechanism** -- detects intent-without-action and nudges the agent to execute
- **Requirements clarification** -- agent asks clarifying questions for vague requests before acting
- **Cross-platform** -- Windows, macOS Intel, macOS Silicon (ARM64)

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
#    http://localhost:8080

# 6. Start chatting with Claude about 3D design!
```

---

## Platform Notes

### Windows

Standard installation. No extra steps required:

```bash
pip install -r requirements.txt
python main.py
```

### macOS -- Apple Silicon (M1 / M2 / M3 / M4)

The default async runtime (`eventlet`) can have build issues on ARM64.
If you run into problems, install `gevent` instead -- the app auto-detects
the best available runtime at startup:

```bash
pip install -r requirements.txt
# If eventlet fails to build:
pip uninstall eventlet -y
pip install gevent>=24.2.1
python main.py          # auto-selects gevent
```

### macOS -- Intel / Linux

Same as Windows:

```bash
pip install -r requirements.txt
python main.py
```

The `/api/status` endpoint reports the active async runtime and platform
information so you can verify which mode is in use.

---

## Fusion 360 Add-in Installation

### Automated (recommended)

```bash
python scripts/install_addin.py            # install
python scripts/install_addin.py uninstall  # remove
```

The script auto-detects the OS and copies files to the correct location.

### Manual

Copy the `fusion_addin/` folder into your Fusion 360 AddIns directory:

   | OS      | Path                                                                                 |
   |---------|--------------------------------------------------------------------------------------|
   | Windows | `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\Fusion360MCP\`                   |
   | macOS   | `~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/Fusion360MCP/`|

Then:

1. Restart Fusion 360.
2. Open **Scripts and Add-Ins** (Shift+S), switch to the **Add-Ins** tab, and enable **Fusion360MCP**.
3. The add-in starts a TCP server on **port 9876** that the bridge connects to.

---

## Available MCP Tools

| Category         | Tools                                                                   |
|------------------|-------------------------------------------------------------------------|
| Document         | `get_document_info`, `save_document`, `list_documents`, `switch_document`, `new_document`, `close_document` |
| Creation         | `create_cylinder`, `create_box`, `create_sphere`                        |
| Sketching        | `create_sketch`, `add_sketch_line`, `add_sketch_circle`, `add_sketch_rectangle`, `add_sketch_arc` |
| Features         | `extrude`, `revolve`, `add_fillet`, `add_chamfer`                       |
| Body Operations  | `mirror_body`, `create_component`, `apply_material`, `delete_body`      |
| Query            | `get_body_list`, `get_timeline`, `get_body_properties`, `get_sketch_info`, `get_face_info`, `measure_distance`, `get_component_info`, `validate_design` |
| Utility          | `undo`, `redo`, `set_parameter`                                         |
| Export           | `export_stl`, `export_step`, `export_f3d`                              |
| Vision           | `take_screenshot`                                                       |
| Scripting        | `execute_script`                                                        |

**38 tools total**

---

## Development

```bash
# Run the full test suite (439 tests, simulation mode -- no Fusion 360 needed)
python -m pytest
```

### Project Structure

```
Fusion_360_MCP/
  main.py                 # Entry point
  requirements.txt        # Python dependencies
  ai/                     # Agent intelligence (~11 modules: claude_client, system_prompt,
                          #   context_manager, modes, task_manager, checkpoint_manager,
                          #   rules_loader, error_classifier, rate_limiter,
                          #   repetition_detector, conversation_manager)
  config/                 # Settings module + config.json + rules/
  data/conversations/     # Persisted chat history
  docs/                   # ARCHITECTURE.md, F360_SKILL.md, AGENT_INTELLIGENCE.md
  fusion/                 # FusionBridge (TCP client to add-in)
  fusion_addin/           # Fusion 360 add-in (TCP server)
  mcp/                    # MCP tool server, tool definitions, tool groups
  tests/                  # Pytest test suite (15 test files, 439 tests)
  web/                    # Flask app, Socket.IO events, routes, templates, static
```

---

## Configuration

- Settings are stored in `config/config.json` (auto-created on first run).
- All settings can be changed at runtime via the **web UI settings panel**.
- Set `ANTHROPIC_API_KEY` in a `.env` file (see `.env.example`) or enter it in the settings panel.
- API keys are stored with base64 obfuscation in `config.json`.
- **Modes**: Switch between CAD modes via the mode selector or `/api/modes/{slug}` API
- **Rules**: Drop `.md` or `.txt` files into `config/rules/` for global rules, `config/rules-{mode}/` for mode-specific rules
- **Checkpoints**: Save/restore design states via `/api/checkpoints` API

### Using Ollama (Local LLMs)
1. Install Ollama: https://ollama.ai
2. Pull a model with tool support: `ollama pull llama3.1`
3. Start Ollama: `ollama serve`
4. In the web UI settings panel, switch to the "Ollama" tab
5. The app will auto-detect running models
6. Tool-calling models: llama3.1, qwen2.5, mistral, command-r

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
- [`docs/AGENT_INTELLIGENCE.md`](docs/AGENT_INTELLIGENCE.md) -- Agent verification, error recovery, and query strategy
- [`FEATURES.md`](FEATURES.md) -- feature roadmap and version history

---

## License

See `LICENSE` file.
