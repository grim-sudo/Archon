<p align="center">
  <img src="assets/archon_logo.svg" alt="Archon" width="160" height="160">
</p>

<h1 align="center">Archon</h1>

<p align="center"><strong>A natural-language automation framework with an AI planning core.</strong></p>

<p align="center">
  <a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.10+-blue.svg" alt="Python"></a>
  <a href="https://www.rust-lang.org"><img src="https://img.shields.io/badge/Rust-stable-orange.svg" alt="Rust"></a>
  <a href="https://github.com/grim-sudo/Automation/actions"><img src="https://github.com/grim-sudo/Automation/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/Platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey.svg" alt="Platform">
</p>

Archon turns plain-English instructions into real actions on your machine. You
describe an outcome — *"scaffold a React app called dashboard"*, *"delete every
`.tmp` file in Downloads"*, *"build a minimal Debian ISO with nginx"* — and
Archon classifies the intent, plans the steps with an AI model, and executes
them through a hardened, cross-platform automation layer.

It ships three ways to drive it: a **command-line interface**, an **interactive
chatbot**, and a **desktop command center** (a native Tauri app that reads live
system telemetry in Rust and drives the Python engine over an IPC bridge).

---

## Contents

- [Why Archon](#why-archon)
- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [Interfaces](#interfaces)
- [Capabilities](#capabilities)
- [Installation](#installation)
- [Configuration](#configuration)
- [Desktop command center](#desktop-command-center)
- [Command examples](#command-examples)
- [Architecture](#architecture)
- [Python API](#python-api)
- [Security model](#security-model)
- [Development](#development)
- [System requirements](#system-requirements)
- [Troubleshooting](#troubleshooting)
- [Documentation](#documentation)

---

## Why Archon

- **Plain language in, real work out.** No DSL to learn. Ten intent categories
  (create, delete, modify, query, execute, configure, analyze, help, n8n
  workflows, distro builds) route each request to the right handler.
- **AI planning, not AI guessing.** Requests are decomposed into a structured,
  validated `TaskPlan` (Pydantic v2) before anything runs. Malformed model
  output is repaired or falls back to a safe empty plan — it never crashes the
  run.
- **Local-first, multi-provider AI.** Archon runs its planning core on a local
  LLM through [Ollama](https://ollama.com) (`qwen3.5:9b`) by default — no API
  key, no cloud dependency. It can also target FreeLLMAPI, OpenAI, or Anthropic
  via `AI_PROVIDER`, and falls back to local Ollama automatically if a chosen
  cloud provider is unreachable.
- **Persistent memory.** Archon retains what it learns across sessions in a
  local SQLite store (`~/.archon/memory.db`), injected into prompts for every
  provider. It auto-extracts durable facts from conversations and honours
  explicit `remember that …` / `forget …` / `what do you remember` commands.
  Provider-agnostic — memory sits above the backend, so it works the same on
  local or cloud models. Toggle with `MEMORY_ENABLED` / `MEMORY_AUTO_EXTRACT`.
- **Secure by construction.** Every shell interaction goes through a list-form
  `safe_run()` — there is no `shell=True` anywhere in the codebase. Paths are
  validated against traversal and null-byte injection before use.
- **Cross-platform.** Dedicated OS adapters for Linux, macOS, Windows, and Arch
  Linux abstract filesystem, process, and system operations.
- **Two runtimes, one brain.** An optional compiled Rust CLI and a native Tauri
  desktop app both delegate intelligence back to the same Python engine.

---

## How it works

```
  "create a python project called my-api"
                │
                ▼
   ┌───────────────────────────┐
   │  NLP layer                 │  intent classification, slot filling,
   │  (semantic + spell-correct)│  typo tolerance (Levenshtein)
   └───────────────────────────┘
                │  IntentResult
                ▼
   ┌───────────────────────────┐
   │  AI planning layer         │  Ollama / FreeLLMAPI / OpenAI / Anthropic;
   │  (provider + planner)      │  structured TaskPlan with JSON repair
   └───────────────────────────┘
                │  TaskPlan(steps=[…])
                ▼
   ┌───────────────────────────┐
   │  Core engine + plugins     │  dispatch each step to the right plugin
   └───────────────────────────┘
                │
                ▼
   ┌───────────────────────────┐
   │  OS adapters + security    │  safe_run(), PathValidator, permissions
   └───────────────────────────┘
                │
                ▼
          actions on disk / processes / network
```

---

## Quick start

```bash
# 1. Clone and install
git clone https://github.com/grim-sudo/Automation.git
cd Automation
python -m venv .venv && source .venv/bin/activate
pip install -e .

# 2. Set up the local AI model (default backend)
ollama pull qwen3.5:9b        # requires https://ollama.com

# 3. Run your first command
python archon.py run "create a folder named my-project"
```

Full installation and configuration details are in **[SETUP.md](SETUP.md)**.

---

## Interfaces

| Command | Description |
|---------|-------------|
| `archon run "…"` | Execute one natural-language command and exit |
| `archon chatbot` | Multi-turn conversational REPL with streaming, context, and a `/model` switcher |
| `archon gui` | Launch the desktop command center (Tauri app in `ui-tauri/`) |
| `archon batch FILE` | Run a file of commands, one per line |
| `archon n8n …` | Manage n8n workflows — `list`, `create`, `run`, `status` |
| `archon distro …` | Build custom Linux ISOs — `profiles`, `estimate`, `build` |
| `archon mcp …` | Expose Archon as MCP tools (`serve`) or drive a local model against them (`agent`) |
| `archon --version` | Print the installed version |

Global flags apply to any command: `--debug` (verbose logging), `--safe-mode`
(confirm before destructive operations), `--log-file PATH` (structured JSON
logs), `-m/--model` (force a specific model).

> If your venv's `bin/` is on `PATH`, use `archon` directly. Otherwise call
> `python archon.py` from the project root.

---

## Capabilities

Archon recognises ten intent categories and dispatches to the matching plugin:

| Intent | Example triggers |
|--------|------------------|
| `CREATE` | create, make, generate, scaffold, build, setup, new |
| `DELETE` | delete, remove, erase, purge, wipe, uninstall |
| `MODIFY` | move, copy, rename, update, convert, transform |
| `QUERY` | show, list, find, search, check, status, display |
| `EXECUTE` | run, start, deploy, release, trigger, launch, ship |
| `CONFIGURE` | configure, enable, disable, activate, tune, set |
| `ANALYZE` | analyze, audit, inspect, review, assess, measure |
| `HELP` | help, what can you do, commands |
| `N8N_WORKFLOW` | n8n, workflow, list/trigger workflow |
| `BUILD_DISTRO` | build iso, create distro, compile kernel, linux image |

What those intents can actually do:

| Domain | Examples |
|--------|----------|
| **Files & folders** | create, delete, copy, move, rename, list, bulk-create, nested trees |
| **Package management** | install, uninstall, search, list (apt / pacman / pip / npm / brew) |
| **Project scaffolding** | Python, C, Java, React, Next.js, Express, web-scraping, data-science starters, plus AI-driven multi-file generation for arbitrary projects (e.g. a transformer model) |
| **DevOps & infrastructure** | Dockerfile, docker-compose, Kubernetes manifests, CI/CD, Terraform, monitoring |
| **Document generation** | Word (`.docx`), PowerPoint (`.pptx`), Excel (`.xlsx`), PDF |
| **Web automation** | browser control and scraping; text / link / image / table extraction |
| **System administration** | services, firewall, users, permissions, scheduled tasks |
| **Security** | SSL setup, vulnerability scan, compliance check, hardening |
| **n8n workflows** | create, list, run, and check status via the n8n REST API |
| **Linux distro builder** | Debian / Arch / Buildroot ISOs from TOML profiles or a description; picks the desktop/compositor (GNOME, KDE, Hyprland, sway, i3, …) and kernel flavor (standard, hardened, lts, zen/cachyos, kali) from the request, with descriptive auto-naming |

A complete command reference with examples for every capability lives in
**[docs/usage.md](docs/usage.md)**.

---

## Installation

```bash
# Core package (NLP + AI planning + CLI)
pip install -e .

# Optional feature groups
pip install -e ".[dev]"                    # tests, linting, type checking
pip install -e ".[n8n]"                    # n8n workflow bridge
pip install -e ".[distro]"                 # Linux ISO builder
pip install -e ".[web]"                    # Selenium / Playwright browser automation
pip install -e ".[gui]"                    # desktop automation (screen/input control)
pip install -e ".[data]"                   # numpy, pandas, matplotlib, seaborn
pip install -e ".[mcp]"                    # FastMCP server + local agent
pip install -e ".[dev,n8n,distro,web]"     # a full working set
```

| Group | Adds |
|-------|------|
| `dev` | pytest, pytest-asyncio, respx, ruff, mypy |
| `n8n` | aiohttp (webhook listener) |
| `distro` | kconfiglib (kernel config helpers) |
| `web` | selenium, playwright, webdriver-manager |
| `gui` | pyautogui, pynput, pillow, psutil (screen / input automation) |
| `mcp` | fastmcp (MCP server + local Ollama agent) |
| `data` | numpy, pandas, matplotlib, seaborn |

The desktop command center has its own toolchain — see
[Desktop command center](#desktop-command-center).

---

## Configuration

Settings resolve in priority order (highest wins):

```
CLI flag  →  SECTION__FIELD env var  →  flat env var  →  .env file  →  ~/.archon/config.toml  →  default
```

**Minimal `.env`:**

```dotenv
# Default: local AI — no key needed. Just run Ollama:
#   ollama pull qwen3.5:9b
# AI_PROVIDER=ollama

# Or select a cloud provider (falls back to Ollama if unreachable):
# AI_PROVIDER=freellmapi
# FREELLMAPI_URL=http://localhost:3001/v1
# FREELLMAPI_API_KEY=freellmapi-...
# FREELLMAPI_MODEL=auto
# AI_PROVIDER=openai        # OPENAI_API_KEY, OPENAI_MODEL
# AI_PROVIDER=anthropic     # ANTHROPIC_API_KEY, ANTHROPIC_MODEL

# n8n (optional — only for `archon n8n` commands)
N8N_URL=http://localhost:5678
N8N_API_KEY=your-n8n-key
```

**Key `~/.archon/config.toml` settings:**

```toml
[ai]
provider     = "ollama"                    # ollama | freellmapi | openai | anthropic
ollama_url   = "http://127.0.0.1:11434"    # local Ollama server
ollama_model = "qwen3.5:9b"                # default local model
model       = ""          # pin a model; blank uses ollama_model
max_tokens  = 8000
timeout     = 30
max_retries = 3

# Cloud backends (used when 'provider' selects them; fall back to Ollama when down)
# freellmapi_api_key = ""
# freellmapi_model   = "auto"
# openai_api_key     = ""
# anthropic_api_key  = ""

[n8n]
url     = "http://localhost:5678"
api_key = ""

[distro_builder]
output_dir = "./distro_output"

debug     = false
safe_mode = false
```

Generate a commented example with:

```bash
python -c "from archon.config import generate_example_config; generate_example_config()"
# writes ~/.archon/config.example.toml
```

The full configuration reference is in **[SETUP.md](SETUP.md)**.

---

## Desktop command center

Archon's graphical interface is a native desktop app built with **Tauri v2**
(Rust backend) and **Vite + TypeScript** (frontend), living in `ui-tauri/`. It
is not a wrapper around the CLI — it is a purpose-built operations console:

- **Live system telemetry** read natively in Rust via `sysinfo` — CPU, memory,
  processes, network, and filesystem, without blocking the UI.
- **The Python engine as the brain.** AI, capabilities, workflows, distro
  builds, and history are driven over a JSON IPC bridge
  (`python archon.py --tauri-ipc`).
- **Honest data.** Views distinguish real data from unavailable or
  not-yet-implemented backends rather than faking metrics.

### Running it

`archon gui` launches a built binary if one exists; otherwise it prints the
commands below. To build or run it directly:

```bash
cd ui-tauri
npm install
npm run tauri dev       # run in development mode
# or
npm run tauri build     # produce a release binary, then `archon gui` finds it
```

**Prerequisites:** Node.js, a Rust toolchain, and the Tauri system
dependencies for your platform (on Linux: `webkit2gtk-4.1` and `gtk3`). See the
[Tauri prerequisites guide](https://tauri.app/start/prerequisites/).

---

## MCP & autonomous agent

Archon exposes its whole capability surface over the
[Model Context Protocol](https://modelcontextprotocol.io) and can let your local
Ollama model drive those tools autonomously.

Install the optional extra (pulls in FastMCP):

```bash
pip install -e ".[mcp]"
```

**Serve Archon as MCP tools** for any MCP client (Claude Desktop, etc.):

```bash
archon mcp serve                       # stdio transport (default)
archon mcp serve --transport http --port 8000
```

The server publishes three tools: `list_capabilities`, `run_automation` (plain
natural language → every capability, including the OS builder), and
`dispatch_action` (a precise `capability`/`action`/`params` call). It enforces
Archon's permission policy but adds no per-call confirmation — only expose it to
trusted clients.

**Let the local model drive the tools** with a confirmation gate:

```bash
archon mcp agent "create a folder named reports on the desktop"
archon mcp agent                       # interactive session
```

The model (default `qwen3.5:9b`) plans and calls tools itself. Low/medium-risk
actions run automatically; **high-risk or destructive actions** (deleting data,
killing processes, changing system settings, building an OS) pause for explicit
confirmation. Pass `--yes` to auto-approve them (use with care). The model never
reaches a raw shell — every action goes through the capability registry.

---

## Command examples

```bash
# Files & folders
python archon.py run "create a folder named reports"
python archon.py run "create 50 folders named test1 to test50"
python archon.py run "delete all .tmp files in ~/Downloads"
python archon.py run "copy all PDFs from ~/Documents to ~/archive"

# Projects
python archon.py run "create a Python project called my-api"
python archon.py run "scaffold a React app named dashboard"
python archon.py run "generate an Express backend named api-server"

# DevOps
python archon.py run "create a Dockerfile for a Node.js app"
python archon.py run "generate a docker-compose for postgres and redis"
python archon.py run "create a GitHub Actions CI pipeline"

# Documents
python archon.py run "create a Word document named quarterly-report.docx"
python archon.py run "generate a PowerPoint presentation about AI trends"

# Packages
python archon.py run "install nginx"
python archon.py run "search for python packages matching http"

# Web
python archon.py run "scrape https://example.com and extract all links"

# n8n
python archon.py n8n list
python archon.py n8n create "send a Slack message every morning at 9am"
python archon.py n8n run <workflow-id>

# Distro builder
python archon.py distro profiles
python archon.py distro estimate --profile debian_base
sudo python archon.py distro build --profile debian_base --output ./dist

# Typo tolerance (auto-corrected)
python archon.py run "creat a fodler named test"
python archon.py run "intall packge nginx"

# Global flags
python archon.py --debug run "deploy the app"
python archon.py --safe-mode run "delete old logs"
python archon.py --log-file ~/archon.jsonl batch tasks.txt
```

---

## Architecture

```
Automation/
├── archon.py                     Unified entry point: Rust-CLI flag, Tauri IPC, then Python CLI
├── main.py                       Legacy argparse shim → delegates to the Python CLI
├── archon/                       The Python package
│   ├── core/
│   │   ├── engine.py             Main orchestrator (chat / execute / history / status)
│   │   └── plugin_manager.py     Plugin discovery and dispatch
│   ├── ai/
│   │   ├── ollama_integration.py  Local Ollama backend
│   │   ├── openai_compat.py       OpenAI-compatible backend (FreeLLMAPI / OpenAI)
│   │   ├── anthropic_provider.py  Anthropic Messages API backend
│   │   ├── provider_factory.py    Provider selection + fallback to Ollama
│   │   ├── automation_ai.py       Multi-provider AI facade
│   │   ├── memory.py             Persistent cross-session memory (SQLite; provider-agnostic)
│   │   ├── context_manager.py    Sliding-window context (tiktoken)
│   │   ├── response_parser.py    Pydantic v2 TaskPlan / IntentResult + JSON repair
│   │   └── task_planner.py       High-level planning orchestration
│   ├── nlp/
│   │   ├── semantic_engine.py    10-intent classifier
│   │   ├── flexible_processor.py Contextual slot filling
│   │   └── spell_corrector.py    Levenshtein typo correction
│   ├── parsers/                  Structured + AI-enhanced step extraction
│   ├── plugins/
│   │   ├── universal_automation.py   Catch-all automation plugin
│   │   ├── folder_operations.py      Bulk / nested folder operations
│   │   ├── project_generator.py      Project scaffolding
│   │   ├── devops_generator.py       DevOps config generation
│   │   ├── web_automation.py         Selenium / Playwright browser control
│   │   └── n8n_bridge/               n8n REST API async client
│   ├── os_adapters/              Linux / macOS / Windows / Arch adapters + factory
│   ├── distro_builder/           Multi-stage async ISO build pipeline
│   ├── mcp/                       FastMCP server + local Ollama tool-calling agent
│   ├── security/                 safe_run(), PathValidator, permission manager
│   ├── config.py                 pydantic-settings (TOML + env vars + .env)
│   └── ui/                       Terminal interfaces: cli.py, chatbot.py
├── crates/                       Rust workspace
│   ├── archon-cli/               Optional compiled CLI (ARCHON_RUST_CLI=1)
│   └── archon-core/              Shared Rust core (intent / security / config)
├── ui-tauri/                     Desktop command center (Tauri v2 + Vite + TypeScript)
├── userguide.md                  End-to-end user guide (every interface and flag)
├── docs/                         usage.md
└── tests/                        pytest suite (465 tests)
```

**Entry points**

1. `archon.py` (canonical). In order: if `ARCHON_RUST_CLI=1`, exec the compiled
   Rust binary; if `--tauri-ipc`, run the JSON stdin/stdout dispatcher for the
   desktop app; otherwise run the Python Typer CLI.
2. `main.py` — legacy argparse shim that translates flags to the Python CLI.
3. `pyproject.toml` console script — `archon = archon._cli:app`.

---

## Python API

```python
# High-level engine
from archon.core.engine import Archon

engine = Archon()
result = engine.execute("create folder my_project")
engine.shutdown()

# Async AI planning directly
import asyncio
from archon.ai.automation_ai import OllamaAutomationAI

ai = OllamaAutomationAI()
plan = asyncio.run(ai.analyze_automation_request_async("setup a Python project"))
for step in plan.steps:
    print(step.action, step.params)

# n8n bridge
import asyncio
from archon.plugins.n8n_bridge import WorkflowManager, N8nConfig

cfg = N8nConfig(url="http://localhost:5678", api_key="...")
mgr = WorkflowManager(cfg)
workflows = asyncio.run(mgr.list_workflows())
```

---

## Security model

- All subprocess calls go through list-form `safe_run()` — there is no
  `shell=True` anywhere in the codebase.
- `PathValidator` blocks `..` traversal sequences and null-byte injection before
  any path operation.
- `--safe-mode` requires explicit confirmation before any destructive operation.
- API keys are never logged and are kept out of error tracebacks.
- Structured JSON logging (`loguru`) keeps sensitive fields out of plain-text
  output.
- Distro builds require root and print exactly what they will run — review TOML
  profiles before executing with `sudo`.

---

## Development

```bash
pip install -e ".[dev]"

# Run the test suite (465 tests)
pytest tests/ -v -m "not integration and not slow"

# Lint, format, and type-check
ruff check --fix archon/ tests/
ruff format archon/ tests/
mypy archon/ --ignore-missing-imports

# Rust CLI / core
cargo test --workspace
cargo check --workspace
```

---

## System requirements

| | Minimum | Recommended |
|-|---------|-------------|
| Python | 3.10 | 3.12+ |
| RAM | 512 MB | 4 GB |
| Disk | 200 MB | 2 GB (more for distro builds) |
| OS | Ubuntu 20.04 / macOS 12 / Windows 10 | Latest stable |
| Internet | Required for AI calls | Broadband |

**Distro builder (Linux only):** `debootstrap`, `xorriso`, `syslinux`,
`arch-install-scripts`; root access required.

**Desktop command center:** Node.js, a Rust toolchain, and the Tauri platform
dependencies.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `archon: command not found` | Activate the venv (`source .venv/bin/activate`) or call `python archon.py` |
| AI not responding | On the default local backend, verify Ollama: `curl http://127.0.0.1:11434/api/tags` and `ollama pull qwen3.5:9b`. On a cloud `AI_PROVIDER`, check the sidebar **Backend** field — if it fell back to Ollama the provider was unreachable; verify its endpoint/key. Run `archon --debug run "hello"` for full traffic |
| n8n returns 401 | Ensure `N8N_URL` and `N8N_API_KEY` are set in `.env` (flat names, not `N8N__URL`) |
| GUI won't launch | Build it once: `cd ui-tauri && npm install && npm run tauri build`, or `npm run tauri dev` |
| Distro: permission denied | Run `distro build` with `sudo` |
| Import errors | Ensure the venv is active and `pip install -e .` ran from the project root |

More detailed troubleshooting is in **[SETUP.md](SETUP.md)**.

---

## Documentation

| Document | Contents |
|----------|----------|
| [userguide.md](userguide.md) | End-to-end user guide — every interface, command, and flag |
| [SETUP.md](SETUP.md) | Installation, configuration, and development workflow |
| [docs/usage.md](docs/usage.md) | Every capability with example commands |

---

## Support

- **Issues:** [GitHub Issues](https://github.com/grim-sudo/Automation/issues)
- **Local model:** [ollama.com](https://ollama.com) (`ollama pull qwen3.5:9b`)
- **Cloud providers:** set `AI_PROVIDER` to `freellmapi`, `openai`, or `anthropic` — see [SETUP.md](SETUP.md#step-4--set-up-the-ai-backend)
</content>
