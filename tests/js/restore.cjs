const load = require("./harness.cjs");
const page = load({
  saved: {view: "search", query: "beacon", source: "all", from: "", to: "", selected: {kind: "chats", id: "m9"},
          scroll: 40, generation: "g1"},
  answer: p => p === "/api/atlas" ? {status: "ok", generation: "g1", atlas_revision: "r1", data: {days: {}}, freshness: {}, sources: {}}
    : p === "/api/search" ? {status: "ok", generation: "g1", more: false, keyword_only: false,
        hits: [{kind: "summaries", id: "s1", time: "", title: "t", source_label: "x", excerpt: "beacon",
                match: {type: "keyword", spans: [[0, 6]]}}]}
    : p === "/api/record" ? {status: "ok", generation: "g1", title: "r", items: [], code: false} : {status: "ok"},
});
page.sleep(200).then(() => {
  const search = page.calls.find(c => c[0] === "/api/search");
  const record = page.calls.find(c => c[0] === "/api/record");
  const saved = JSON.parse(page.store["inkvault-search"]);
  const ok = search && search[1].query === "beacon" && record && record[1].id === "m9" && record[1].generation === "g1"
    && saved.query === "beacon" && saved.selected.id === "m9" && saved.view === "search";
  if (!ok) { console.error("restore failed", JSON.stringify({search, record, saved})); process.exit(1); }
});
