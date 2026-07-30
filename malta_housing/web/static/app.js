const PAGE_SIZE = 50;
const DEFAULT_SORT = "ai_score_desc";
const FILTER_KEYS = [
  "q",
  "locality",
  "source",
  "seller_type",
  "property_type",
  "min_price",
  "max_price",
  "freehold",
  "airspace",
  "show_hidden",
  "fav_only",
  "sort",
];

const state = {
  offset: 0,
  total: 0,
  filters: { sort: DEFAULT_SORT },
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
  detailFav: document.getElementById("detail-fav"),
  detailHidden: document.getElementById("detail-hidden"),
  detailNotes: document.getElementById("detail-notes"),
  detailNotesSave: document.getElementById("detail-notes-save"),
  detailNotesStatus: document.getElementById("detail-notes-status"),
  detailReady: document.getElementById("detail-ready"),
};

function euro(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return new Intl.NumberFormat("en-MT", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 0,
  }).format(Number(value));
}

function formatPricePerSqm(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return euro(value);
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
  if (!filters.sort) {
    filters.sort = DEFAULT_SORT;
  }
  return filters;
}

function pageFromOffset(offset) {
  return Math.floor(offset / PAGE_SIZE) + 1;
}

function offsetFromPage(page) {
  const n = Number.parseInt(String(page), 10);
  if (!Number.isFinite(n) || n < 1) return 0;
  return (n - 1) * PAGE_SIZE;
}

function readUrlState() {
  const params = new URLSearchParams(window.location.search);
  const filters = { sort: DEFAULT_SORT };
  for (const key of FILTER_KEYS) {
    const value = params.get(key);
    if (value != null && value.trim() !== "") {
      filters[key] = value.trim();
    }
  }
  return {
    filters,
    offset: offsetFromPage(params.get("page")),
  };
}

function applyStateToForm(filters) {
  for (const element of els.form.elements) {
    if (!element.name) continue;
    if (element.type === "checkbox") {
      element.checked = element.name in filters;
    } else {
      element.value = filters[element.name] ?? "";
    }
  }
  if (els.sortSelect) {
    els.sortSelect.value = filters.sort || DEFAULT_SORT;
  }
}

function syncUrl({ push = false } = {}) {
  const params = new URLSearchParams();
  for (const key of FILTER_KEYS) {
    const value = state.filters[key];
    if (value == null || String(value).trim() === "") continue;
    if (key === "sort" && value === DEFAULT_SORT) continue;
    params.set(key, String(value).trim());
  }
  const page = pageFromOffset(state.offset);
  if (page > 1) {
    params.set("page", String(page));
  }
  const qs = params.toString();
  const target = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
  const current = `${window.location.pathname}${window.location.search}`;
  if (current === target) return;
  if (push) {
    history.pushState(null, "", target);
  } else {
    history.replaceState(null, "", target);
  }
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
  commitListings({ push: true });
}

function flagsFor(item) {
  const bits = [];
  if (item.is_freehold) bits.push("Freehold");
  if (item.has_airspace) bits.push("Airspace");
  if (item.has_sea_view) bits.push("Sea view");
  if (item.is_shell_form) bits.push("Shell");
  return bits;
}

function formatReady(value) {
  if (value === true) return "Yes";
  if (value === false) return "No";
  return "?";
}

function readySelectValue(value) {
  if (value === true) return "true";
  if (value === false) return "false";
  return "unknown";
}

function readyFromSelect(value) {
  if (value === "true") return true;
  if (value === "false") return false;
  return null;
}

function readyClass(value) {
  if (value === true) return "ready-yes";
  if (value === false) return "ready-no";
  return "ready-unknown";
}

function formatKm(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const n = Number(value);
  return `${Number.isInteger(n) ? n : n.toFixed(1)} km`;
}

