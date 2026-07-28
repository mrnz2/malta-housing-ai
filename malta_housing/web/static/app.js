const PAGE_SIZE = 50;

const state = {
  offset: 0,
  total: 0,
  filters: { sort: "ai_score_desc" },
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
  statScored: document.getElementById("stat-scored"),
  statAvgScore: document.getElementById("stat-avg-score"),
  sortScore: document.getElementById("sort-score"),
  sortSelect: document.getElementById("filter-sort"),
  locality: document.getElementById("filter-locality"),
  source: document.getElementById("filter-source"),
  seller: document.getElementById("filter-seller"),
  type: document.getElementById("filter-type"),
  dialog: document.getElementById("detail-dialog"),
  close: document.getElementById("detail-close"),
  detailHidden: document.getElementById("detail-hidden"),
  detailNotes: document.getElementById("detail-notes"),
  detailNotesSave: document.getElementById("detail-notes-save"),
  detailNotesStatus: document.getElementById("detail-notes-status"),
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
  els.statScored.textContent = String(stats.with_score ?? 0);
  els.statAvgScore.textContent =
    stats.avg_ai_score != null ? formatScore(stats.avg_ai_score) : "—";
  fillSelect(els.locality, stats.localities || []);
  fillSelect(els.source, Object.keys(stats.sources || {}));
  fillSelect(els.seller, stats.seller_types || []);
  fillSelect(els.type, stats.property_types || []);
}

function syncSortHeader() {
  const sort = state.filters.sort || "ai_score_desc";
  const isScoreSort = sort === "ai_score_desc" || sort === "ai_score_asc";
  if (!els.sortScore) return;
  els.sortScore.classList.toggle("is-active", isScoreSort);
  els.sortScore.dataset.direction = sort === "ai_score_asc" ? "asc" : "desc";
  if (els.sortSelect && [...els.sortSelect.options].some((o) => o.value === sort)) {
    els.sortSelect.value = sort;
  }
}

function setScoreSort(direction) {
  const sort = direction === "asc" ? "ai_score_asc" : "ai_score_desc";
  state.filters = { ...state.filters, sort };
  state.offset = 0;
  syncSortHeader();
  loadListings();
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

function formatSeaProximity(value) {
  if (!value) return "—";
  const labels = {
    nad_morzem: "nad morzem",
    blisko: "blisko",
    daleko: "daleko",
  };
  return labels[value] || String(value).replaceAll("_", " ");
}

function formatAdjustment(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const n = Number(value);
  const sign = n > 0 ? "+" : "";
  return `${sign}${Number.isInteger(n) ? n : n.toFixed(1)}`;
}

function formatScore(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const n = Number(value);
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

function formatBreakdown(breakdown) {
  if (!breakdown || typeof breakdown !== "object") return "";
  return Object.entries(breakdown)
    .map(([key, value]) => `${key}: ${value}`)
    .join(" · ");
}

function formatDate(value) {
  if (value == null || value === "") return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(d);
}

function scoreClass(value) {
  if (value == null || Number.isNaN(Number(value))) return "score-none";
  const n = Number(value);
  if (n >= 8) return "score-high";
  if (n >= 5) return "score-mid";
  return "score-low";
}

function renderRows(items) {
  els.body.innerHTML = "";
  if (!items.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="10"><div class="empty">No listings match these filters.<br/>Run the scrape → parse → db pipeline, then refresh.</div></td>`;
    els.body.appendChild(tr);
    return;
  }

  for (const item of items) {
    const tr = document.createElement("tr");
    tr.tabIndex = 0;
    tr.dataset.id = String(item.id);
    if (item.is_hidden) tr.classList.add("is-hidden-row");
    const flags = flagsFor(item)
      .map((f) => `<span class="flag">${f}</span>`)
      .join("");
    const notesText = typeof item.notes === "string" ? item.notes.trim() : "";
    const notesCell = notesText
      ? `<td class="notes-cell"><span class="cell-notes" title="${escapeHtml(notesText)}">${escapeHtml(notesText)}</span></td>`
      : `<td class="notes-cell"></td>`;
    const score = item.ai_score;
    const scoreCell = score == null
      ? `<td class="score-cell"><span class="score-missing">—</span></td>`
      : `<td class="score-cell"><span class="score-badge ${scoreClass(score)}" title="${escapeHtml(item.ai_summary || "")}">${formatScore(score)}<span class="score-denom">/10</span></span></td>`;
    tr.innerHTML = `
      <td>
        <div class="cell-title">${escapeHtml(item.title || "Untitled")}</div>
        <span class="cell-meta">${flags || escapeHtml(item.property_type || "")}</span>
      </td>
      ${scoreCell}
      <td>${escapeHtml(item.locality || "—")}</td>
      <td class="price">${formatKm(item.distance_to_gzira_km)}</td>
      <td>${escapeHtml(formatSeaProximity(item.sea_proximity))}</td>
      <td class="price">${euro(item.price_eur)}</td>
      <td>${item.bedrooms ?? "—"}</td>
      <td>${escapeHtml(item.source || "—")}</td>
      ${notesCell}
      <td class="hide-cell">
        <label class="check hide-check" title="Hide property">
          <input type="checkbox" class="hide-toggle" data-id="${item.id}" ${item.is_hidden ? "checked" : ""} />
          <span class="hide-label">Hide property</span>
        </label>
      </td>
    `;
    tr.addEventListener("click", () => openDetail(item.id));
    tr.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openDetail(item.id);
      }
    });
    const hideToggle = tr.querySelector(".hide-toggle");
    const hideCheck = tr.querySelector(".hide-check");
    hideCheck.addEventListener("click", (e) => e.stopPropagation());
    hideToggle.addEventListener("change", () => {
      setHidden(item.id, hideToggle.checked);
    });
    els.body.appendChild(tr);
  }
}

async function setHidden(id, hidden) {
  const res = await fetch(`/api/listings/${id}/hidden`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hidden: Boolean(hidden) }),
  });
  if (!res.ok) {
    await loadListings();
    return;
  }
  await loadStats();
  // If we hid an item and are not showing hidden, remove it from the current view.
  if (hidden && !state.filters.show_hidden) {
    await loadListings();
    return;
  }
  const row = els.body.querySelector(`tr[data-id="${id}"]`);
  if (row) {
    row.classList.toggle("is-hidden-row", Boolean(hidden));
  }
}

async function saveNotes(id, notes) {
  const res = await fetch(`/api/listings/${id}/notes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ notes }),
  });
  if (!res.ok) {
    els.detailNotesStatus.hidden = false;
    els.detailNotesStatus.textContent = "Could not save notes.";
    return false;
  }
  const listing = await res.json();
  updateNotesCell(id, listing.notes || "");
  els.detailNotes.value = listing.notes || "";
  els.detailNotesStatus.hidden = false;
  els.detailNotesStatus.textContent = "Notes saved.";
  return true;
}

