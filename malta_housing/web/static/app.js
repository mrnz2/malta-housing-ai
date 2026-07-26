const PAGE_SIZE = 50;

const state = {
  offset: 0,
  total: 0,
  filters: {},
};

const els = {
  form: document.getElementById("filter-form"),
  body: document.getElementById("listings-body"),
  count: document.getElementById("results-count"),
  pager: document.getElementById("pager"),
  pageLabel: document.getElementById("page-label"),
  prev: document.getElementById("prev-page"),
  next: document.getElementById("next-page"),
  statTotal: document.getElementById("stat-total"),
  statAvg: document.getElementById("stat-avg"),
  locality: document.getElementById("filter-locality"),
  source: document.getElementById("filter-source"),
  seller: document.getElementById("filter-seller"),
  type: document.getElementById("filter-type"),
  dialog: document.getElementById("detail-dialog"),
  close: document.getElementById("detail-close"),
};

function euro(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return new Intl.NumberFormat("en-MT", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 0,
  }).format(Number(value));
}

function fillSelect(select, values, blankLabel = "All") {
  const current = select.value;
  select.innerHTML = "";
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = blankLabel;
  select.appendChild(blank);
  for (const value of values) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = value;
    select.appendChild(opt);
  }
  if ([...select.options].some((o) => o.value === current)) {
    select.value = current;
  }
}

function readFilters() {
  const data = new FormData(els.form);
  const filters = {};
  for (const [key, value] of data.entries()) {
    if (typeof value === "string" && value.trim() !== "") {
      filters[key] = value.trim();
    }
  }
  return filters;
}

function buildQuery(extra = {}) {
  const params = new URLSearchParams({ ...state.filters, ...extra });
  params.set("limit", String(PAGE_SIZE));
  params.set("offset", String(state.offset));
  return params.toString();
}

async function loadStats() {
  const res = await fetch("/api/stats");
  const stats = await res.json();
  els.statTotal.textContent = String(stats.total ?? 0);
  els.statAvg.textContent = euro(stats.avg_price);
  fillSelect(els.locality, stats.localities || []);
  fillSelect(els.source, Object.keys(stats.sources || {}));
  fillSelect(els.seller, stats.seller_types || []);
  fillSelect(els.type, stats.property_types || []);
}

function flagsFor(item) {
  const bits = [];
  if (item.is_freehold) bits.push("Freehold");
  if (item.has_airspace) bits.push("Airspace");
  if (item.has_sea_view) bits.push("Sea view");
  if (item.is_shell_form) bits.push("Shell");
  return bits;
}

function formatKm(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const n = Number(value);
  return `${Number.isInteger(n) ? n : n.toFixed(1)} km`;
}