function formatSeaProximity(value) {
  if (!value) return "—";
  const labels = {
    nad_morzem: "seafront",
    blisko: "near sea",
    daleko: "inland",
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

const BREAKDOWN_LABELS = {
  price_per_sqm: "Price/m²",
  distance_to_gzira: "Distance to Gżira",
  sea_proximity: "Sea proximity",
  area_sqm: "Area",
  structured_flags: "Property flags",
};

function formatBreakdown(breakdown) {
  if (!breakdown || typeof breakdown !== "object") return "";
  return Object.entries(breakdown)
    .map(([key, value]) => `${BREAKDOWN_LABELS[key] || key}: ${value}`)
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

const TABLE_COLS = 12;

function favIcon(isFav) {
  return isFav ? "♥" : "♡";
}

function syncFavButton(btn, isFav) {
  if (!btn) return;
  btn.textContent = favIcon(isFav);
  btn.classList.toggle("is-fav", Boolean(isFav));
  btn.setAttribute("aria-pressed", String(Boolean(isFav)));
}

function renderNotesRow(id, notesText) {
  const tr = document.createElement("tr");
  tr.className = "notes-row";
  tr.dataset.notesFor = String(id);
  tr.innerHTML = `<td colspan="${TABLE_COLS}"><div class="row-notes">${escapeHtml(notesText)}</div></td>`;
  tr.addEventListener("click", () => openDetail(id));
  return tr;
}

function renderRows(items) {
  els.body.innerHTML = "";
  if (!items.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="${TABLE_COLS}"><div class="empty">No listings match these filters.<br/>Run the scrape → parse → db pipeline, then refresh.</div></td>`;
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
    const newBadge = item.is_new ? `<span class="badge-new">New</span>` : "";
    const score = item.ai_score;
    const scoreCell = score == null
      ? `<td class="score-cell"><span class="score-missing">—</span></td>`
      : `<td class="score-cell"><span class="score-badge ${scoreClass(score)}" title="${escapeHtml(item.ai_summary || "")}">${formatScore(score)}<span class="score-denom">/10</span></span></td>`;
    tr.innerHTML = `
      <td>
        <div class="cell-title">${newBadge}${escapeHtml(item.title || "Untitled")}</div>
        <span class="cell-meta">${flags || escapeHtml(item.property_type || "")}</span>
      </td>
      ${scoreCell}
      <td>${escapeHtml(item.locality || "—")}</td>
      <td class="price">${formatKm(item.distance_to_gzira_km)}</td>
      <td>${escapeHtml(formatSeaProximity(item.sea_proximity))}</td>
      <td class="price">${euro(item.price_eur)}</td>
      <td class="price">${formatPricePerSqm(item.price_per_sqm)}</td>
      <td>${item.bedrooms ?? "—"}</td>
      <td>${escapeHtml(item.source || "—")}</td>
      <td class="ready-cell"><span class="ready-badge ${readyClass(item.ready)}">${formatReady(item.ready)}</span></td>
      <td class="fav-cell">
        <button type="button" class="fav-btn ${item.is_fav ? "is-fav" : ""}" data-id="${item.id}" aria-label="Favorite" aria-pressed="${item.is_fav ? "true" : "false"}">${favIcon(item.is_fav)}</button>
      </td>
      <td class="hide-cell">
        <label class="check hide-check" title="Hide">
          <input type="checkbox" class="hide-toggle" data-id="${item.id}" ${item.is_hidden ? "checked" : ""} />
          <span class="hide-label">Hide</span>
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
    const favBtn = tr.querySelector(".fav-btn");
    hideCheck.addEventListener("click", (e) => e.stopPropagation());
    favBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const current = favBtn.classList.contains("is-fav");
      setFav(item.id, !current);
    });
    hideToggle.addEventListener("change", () => {
      setHidden(item.id, hideToggle.checked);
    });
    els.body.appendChild(tr);
    if (notesText) {
      const notesRow = renderNotesRow(item.id, notesText);
      if (item.is_hidden) notesRow.classList.add("is-hidden-row");
      els.body.appendChild(notesRow);
    }
  }
}

async function setFav(id, fav) {
  const res = await fetch(`/api/listings/${id}/fav`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fav: Boolean(fav) }),
  });
  if (!res.ok) {
    await loadListings();
    return;
  }
  const listing = await res.json();
  const actualFav = Boolean(listing.is_fav);
  updateFavCell(id, actualFav);
  if (els.detailFav && els.dialog.open && Number(els.dialog.dataset.listingId) === id) {
    syncFavButton(els.detailFav, actualFav);
  }
  return actualFav;
}

