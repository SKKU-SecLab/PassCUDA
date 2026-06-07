# Password Change Automation

An automated web agent for navigating to and completing the password change flow across websites, using a Tree-of-Thought (ToT) search strategy with a Vision-Language Model (VLM) backbone.

## Overview

This agent automates the full pipeline:

1. **Step Identification** — determines the current navigation stage (login, profile navigation, password change, etc.)
2. **Action Generation** — proposes candidate UI actions via VLM
3. **Element Selection** — identifies the target DOM element corresponding to the proposed action
4. **BBox Refinement** — iteratively refines the bounding box of the target element
5. **Click Point Localization** — computes the precise click coordinate within the validated bounding box

A Tree-of-Thought (ToT) structure manages branching, backtracking, and pruning across the search process.

## Project Structure

```
.
├── main.py                   # Entry point
├── components/
│   ├── agent.py              # VLM client and per-stage agent functions
│   ├── ToT.py                # Tree-of-Thought data structures and traversal logic
│   ├── prompt_governor.py    # Output schema validation and auto-fix for each stage
│   ├── element_utils.py      # Selenium-based DOM utilities
│   ├── element_utils_pw.py   # Playwright-based DOM utilities
│   └── credential_fetcher.py # Credential loader from CSV
├── prompt/
│   ├── 1_StepIdentification_{system,user}.md
│   ├── 2_ActionGeneration_{system,user}.md
│   ├── 3_ElementSelection_{system,user}.md
│   ├── 4_BBoxRefinement_{system,user}.md
│   └── 5_ClickPoint_{system,user}.md
├── credentials.csv           # (not included) domain, rank, url, ID, Password columns
├── output/                   # Logs and tree snapshots (auto-created)
└── screenshot/               # Screenshots and debug crops (auto-created)
```

## Requirements

```
openai
anthropic
google-generativeai
selenium
seleniumbase
playwright
patchright
transformers
pillow
pandas
networkx
matplotlib
```

Install:

```bash
pip install -r requirements.txt
```

## Configuration

Edit the configuration block in `main.py`:

```python
domain    = "example"         # target domain key in credentials.csv
PROVIDER  = "openai"          # "openai" | "claude" | "gemini" | "vllm"
MODEL     = "gpt-4o"
BASE_URL  = None              # set for vLLM endpoints
API_KEY   = os.environ.get("OPENAI_API_KEY")
CHROME_BINARY = "/usr/bin/google-chrome"
MODE      = "auto"            # "auto" | "manual"
```

Credentials are read from `credentials.csv` with columns: `domain`, `rank`, `url`, `ID`, `Password`.

## Running

```bash
python main.py
```

Outputs are written to:
- `screenshot/<run_id>/` — full-page and debug crop screenshots
- `output/<domain>/` — log file and tree JSON snapshots

## Supported Providers

| Provider | `PROVIDER` value | Notes |
|----------|-----------------|-------|
| OpenAI   | `"openai"`      | GPT-4o, GPT-4.1, etc. |
| Anthropic | `"claude"`     | Claude Sonnet / Opus |
| Google   | `"gemini"`      | Gemini 2.5 Flash, etc. |
| vLLM     | `"vllm"`        | Self-hosted; set `BASE_URL` |
