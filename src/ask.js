// Single-turn helper: send one prompt to Gemini and print the reply.
// Usage: node src/ask.js "your question here"  (or: npm run ask -- "...")
import { GoogleGenAI } from "@google/genai";
import { config } from "./config.js";

const ai = new GoogleGenAI({ apiKey: config.apiKey });

export async function ask(prompt) {
  const response = await ai.models.generateContent({
    model: config.model,
    contents: prompt,
  });
  return response.text;
}

// Run directly from the command line.
const isMain = process.argv[1] && import.meta.url.endsWith(process.argv[1].replace(/\\/g, "/"));
if (isMain) {
  const prompt = process.argv.slice(2).join(" ") || "Say hello from Wicked Warm.";
  try {
    const reply = await ask(prompt);
    console.log(reply);
  } catch (err) {
    console.error("Error talking to Gemini:", err?.message || err);
    process.exit(1);
  }
}
