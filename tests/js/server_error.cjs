// A bare HTTP 500 means the helper is there but couldn't answer: the page says so and offers Retry. Only a network
// failure or a 403 means the Atlas is closed (Codex build review #2).
const load = require("./harness.cjs");
let broken = true;
const page = load({answer: (p, body) => {
  if (broken && p !== "/api/ping") return {httpStatus: 500, body: {status: "closed"}};  // the status code decides, whatever the body says
  if (p === "/api/atlas") return {status: "ok", generation: "g1", atlas_revision: "r1", data: {days: {}}, freshness: {}, sources: {}};
  if (p === "/api/ping") return {status: "ok", generation: "g1", rebuilding: false, atlas_revision: "r1"};
  if (p === "/api/search") return {status: "ok", generation: "g1", more: false, keyword_only: false, hits: []};
  return {status: "ok"};
}});
const fail = msg => { console.error(msg); process.exit(1); };
page.els.closed.hidden = true;  // as in the real page; closed() is the only thing that shows it
(async () => {
  await page.sleep(50);
  if (page.els.notice.hidden || !/can't read the vault/i.test(page.els["notice-text"].textContent)) fail("a 500 for the Atlas didn't show the unavailable notice");
  if (page.els["notice-retry"].hidden || typeof page.els["notice-retry"].onclick !== "function") fail("the notice has no Retry");
  if (!page.els.closed.hidden) fail("a 500 showed the closed panel");

  page.els.q.value = "something";
  page.handlers["search-form:submit"]({preventDefault() {}});
  await page.sleep(50);
  if (!/can't read the vault/i.test(page.els["notice-text"].textContent) || page.els["notice-retry"].hidden) fail("a 500 for a search didn't show unavailable with Retry");
  if (!page.els.closed.hidden) fail("a 500 for a search showed the closed panel");

  broken = false;
  page.els["notice-retry"].onclick();
  await page.sleep(50);
  if (!page.els.closed.hidden) fail("Retry ended up closed");
})();
