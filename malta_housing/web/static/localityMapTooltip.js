/**
 * Locality map tooltip — hover a town name to preview its position on Malta.
 *
 * Equirectangular projection (same extent as map.svg):
 *   N 36.10° / S 35.7833° / W 14.1667° / E 14.60°
 *
 *   x = ((lng - minLng) / (maxLng - minLng)) * viewBoxWidth
 *   y = (1 - (lat - minLat) / (maxLat - minLat)) * viewBoxHeight
 */

const SVG_NS = "http://www.w3.org/2000/svg";
const DEFAULT_MAP_URL = "/static/map.svg";
const DEFAULT_COORDS_URL = "/api/localities";
const SHOW_DELAY_MS = 120;
const HIDE_DELAY_MS = 80;
const FALLBACK_VIEWBOX = { x: 0, y: 0, width: 800, height: 600 };

/** @type {{ minLat: number, maxLat: number, minLng: number, maxLng: number }} */
export const MAP_BOUNDS = {
  minLat: 35.7833,
  maxLat: 36.1,
  minLng: 14.1667,
  maxLng: 14.6,
};

/**
 * Parse an SVG viewBox string or read it from an element.
 * @param {string | SVGSVGElement | null | undefined} source
 * @returns {{ x: number, y: number, width: number, height: number }}
 */
export function parseViewBox(source) {
  let raw = "";
  if (typeof source === "string") {
    raw = source;
  } else if (source && typeof source.getAttribute === "function") {
    raw = source.getAttribute("viewBox") || "";
    if (!raw) {
      const width = parseFloat(source.getAttribute("width") || "");
      const height = parseFloat(source.getAttribute("height") || "");
      if (Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0) {
        return { x: 0, y: 0, width, height };
      }
    }
  }
  const parts = String(raw)
    .trim()
    .replace(/,/g, " ")
    .split(/\s+/)
    .map(Number);
  if (parts.length === 4 && parts.every((n) => Number.isFinite(n))) {
    const [x, y, width, height] = parts;
    if (width > 0 && height > 0) return { x, y, width, height };
  }
  return { ...FALLBACK_VIEWBOX };
}

/**
 * Convert WGS-84 lat/lng to SVG user units (equirectangular).
 * @param {number} lat
 * @param {number} lng
 * @param {{ x?: number, y?: number, width: number, height: number }} viewBox
 * @param {{ minLat: number, maxLat: number, minLng: number, maxLng: number }} [bounds]
 * @returns {{ x: number, y: number }}
 */
export function latLngToSvgPoint(lat, lng, viewBox, bounds = MAP_BOUNDS) {
  const originX = viewBox.x ?? 0;
  const originY = viewBox.y ?? 0;
  const spanLng = bounds.maxLng - bounds.minLng;
  const spanLat = bounds.maxLat - bounds.minLat;
  const x =
    originX + ((Number(lng) - bounds.minLng) / spanLng) * viewBox.width;
  const y =
    originY +
    (1 - (Number(lat) - bounds.minLat) / spanLat) * viewBox.height;
  return { x, y };
}

