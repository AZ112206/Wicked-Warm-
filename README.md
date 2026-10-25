# 🔥 Wicked Warm

**Know your heating bill before it hits.** Wicked Warm predicts a home's annual
heating cost from real weather and energy-price data, shows how much a heat pump
would save, and hands you an AI-generated action plan to cut the bill — all from
a single form, no account required.

Built for Massachusetts first, designed to extend anywhere.

---

## ✨ What it does

| | |
|---|---|
| 🌡️ **Real forecasts** | Pulls live daily temperatures (Open-Meteo) and residential energy prices (U.S. EIA) to estimate a home's annual heating cost — not a random mock number. |
| ♨️ **Heat-pump savings** | Compares conventional heating vs. a cold-climate heat pump using a temperature-dependent COP curve and Massachusetts' seasonal electric rate. |
| 🧾 **Bill calibration** | Upload a recent utility bill amount and the model calibrates itself to *your* home, tightening the estimate. |
| 🤖 **AI action plan** | Google Gemini turns the prediction into a prioritized, plain-English list of ways to save. |
| 📈 **Price tracker** | A live view of electricity/gas price trends and a regional comparison. |

---

## 🏗️ How it's built

Three layers, one clean data flow. Secrets never touch the browser.

```
┌───────────┐     ┌──────────────────────┐     ┌───────────────────────────┐
│  Browser  │ ──▶ │  Node_Server (Express)│ ──▶ │  Python_Service           │
│ frontend/ │     │  src/server.js        │     │  energy_cost_predictor/   │
│ HTML/CSS/ │ ◀── │  • form → engine map  │ ◀── │  • forecast engine        │
│   JS      │     │  • secret GEMINI key  │     │  • live weather + prices  │
└───────────┘     │  • calls Gemini (AI)  │     │  • secret EIA key         │
                  └──────────────────────┘     └───────────────────────────┘
```

- **Python engine** (`energy_cost_predictor/`) — the forecasting core and a
  small `http.server` API. **Pure Python standard library** (no numpy/pandas at
  runtime), so there is **nothing to pip-install**.
- **Node server** (`src/`) — an Express app that serves the site, maps the form
  onto the engine, holds the Gemini key server-side, and generates the AI plan.
- **Frontend** (`frontend/`) — a static multi-page site, no build step.

---

## 🚀 Quick start (judges, start here)

### 0. Prerequisites

| Tool | Version | Check | Get it |
|---|---|---|---|
| **Node.js** | 18+ (tested on 24) | `node --version` | https://nodejs.org (LTS) |
| **Python** | 3.14+ | `py --version` (Windows) · `python3 --version` | https://www.python.org/downloads/ |
| **Gemini API key** | — | — | https://aistudio.google.com/app/apikey (free) |

> 💡 The EIA energy-price key is **optional and free**. Without it the app uses
> built-in placeholder prices and still produces a real, weather-driven forecast.

### 1. Install the Node dependencies

```bash
npm install
```

> ⚠️ **Do _not_ run `pip install`.** The Python engine uses only the standard
> library. (The pins in `pyproject.toml` are nominal — the code never imports
> numpy/pandas at runtime.)

### 2. Add your keys

Copy the template and open the new `.env`:

```powershell
Copy-Item .env.example .env   # Windows PowerShell
# cp .env.example .env         # macOS / Linux
```

Fill it in:

```ini
# REQUIRED — get one free at https://aistudio.google.com/app/apikey
GEMINI_API_KEY=your_real_key_here
GEMINI_MODEL=gemini-3.8-flash

# OPTIONAL — free + instant at https://www.eia.gov/opendata/register.php
# Leave blank to use placeholder prices (forecast still works).
EIA_API_KEY=
```

### 3. Run it

**Windows — one click:** double-click **`start.bat`**. It launches both
servers in their own windows and opens the app in your browser automatically.

**Any OS — two terminals:**

```bash
# Terminal 1 — the forecast engine (leave it running)
py -m energy_cost_predictor.service          # Windows
# python3 -m energy_cost_predictor.service   # macOS / Linux

# Terminal 2 — the web server (leave it running)
node src/server.js
```

### 4. Open the app

👉 **http://127.0.0.1:3000/home/index.html**

