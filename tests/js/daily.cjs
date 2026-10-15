// daily.js: pure helpers (fake clocks) and the cards driven against a tiny stand-in DOM. Fictional data.
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const SRC = path.join(__dirname, "..", "..", "src", "inkvault", "atlas", "daily.js");
const fail = msg => { console.error(msg); process.exit(1); };
const eq = (a, b, what) => { if (JSON.stringify(a) !== JSON.stringify(b)) fail(`${what}: got ${JSON.stringify(a)}, wanted ${JSON.stringify(b)}`); };

const RealDate = Date;
const RealSet = setTimeout;
const handlers = {}, store = {};
let throwing = false, clock = new RealDate(2026, 9, 15, 12, 0, 0), active = null, explored = [], timers = [];
function mk(tag) {
  return {tag, className: "", textContent: "", hidden: false, children: [], attrs: {}, listeners: {}, id: "", href: "", target: "", rel: "", type: "",
    append(...c) { this.children.push(...c); }, replaceChildren(...c) { this.children = c; },
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    addEventListener(ev, fn) { this.listeners[ev] = fn; }, click() { this.listeners.click(); }, focus() { active = this; }};
}
const box = mk("div");
global.window = {addEventListener: (ev, fn) => { handlers["window:" + ev] = fn; }, exploreDay: d => explored.push(d)};
global.document = {getElementById: id => id === "daily" ? box : null, createElement: mk, visibilityState: "visible",
  addEventListener: (ev, fn) => { handlers["document:" + ev] = fn; }};
global.localStorage = {getItem: k => { if (throwing) throw new Error("denied"); return store[k] ?? null; },
  setItem: (k, v) => { if (throwing) throw new Error("denied"); store[k] = v; }};
let quoteFile = null;
global.fetch = async () => { if (quoteFile instanceof Error) throw quoteFile; return {ok: true, json: async () => quoteFile}; };
global.setTimeout = (fn, ms) => { timers.push([fn, ms]); return timers.length; };
global.clearTimeout = () => {};
global.Date = class extends RealDate {
  constructor(...a) { super(...(a.length ? a : [clock.getTime()])); }
  static now() { return clock.getTime(); }
};

function load() {
  box.children = [];
  vm.runInThisContext(fs.readFileSync(SRC, "utf8"), {filename: SRC});
  return window.DailyLight;
}
const walk = (n, f) => { f(n); (n.children || []).forEach(c => walk(c, f)); };
const find = (n, pred) => { let r = null; walk(n, x => { if (!r && pred(x)) r = x; }); return r; };
const text = n => { let s = ""; walk(n, x => { if (!x.children.length) s += x.textContent; }); return s; };
const wait = () => new Promise(r => RealSet(r, 20));
const q = (o = {}) => Object.assign({id: "a", text: "A calm sentence.", author: "Someone", work: "A Book (1900)", source_url: "https://example.org/x", note: ""}, o);
const quoteCard = () => find(box, n => n.className.includes("daily-quote"));
const button = label => find(box, n => n.textContent === label);

