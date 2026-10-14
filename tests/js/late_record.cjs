const load = require("./harness.cjs");
const page = load({
  saved: {view: "search", query: "first", source: "all", from: "", to: "", selected: {kind: "chats", id: "slow"},
          scroll: 0, generation: "g1"},
  answer: async (p, b) => {
    if (p === "/api/atlas") return {status: "ok", generation: "g1", atlas_revision: "r1", data: {days: {}}, freshness: {}, sources: {}};
    if (p === "/api/search") return {status: "ok", generation: "g1", more: false, keyword_only: false,
      hits: [{kind: "summaries", id: b.query, time: "", title: b.query, source_label: "x", excerpt: "e", match: {type: "meaning"}}]};
    if (p === "/api/record") { await new Promise(r => setTimeout(r, 300)); return {status: "ok", generation: "g1", title: "STALE", items: [], code: false}; }
    return {status: "ok"};
  },
});
(async () => {
  await page.sleep(50);                                       // restore has started the slow record open
  if (!page.calls.some(c => c[0] === "/api/record")) { console.error("the slow record open never started"); process.exit(1); }
  page.els.q.value = "second";
  page.handlers["search-form:submit"]({preventDefault() {}}); // a new search replaces the results
  await page.sleep(500);                                      // the slow answer has arrived
  const reader = page.els.reader.children.map(c => c.textContent).join(" ");
  if (reader.includes("STALE")) { console.error("a record from the old results appeared"); process.exit(1); }
})();