function renderRows(items) {
  els.body.innerHTML = "";
  if (!items.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="7"><div class="empty">No listings match these filters.<br/>Run the scrape → parse → db pipeline, then refresh.</div></td>`;
    els.body.appendChild(tr);
    return;
  }

  for (const item of items) {
    const tr = document.createElement("tr");
    tr.tabIndex = 0;
    tr.dataset.id = String(item.id);
    const flags = flagsFor(item)
      .map((f) => `<span class="flag">${f}</span>`)
      .join("");
    tr.innerHTML = `
      <td>
        <div class="cell-title">${escapeHtml(item.title || "Untitled")}</div>
        <span class="cell-meta">${flags || escapeHtml(item.property_type || "")}</span>
      </td>
      <td>${escapeHtml(item.locality || "—")}</td>
      <td class="price">${formatKm(item.distance_to_gzira_km)}</td>
      <td class="price">${euro(item.price_eur)}</td>
      <td>${item.bedrooms ?? "—"}</td>
      <td>${escapeHtml(item.seller_type || "—")}</td>
      <td>${escapeHtml(item.source || "—")}</td>
    `;
    tr.addEventListener("click", () => openDetail(item.id));
    tr.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openDetail(item.id);
      }
    });
    els.body.appendChild(tr);
  }
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function loadListings() {
  els.count.textContent = "Loading…";
  const res = await fetch(`/api/listings?${buildQuery()}`);
  const data = await res.json();
  state.total = data.total || 0;
  renderRows(data.items || []);
  const from = state.total === 0 ? 0 : state.offset + 1;
  const to = Math.min(state.offset + PAGE_SIZE, state.total);
  els.count.textContent = `${state.total} match${state.total === 1 ? "" : "es"} · showing ${from}–${to}`;

  const pages = Math.max(1, Math.ceil(state.total / PAGE_SIZE));
  const page = Math.floor(state.offset / PAGE_SIZE) + 1;
  els.pager.hidden = state.total <= PAGE_SIZE;
  els.pageLabel.textContent = `Page ${page} / ${pages}`;
  els.prev.disabled = state.offset <= 0;
  els.next.disabled = state.offset + PAGE_SIZE >= state.total;
}

async function openDetail(id) {
  const [listingRes, historyRes] = await Promise.all([
    fetch(`/api/listings/${id}`),
    fetch(`/api/listings/${id}/history`),
  ]);
  if (!listingRes.ok) return;
  const listing = await listingRes.json();
  const historyPayload = await historyRes.json();
  const history = historyPayload.items || [];

  document.getElementById("detail-kicker").textContent = [
    listing.locality,
    listing.property_type,
    listing.source,
  ]
    .filter(Boolean)
    .join(" · ");
  document.getElementById("detail-title").textContent = listing.title || "Untitled";
  document.getElementById("detail-price").textContent = euro(listing.price_eur);

  const grid = document.getElementById("detail-grid");
  const fields = [
    ["Bedrooms", listing.bedrooms ?? "—"],
    ["Seller", listing.seller_type || "—"],
    ["Distance to Gżira", formatKm(listing.distance_to_gzira_km)],
    ["Freehold", listing.is_freehold ? "Yes" : "No"],
    ["Airspace", listing.has_airspace ? "Yes" : "No"],
    ["Sea view", listing.has_sea_view ? "Yes" : "No"],
    ["Shell form", listing.is_shell_form ? "Yes" : "No"],
    ["Scraped", listing.scraped_at || "—"],
    ["Updated", listing.updated_at || "—"],
  ];
  grid.innerHTML = fields
    .map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd></div>`)
    .join("");

  const features = document.getElementById("detail-features");
  const feats = Array.isArray(listing.key_features) ? listing.key_features : [];
  features.innerHTML = feats.length
    ? feats.map((f) => `<span class="feature">${escapeHtml(f)}</span>`).join("")
    : "";

  const hist = document.getElementById("detail-history");
  hist.innerHTML = history.length
    ? history
        .map(
          (h) =>
            `<li><span>${escapeHtml(h.recorded_at || "—")}</span><strong>${euro(h.price_eur)}</strong></li>`
        )
        .join("")
    : "<li><span>No history yet</span><strong>—</strong></li>";

  const link = document.getElementById("detail-url");
  link.href = listing.url || "#";
  link.hidden = !listing.url;

  els.dialog.showModal();
}

els.form.addEventListener("submit", (e) => {
  e.preventDefault();
  state.filters = readFilters();
  state.offset = 0;
  loadListings();
});

els.form.addEventListener("reset", () => {
  setTimeout(() => {
    state.filters = {};
    state.offset = 0;
    loadListings();
  }, 0);
});

els.prev.addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - PAGE_SIZE);
  loadListings();
});

els.next.addEventListener("click", () => {
  state.offset += PAGE_SIZE;
  loadListings();
});

els.close.addEventListener("click", () => els.dialog.close());
els.dialog.addEventListener("click", (e) => {
  if (e.target === els.dialog) els.dialog.close();
});

await loadStats();
await loadListings();