Fill in a location (e.g. `Amherst, MA`), household size, and home type, optionally
attach a utility bill, and hit **Generate my forecast**. You'll get a predicted
monthly cost, a 6-month trend, and an AI action plan.

Other pages:
- 📊 Price tracker — http://127.0.0.1:3000/price-tracker/price-tracker.html
- ℹ️ About — http://127.0.0.1:3000/about/about.html

> **Keep both terminal windows open while using the app.** Closing them stops the
> servers. `http://127.0.0.1:3000/` by itself returns a JSON 404 — that's normal;
> use the `/home/index.html` link above.

---

## 🧪 Running the tests

The engine ships with a `pytest` + `hypothesis` suite (property-based tests over
the forecasting math):

```bash
py -m pytest            # Windows
# python3 -m pytest     # macOS / Linux
```

---

## 🩺 Troubleshooting

| Symptom | Cause & fix |
|---|---|
| **"Something went wrong generating your forecast."** | One of the two servers isn't running, or the Gemini model is stale. Make sure both terminals are up, and that `GEMINI_MODEL=gemini-3.8-flash` in `.env` (older `gemini-2.0-flash` has been retired by Google). |
| **`npm.ps1 cannot be loaded ... running scripts is disabled`** | PowerShell's execution policy blocks the npm wrapper. Run `node src/server.js` directly, use **Command Prompt**, or run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. |
| **`{"error":"Route not found"}` at `http://127.0.0.1:3000/`** | Expected — the root isn't a page. Go to `/home/index.html`. |
| **`Missing GEMINI_API_KEY`** | `.env` is missing or still has the placeholder. Add a real key from AI Studio. |
| **Forecast works but no AI plan** | The Gemini call failed. Check the key is a valid AI Studio key and `GEMINI_MODEL` is a current model. |
| **Prices look generic** | No `EIA_API_KEY` set — the app fell back to placeholder prices. Add a free EIA key for live prices. (Weather is always live.) |

---

## 🔐 Security

- `.env` is **gitignored** — only `.env.example` (placeholders) is committed.
  Confirm with `git check-ignore .env` (should print `.env`).
- Both API keys stay **server-side**; the browser never sees them.
- If a key is ever exposed, rotate it at its provider and update `.env`.

---

## 📁 Project structure

```
Wicked-Warm/
├── start.bat                       # One-click launcher (Windows)
├── .env.example                    # Env template (committed)
├── .env                            # Your real keys (gitignored)
├── package.json                    # Node deps + "serve" script
├── pyproject.toml                  # Python project metadata + test deps
│
├── frontend/                       # Static site (no build step)
│   ├── home/        index.html · home.css · home.js     # forecast + AI plan
│   ├── price-tracker/  *.html · *.css · *.js            # price trends
│   ├── about/          *.html · *.css
│   └── shared/         shared.css                        # shared styling
│
├── src/                            # Node server (Express)
│   ├── server.js                   # routes, static serving, error handling
│   ├── pythonClient.js             # HTTP client to the Python engine
│   ├── mapping.js                  # form → engine request, engine → forecast
│   ├── ask.js                      # Gemini helper
│   └── config.js                   # loads + validates .env
│
├── energy_cost_predictor/          # Python forecast engine (pure stdlib)
│   ├── service.py                  # HTTP API (predict / savings / prices / regions)
│   ├── predictor.py                # orchestration + savings comparison
│   ├── demand.py · heat_pump.py · cost.py · seasonal_rate.py   # core math
│   ├── calibration.py · accuracy.py                            # bill calibration
│   ├── live_weather_provider.py    # Open-Meteo (live weather)
│   ├── live_pricing_provider.py    # U.S. EIA (live prices)
│   ├── placeholder_providers.py    # offline fallbacks
│   ├── regions.py · models.py · errors.py · providers.py
│   └── example.py                  # standalone engine demo
│
└── tests/                          # pytest + hypothesis suite
```

---

## 🙏 Credits & data sources

- **Weather:** [Open-Meteo](https://open-meteo.com) — free historical & forecast API (no key).
- **Energy prices:** [U.S. Energy Information Administration](https://www.eia.gov/opendata/) Open Data API.
- **Heat-pump COP data:** modeled on [NEEP](https://neep.org)'s Cold Climate ASHP specifications.
- **AI action plan:** [Google Gemini](https://ai.google.dev/).

Made wicked warm in Massachusetts. 🧣
