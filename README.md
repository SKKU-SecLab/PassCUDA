# PassCUDA

**Password Change URL Discovery Agent**

PASSCUDA is an MLLM-based agent that automatically navigates to a website's password change page and records its URL, so password managers can direct users straight to it without manual navigation. See our paper for the full system description and benchmark results.

---

## Requirements

### Python dependencies

```bash
pip install -r requirements.txt
```

### Browser

PASSCUDA uses [CloakBrowser](https://github.com/requirements-browser/cloakbrowser) to avoid passive bot detection that blocks standard Chromium instances.

1. Download the CloakBrowser binary from the repository above.
2. Download the **matching ChromeDriver version** for your CloakBrowser build. Version mismatches will cause the driver to fail at launch — check the CloakBrowser release notes for the exact Chromium version and download the corresponding ChromeDriver from [chromedriver.chromium.org](https://chromedriver.chromium.org/downloads).
3. Set the paths in `main.ipynb` (Initialization section):

```python
CLOAK_CHROMEDRIVER = "/path/to/chromedriver"   # ← set this
CLOAK_BINARY       = "/path/to/cloakbrowser"   # ← and this
```

---

## Credentials

Login credentials are loaded from `credentials.csv` at runtime by `credential_fetcher.py`. The MLLM never receives the actual credential values — PASSCUDA's Credential Fetcher injects them directly into the browser at execution time, so passwords are never exposed to the model.

The CSV should follow this format:

```
Num,rank,domain,category,ID,Password,url
39,199,forbes,News & Information,your@email.com,yourpassword,https://www.forbes.com/
```

The `domain` field is used as the lookup key. `ID` is the login email or username, and `Password` is the account password.

---

## Components

### `external_handlers.py` — External Handler

Implements the pluggable handler modules described in the paper. Two handlers require site- or infrastructure-specific implementations:

**CAPTCHA Solver** — invoked when State Identification classifies the current page as a CAPTCHA state. The stub calls `input()` so a human operator can resolve it manually. Replace this with a third-party CAPTCHA solver (e.g., 2captcha, CapSolver) or your own implementation.

**2FA Handler** (`credential_fetcher.get_2FA_code`) — invoked when Element Selection specifies `2FA_retriever` as the value source. The stub prompts for manual input. In our experiments, we retrieved OTP codes programmatically by accessing Gmail via IMAP and extracting the code with a regex. Because the base email account differs per deployment, we left this as an `input()` call. You can implement your own IMAP retrieval or SMS handler and drop it in here — the rest of the pipeline does not change.

**Credential Fetcher** (`credential_fetcher.py`) — reads `credentials.csv` and returns the email/username or password for a given domain. Replace or extend this if your credential store is elsewhere (e.g., a PM API, environment variables).

---

### `prompt_governor.py` — Prompt Governor

Post-processes every raw MLLM output before it is acted upon. Validates against a predefined JSON schema and applies rule-based corrections for common failure modes: malformed JSON, out-of-range field values, misplaced confidence scores, and action type mismatches. Each pipeline stage (State Identification, Action Generation, Element Selection, BBox Refinement, Click Point) has a corresponding `Schema` / `Governor` pair.

---

### `element_utils.py` — Browser Wrapper

Controls the browser via Selenium and provides all page observation and action execution primitives. Key responsibilities:

- Collects overlapping interactive elements (inputs and clickables) within a predicted action boundary, traversing shadow DOM subtrees and cross-origin iframes in full to minimize element omissions.
- Dispatches actions as coordinate-based input events rather than direct DOM property assignments, ensuring compatibility with reactive frameworks (React, Vue, etc.).
- Provides screenshot cropping, bbox coordinate conversion, and visual debug overlays used during Element Verification.

---

### `agent.py` — MLLM Pipeline

Implements the four sequential MLLM inference stages:

- **State Identification** — classifies the current page state (pre-login, login, post-login navigation, CAPTCHA, alert/interstitial, success) and determines whether to continue or prune the current path.
- **Action Generation** — generates candidate actions with confidence scores using Tree-of-Thought exploration. Maintains a persistent action tree (`ToT.py`) that enables backtracking and alternative path selection when navigation fails.
- **Element Selection** — grounds the abstract action to a focused DOM candidate set collected by the Browser Wrapper. Handles chunked candidate lists for pages with many elements.
- **Element Verification** (BBox Refinement + Click Point) — visually validates the selected element by iteratively refining its bounding box in a cropped screenshot, then determines the exact click coordinate before execution.

Supports `vllm` and `openai` providers via `VLMClient`.

---

### `ToT.py` — Execution Monitor

Maintains the action tree. Each node stores the action taken, element metadata (raw HTML, iframe path, key attributes), and navigation state. The tree supports pruning, backtracking, and replay — when execution is interrupted, the stored trace can resume from the point of interruption without restarting from the homepage.

---

## Running `main.ipynb`

Run the Setup, Prompt Governor, Module, and Initialization sections before starting automation. These can be run all at once at the beginning of a session.

### Setup

Sets environment variables (X display for headless environments) and temp directory. Adjust as needed for your server setup.

### Prompt Governor

Initializes all schema/governor pairs used throughout the pipeline. No changes needed unless you are adjusting schema parameters (e.g., confidence thresholds, allowed value tokens).

### Module

Imports and reloads all components. Defines the `ExecContext` dataclass and the five module-level functions (`run_state_module`, `run_action_module`, `run_element_module`, `run_refine_bbox_module`, `run_bbox_adjustment_loop`) that wrap each pipeline stage.

### Initialization

**Fill in before running:**

| Variable | Description |
|---|---|
| **`domain`** | Short identifier for the target site (used in file naming) |
| **`home_url`** | Starting URL for the browser session |
| **`rank`** | Rank identifier (used in file naming) |
| **`PROVIDER`** | `"vllm"` or `"openai"` |
| **`MODEL`** | Model string (e.g., `"Qwen/Qwen3.5-27B-FP8"`, `"gpt-5.1"`) |
| **`BASE_URL`** | vLLM server endpoint, or `None` for OpenAI |
| **`API_KEY`** | API key for the provider, or `"EMPTY"` for local vLLM |

Output paths (`screenshot_path`, `bbox_dir`, `tree_dir`, `log_path`) are derived automatically from these values and created if they do not exist.

Also sets up the logger and initializes the `ExecContext` and `ToTTree`.

### Run

Starts the browser session via Playwright + Selenium (connected to the same browser process via remote debugging port) and runs the main automation loop for up to 50 steps.

### Visualize Tree

Renders the current action tree as a node graph. Can be run at any point during or after automation to inspect which paths were explored, pruned, or are still on the frontier.

---

## Outputs

| Path | Contents |
|---|---|
| `screenshot/` | Per-step screenshots and debug crop images |
| `output/` | Per-run log files |
| `output/tree/` | Action tree JSON, saved after each step |

The `example/` directory contains a sample run on forbes.com using Qwen3.5-27B. Screenshots where the username or email address appears have been masked.
