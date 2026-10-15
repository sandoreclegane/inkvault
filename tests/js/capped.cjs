// The API says a result window is capped: More stays hidden and the status tells the reader to narrow the search.
const load = require("./harness.cjs");
const fail = msg => { console.error(msg); process.exit(1); };
let capped = true;
const page = load({answer: p => p === "/api/atlas"
  ? {status: "ok", generation: "g1", atlas_revision: "r1", data: {days: {}}, freshness: {}, sources: {}}
  : p === "/api/search"
    ? {status: "ok", generation: "g1", more: false, capped, keyword_only: false,
       hits: [{kind: "events", id: "e1", time: "2026-02-27 15:00", title: "retry.ts", source_label: "Pieces capture",
               excerpt: "exponential backoff helper", match: {type: "date"}}]}
    : {status: "ok"}});
(async () => {
  await page.sleep(50);
  window.exploreDay("2026-02-27");
  await page.sleep(30);
  if (!page.els.more.hidden) fail("More shown at the cap");
  const t = page.els["search-status"].textContent;
  if (!t.includes("Showing the first 1 results. Narrow the dates or pick a source to see more.")) fail("note: " + t);
  capped = false;
  window.exploreDay("2026-02-28");
  await page.sleep(30);
  if (/Narrow the dates/.test(page.els["search-status"].textContent)) fail("note shown without the cap");
})();
