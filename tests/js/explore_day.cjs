// exploreDay(day): clears the query, sets All sources and From = To = day, shows the day in the date inputs and runs the
// normal search path (an empty query is sent with both days); date hits have no highlight and say "on this day".
const load = require("./harness.cjs");
const fail = msg => { console.error(msg); process.exit(1); };
let empty = false;
const page = load({answer: p => p === "/api/atlas"
  ? {status: "ok", generation: "g1", atlas_revision: "r1", data: {days: {}}, freshness: {}, sources: {}}
  : p === "/api/search"
    ? {status: "ok", generation: "g1", more: false, keyword_only: false,
       hits: empty ? [] : [{kind: "events", id: "e1", time: "2026-02-27 15:00", title: "retry.ts", source_label: "Pieces capture",
               excerpt: "exponential backoff helper", match: {type: "date"}}]}
    : {status: "ok"}});
(async () => {
  await page.sleep(50);
  page.els.q.value = "old words";
  window.exploreDay("2026-02-27");
  await page.sleep(50);
  const call = page.calls.find(c => c[0] === "/api/search");
  if (!call) fail("exploreDay sent no search");
  const b = call[1];
  if (b.query !== "" || b.from !== "2026-02-27" || b.to !== "2026-02-27" || b.source !== "all" || b.offset !== 0)
    fail("wrong request: " + JSON.stringify(b));
  if (page.els.q.value !== "" || page.els["s-from"].value !== "2026-02-27" || page.els["s-to"].value !== "2026-02-27")
    fail("inputs not set");
  const saved = JSON.parse(page.store["inkvault-search"]);
  if (saved.view !== "search" || saved.query !== "" || saved.from !== "2026-02-27") fail("state not saved: " + JSON.stringify(saved));
  const hit = page.els["results"].children[0];
  const ex = hit.children[0].children[2];
  const text = [ex.children[0], ...ex.children.slice(1).map(c => c.textContent)].join("");
  if (!/on this day/.test(text) || ex.children.some(c => c.id === "mark")) fail("hit not labelled: " + text);
  if (/by meaning/.test(text)) fail("labelled by meaning");
  if (!/^1 result/.test(page.els["search-status"].textContent)) fail("status: " + page.els["search-status"].textContent);
  empty = true;
  window.exploreDay("2026-02-28");
  await page.sleep(50);
  if (page.els["search-status"].textContent !== "Nothing indexed on this day.") fail("empty: " + page.els["search-status"].textContent);
})();