/** Match Python `geo._normalize` so CSV aliases resolve in the browser. */
export function normalizeLocalityName(text) {
  return String(text || "")
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .replace(/[ħĦ]/g, "h")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function candidateTokens(locality) {
  const text = String(locality || "").trim();
  if (!text) return [];
  const parts = [text, ...text.split(/[,/|]/).map((p) => p.trim()).filter(Boolean)];
  const candidates = [];
  for (const part of parts) {
    const norm = normalizeLocalityName(part);
    if (norm) candidates.push(norm);
    if (part.includes("(")) {
      const before = normalizeLocalityName(part.split("(", 1)[0]);
      if (before) candidates.push(before);
    }
    for (const suffix of [" malta", " area", " bay"]) {
      if (norm.endsWith(suffix) && norm.length > suffix.length + 2) {
        candidates.push(norm.slice(0, -suffix.length).trim());
      }
    }
  }
  return [...new Set(candidates)];
}

/**
 * @param {string} name
 * @param {Record<string, { name: string, lat: number, lng: number }>} lookup
 * @returns {{ name: string, lat: number, lng: number } | null}
 */
export function lookupLocality(name, lookup) {
  if (!name || !lookup) return null;
  for (const key of candidateTokens(name)) {
    if (lookup[key]) return lookup[key];
  }
  const joined = normalizeLocalityName(name);
  if (!joined) return null;
  for (const [key, place] of Object.entries(lookup)) {
    if (key.length >= 4 && (key.includes(joined) || joined.includes(key))) {
      return place;
    }
  }
  return null;
}

function positionTooltip(tooltipEl, anchor) {
  const rect = anchor.getBoundingClientRect();
  const margin = 8;
  const gap = 8;
  tooltipEl.hidden = false;
  const tipRect = tooltipEl.getBoundingClientRect();

  let left = rect.left + rect.width / 2 - tipRect.width / 2;
  let top = rect.bottom + gap;

  if (left + tipRect.width > window.innerWidth - margin) {
    left = window.innerWidth - tipRect.width - margin;
  }
  if (left < margin) left = margin;

  if (top + tipRect.height > window.innerHeight - margin) {
    top = rect.top - tipRect.height - gap;
  }
  if (top < margin) top = margin;

  tooltipEl.style.left = `${Math.round(left)}px`;
  tooltipEl.style.top = `${Math.round(top)}px`;
}

function createSvgEl(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    el.setAttribute(key, String(value));
  }
  return el;
}

function setMarker(svg, x, y) {
  let group = svg.querySelector(".locality-map-marker");
  if (!group) {
    group = createSvgEl("g", { class: "locality-map-marker" });
    const ring = createSvgEl("circle", {
      class: "locality-map-marker__ring",
      r: "16",
      fill: "none",
      stroke: "#e23b3b",
      "stroke-width": "2.2",
    });
    const dot = createSvgEl("circle", {
      class: "locality-map-marker__dot",
      r: "7",
      fill: "#e23b3b",
      stroke: "#fff",
      "stroke-width": "2.4",
    });
    group.append(ring, dot);
    svg.appendChild(group);
  }
  group.setAttribute("transform", `translate(${x} ${y})`);
  group.setAttribute("visibility", "visible");
}

/**
 * Singleton tooltip: one map, one marker, delay so it does not flicker on the table.
 * @param {{ mapUrl?: string, coordsUrl?: string, showDelayMs?: number, hideDelayMs?: number }} [options]
 */
