const load = require("./harness.cjs");
let atlasCalls = 0;
const page = load({answer: p => {
  if (p === "/api/atlas") { atlasCalls++; return {status: "ok", generation: "g1", atlas_revision: "r" + atlasCalls, data: {days: {}}, freshness: {}, sources: {}}; }
  if (p === "/api/ping") return {status: "ok", generation: "g1", rebuilding: false, atlas_revision: "r9"};
  if (p === "/api/search") return {status: "ok", generation: "g1", more: false, keyword_only: false, hits: []};
  return {status: "ok"};
}});
(async () => {
  await page.sleep(50);
  page.els.q.value = "keep me";
  page.handlers["search-form:submit"]({preventDefault() {}});
  await page.sleep(50);
  const shown = page.atlas.shown;
  page.handlers["window:focus"]();   // focus pings; the ping reports a different atlas revision
  await page.sleep(100);
  if (!(atlasCalls === 2 && page.atlas.shown === shown + 1 && page.els.q.value === "keep me")) {
    console.error("charts didn't reload, or the search was lost", atlasCalls, page.atlas.shown, page.els.q.value);
    process.exit(1);
  }
})();
