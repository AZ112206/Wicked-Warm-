// Loads and validates environment configuration for the Wicked Warm chatbot.
// The real GEMINI_API_KEY lives in .env (gitignored), never in source.
import dotenv from "dotenv";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

// Load .env from the project root (one level up from this src/ file),
// so it works no matter which directory the command is run from.
const __dirname = dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: join(__dirname, "..", ".env") });

const apiKey = process.env.GEMINI_API_KEY;
const model = process.env.GEMINI_MODEL || "gemini-2.0-flash";

if (!apiKey || apiKey === "your_gemini_api_key_here") {
  console.error(
    [
      "",
      "  Missing GEMINI_API_KEY.",
      "",
      "  1. Copy .env.example to .env",
      "     Copy-Item .env.example .env   (Windows PowerShell)",
      "  2. Put your real Gemini API key in .env",
      "     Get one at https://aistudio.google.com/app/apikey",
      "",
    ].join("\n")
  );
  process.exit(1);
}

export const config = { apiKey, model };