function updateNotesCell(id, notes) {
  const row = els.body.querySelector(`tr[data-id="${id}"]`);
  if (!row) return;
  const cell = row.querySelector(".notes-cell");
  if (!cell) return;
  const text = typeof notes === "string" ? notes.trim() : "";
  if (text) {
    cell.innerHTML = `<span class="cell-notes" title="${escapeHtml(text)}">${escapeHtml(text)}</span>`;
  } else {
    cell.innerHTML = "";
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
  syncSortHeader();
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

  const evalBlock = document.getElementById("detail-evaluation");
  const hasScore = listing.ai_score != null && !Number.isNaN(Number(listing.ai_score));
  evalBlock.hidden = !hasScore;
  if (hasScore) {
    const base = listing.base_score;
    const adj = listing.qualitative_adjustment;
    const scoreText =
      base != null && adj != null
        ? `${formatScore(listing.ai_score)} / 10 (base ${formatScore(base)} + LLM ${formatAdjustment(adj)})`
        : `${formatScore(listing.ai_score)} / 10`;
    document.getElementById("detail-score").textContent = scoreText;
    const breakdownEl = document.getElementById("detail-score-breakdown");
    const breakdownText = formatBreakdown(listing.score_breakdown);
    breakdownEl.textContent = breakdownText;
    breakdownEl.hidden = !breakdownText;
    document.getElementById("detail-summary").textContent = listing.ai_summary || "";
    const pros = Array.isArray(listing.pros) ? listing.pros : [];
    const cons = Array.isArray(listing.cons) ? listing.cons : [];
    document.getElementById("detail-pros").innerHTML = pros.length
      ? `<li class="eval-label">Pros</li>${pros.map((p) => `<li>${escapeHtml(p)}</li>`).join("")}`
      : "";
    document.getElementById("detail-cons").innerHTML = cons.length
      ? `<li class="eval-label">Cons</li>${cons.map((c) => `<li>${escapeHtml(c)}</li>`).join("")}`
      : "";
  }

  const grid = document.getElementById("detail-grid");
  const fields = [
    ["Bedrooms", listing.bedrooms ?? "—"],
    ["Seller", listing.seller_type || "—"],
    ["Distance to Gżira", formatKm(listing.distance_to_gzira_km)],
    ["Sea proximity", formatSeaProximity(listing.sea_proximity)],
    ["Freehold", listing.is_freehold ? "Yes" : "No"],
    ["Airspace", listing.has_airspace ? "Yes" : "No"],
    ["Sea view", listing.has_sea_view ? "Yes" : "No"],
    ["Shell form", listing.is_shell_form ? "Yes" : "No"],
    ["AI evaluated", formatDate(listing.ai_evaluated_at)],
    ["Scraped", formatDate(listing.scraped_at)],
    ["Updated", formatDate(listing.updated_at)],
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
            `<li><span>${escapeHtml(formatDate(h.recorded_at))}</span><strong>${euro(h.price_eur)}</strong></li>`
        )
        .join("")
    : "<li><span>No history yet</span><strong>—</strong></li>";

  const link = document.getElementById("detail-url");
  link.href = listing.url || "#";
  link.hidden = !listing.url;

  els.detailHidden.checked = Boolean(listing.is_hidden);
  els.detailHidden.onchange = async () => {
    await setHidden(listing.id, els.detailHidden.checked);
    if (els.detailHidden.checked && !state.filters.show_hidden) {
      els.dialog.close();
    }
  };

  els.detailNotes.value = listing.notes || "";
  els.detailNotesStatus.hidden = true;
  els.detailNotesStatus.textContent = "";
  els.detailNotesSave.onclick = async () => {
    await saveNotes(listing.id, els.detailNotes.value);
  };

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
    state.filters = { sort: "ai_score_desc" };
    state.offset = 0;
    loadListings();
  }, 0);
});

if (els.sortScore) {
  els.sortScore.addEventListener("click", () => {
    const current = state.filters.sort || "ai_score_desc";
    const next = current === "ai_score_desc" ? "ai_score_asc" : "ai_score_desc";
    setScoreSort(next === "ai_score_asc" ? "asc" : "desc");
  });
}

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
