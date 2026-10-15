// Clearing From or To while a date-only browse is showing: the list, reader and More go, the page says to choose both
// dates, nothing unbounded is sent, and a browse answer still on its way never renders.
const load = require("./harness.cjs");
const fail = msg => { console.error(msg); process.exit(1); };
let delay = 0;
const row = {kind: "events", id: "e1", time: "2026-02-27 15:00", title: "retry.ts", source_label: "Pieces capture",
             excerpt: "exponential backoff helper", match: {type: "date"}};
const page = load({answer: async p => {
  if (p === "/api/atlas") return {status: "ok", generation: "g1", atlas_revision: "r1", data: {days: {}}, freshness: {}, sources: {}};
  if (p !== "/api/search") return {status: "ok"};
  if (delay) await new Promise(r => setTimeout(r, delay));
  return {status: "ok", generation: "g1", more: true, keyword_only: false, hits: [row]};
}});
const MSG = "Choose both dates to list a day or range.";
const searches = () => page.calls.filter(c => c[0] === "/api/search");
async function explore() {
  delay = 0;
  window.exploreDay("2026-02-27");
  await page.sleep(30);
  if (page.els.results.children.length !== 1 || page.els.more.hidden) fail("browse did not show");
}
function cleared(what) {
  if (page.els.results.children.length || page.els.reader.children.length) fail(what + ": results/reader remain");
  if (!page.els.more.hidden) fail(what + ": More remains");
  if (page.els["search-status"].textContent !== MSG) fail(what + ": status " + page.els["search-status"].textContent);
  const saved = JSON.parse(page.store["inkvault-search"]);
  if (saved.selected) fail(what + ": selection kept");
}
async function change(id, value) {
  page.els[id].value = value;
  page.handlers[id + ":change"]();
  await page.sleep(10);
}
(async () => {
  await page.sleep(50);
  for (const id of ["s-from", "s-to"]) {
    await explore();
    const before = searches().length;
    await change(id, "");
    if (searches().length !== before) fail(id + ": a request was sent");
    cleared("clear " + id);
    // More (hidden now) and a later change with a bound still missing do nothing either
    page.handlers["more:click"]();
    await page.sleep(10);
    if (searches().length !== before) fail(id + ": More sent a request");
    // choosing the bound again browses again
    await change(id, "2026-02-27");
    if (searches().length !== before + 1 || page.els.results.children.length !== 1) fail(id + ": did not browse again");
  }
  // A browse answer still pending when a bound is cleared must not render.
  await explore();
  delay = 80;
  page.handlers["s-to:change"]();            // same values: runs a fresh browse that will answer late
  await page.sleep(10);
  await change("s-from", "");
  await page.sleep(150);
  cleared("late answer");
})();
