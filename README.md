---
title: Ai Business Research
sdk: docker
app_port: 7860
pinned: false
---

# AI Business Research Agent

Autonomous multi-agent business discovery and verification system for queries such as
`Hair Salons in Lakeway, Texas` or `Dentists in Austin`.

The project intentionally avoids prohibited business-data APIs and commercial scraping
APIs. Discovery and collection are performed through a visible Playwright browser that
opens public search engines and public web pages. Reasoning and extraction use a local
Ollama model by default, so no paid or limited free-tier cloud LLM is required for core
functionality.

## Architecture

```text
User query
  |
  v
Query Agent
  - category/location parsing
  - semantic search variations
  - deterministic fallback if the local LLM is unavailable
  |
  v
Discovery Orchestrator + Browser Agent
  - visible Chromium automation
  - Google, Bing, and DuckDuckGo web UI searches
  - scrolling, pagination, load-more handling, CAPTCHA pause/recovery
  - plateau-based search expansion
  |
  v
Extraction Agent
  - page text, links, and media URL extraction
  - local LLM profile extraction
  - no guessing for missing fields
  |
  v
Entity Resolution Agent
  - phone, website, address, fuzzy name matching
  - local LLM tiebreak for ambiguous records
  |
  v
Verification Agent
  - cross-source field voting
  - source trust and learned reliability scoring
  - conflict-resolution explanations
  - per-field confidence, status, evidence, and source URLs
  |
  +--> Learning Agent
  |     - updates domain reliability in data/source_reliability.json
  |
  +--> Storage
  |     - SQLite run history and query/location profile cache
  |
  v
Reporting Agent
  - structured JSON report
  - CSV export
  - real-time WebSocket dashboard updates
```

## Project Layout

```text
agents/
  query_agent.py
  browser_agent.py
  discovery_orchestrator.py
  extraction_agent.py
  entity_resolution_agent.py
  verification_agent.py
  learning_agent.py
  reporting_agent.py
core/
  config.py
  llm_client.py
  pipeline.py
  storage.py
frontend/index.html
main.py
server.py
run_server.py
tests/
```

## Install

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

## Run A Local LLM

```bash
ollama pull qwen2.5:3b-instruct
ollama serve
```

Optional environment variables:

```bash
set LOCAL_LLM_MODEL=qwen2.5:3b-instruct
set LOCAL_LLM_URL=http://localhost:11434
set LOCAL_LLM_TIMEOUT=20
set DB_PATH=data/research.db
set RESEARCH_MAX_RUNTIME_SECONDS=240
set DISCOVERY_TIMEOUT_SECONDS=90
set MAX_SEARCH_ROUNDS=4
set MAX_RESULTS_PER_QUERY=6
set MAX_PAGES_TO_FETCH=8
set CAPTCHA_TIMEOUT_MS=240000
set BROWSER_PROFILE_DIR=data/browser_profile
```

## Run

Web dashboard:

```bash
python run_server.py
```

Open `http://127.0.0.1:8000`.

If port 8000 is already busy, `run_server.py` automatically uses the next free
local port and prints the dashboard URL.

CLI:

```bash
python main.py "Hair Salons in Lakeway, Texas"
```

The CLI writes `data/last_run_output.json`.

## Output

Each verified business includes the challenge fields plus verification metadata:

```json
{
  "business_name": "",
  "address": "",
  "phone": "",
  "email": "",
  "website": "",
  "working_hours": "",
  "rating": "",
  "review_count": "",
  "services": [],
  "specialties": [],
  "license_information": "",
  "certifications": [],
  "awards": [],
  "owner_name": "",
  "team_members": [],
  "years_in_business": "",
  "insurance_information": "",
  "accepted_payments": [],
  "appointment_booking_url": "",
  "social_profiles": [],
  "images_urls": [],
  "videos_urls": [],
  "business_description": "",
  "faq": [],
  "source_urls": {},
  "field_confidence": {},
  "verification_status": {},
  "field_evidence": {},
  "conflict_resolution": {},
  "verification_score": 0,
  "all_sources": []
}
```

## Security Measures

- No prohibited business search APIs, paid business data APIs, or commercial scraping
  APIs are used.
- Core LLM work is local through Ollama.
- Browser automation uses public web pages only.
- SQLite storage is local; no external database service is required.
- Extracted data is source-attributed and missing values remain empty instead of being
  invented.
- The browser starts with a normal user agent and visible window; CAPTCHA handling pauses
  for manual recovery instead of bypassing protections.
- CORS defaults to localhost origins. Set `ALLOWED_ORIGINS` explicitly before deploying
  behind another host name.
- Do not expose the dashboard directly on the public internet without adding
  authentication, rate limits, request size limits, and network egress controls.

## Testing

The focused non-browser tests cover query fallback, entity resolution, verification
conflict handling, and reporting:

```bash
python -m unittest discover -s tests
```

Full autonomous browser testing requires a working Chromium install and internet access:

```bash
python main.py "Dentists in Austin"
```

## Troubleshooting

### Search engines show CAPTCHA

The project does not bypass CAPTCHA. It opens a visible Chromium window and pauses so
you can solve the challenge manually.

Recommended flow:

1. Start the dashboard with `python run_server.py`.
2. Run a search.
3. If Chromium shows a CAPTCHA, solve it in that visible browser window.
4. Leave `data/browser_profile` in place. It stores normal browser cookies/session data
   so future runs are less likely to ask again immediately.

The default CAPTCHA wait is 4 minutes. Change it with:

```powershell
$env:CAPTCHA_TIMEOUT_MS = "600000"
python run_server.py
```

### Research is taking too long

The default run is tuned for roughly 2-4 minutes:

- up to 4 search rounds
- up to 6 results per search query
- up to 8 candidate pages opened for extraction
- 90 seconds reserved for discovery before extraction starts
- 240 seconds overall research budget
- 20 seconds per local LLM request

You can make it faster or more exhaustive before starting the server:

```powershell
$env:RESEARCH_MAX_RUNTIME_SECONDS = "180"
$env:DISCOVERY_TIMEOUT_SECONDS = "60"
$env:MAX_SEARCH_ROUNDS = "3"
$env:MAX_RESULTS_PER_QUERY = "5"
$env:MAX_PAGES_TO_FETCH = "6"
$env:LOCAL_LLM_TIMEOUT = "15"
python run_server.py
```

If Google repeatedly blocks automation, use the default search order, which tries
DuckDuckGo first, then Bing, then Google.

### Windows says files in `venv` are denied during deletion

This usually means the dashboard server or another Python process is still using native
extension files such as `.pyd` modules. Stop the running server first, close terminals
using the venv, then retry.

Find the process using port 8000:

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen
```

If you started this project server in the current terminal, press `Ctrl+C`. If you need
to run another copy without stopping the old one, use a different port:

```powershell
$env:PORT = "8001"
python run_server.py
```

## Known Limitations

- CAPTCHA solving is manual pause/retry, not automated.
- OCR/vision extraction from screenshots is not implemented yet.
- Distributed crawling and proxy rotation are optional bonus items and are not included.
- Completeness depends on search-engine accessibility, page availability, and local LLM
  quality.
