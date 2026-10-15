const load = require("./harness.cjs");
let atlasCalls = 0;
const page = load({answer: p => {
  if (p === "/api/atlas") { atlasCalls++; return {status: "ok", generation: atlasCalls === 1 ? "g1" : "g2", atlas_revision: "r", data: {days: {}}, freshness: {}, sources: {}}; }
  if (p === "/api/search") return {status: "ok", generation: "g1", more: false, keyword_only: false,
    hits: [{kind: "chats", id: "m1", time: "", title: "t", source_label: "x", excerpt: "e", match: {type: "meaning"}}]};
  if (p === "/api/record") return {status: "ok", generation: "g2", title: "NEWER STATE", items: [], code: false};
  return {status: "ok"};
}});
(async () => {
  await page.sleep(50);
  page.els.q.value = "beacon";
  page.handlers["search-form:submit"]({preventDefault() {}});
  await page.sleep(50);
  page.handlers["button:click"]();   // the one hit's button
  await page.sleep(100);
  const reader = page.els.reader.children.map(c => c.textContent).join(" ");
  const savedQuery = JSON.parse(page.store["inkvault-search"] || "{}").query;
  if (reader.includes("NEWER STATE") || page.els.results.children.length || savedQuery || atlasCalls < 2) {
    console.error("a record from a newer vault state was shown beside older results", reader, atlasCalls);
    process.exit(1);
  }
})();
