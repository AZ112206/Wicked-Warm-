/* Wicked Warm - home page logic
   Gated flow: user submits their info -> prediction is generated ->
   recommendations (AI action plan) are revealed only after a prediction exists.

   Every data function below is a STUB returning mock data.
   Swap each one for a real backend call where marked `TODO`. */

// ---------------------------------------------------------------------------
// Backend stubs (mock data for the hackathon template)
// ---------------------------------------------------------------------------

/**
 * Returns a data-driven forecast. This is a fixed placeholder for now — the real
 * prediction is computed from the user's usage, weather and climate data.
 * It is intentionally NOT random.
 *
 * TODO: replace with backend API call, e.g.
 *   const res = await fetch("/api/predict", { method: "POST", body: formData });
 *   return await res.json();
 * The browser should call YOUR backend, which holds any API keys server-side.
 */
async function fetchPrediction(userInput) {
  // Calls the Node_Server, which maps the form to the engine request, calls the
  // Python_Service, and returns the forecast payload. The browser never holds
  // API keys. Response shape: {amount, lastMonth, currency, series:[{label,value,forecast}]}.
  const res = await fetch("/api/predict", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(userInput),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "Forecast failed");
  }
  return await res.json();
}

/**
 * TODO: replace with backend API call, e.g.
 *   const res = await fetch("/api/recommendations", { method: "POST", body: ... });
 *   return await res.json();
 * The action plan should be generated server-side (e.g. by the Gemini model)
 * based on the prediction and the user's usage/weather signals.
 */
async function fetchRecommendations(prediction, userInput) {
  // Calls the Node_Server, which builds a Gemini prompt from the predicted cost
  // and returns a validated action plan. Response shape: [{title, body}, ...].
  const res = await fetch("/api/recommendations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prediction, userInput }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "Recommendations failed");
  }
  return await res.json();
}

// ---------------------------------------------------------------------------
// UI wiring
// ---------------------------------------------------------------------------

const form = document.getElementById("prediction-form");
const fileInput = document.getElementById("bill-files");
const fileList = document.getElementById("file-list");
const formError = document.getElementById("form-error");

const predictionSection = document.getElementById("prediction");
const recommendationsSection = document.getElementById("recommendations");

// Show selected bill file names (mock — no real upload).
fileInput.addEventListener("change", () => {
  fileList.innerHTML = "";
  [...fileInput.files].forEach((file) => {
    const li = document.createElement("li");
    li.textContent = "📄 " + file.name;
    fileList.appendChild(li);
  });
});

function showError(message) {
  formError.textContent = message;
  formError.classList.remove("is-hidden");
}
function clearError() {
  formError.classList.add("is-hidden");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError();

  const data = new FormData(form);
  const location = (data.get("location") || "").toString().trim();
  const household = (data.get("household") || "").toString().trim();
  const hasBill = fileInput.files.length > 0;

  // Gate: require enough info before we generate anything.
  if (!location || !household) {
    showError("Please add your location and household size to continue.");
    return;
  }
  if (!hasBill) {
    showError("Please upload at least one recent utility bill so we can forecast accurately.");
    return;
  }

  const userInput = {
    location,
    household: Number(household),
    homeType: data.get("homeType"),
    income: data.get("income"),
    billFileNames: [...fileInput.files].map((f) => f.name),
  };

  const submitBtn = form.querySelector("button[type=submit]");
  submitBtn.disabled = true;
  submitBtn.textContent = "Generating…";

  try {
    const prediction = await fetchPrediction(userInput);
    renderPrediction(prediction);
    predictionSection.classList.remove("is-hidden");

    // Recommendations only appear AFTER a prediction exists.
    const recs = await fetchRecommendations(prediction, userInput);
    renderRecommendations(prediction, recs);
    recommendationsSection.classList.remove("is-hidden");

    predictionSection.scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    showError("Something went wrong generating your forecast. Please try again.");
    console.error(err);
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Generate my forecast";
  }
});

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function renderPrediction(p) {
  const amountEl = document.getElementById("pred-amount");
  const trendEl = document.getElementById("pred-trend");
  const subEl = document.getElementById("pred-sub");

  amountEl.textContent = formatMoney(p.amount);

  const diff = p.amount - p.lastMonth;
  const pct = Math.round((diff / p.lastMonth) * 100);
  const rising = diff > 0;
  trendEl.textContent = (rising ? "▲ " : "▼ ") + Math.abs(pct) + "% vs. last month";
  trendEl.className = "ww-predcard__trend " + (rising ? "trend-up" : "trend-down");
  subEl.textContent = "Last month: " + formatMoney(p.lastMonth);

  renderChart(p.series);
}

