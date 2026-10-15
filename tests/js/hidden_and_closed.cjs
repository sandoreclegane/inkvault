// A hidden tab still sends the keep-alive; when the helper goes away the page empties everything it showed.
const load = require("./harness.cjs");
let closedNow = false;
const page = load({answer: (p, body) => {
  if (closedNow) throw new Error("connection refused");
  if (p === "/api/atlas") return {status: "ok", generation: "g1", atlas_revision: "r1", data: {days: {}}, freshness: {}, sources: {}};
  if (p === "/api/ping") return {status: "ok", generation: "g1", rebuilding: false, atlas_revision: "r1"};
  if (p === "/api/search") return {status: "ok", generation: "g1", more: false, keyword_only: false, hits: []};
  return {status: "ok"};
}});
const fail = msg => { console.error(msg); process.exit(1); };
(async () => {
  await page.sleep(50);
  document.visibilityState = "hidden";
  const before = page.calls.filter(c => c[0] === "/api/ping").length;
  page.handlers["window:focus"]();
  await page.sleep(50);
  if (page.calls.filter(c => c[0] === "/api/ping").length !== before + 1) fail("a hidden tab didn't ping");

  page.els.q.value = "something";
  page.handlers["search-form:submit"]({preventDefault() {}});
  await page.sleep(50);
  page.els.results.children = ["x"]; page.els.reader.children = ["y"]; page.els.freshness.textContent = "built";
  page.store["inkvault-search"] = "{}";
  const cleared = page.atlas.cleared;
  closedNow = true;
  page.handlers["window:focus"]();
  await page.sleep(100);
  if (page.els.results.children.length || page.els.reader.children.length || page.els.freshness.textContent)
    fail("the closed page still shows results, the reader or freshness");
  if (page.atlas.cleared !== cleared + 1) fail("the charts weren't cleared");
  if ("inkvault-search" in page.store) fail("the saved state wasn't removed");
})();
