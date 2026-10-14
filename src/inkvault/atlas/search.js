"use strict";
// Search, the reader and every request to the helper (spec §4.2-§4.4). Memory text reaches the page only through
// textContent and text nodes (spec §5 rule 8): nothing here builds HTML.
(() => {
const $ = id => document.getElementById(id);
const STATE = "inkvault-search";
const TEXT = {
  rebuilding: "Search is being rebuilt after something was removed. This page fills in again when it's ready.",
  updated: "Your memory was just updated, so this page was cleared.",
  changed: "Search was just updated: search again.",
  unavailable: "Can't read the vault right now.",
  busy: "The Atlas is being updated. Trying again shortly…",
  noDays: "Nothing to chart yet. Search still works.",
};
// Read before anything can save (Codex plan review #4): startup must never overwrite what it's about to restore.
const SAVED = readSaved();
let generation = null;                        // the vault state everything on screen came from
let atlasRevision;                            // which atlas.json the charts show; undefined until first loaded
const turn = {atlas: 0, search: 0, reader: 0}; // answers to older turns are ignored (spec §4.4)
let pollTimer = null, pingTimer = null;
let restored = false, restoring = false;
let state = blank();
let hits = [];

function blank() { return {view: "atlas", query: "", source: "all", from: "", to: "", selected: null, scroll: 0}; }
function next(kind) { return ++turn[kind]; }
function el(tag, text, cls) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; }

function readSaved() {
  try { return JSON.parse(sessionStorage.getItem(STATE)); } catch (e) { return null; }
}

async function api(path, body) {
  const options = {method: body === undefined ? "GET" : "POST", credentials: "same-origin", cache: "no-store",
                   headers: {"X-InkVault": "1"}};
  if (body !== undefined) { options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
  let res;
  try { res = await fetch(path, options); } catch (e) { return {status: "closed"}; }
  if (res.status === 403) return {status: "closed"};
  try { return await res.json(); } catch (e) { return {status: "unavailable"}; }
}

// Every answer names the vault state it came from; a different one than what's on screen means something was
// removed or rebuilt, and everything shown is cleared before anything new appears (spec §4.4).
function current(answer) {
  if (!answer.generation) return true;
  if (generation && answer.generation !== generation) { clearEverything("updated"); return false; }
  generation = answer.generation;
  return true;
}

function save() {
  if (restoring) return;  // a half-restored state is never written back
  try { sessionStorage.setItem(STATE, JSON.stringify({...state, generation, scroll: $("results").scrollTop})); } catch (e) {}
}

function clearEverything(reason) {
  for (const k of Object.keys(turn)) turn[k]++;
  generation = null;
  atlasRevision = undefined;
  hits = [];
  restoring = false;
  state = {...blank(), view: state.view};
  try { sessionStorage.removeItem(STATE); } catch (e) {}
  $("q").value = ""; $("s-from").value = ""; $("s-to").value = "";
  $("results").replaceChildren(); $("reader").replaceChildren();
  $("freshness").textContent = ""; $("search-status").textContent = ""; $("more").hidden = true;
  setChip("all", false);
  Atlas.clear();
  notice(TEXT[reason] || TEXT.updated);
  loadAtlas();
}

function notice(text, retry) {
  $("notice").hidden = !text;
  $("notice-text").textContent = text || "";
  $("notice-retry").hidden = !retry;
  $("notice-retry").onclick = retry || null;
}

function showOnly(id) {
  for (const s of ["welcome", "closed", "view-atlas", "view-search"]) $(s).hidden = s !== id;
}

function view(name) {
  state.view = name;
  $("tab-atlas").setAttribute("aria-pressed", String(name === "atlas"));
  $("tab-search").setAttribute("aria-pressed", String(name === "search"));
  showOnly(name === "atlas" ? "view-atlas" : "view-search");
  save();
}

// The helper has stopped. A tab that's already open says so; a reload gets the browser's own connection error,
// because nothing is left to serve a page (Codex plan review, limits).
function closed() {
  for (const k of Object.keys(turn)) turn[k]++;
  clearTimeout(pollTimer); clearInterval(pingTimer);
  notice(null);
  showOnly("closed");
}

async function loadAtlas() {
  clearTimeout(pollTimer);
  const my = next("atlas");
  const slow = setTimeout(() => notice("Building the Atlas…"), 800);  // the helper builds missing data in this call
  const a = await api("/api/atlas");
  clearTimeout(slow);
  if (my !== turn.atlas) return;
  if (a.status === "closed") return closed();
  if (a.status === "empty") { notice(null); return showOnly("welcome"); }
  if (a.status === "rebuilding" || a.status === "busy" || a.status === "changed") {
    notice(TEXT[a.status] || TEXT.changed);
    pollTimer = setTimeout(loadAtlas, 10000);
    return;
  }
  if (a.status !== "ok" && a.status !== "no-days") return notice(TEXT.unavailable, loadAtlas);
  if (!current(a)) return;
  notice(null);
  showFreshness(a.freshness);
  showChips(a.sources || {});
  atlasRevision = a.atlas_revision || null;
  $("atlas-empty").hidden = a.status === "ok";
  $("atlas-empty").textContent = a.status === "ok" ? "" : TEXT.noDays;
  Atlas.clear();
  if (a.status === "ok") Atlas.show(a.data);
  if (!restored) {
    restored = true;
    if (SAVED && SAVED.generation === generation && SAVED.query) return restore(SAVED);
  }
  view(state.view);
}

function showFreshness(f) {
  const built = f && f.built ? new Date(f.built) : null;
  const parts = [built ? `Search index updated ${built.toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"})}`
                       : "Search index: build time not recorded"];
  if (f && f.newer_screen) parts.push("Newer screen activity appears after the next search update");
  $("freshness").textContent = parts.join(". ") + ".";
  $("tz").textContent = f && f.timezone_now ? `(${f.timezone_now})` : "";
}

function showChips(sources) {
  document.querySelectorAll("[data-source]").forEach(b => { b.hidden = b.dataset.source !== "all" && !sources[b.dataset.source]; });
}

function setChip(name, search = true) {
  state.source = name;
  document.querySelectorAll("[data-source]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.source === name)));
  if (search && state.query) runSearch(0);
}

async function runSearch(offset) {
  const q = offset ? state.query : $("q").value.trim();
  if (!q) return;
  if (!offset) {
    next("reader");  // a record still loading belongs to the old results: its answer must not appear (review #2)
    state.query = q; state.selected = null; hits = [];
    $("results").replaceChildren(); $("reader").replaceChildren(); $("more").hidden = true;
  }
  view("search");
  const my = next("search");
  $("search-status").textContent = "Searching…";
  const a = await api("/api/search", {query: state.query, source: state.source, from: state.from || null,
                                      to: state.to || null, offset, generation: offset ? generation : null});
  if (my !== turn.search) return;
  if (a.status === "closed") return closed();
  if (a.status === "changed") return clearEverything("changed");
  if (a.status === "rebuilding") return clearEverything("rebuilding");
  if (a.status === "empty") { notice(null); return showOnly("welcome"); }
  if (a.status === "bad") { $("search-status").textContent = a.message; return; }
  if (a.status !== "ok") { $("search-status").textContent = TEXT.unavailable; return notice(TEXT.unavailable, () => runSearch(offset)); }
  if (!current(a)) return;
  const start = hits.length;
  hits.push(...a.hits);
  a.hits.forEach((h, i) => $("results").append(hitNode(h, start + i)));
  $("more").hidden = !a.more;
  const count = hits.length ? `${hits.length}${a.more ? "+" : ""} result${hits.length === 1 ? "" : "s"}.`
                            : "No matches. Try All sources or a wider date range.";
  $("search-status").textContent = count + (a.keyword_only ? " Keyword search only." : "");
  save();
  return a;
}

function hitNode(h, index) {
  const li = document.createElement("li");
  const b = el("button", undefined, "hit");
  b.type = "button";
  b.dataset.index = String(index);
  b.append(el("div", [h.source_label, h.time].filter(Boolean).join(" · "), "src"), el("div", h.title || "(untitled)", "t"));
  const ex = el("div", undefined, "ex");
  excerpt(ex, h);
  b.append(ex);
  b.addEventListener("click", () => openHit(index));
  li.append(b);
  return li;
}

function excerpt(box, h) {
  if (h.match.type !== "keyword") {
    box.append(h.excerpt, el("span", " · related by meaning", "by-meaning"));
    return;
  }
  let at = 0;
  for (const [a, b] of h.match.spans) {
    box.append(h.excerpt.slice(at, a), el("mark", h.excerpt.slice(a, b)));
    at = b;
  }
  box.append(h.excerpt.slice(at));
}

function openHit(index) {
  const h = hits[index];
  if (!h) return;
  document.querySelectorAll(".hit").forEach(b => b.setAttribute("aria-current", String(b.dataset.index === String(index))));
  return openRecord(h.kind, h.id);
}

// Opening a record replaces whatever the reader was loading; the answer must come from the vault state the page
// shows, which the helper checks (it answers "changed" otherwise) and the page checks again (review #2).
async function openRecord(kind, id) {
  state.selected = {kind, id};
  save();
  const my = next("reader");
  $("reader").replaceChildren(el("p", "Opening…", "meta"));
  const r = await api("/api/record", {kind, id, generation});
  if (my !== turn.reader) return;
  if (!readerAnswer(r)) return;
  renderRecord(r);
}

// Shared by every reader request: true when r is a record (or chunk) to show.
function readerAnswer(r) {
  if (r.status === "closed") { closed(); return false; }
  if (r.status === "changed") { clearEverything("changed"); return false; }
  if (r.status === "rebuilding") { clearEverything("rebuilding"); return false; }
  if (!current(r)) return false;
  if (r.status === "missing") { $("reader").replaceChildren(el("p", "This memory was removed.", "note")); return false; }
  if (r.status !== "ok") { $("reader").replaceChildren(el("p", TEXT.unavailable, "note")); return false; }
  return true;
}

const ROLES = {USER: "You", ASSISTANT: "Assistant"};

function renderRecord(r) {
  const box = $("reader");
  box.replaceChildren(el("h2", r.title || "(untitled)"), el("p", [r.source_label, r.meta].filter(Boolean).join(" · "), "meta"));
  if (r.note) box.append(el("p", r.note, "note"));
  if (r.url) box.append(link(r.url));
  if (r.visits) {
    const ul = document.createElement("ul");
    for (const v of r.visits) ul.append(el("li", `${v.time} · ${v.profile}${v.synced ? " (synced from another device)" : ""}`));
    box.append(ul);
  }
  const list = el("div");
  if (r.earlier) list.append(pager("Earlier messages", r.earlier, "earlier"));
  for (const it of r.items) list.append(itemNode(it, r.code));
  if (r.later) list.append(pager("Later messages", r.later, "later"));
  box.append(list);
  const m = list.querySelector(".matched");
  if (m) m.scrollIntoView({block: "center"});
}

function link(url) {
  if (!/^https?:\/\//i.test(url)) return el("p", url, "meta");
  const p = el("p", undefined, "meta");
  const a = el("a", url);
  a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer";
  p.append(a);
  return p;
}

function itemNode(it, code) {
  const role = (it.role || "").toLowerCase().replace(/[^a-z]/g, "");
  const div = el("div", undefined, "msg" + (role ? " role-" + role : "") + (it.matched ? " matched" : ""));
  const who = [ROLES[it.role] || it.role, it.time, it.matched ? "matched" : ""].filter(Boolean).join(" · ");
  if (who) div.append(el("div", who, "who"));
  const body = el(code ? "pre" : "div", it.text, "text");
  div.append(body);
  if (it.truncated && it.expand) div.append(expander(body, it.expand));
  return div;
}

// Paging and expansion belong to the record on screen: a click remembers the reader's turn, and an answer that
// arrives after another record (or a new search) took the reader is dropped.
function expander(body, cursor) {
  const b = el("button", "Show more of this", "expand");
  b.type = "button";
  b.addEventListener("click", async () => {
    const my = turn.reader;
    b.disabled = true;
    const a = await api("/api/record", {cursor: b.dataset.cursor || cursor});
    if (my !== turn.reader || !readerAnswer(a)) return;
    body.append(a.text);
    if (a.next) { b.dataset.cursor = a.next; b.disabled = false; } else b.remove();
  });
  return b;
}

function pager(label, cursor, dir) {
  const b = el("button", label, "pager");
  b.type = "button";
  b.addEventListener("click", async () => {
    const my = turn.reader;
    b.disabled = true;
    const a = await api("/api/record", {cursor});
    if (my !== turn.reader || !readerAnswer(a)) return;
    const nodes = a.items.map(it => itemNode(it, a.code));
    const more = dir === "earlier" ? a.earlier : a.later;
    if (more) nodes[dir === "earlier" ? "unshift" : "push"](pager(label, more, dir));
    b.replaceWith(...nodes);
  });
  return b;
}

// After a reload: the saved query, filters, view, scroll and selected record come back. A selected record that
// isn't on the first page of results is opened directly, so the selection survives (review #4).
async function restore(saved) {
  restoring = true;
  try {
    Object.assign(state, {source: saved.source || "all", from: saved.from || "", to: saved.to || ""});
    $("q").value = saved.query; $("s-from").value = state.from; $("s-to").value = state.to;
    setChip(state.source, false);
    const a = await runSearch(0);
    if (!a || !restoring) return;
    $("results").scrollTop = saved.scroll || 0;
    if (saved.selected) {
      const i = hits.findIndex(h => h.kind === saved.selected.kind && h.id === saved.selected.id);
      if (i >= 0) openHit(i); else openRecord(saved.selected.kind, saved.selected.id);
    }
    view(saved.view || "search");
  } finally {
    restoring = false;
    save();
  }
}

async function ping() {
  if (document.visibilityState !== "visible") return;
  const a = await api("/api/ping", {});
  if (a.status === "closed") return closed();
  if (a.rebuilding && generation) return clearEverything("rebuilding");
  if (a.generation && generation && a.generation !== generation) return clearEverything("updated");
  // The charts' data changed on its own (rebuilt, replaced or removed): redraw them, keep the search (review #7).
  if (atlasRevision !== undefined && "atlas_revision" in a && (a.atlas_revision || null) !== atlasRevision) {
    atlasRevision = undefined;
    Atlas.clear();
    loadAtlas();
  }
}

$("search-form").addEventListener("submit", e => { e.preventDefault(); runSearch(0); });
$("tab-atlas").addEventListener("click", () => view("atlas"));
$("tab-search").addEventListener("click", () => view("search"));
$("more").addEventListener("click", () => runSearch(hits.length));
document.querySelectorAll("[data-source]").forEach(b => b.addEventListener("click", () => setChip(b.dataset.source)));
for (const id of ["s-from", "s-to"]) $(id).addEventListener("change", () => {
  state.from = $("s-from").value; state.to = $("s-to").value;
  if (state.query) runSearch(0);
});
$("results").addEventListener("keydown", e => {
  if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
  const all = [...document.querySelectorAll(".hit")];
  const i = all.indexOf(document.activeElement);
  const j = e.key === "ArrowDown" ? Math.min(all.length - 1, i + 1) : Math.max(0, i - 1);
  if (all[j]) { all[j].focus(); e.preventDefault(); }
});
$("results").addEventListener("scroll", () => { state.scroll = $("results").scrollTop; save(); });
document.addEventListener("visibilitychange", ping);
window.addEventListener("focus", ping);
pingTimer = setInterval(ping, 60000);
loadAtlas();
})();
