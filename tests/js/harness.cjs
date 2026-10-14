// A stand-in page for search.js: just enough DOM, storage and fetch to drive it without a browser. Fictional data.
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const SEARCH = path.join(__dirname, "..", "..", "src", "inkvault", "atlas", "search.js");

module.exports = function load({saved = null, answer}) {
  const handlers = {}, calls = [];
  const mk = id => ({id, value: "", textContent: "", hidden: false, scrollTop: 0, dataset: {}, children: [], disabled: false,
    replaceChildren(...c) { this.children = c; }, append(...c) { this.children.push(...c); },
    setAttribute() {}, addEventListener(ev, fn) { handlers[id + ":" + ev] = fn; }, querySelector() { return null; },
    focus() {}, scrollIntoView() {}, remove() {}, replaceWith() {}});
  // Elements exist from the first touch, by the page or by a test.
  const els = new Proxy({}, {get: (t, id) => (typeof id === "string" ? (t[id] || (t[id] = mk(id))) : t[id])});
  const store = saved ? {"inkvault-search": JSON.stringify(saved)} : {};
  const atlas = {shown: 0, cleared: 0};
  Object.assign(global, {
    document: {getElementById: id => els[id] || (els[id] = mk(id)), querySelectorAll: () => [], createElement: t => mk(t),
               visibilityState: "visible", addEventListener: (ev, fn) => { handlers["document:" + ev] = fn; }},
    window: {addEventListener: (ev, fn) => { handlers["window:" + ev] = fn; }},
    sessionStorage: {getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; }, removeItem: k => { delete store[k]; }},
    Atlas: {show() { atlas.shown++; }, clear() { atlas.cleared++; }},
    setInterval: () => 0, clearInterval: () => {},
    fetch: async (p, o) => {
      const body = o.body ? JSON.parse(o.body) : null;
      calls.push([p, body]);
      const a = await answer(p, body);
      return {status: 200, json: async () => a};
    },
  });
  vm.runInThisContext(fs.readFileSync(SEARCH, "utf8"), {filename: SEARCH});
  return {els, handlers, calls, store, atlas, sleep: ms => new Promise(r => setTimeout(r, ms))};
};