function updateFavCell(id, fav) {
  const row = els.body.querySelector(`tr[data-id="${id}"]`);
  if (!row) return;
  const btn = row.querySelector(".fav-btn");
  syncFavButton(btn, fav);
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
  // Remove item from current view when it no longer matches the hidden filter.
  if ((hidden && !state.filters.show_hidden) || (!hidden && state.filters.show_hidden)) {
    await loadListings();
    return;
  }
  const row = els.body.querySelector(`tr[data-id="${id}"]`);
  if (row) {
    row.classList.toggle("is-hidden-row", Boolean(hidden));
    const notesRow = els.body.querySelector(`tr.notes-row[data-notes-for="${id}"]`);
    if (notesRow) notesRow.classList.toggle("is-hidden-row", Boolean(hidden));
  }
}

function updateReadyCell(id, ready) {
  const row = els.body.querySelector(`tr[data-id="${id}"]`);
  if (!row) return;
  const badge = row.querySelector(".ready-badge");
  if (!badge) return;
  badge.textContent = formatReady(ready);
  badge.className = `ready-badge ${readyClass(ready)}`;
}

async function setReady(id, ready) {
  const res = await fetch(`/api/listings/${id}/ready`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ready }),
  });
  if (!res.ok) return null;
  const listing = await res.json();
  updateReadyCell(id, listing.ready);
  return listing;
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
  const existing = els.body.querySelector(`tr.notes-row[data-notes-for="${id}"]`);
  const text = typeof notes === "string" ? notes.trim() : "";
  if (!text) {
    existing?.remove();
    return;
  }
  if (existing) {
    const el = existing.querySelector(".row-notes");
    if (el) el.textContent = text;
    return;
  }
  row.after(renderNotesRow(id, text));
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function clampOffset() {
  if (state.total <= 0) {
    state.offset = 0;
    return false;
  }
  const maxOffset = Math.max(0, Math.floor((state.total - 1) / PAGE_SIZE) * PAGE_SIZE);
  if (state.offset <= maxOffset) return false;
  state.offset = maxOffset;
  return true;
}

async function loadListings({ syncUrl: shouldSyncUrl = false, push = false } = {}) {
  els.count.textContent = "Loading…";
  const res = await fetch(`/api/listings?${buildQuery()}`);
  const data = await res.json();
  state.total = data.total || 0;
  if (clampOffset()) {
    if (shouldSyncUrl) syncUrl({ push });
    return loadListings();
  }
  if (shouldSyncUrl) syncUrl({ push });
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

function commitListings({ push = false } = {}) {
  return loadListings({ syncUrl: true, push });
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
    listing.is_new ? "New" : null,
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

  els.dialog.dataset.listingId = String(listing.id);
  syncFavButton(els.detailFav, listing.is_fav);
  els.detailFav.onclick = async (e) => {
    e.stopPropagation();
    const next = !listing.is_fav;
    const actualFav = await setFav(listing.id, next);
    if (actualFav != null) listing.is_fav = actualFav;
  };

  els.detailHidden.checked = Boolean(listing.is_hidden);
  els.detailReady.value = readySelectValue(listing.ready);
  els.detailReady.onchange = async () => {
    const updated = await setReady(listing.id, readyFromSelect(els.detailReady.value));
    if (updated) listing.ready = updated.ready;
  };
  els.detailHidden.onchange = async () => {
    await setHidden(listing.id, els.detailHidden.checked);
    if (
      (els.detailHidden.checked && !state.filters.show_hidden) ||
      (!els.detailHidden.checked && state.filters.show_hidden)
    ) {
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
  commitListings({ push: true });
});

els.form.addEventListener("reset", () => {
  setTimeout(() => {
    state.filters = { sort: DEFAULT_SORT };
    state.offset = 0;
    commitListings({ push: true });
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
  commitListings({ push: true });
});

els.next.addEventListener("click", () => {
  state.offset += PAGE_SIZE;
  commitListings({ push: true });
});

window.addEventListener("popstate", () => {
  const urlState = readUrlState();
  state.filters = urlState.filters;
  state.offset = urlState.offset;
  applyStateToForm(urlState.filters);
  loadListings();
});

els.close.addEventListener("click", () => els.dialog.close());
els.dialog.addEventListener("click", (e) => {
  if (e.target === els.dialog) els.dialog.close();
});

const initialUrlState = readUrlState();
state.filters = initialUrlState.filters;
state.offset = initialUrlState.offset;

await loadStats();
applyStateToForm(initialUrlState.filters);
syncUrl();
await loadListings();
