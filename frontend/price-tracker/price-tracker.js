/* Wicked Warm - price tracker logic
   Renders energy price trends + a regional comparison.
   Data comes from STUBS returning mock data — swap for backend calls at TODO. */

// ---------------------------------------------------------------------------
// Backend stubs (mock data)
// ---------------------------------------------------------------------------

/**
 * TODO: replace with backend API call, e.g.
 *   const res = await fetch(`/api/prices?metric=${metric}&months=${months}`);
 *   return await res.json();
 */
async function fetchPriceSeries(metric, months) {
  await new Promise((r) => setTimeout(r, 200));

  // Base monthly values per metric (12 months).
  const base = {
    electricity: [14.8, 15.1, 15.0, 15.4, 16.2, 16.9, 17.5, 17.1, 16.7, 16.4, 16.9, 17.8],
    gas: [1.12, 1.08, 1.05, 1.02, 1.0, 1.04, 1.15, 1.22, 1.3, 1.28, 1.35, 1.42],
  };
  const labels = ["Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct"];

  const values = base[metric] || base.electricity;
  const start = values.length - months;
  return {
    metric,
    unit: metric === "gas" ? "$/therm" : "¢/kWh",
    points: values.slice(start).map((value, i) => ({ label: labels[start + i], value })),
  };
}

/**
 * TODO: replace with backend API call, e.g.
 *   const res = await fetch("/api/prices/regions");
 *   return await res.json();
 */
async function fetchRegionalPrices() {
  await new Promise((r) => setTimeout(r, 150));
  return [
    { region: "Northeast", rate: 19.4, change: 3.2 },
    { region: "Midwest", rate: 15.1, change: -1.1 },
    { region: "South", rate: 13.8, change: 0.6 },
    { region: "West", rate: 21.7, change: 4.0 },
  ];
}

// ---------------------------------------------------------------------------
// UI wiring
// ---------------------------------------------------------------------------

const metricSel = document.getElementById("pt-metric");
const rangeSel = document.getElementById("pt-range");

async function refresh() {
  const metric = metricSel.value;
  const months = Number(rangeSel.value);
  const series = await fetchPriceSeries(metric, months);
  renderSummary(series);
  renderChart(series);
}

function renderSummary(series) {
  const values = series.points.map((p) => p.value);
  const current = values[values.length - 1];
  const prev = values[values.length - 2] ?? current;
  const high = Math.max(...values);
  const low = Math.min(...values);
  const diffPct = Math.round(((current - prev) / prev) * 100);
  const rising = current >= prev;

  document.getElementById("pt-current").textContent = fmt(current, series.unit);
  document.getElementById("pt-high").textContent = fmt(high, series.unit);
  document.getElementById("pt-low").textContent = fmt(low, series.unit);

  const trendEl = document.getElementById("pt-current-trend");
  trendEl.textContent = (rising ? "▲ " : "▼ ") + Math.abs(diffPct) + "% vs. prev month";
  trendEl.className = "ww-predcard__note " + (rising ? "trend-up" : "trend-down");
}

function renderChart(series) {
  const chart = document.getElementById("pt-chart");
  chart.innerHTML = "";
  const max = Math.max(...series.points.map((p) => p.value));
  series.points.forEach((point) => {
    const bar = document.createElement("div");
    bar.className = "bar";
    const fill = document.createElement("span");
    fill.className = "fill";
    fill.style.height = Math.round((point.value / max) * 100) + "%";
    fill.title = fmt(point.value, series.unit);
    const label = document.createElement("small");
    label.textContent = point.label;
    bar.appendChild(fill);
    bar.appendChild(label);
    chart.appendChild(bar);
  });
}

async function renderRegions() {
  const rows = await fetchRegionalPrices();
  const tbody = document.getElementById("pt-regions");
  tbody.innerHTML = "";
  rows.forEach((r) => {
    const tr = document.createElement("tr");
    const rising = r.change >= 0;
    tr.innerHTML =
      `<td>${r.region}</td>` +
      `<td>${r.rate.toFixed(1)} ¢/kWh</td>` +
      `<td class="${rising ? "trend-up" : "trend-down"}">${rising ? "▲" : "▼"} ${Math.abs(r.change).toFixed(1)}%</td>`;
    tbody.appendChild(tr);
  });
}

function fmt(value, unit) {
  if (unit === "$/therm") return "$" + value.toFixed(2);
  return value.toFixed(1) + "¢";
}

metricSel.addEventListener("change", refresh);
rangeSel.addEventListener("change", refresh);

// Initial render
refresh();
renderRegions();