(async () => {
  const D = load();
  // localDay uses the local parts: a Date-like whose local day differs from its UTC day.
  eq(D.localDay({getFullYear: () => 2026, getMonth: () => 0, getDate: () => 5, toISOString: () => "2026-01-06T02:00:00.000Z"}), "2026-01-05", "localDay");
  eq(D.localDay(new RealDate(2026, 11, 31, 23, 59)), "2026-12-31", "localDay real");
  // pickQuote: stable, next day moves by one, year rollover moves by one.
  const qs = ["a", "b", "c", "d", "e"];
  const i = d => qs.indexOf(D.pickQuote(qs, d));
  eq(D.pickQuote(qs, "2026-10-15"), D.pickQuote(qs, "2026-10-15"), "stable");
  eq((i("2026-10-16") - i("2026-10-15") + 5) % 5, 1, "next day moves by one");
  eq((i("2027-01-01") - i("2026-12-31") + 5) % 5, 1, "year rollover moves by one");
  eq(D.pickQuote(["only"], "2026-10-15"), "only", "single");
  eq(D.pickQuote(qs, "1970-01-01"), "a", "epoch is index 0");
  // onThisDay
  eq(D.onThisDay(["2024-10-15", "2025-10-15", "2026-10-15", "2027-10-15", "2025-10-16"], "2026-10-15"), "2025-10-15", "most recent earlier year");
  eq(D.onThisDay(["2026-10-15", "2027-10-15"], "2026-10-15"), null, "ignores today and future");
  eq(D.onThisDay(["2025-02-28", "2024-02-29"], "2028-02-29"), "2024-02-29", "Feb 29 only earlier Feb 29");
  eq(D.onThisDay(["2025-02-28", "2025-03-01"], "2028-02-29"), null, "Feb 29 without earlier Feb 29");
  eq(D.onThisDay([], "2026-10-15"), null, "none");
  eq(D.msToMidnight(new RealDate(2026, 9, 15, 23, 59, 0)), 60000, "msToMidnight");
  eq(D.httpsUrl("javascript:alert(1)"), null, "js url");
  eq(D.httpsUrl("http://example.org"), null, "http url");
  eq(D.httpsUrl("not a url"), null, "garbage url");

  // Hostile quote text stays text; a javascript: source gets no link.
  quoteFile = {version: 1, quotes: [q({text: "<img src=x onerror=alert(1)>", source_url: "javascript:alert(1)"})]};
  load(); await wait();
  window.DailyLight.show(["2025-10-15", "2026-10-14"]);
  const card = quoteCard();
  if (!card) fail("no quote card");
  if (!text(card).includes("<img src=x onerror=alert(1)>")) fail("hostile text not shown as text");
  if (find(card, n => n.tag === "img")) fail("an element was made from quote text");
  if (find(card, n => n.tag === "a")) fail("javascript: source got a link");
  if (!text(card).includes("A little light")) fail("heading");
  const why = find(card, n => n.id === "daily-why"), whyBtn = button("Why this quote?");
  if (!why.hidden || whyBtn.getAttribute("aria-expanded") !== "false" || whyBtn.getAttribute("aria-controls") !== "daily-why") fail("disclosure start state");
  whyBtn.click();
  if (why.hidden || whyBtn.getAttribute("aria-expanded") !== "true") fail("disclosure open");
  eq(why.textContent, "Selected from a curated collection for your local calendar day. Your history was not used.", "why sentence");
  whyBtn.click();
  if (!why.hidden) fail("disclosure close");

  // On this day
  const otd = find(box, n => n.className.includes("daily-otd"));
  if (!otd || !text(otd).includes("On this day")) fail("otd card");
  if (!/^Revisit .*2025\.$/.test(find(otd, n => n.textContent.startsWith("Revisit")).textContent)) fail("otd text");
  find(otd, n => n.textContent === "Explore this day").click();
  eq(explored, ["2025-10-15"], "exploreDay called");
  window.DailyLight.clear();
  if (find(box, n => n.className.includes("daily-otd"))) fail("otd survived clear");
  if (!quoteCard()) fail("quote vanished on clear");
  window.DailyLight.show(["2026-10-14"]);
  if (find(box, n => n.className.includes("daily-otd"))) fail("otd with no eligible day");

  // Hide / show, focus, storage.
  button("Hide daily quote").click();
  if (quoteCard()) fail("quote still there after hide");
  const show = button("Show daily quote");
  if (!show || active !== show) fail("focus not on Show button");
  eq(store["inkvault-quote-hidden"], "1", "hidden stored");
  show.click();
  const hideAgain = button("Hide daily quote");
  if (!hideAgain || active !== hideAgain) fail("focus not on Hide button");
  eq(store["inkvault-quote-hidden"], "0", "shown stored");
  store["inkvault-quote-hidden"] = "1"; load(); await wait();
  if (!button("Show daily quote")) fail("stored preference ignored");
  store["inkvault-quote-hidden"] = "0";

  // Throwing storage: still works, in memory.
  throwing = true;
  load(); await wait();
  button("Hide daily quote").click();
  if (!button("Show daily quote")) fail("hide failed with throwing storage");
  throwing = false;

  // An https source gets a safe link.
  quoteFile = {version: 1, quotes: [q()]};
  load(); await wait();
  const a = find(box, n => n.tag === "a");
  if (!a || a.href !== "https://example.org/x" || a.target !== "_blank" || a.rel !== "noopener noreferrer" || a.getAttribute("aria-label") !== "Source (opens external site)") fail("source link");

  // Invalid / empty / failed collections give no card.
  for (const bad of [{version: 1, quotes: []}, {version: 2, quotes: [q()]}, {version: 1, quotes: [q({text: 5})]}, {version: 1, quotes: [q({author: ""})]}, {version: 1}, null, new Error("offline")]) {
    quoteFile = bad; load(); await wait();
    window.DailyLight.show(["2025-10-15"]);
    if (quoteCard() || button("Show daily quote")) fail("card for bad collection " + (bad instanceof Error ? "error" : JSON.stringify(bad)));
  }

  // Day change: focus re-renders when the local day has moved on, and the timer is re-armed to the next midnight + 1s.
  quoteFile = {version: 1, quotes: [q({text: "one"}), q({id: "b", text: "two"})]};
  clock = new RealDate(2026, 9, 15, 23, 59, 0); timers = [];
  load(); await wait();
  const before = text(quoteCard());
  clock = new RealDate(2026, 9, 16, 0, 1, 0);
  handlers["window:focus"]();
  if (before === text(quoteCard())) fail("did not re-render on a new day");
  eq(timers[timers.length - 1][1], 23 * 3600 * 1000 + 59 * 60 * 1000 + 1000, "re-armed to next midnight + 1s");
})();