function renderChart(series) {
  const chart = document.getElementById("pred-chart");
  chart.innerHTML = "";
  const max = Math.max(...series.map((s) => s.value));
  series.forEach((point) => {
    const bar = document.createElement("div");
    bar.className = "bar" + (point.forecast ? " is-peak" : "");
    const fill = document.createElement("span");
    fill.className = "fill";
    fill.style.height = Math.round((point.value / max) * 100) + "%";
    const label = document.createElement("small");
    label.textContent = point.label;
    bar.appendChild(fill);
    bar.appendChild(label);
    chart.appendChild(bar);
  });
}

function renderRecommendations(prediction, recs) {
  const intro = document.getElementById("rec-intro");
  const list = document.getElementById("rec-list");
  const rising = prediction.amount > prediction.lastMonth;

  intro.textContent = rising
    ? "Your bill is projected to rise next month. Here's how to soften the impact:"
    : "Your bill looks steady — here are ways to push it even lower:";

  list.innerHTML = "";
  recs.forEach((rec) => {
    const li = document.createElement("li");
    const strong = document.createElement("strong");
    strong.textContent = rec.title;
    const span = document.createElement("span");
    span.textContent = rec.body;
    li.appendChild(strong);
    li.appendChild(span);
    list.appendChild(li);
  });
}

function formatMoney(n) {
  return "$" + Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// ---------------------------------------------------------------------------
// Hero teaser card — DEMO ONLY.
// Auto-randomizes every 5s so visitors see what a forecast looks like.
// This is purely illustrative and unrelated to the real prediction above.
// ---------------------------------------------------------------------------
(function animateTeaser() {
  const amountEl = document.getElementById("teaser-amount");
  const trendEl = document.getElementById("teaser-trend");
  const barsEl = document.getElementById("teaser-bars");
  if (!amountEl || !trendEl || !barsEl) return;

  // Smooth transition on the bars.
  [...barsEl.children].forEach((bar) => {
    bar.style.transition = "height 0.6s ease";
  });

  const rand = (min, max) => Math.random() * (max - min) + min;

  // Map a bar height (30–95%) onto a dollar amount ($80–$350) so the headline
  // price scales with the projected bar — taller bar = higher bill.
  const heightToPrice = (h) => 80 + ((h - 30) / (95 - 30)) * (350 - 80);

  function reroll() {
    const bars = [...barsEl.children];
    const lastIdx = bars.length - 1;

    // 1) Random bar heights. Earlier bars = PAST months; last bar = projected.
    const heights = bars.map(() => Math.round(rand(30, 95)));

    // 2) Apply heights; highlight only the last (projected) bar.
    bars.forEach((bar, i) => {
      bar.style.height = heights[i] + "%";
      bar.classList.toggle("is-peak", i === lastIdx);
    });

    // 3) Derive trend from the projected bar vs. the previous (last actual) bar,
    //    so the arrow/color matches what the bars show.
    const projected = heights[lastIdx];
    const previous = heights[lastIdx - 1];
    const pct = Math.round(((projected - previous) / previous) * 100);
    const rising = projected >= previous;
    trendEl.textContent = (rising ? "▲ " : "▼ ") + Math.abs(pct) + "% vs. last month";
    trendEl.className = "ww-predcard__trend " + (rising ? "trend-up" : "trend-down");

    // 4) Derive the headline price from the projected bar so all three agree.
    const amount = heightToPrice(projected);
    const dollars = Math.floor(amount);
    const cents = Math.round((amount - dollars) * 100).toString().padStart(2, "0");
    amountEl.innerHTML = "$" + dollars + "<span>." + cents + "</span>";
  }

  reroll();
  setInterval(reroll, 5000);
})();
