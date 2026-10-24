/* Wicked Warm - about page
   DEMO ONLY: the "Today's projection" stat box auto-randomizes every 5s so
   visitors see what the daily-updated forecast looks like. Not a real value. */

(function animateAboutStat() {
  const amountEl = document.getElementById("about-amount");
  const trendEl = document.getElementById("about-trend");
  if (!amountEl || !trendEl) return;

  const rand = (min, max) => Math.random() * (max - min) + min;
  const money = (n) =>
    "$" + Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  function reroll() {
    // Random projected bill and a random "last month" baseline ($80–$350).
    const projected = rand(80, 350);
    const lastMonth = rand(80, 350);

    const diff = projected - lastMonth;
    const pct = (diff / lastMonth) * 100;
    const rising = diff >= 0;
    const arrow = rising ? "▲" : "▼";
    const sign = rising ? "+" : "−";

    amountEl.textContent = money(projected);
    trendEl.textContent =
      `${arrow} ${Math.abs(pct).toFixed(1)}% · ${sign}${money(Math.abs(diff)).slice(1)} vs. last month`;
    trendEl.className = "ww-predcard__note " + (rising ? "trend-up" : "trend-down");
  }

  reroll();
  setInterval(reroll, 5000);
})();

// ---------------------------------------------------------------------------
// Looking-ahead future card — DEMO ONLY.
// Slider sets the forecast horizon (0mo..5yr); values re-randomize every 10s.
// Regular orange = past months, dark orange (is-peak) = predicted months.
// ---------------------------------------------------------------------------
(function animateFutureCard() {
  const chartEl = document.getElementById("future-chart");
  const amountEl = document.getElementById("future-amount");
  const trendEl = document.getElementById("future-trend");
  const sliderEl = document.getElementById("future-slider");
  const horizonEl = document.getElementById("future-horizon");
  if (!chartEl || !amountEl || !trendEl || !sliderEl || !horizonEl) return;

  const rand = (min, max) => Math.random() * (max - min) + min;
  const money = (n) =>
    "$" + Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const heightToPrice = (h) => 80 + ((h - 30) / (95 - 30)) * (350 - 80);

  const PAST_BARS = 6;
  // Slider index -> { label, predicted bar count }. 5yr aggregates to 12 bars.
  const STOPS = [
    { label: "0 months", predicted: 0 },
    { label: "1 month", predicted: 1 },
    { label: "3 months", predicted: 3 },
    { label: "6 months", predicted: 6 },
    { label: "1 year", predicted: 12 },
    { label: "5 years", predicted: 12 }, // aggregated/bucketed for readability
  ];

  // State: heights persist across slider moves; only the 10s timer rerolls them.
  let pastHeights = [];
  let predictedHeights = [];

  // Create a persistent, fixed set of bar elements once (max 6 past + 12 pred).
  // Reusing the same elements is what lets CSS transitions animate smoothly
  // when the slider adds or removes predicted bars.
  const MAX_BARS = PAST_BARS + 12;
  const bars = [];
  for (let i = 0; i < MAX_BARS; i++) {
    const bar = document.createElement("div");
    bar.className = "bar";
    bar.style.height = "0%";
    bars.push(bar);
    chartEl.appendChild(bar);
  }

  function rollHeights() {
    pastHeights = Array.from({ length: PAST_BARS }, () => Math.round(rand(30, 95)));
    // Enough predicted bars for the largest horizon; slice per-stop when rendering.
    predictedHeights = Array.from({ length: 12 }, () => Math.round(rand(30, 95)));
  }

  function render() {
    const stop = STOPS[Number(sliderEl.value)];
    horizonEl.textContent = stop.label;

    const predicted = predictedHeights.slice(0, stop.predicted);
    const all = [...pastHeights, ...predicted];
    const firstPredictedIdx = pastHeights.length; // where dark-orange begins

    // Update the persistent bar elements in place so CSS height transitions
    // animate smoothly as the slider adds/removes predicted bars.
    // Bars beyond the current horizon shrink to 0 (and fade) instead of being
    // removed from the DOM, which is what makes sliding smooth.
    bars.forEach((bar, i) => {
      const active = i < all.length;
      const isPredicted = i >= firstPredictedIdx;
      bar.classList.toggle("is-peak", active && isPredicted);
      bar.classList.toggle("is-inactive", !active);
      if (active) bar.style.height = all[i] + "%";
    });

    // Readout: final predicted bar vs. the bar before it (consistent w/ chart).
    if (stop.predicted === 0) {
      // Past only — show latest past month, no forward trend.
      const latest = pastHeights[pastHeights.length - 1];
      amountEl.textContent = money(heightToPrice(latest));
      trendEl.textContent = "Past months only";
      trendEl.className = "ww-predcard__note";
    } else {
      const finalH = all[all.length - 1];
      const prevH = all[all.length - 2];
      const pct = Math.round(((finalH - prevH) / prevH) * 100);
      const rising = finalH >= prevH;
      amountEl.textContent = money(heightToPrice(finalH));
      trendEl.textContent =
        (rising ? "▲ " : "▼ ") + Math.abs(pct) + "% vs. previous";
      trendEl.className = "ww-predcard__note " + (rising ? "trend-up" : "trend-down");
    }
  }

  // Slider changes the horizon instantly (keeps current values).
  sliderEl.addEventListener("input", render);

  // Initial + every 10s: reroll values and re-render at the current horizon.
  function refresh() {
    rollHeights();
    render();
  }
  refresh();
  setInterval(refresh, 10000);
})();
