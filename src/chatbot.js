// Wicked Warm - multi-turn Gemini chatbot assistant (CLI).
// Keeps conversation history so the bot remembers earlier messages.
//   npm start     (or)   node src/chatbot.js
import { GoogleGenAI } from "@google/genai";
import readline from "node:readline";
import { config } from "./config.js";

const SYSTEM_INSTRUCTION =
  "You are Wicked Warm, a friendly and helpful chatbot assistant. " +
  "Keep answers clear and concise.";

const ai = new GoogleGenAI({ apiKey: config.apiKey });

function newChat() {
  return ai.chats.create({
    model: config.model,
    config: { systemInstruction: SYSTEM_INSTRUCTION },
  });
}

let chat = newChat();

const rl = readline.createInterface({
  input: process.stdin,
  output: process.stdout,
});

function prompt() {
  rl.question("\nYou: ", async (input) => {
    const text = input.trim();

    if (!text) return prompt();

    const cmd = text.toLowerCase();
    if (cmd === "exit" || cmd === "quit") {
      console.log("\nGoodbye! Stay wicked warm.");
      rl.close();
      return;
    }
    if (cmd === "reset") {
      chat = newChat();
      console.log("(conversation reset)");
      return prompt();
    }

    try {
      const response = await chat.sendMessage({ message: text });
      console.log("\nWicked Warm: " + response.text);
    } catch (err) {
      console.error("\n[error] Could not reach Gemini:", err?.message || err);
    }
    prompt();
  });
}

console.log("Wicked Warm chatbot (" + config.model + ")");
console.log("Type your message. Commands: 'reset' to clear memory, 'exit' to quit.");
prompt();
