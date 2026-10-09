# Archon — Setup & Installation Guide

> **Quick links**: [README](README.md) | [User guide](userguide.md) | [Usage examples](docs/usage.md)

---

## System Requirements

### Minimum

- Python 3.10 or higher
- 512 MB available RAM
- 200 MB free disk space
- Windows 10+, Ubuntu 20.04+, or macOS 12+

### Recommended

- Python 3.12+
- 4 GB available RAM
- 2 GB free disk space (much more for distro builds — see [Distro Builder](#custom-linux-distro-builder))
- An AI backend: local via [Ollama](https://ollama.com) (install Ollama and
  pull `qwen3.5:9b`, no API key), or a cloud provider — FreeLLMAPI, OpenAI, or
  Anthropic (see [Step 4](#step-4--set-up-the-ai-backend)).

---

## Step 1 — Clone the Repository

```bash
git clone https://github.com/grim-sudo/Automation.git
cd Automation
```

---

## Step 2 — Create a Virtual Environment

A virtual environment keeps Archon's dependencies isolated from your system Python.

```bash
# Create the venv (name it whatever you like; the project uses "venv" by convention)
python -m venv venv

# Activate it
source venv/bin/activate        # Linux / macOS
venv\Scripts\activate           # Windows (PowerShell)
venv\Scripts\activate.bat       # Windows (CMD)
```

You should see `(venv)` at the start of your prompt once activated.

> **Arch Linux note:** The system Python is managed by pacman (PEP 668 restriction). Always use a
> virtual environment — never install packages into the system Python.

---

## Step 3 — Install Dependencies

```bash
# Core package only (NLP + AI layer + CLI)
pip install -e .

# Core + development tools (tests, linting, type checking) ← recommended
pip install -e ".[dev]"

# Add optional feature groups as needed:
pip install -e ".[dev,n8n]"             # + n8n workflow bridge
pip install -e ".[dev,distro]"          # + Linux ISO builder
pip install -e ".[dev,gui]"             # + desktop automation (screen/input control)
pip install -e ".[dev,web]"             # + Selenium / Playwright
pip install -e ".[dev,n8n,distro,web]"  # a full working set
```

### What each extras group adds

| Group | Additional packages |
|-------|-------------------|
| `dev` | pytest, pytest-asyncio, respx, ruff, mypy |
| `n8n` | aiohttp (webhook listener) |
| `distro` | kconfiglib (kernel config helpers) |
| `gui` | pyautogui, pynput, pillow, psutil (desktop automation) |
| `web` | selenium, playwright, webdriver-manager |
| `data` | numpy, pandas, matplotlib, seaborn |

---

## Step 4 — Set Up the AI Backend

Archon supports four AI backends, selected with `AI_PROVIDER`:

| `AI_PROVIDER` | Backend | Key required | Notes |
|---------------|---------|--------------|-------|
| `ollama` (default) | Local [Ollama](https://ollama.com) server | No | Fully offline; zero config |
| `freellmapi` | [FreeLLMAPI](https://github.com/tashfeenahmed/freellmapi) OpenAI-compatible router | Unified FreeLLMAPI key | Aggregates many free providers behind one endpoint |
| `openai` | OpenAI (or any OpenAI-compatible endpoint) | OpenAI API key | |
| `anthropic` | Anthropic Messages API | Anthropic API key | |

Whichever cloud provider you pick, **Archon falls back to local Ollama
automatically** if that provider is unreachable or unconfigured — so the app
keeps working. Chain-of-thought / "thinking" tokens are never displayed.

### Option A — Local Ollama (default, no API key)

1. Install [Ollama](https://ollama.com/download) for your platform.
2. Pull the default model and make sure the server is running:

```bash
ollama pull qwen3.5:9b
ollama serve          # usually starts automatically after install
```

Archon talks to Ollama at `http://127.0.0.1:11434`. That's it — no `.env`
needed for AI. To use a different local model or URL:

```dotenv
# .env (all optional; these are the defaults)
AI_PROVIDER=ollama
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3.5:9b
```

### Option B — FreeLLMAPI (OpenAI-compatible router)

Run FreeLLMAPI locally (defaults to `http://localhost:3001`), then point Archon
at it:

```dotenv
AI_PROVIDER=freellmapi
FREELLMAPI_URL=http://localhost:3001/v1
FREELLMAPI_API_KEY=freellmapi-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
FREELLMAPI_MODEL=auto      # 'auto' lets the router pick the best available model
```

### Option C — OpenAI

```dotenv
AI_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
# OPENAI_URL=https://api.openai.com/v1   # override for other OpenAI-compatible endpoints
```

### Option D — Anthropic

```dotenv
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-3-5-sonnet-latest
```

### n8n integration (optional — any provider)

```dotenv
# n8n integration (optional — only needed for archon n8n commands)
N8N_URL=http://localhost:5678
N8N_API_KEY=your-n8n-api-key
```

> **Important:** Always use the flat variable names `N8N_URL` and `N8N_API_KEY` (not
> `N8N__URL`). The compat layer maps these to the correct nested config fields.

> **Security:** Never commit real API keys. Put them in `.env` (which is
> gitignored) or `~/.archon/config.toml`, not in source or tracked files.

Archon reads `.env` automatically on startup.

### Config file

Generate an example config:

```bash
python -c "from archon.config import generate_example_config; generate_example_config()"
# Writes ~/.archon/config.example.toml
```

Copy and edit it:

```bash
cp ~/.archon/config.example.toml ~/.archon/config.toml
```

```toml
[ai]
provider     = "ollama"                    # ollama | freellmapi | openai | anthropic
ollama_url   = "http://127.0.0.1:11434"
ollama_model = "qwen3.5:9b"

# Cloud providers (used when 'provider' names them; fall back to Ollama when down)
freellmapi_url     = "http://localhost:3001/v1"
freellmapi_api_key = ""
freellmapi_model   = "auto"
openai_api_key     = ""
openai_model       = "gpt-4o-mini"
anthropic_api_key  = ""
anthropic_model    = "claude-3-5-sonnet-latest"

[n8n]
url     = "http://localhost:5678"
api_key = "your-n8n-api-key"
```

---

## Step 5 — Verify the Installation

```bash
# Print version
python archon.py --version
# Archon v2.0.0

# Show help
python archon.py --help

# Run a simple test
python archon.py run "create a folder named archon_test"
ls archon_test       # Linux/macOS — should exist
dir archon_test      # Windows

# Clean up
python archon.py run "delete the folder archon_test"
```

> **Note:** If you installed with `pip install -e .` and your venv's `bin/` is on `PATH`, you can
> use `archon` directly instead of `python archon.py`.

---

## Running Modes

### `archon run` — Single Command

Execute one natural-language command and exit.

```bash
python archon.py run "create a folder named reports"
python archon.py run "copy all PDF files to archive"
python archon.py run "show disk usage on /"

# Per-command flags
python archon.py run "delete all logs" --safe-mode      # confirm before destructive ops
python archon.py run "build the project" --debug        # verbose output
python archon.py run "summarise this" -m qwen3.5:9b  # force a specific model
```

### `archon chatbot` — Interactive Mode

Multi-turn conversational interface with context carried across turns.

```bash
python archon.py chatbot
```

Special commands inside the chatbot:

```
/help       — show available commands
/status     — show current model and session status
/model      — list / switch the local Ollama model
/plugins    — list loaded plugins
/config     — show config directory and AI settings
/history    — show recent messages
/context    — show session context
/cd <path>  — change working directory
/pwd        — print working directory
/ls [path]  — list a directory
/explain    — explain the last command
/clear      — redraw the console
/exit       — quit (or Ctrl+D)
```

Input is a keyboard-first composer (arrow-key history, slash-command and path
completion, multiline via Alt+Enter). Tool execution streams into a live
operational view, replies render as Markdown with syntax-highlighted code, and
errors read as short sentences. Set `ARCHON_ASCII=1` to force plain-ASCII
glyphs on terminals without full Unicode support.

### `archon gui` — Desktop Command Center

Archon's desktop UI is a native app built with Tauri v2 (Rust backend) and
Vite + TypeScript (frontend), located in `ui-tauri/`. It reads live system
telemetry natively in Rust and drives the Python engine over a JSON IPC bridge.

`archon gui` launches a built binary if one exists; otherwise it prints the
commands to build or run it.

**Prerequisites:** Node.js, a Rust toolchain (`rustup`), and the Tauri platform
dependencies. On Debian/Ubuntu:

```bash
sudo apt-get install libwebkit2gtk-4.1-dev build-essential curl wget file \
  libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev
```

On Arch Linux:

```bash
sudo pacman -S webkit2gtk-4.1 base-devel curl wget file openssl \
  libayatana-appindicator librsvg
```

See the [Tauri prerequisites guide](https://tauri.app/start/prerequisites/) for
macOS and Windows.

**Run it:**

```bash
cd ui-tauri
npm install
npm run tauri dev      # run in development mode
# or:
npm run tauri build    # produce a release binary, then `archon gui` finds it
```

### `archon batch` — Bulk Execution

Run multiple commands from a plain-text file. Lines starting with `#` and blank lines are skipped.

```text
# tasks.txt
create a folder named reports in ~/Documents
create a folder named archive in ~/Documents
take a screenshot and save to ~/screenshots/before.png
```

```bash
python archon.py batch tasks.txt
python archon.py batch tasks.txt --stop-on-error   # halt on first failure
python archon.py batch tasks.txt --safe-mode       # confirm destructive steps
python archon.py batch tasks.txt --debug           # verbose per-step output
```

---

## n8n Workflow Integration

Requires a running [n8n](https://n8n.io) instance.

### Quick n8n setup (Docker)

```bash
docker run -it --rm -p 5678:5678 -e N8N_BASIC_AUTH_ACTIVE=false n8nio/n8n
```

Open `http://localhost:5678`, go to **Settings → API**, create an API key, and add it to your `.env`:

```dotenv
N8N_URL=http://localhost:5678
N8N_API_KEY=eyJhbGciOiJIUzI1NiIs...
```

### n8n CLI commands

```bash
# List all workflows
python archon.py n8n list

# Create a workflow from natural language
python archon.py n8n create "every hour fetch weather data and post to Slack"

# Trigger a workflow manually
python archon.py n8n run <workflow-id>

# Check execution status
python archon.py n8n status <workflow-id>
```

---

## Custom Linux Distro Builder

Requires Linux, root access, and host build tools.

### Install host tools

```bash
# Debian / Ubuntu
sudo apt-get install debootstrap xorriso syslinux-common isolinux

# Arch Linux
sudo pacman -S arch-install-scripts xorriso syslinux
```

### Install the Python extra

```bash
pip install -e ".[distro]"
```

### Build commands

```bash
# List available profiles
python archon.py distro profiles

# Estimate build time and disk usage (dry run)
python archon.py distro estimate --profile minimal

# Build from a named profile
sudo python archon.py distro build --profile debian_base --output ~/isos/

# Build from natural language
sudo python archon.py distro build --nl "minimal Debian ISO with nginx, headless, no GUI"
```

### Custom build profile

Create `~/.archon/profiles/my-server.toml`:

```toml
[meta]
name = "my-server"
base = "debian"   # debian | arch | unix (buildroot)

[kernel]
version = "latest-stable"
patches = []

[kernel.kconfig]
CONFIG_KVM = "y"
CONFIG_MODULES = "y"

[packages]
base     = ["base-files", "systemd", "openssh-server", "nginx"]
optional = ["curl", "git", "htop"]

[build]
hostname = "my-server"
locale   = "en_US.UTF-8"
timezone = "UTC"
desktop  = ""
```

---

## Configuration Reference

### Priority order (highest wins)

1. CLI flags (`--debug`, `--safe-mode`, `--log-file`)
2. `SECTION__FIELD` env vars — e.g. `AI__MAX_TOKENS=16000`
3. Flat legacy env vars — `AI_PROVIDER`, `OLLAMA_URL`, `OLLAMA_MODEL`, `FREELLMAPI_*`, `OPENAI_*`, `ANTHROPIC_*`, `N8N_API_KEY`, `N8N_URL`, `MAX_RETRIES`
4. `~/.archon/config.toml`
5. Built-in defaults

### Full config skeleton

```toml
# ── Global ────────────────────────────────────────────────────────────────────
debug             = false    # verbose debug logging to console
safe_mode         = false    # require confirmation before destructive operations
continue_on_error = false    # keep going in batch mode after a failure
# log_file = "/var/log/archon.jsonl"   # write JSON logs to file

# ── AI ────────────────────────────────────────────────────────────────────────
[ai]
provider     = "ollama"                    # ollama | freellmapi | openai | anthropic
ollama_url   = "http://127.0.0.1:11434"    # local Ollama server
ollama_model = "qwen3.5:9b"                # default local model

# Cloud backends — used when 'provider' selects them; each falls back to local
# Ollama when its endpoint is unreachable or its key is missing.
freellmapi_url     = "http://localhost:3001/v1"
freellmapi_api_key = ""                    # or FREELLMAPI_API_KEY env var
freellmapi_model   = "auto"                # 'auto' = router picks the model
openai_url         = "https://api.openai.com/v1"
openai_api_key     = ""                    # or OPENAI_API_KEY env var
openai_model       = "gpt-4o-mini"
anthropic_url      = "https://api.anthropic.com/v1"
anthropic_api_key  = ""                    # or ANTHROPIC_API_KEY env var
anthropic_model    = "claude-3-5-sonnet-latest"

# Pin a specific model; blank uses ollama_model
model          = ""
fallback_chain = []

max_tokens  = 8000    # sliding-window token budget
timeout     = 30      # per-request timeout (seconds); local Ollama floors this at 120
max_retries = 3       # retries before advancing fallback chain
retry_delay = 2.0     # base exponential back-off delay (seconds)

# ── n8n ───────────────────────────────────────────────────────────────────────
[n8n]
url                      = "http://localhost:5678"
api_key                  = ""    # or N8N_API_KEY env var
default_error_webhook    = ""    # POST errors to this URL (leave blank to disable)
polling_interval_seconds = 5

# ── Distro Builder ────────────────────────────────────────────────────────────
[distro_builder]
work_dir                  = "/tmp/archon_distro_build"
output_dir                = "./distro_output"
default_jobs              = 0       # 0 = auto from nproc
debian_mirror             = "http://deb.debian.org/debian"
debian_suite              = "bookworm"
kernel_cache_dir          = "~/.archon/kernel_cache"
require_root_confirmation = true
```

### Environment variable reference

| Variable | Config field | Notes |
|----------|-------------|-------|
| `AI_PROVIDER` | `ai.provider` | `ollama` (default) \| `freellmapi` \| `openai` \| `anthropic` |
| `OLLAMA_URL` | `ai.ollama_url` | Default: `http://127.0.0.1:11434` |
| `OLLAMA_MODEL` | `ai.ollama_model` | Default: `qwen3.5:9b` |
| `FREELLMAPI_URL` | `ai.freellmapi_url` | Default: `http://localhost:3001/v1` |
| `FREELLMAPI_API_KEY` | `ai.freellmapi_api_key` | Required when `AI_PROVIDER=freellmapi` |
| `FREELLMAPI_MODEL` | `ai.freellmapi_model` | Default: `auto` |
| `OPENAI_URL` | `ai.openai_url` | Default: `https://api.openai.com/v1` |
| `OPENAI_API_KEY` | `ai.openai_api_key` | Required when `AI_PROVIDER=openai` |
| `OPENAI_MODEL` | `ai.openai_model` | Default: `gpt-4o-mini` |
| `ANTHROPIC_URL` | `ai.anthropic_url` | Default: `https://api.anthropic.com/v1` |
| `ANTHROPIC_API_KEY` | `ai.anthropic_api_key` | Required when `AI_PROVIDER=anthropic` |
| `ANTHROPIC_MODEL` | `ai.anthropic_model` | Default: `claude-3-5-sonnet-latest` |
| `MEMORY_ENABLED` | `ai.memory_enabled` | Retain learned facts across sessions. Default: `true` |
| `MEMORY_AUTO_EXTRACT` | `ai.memory_auto_extract` | Auto-extract durable facts after each exchange. Default: `true` |
| `MEMORY_MAX_INJECT` | `ai.memory_max_inject` | Max remembered facts injected per prompt. Default: `8` |
| `MEMORY_DB_PATH` | `ai.memory_db_path` | Override memory DB path. Default: `~/.archon/memory.db` |
| `MAX_RETRIES` | `ai.max_retries` | Default: 3 |
| `N8N_API_KEY` | `n8n.api_key` | Required for n8n commands |
| `N8N_URL` | `n8n.url` | Default: `http://localhost:5678` |
| `AI__MAX_TOKENS` | `ai.max_tokens` | Nested override format |
| `AI__TIMEOUT` | `ai.timeout` | Nested override format |
| `DEBUG` | `debug` | `true` / `false` |
| `SAFE_MODE` | `safe_mode` | `true` / `false` |

---

## Running the Test Suite

```bash
# All tests
pytest tests/ -v
# Expected: 465 passed, 2 warnings

# Quiet summary
pytest tests/ -q

# Specific file
pytest tests/test_response_parser.py -v

# With coverage (requires pytest-cov)
pytest tests/ --cov=archon --cov-report=term-missing

# Skip slow / integration tests
pytest tests/ -m "not slow and not integration"
```

---

## Development Workflow

```bash
# Lint
ruff check archon/ tests/

# Auto-fix + format
ruff check --fix archon/ tests/
ruff format archon/ tests/

# Type check
mypy archon/ --ignore-missing-imports

# Full CI check (same as GitHub Actions)
ruff check archon/ tests/ && \
mypy archon/ --ignore-missing-imports && \
pytest tests/ -v
```

---

## Troubleshooting

### `archon: command not found`

Activate your venv and verify the install:

```bash
source venv/bin/activate
which archon       # should show path inside venv/
# If not found, use:
python archon.py --help
```

### AI not responding / empty responses

If you're on the default local Ollama backend:

```bash
# Confirm Ollama is up and the model is pulled
curl http://127.0.0.1:11434/api/tags
ollama pull qwen3.5:9b

python archon.py --debug run "hello"   # see full traffic
```

If you set a cloud `AI_PROVIDER` (`freellmapi`/`openai`/`anthropic`) but replies
still look local, the provider was unreachable and Archon fell back to Ollama.
The sidebar's **Backend** field shows which one is actually active. Verify the
endpoint and key:

```bash
# FreeLLMAPI example
curl http://localhost:3001/v1/models -H "Authorization: Bearer $FREELLMAPI_API_KEY"
```

> Local models can be slow to load on the first request. Archon floors the
> local request timeout at 120s; very large models may need more RAM/VRAM.

### n8n returns 401 Unauthorized

Check your `.env` uses the correct flat variable names:

```dotenv
N8N_API_KEY=eyJhbGciOi...      # correct
N8N_URL=http://localhost:5678   # correct

# These do NOT work as flat env vars:
# N8N_API_URL=...               # wrong key name
```

### `ImportError: No module named 'requests'`

The project uses `httpx`. Run a clean reinstall:

```bash
pip install -e . --force-reinstall --no-cache-dir
```

### GUI won't launch

The desktop UI is the Tauri app in `ui-tauri/`. If `archon gui` reports that no
binary was found, build one first:

```bash
cd ui-tauri
npm install
npm run tauri build     # then re-run: archon gui
# or run it directly in dev mode:
npm run tauri dev
```

If the build fails on Linux, install the Tauri platform dependencies
(`webkit2gtk-4.1`, `gtk3`, and the packages listed under
[`archon gui`](#archon-gui--desktop-command-center) above).

### Distro builder: permission denied

`debootstrap` and `pacstrap` require root:

```bash
sudo python archon.py distro build --profile debian_base --output ./isos/
```

### Enable debug logging

```bash
python archon.py --debug run "your command"
python archon.py --log-file /tmp/archon.jsonl run "your command"
python -m json.tool < /tmp/archon.jsonl   # pretty-print JSON logs
```

---

## Security Notes

- **Never commit your API key** — use a `.env` file (add it to `.gitignore`) or `~/.archon/config.toml`
- Use `--safe-mode` to confirm before destructive operations
- All subprocess calls use `safe_run()` with list-form arguments — no `shell=True` anywhere
- `PathValidator` blocks `..` traversal sequences and null-byte injection before any path operation
- Review distro TOML profiles before running with `sudo`

---

## Next Steps

1. Run `python archon.py chatbot` to explore capabilities interactively
2. Read [docs/usage.md](docs/usage.md) for per-task command examples for every capability
3. Read [userguide.md](userguide.md) for the full CLI and interface reference
4. Copy `~/.archon/config.example.toml` to `config.toml` and customise it
