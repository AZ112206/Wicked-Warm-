# Wicked Warm

A friendly chatbot assistant powered by Google's **Gemini 2.0 Flash** model.

## Features

- Multi-turn chat with conversation memory (the bot remembers earlier messages)
- Single-shot question helper for quick one-off prompts
- API key kept out of source and out of git

## Requirements

- Node.js 18+ (tested on v24)
- A Gemini API key from https://aistudio.google.com/app/apikey

## Setup

1. Install dependencies:
   ```bash
   npm install
   ```

2. Create your local env file from the template:
   ```powershell
   Copy-Item .env.example .env   # Windows PowerShell
   # cp .env.example .env        # macOS / Linux
   ```

3. Put your real Gemini API key in `.env`:
   ```
   GEMINI_API_KEY=AIza...your_key...
   GEMINI_MODEL=gemini-2.0-flash
   ```

   > **Note on key format:** Use a key from https://aistudio.google.com/app/apikey.
   > Keys that work with the Gemini API endpoint start with `AIza`. If you only get
   > an `AQ.` key, you're likely in the Google Cloud / Vertex AI console instead of
   > AI Studio, or using a restricted Workspace account — see Troubleshooting.

## Usage

Start the interactive chatbot:
```bash
npm start
```
In the chat:
- Type a message and press Enter
- `reset` clears the conversation memory
- `exit` or `quit` ends the session

Ask a single question without the chat loop:
```bash
node src/ask.js "What should I wear when it's wicked warm outside?"
```

## Security

- **Never commit `.env`.** It is gitignored. Only `.env.example` (placeholders) is committed.
- If a key is ever exposed (in a screenshot, commit, or chat), **rotate it**: delete it in
  AI Studio and generate a new one.
- `git check-ignore .env` should print `.env`, confirming it is ignored.

## Troubleshooting

- **`API key not valid` / `ACCESS_TOKEN_TYPE_UNSUPPORTED`**: your key isn't a valid AI Studio
  Gemini API key. Generate one at https://aistudio.google.com/app/apikey (should start with
  `AIza`). `AQ.` OAuth tokens from the Cloud Console are not accepted by this endpoint.
- **`Missing GEMINI_API_KEY`**: you haven't created `.env` or left the placeholder value in it.

## Front end

A static multi-page site (plain HTML/CSS/JS, no build step) under `frontend/`.
Open `frontend/home/index.html` in a browser, or serve the folder:

```bash
npx serve frontend        # then visit the printed URL
```

Pages:
- **Home** (`home/index.html`) — hero, how it works, and a gated flow: enter your
  home info + a utility bill, generate a forecast, then see the AI action plan.
- **About** (`about/about.html`) — what the full-stack app does and how it works.
- **Price Tracker** (`price-tracker/price-tracker.html`) — energy price trends and
  a regional comparison.

All dynamic data is mock, served by stub functions marked `// TODO: replace with
backend API call` so the backend can be wired in later without restructuring.

## Project structure

```
.
├── frontend/
│   ├── shared/shared.css        # palette, nav, footer (shared by all pages)
│   ├── home/
│   │   ├── index.html           # Home page (gated prediction flow)
│   │   ├── home.css
│   │   └── home.js
│   ├── about/
│   │   ├── about.html
│   │   └── about.css
│   └── price-tracker/
│       ├── price-tracker.html
│       ├── price-tracker.css
│       └── price-tracker.js
├── src/                         # Gemini chatbot (separate workstream)
│   ├── chatbot.js  # Multi-turn chat loop (npm start)
│   ├── ask.js      # Single-shot prompt helper (node src/ask.js "...")
│   └── config.js   # Loads & validates env config
├── Back End/                    # (empty — backend to come)
├── .env.example    # Template (committed)
├── .env            # Real secrets (gitignored)
├── .gitignore
├── package.json
└── README.md
```