export function createLocalityMapTooltip(options = {}) {
  const mapUrl = options.mapUrl || DEFAULT_MAP_URL;
  const coordsUrl = options.coordsUrl || DEFAULT_COORDS_URL;
  const showDelayMs = options.showDelayMs ?? SHOW_DELAY_MS;
  const hideDelayMs = options.hideDelayMs ?? HIDE_DELAY_MS;

  /** @type {HTMLDivElement | null} */
  let root = null;
  /** @type {SVGSVGElement | null} */
  let svg = null;
  /** @type {HTMLElement | null} */
  let titleEl = null;
  /** @type {Element | null} */
  let anchor = null;
  /** @type {ReturnType<typeof setTimeout> | null} */
  let showTimer = null;
  /** @type {ReturnType<typeof setTimeout> | null} */
  let hideTimer = null;
  /** @type {Record<string, { name: string, lat: number, lng: number }>} */
  let lookup = {};
  let bounds = { ...MAP_BOUNDS };
  /** @type {{ x: number, y: number, width: number, height: number }} */
  let viewBox = { ...FALLBACK_VIEWBOX };
  /** @type {Promise<boolean> | null} */
  let ready = null;

  function clearTimers() {
    if (showTimer) {
      clearTimeout(showTimer);
      showTimer = null;
    }
    if (hideTimer) {
      clearTimeout(hideTimer);
      hideTimer = null;
    }
  }

  function ensureDom() {
    if (root) return root;
    root = document.createElement("div");
    root.id = "locality-map-tooltip";
    root.className = "locality-map-tooltip";
    root.hidden = true;
    root.setAttribute("role", "tooltip");
    root.innerHTML = `
      <p class="locality-map-tooltip__title"></p>
      <div class="locality-map-tooltip__stage"></div>
    `;
    titleEl = root.querySelector(".locality-map-tooltip__title");
    document.body.appendChild(root);
    return root;
  }

  async function load() {
    if (ready) return ready;
    ready = (async () => {
      ensureDom();
      const [mapRes, coordsRes] = await Promise.all([
        fetch(mapUrl),
        fetch(coordsUrl),
      ]);
      if (coordsRes.ok) {
        const payload = await coordsRes.json();
        lookup = payload.lookup || {};
        if (payload.bounds) {
          bounds = { ...MAP_BOUNDS, ...payload.bounds };
        }
      }
      if (!mapRes.ok) return false;
      const markup = await mapRes.text();
      const parsed = new DOMParser().parseFromString(markup, "image/svg+xml");
      const parsedSvg = parsed.querySelector("svg");
      if (!parsedSvg) return false;
      viewBox = parseViewBox(parsedSvg);
      svg = createSvgEl("svg", {
        viewBox: `${viewBox.x} ${viewBox.y} ${viewBox.width} ${viewBox.height}`,
        class: "locality-map-tooltip__svg",
        "aria-hidden": "true",
      });
      while (parsedSvg.firstChild) {
        svg.appendChild(parsedSvg.firstChild);
      }
      const stage = root.querySelector(".locality-map-tooltip__stage");
      stage.replaceChildren(svg);
      return true;
    })().catch(() => false);
    return ready;
  }

  function render(place, label) {
    if (!svg || !titleEl) return;
    titleEl.textContent = place.name || label;
    const { x, y } = latLngToSvgPoint(place.lat, place.lng, viewBox, bounds);
    setMarker(svg, x, y);
  }

  async function show(anchorEl, localityName) {
    const ok = await load();
    if (!ok || anchor !== anchorEl) return;
    const place = lookupLocality(localityName, lookup);
    if (!place) {
      hide();
      return;
    }
    render(place, localityName);
    root.hidden = false;
    positionTooltip(root, anchorEl);
  }

  function hide() {
    clearTimers();
    anchor = null;
    if (root) root.hidden = true;
  }

  function scheduleShow(anchorEl, localityName) {
    clearTimers();
    anchor = anchorEl;
    const alreadyOpen = Boolean(root && !root.hidden);
    const delay = alreadyOpen ? 0 : showDelayMs;
    showTimer = setTimeout(() => {
      showTimer = null;
      show(anchorEl, localityName);
    }, delay);
  }

  function scheduleHide(anchorEl) {
    if (showTimer) {
      clearTimeout(showTimer);
      showTimer = null;
    }
    if (anchorEl && anchor !== anchorEl) return;
    hideTimer = setTimeout(() => {
      hideTimer = null;
      hide();
    }, hideDelayMs);
  }

  function reposition() {
    if (!root || root.hidden || !anchor) return;
    positionTooltip(root, anchor);
  }

  /**
   * @param {HTMLElement} el
   * @param {string} localityName
   */
  function wire(el, localityName) {
    const name = (localityName || "").trim();
    if (!name || name === "—") return;
    el.classList.add("has-locality-map");
    el.addEventListener("mouseenter", () => scheduleShow(el, name));
    el.addEventListener("mouseleave", () => scheduleHide(el));
    el.addEventListener("focus", () => {
      if (el.matches(":focus-visible")) scheduleShow(el, name);
    });
    el.addEventListener("blur", () => scheduleHide(el));
  }

  return { init: load, wire, hide, reposition };
}

export const localityMapTooltip = createLocalityMapTooltip();
