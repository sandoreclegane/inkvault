"use strict";
// Daily Light: a quote for the local calendar day and an "On this day" pointer into the vault. Both are chosen from
// things that carry no history (a fixed collection, the day keys the Atlas already has). Text goes in with
// textContent only; nothing from the quote file or the vault is ever treated as markup.
(() => {
  const HIDE_KEY = "inkvault-quote-hidden";
  const WHY = "Selected from a curated collection for your local calendar day. Your history was not used.";
  const pad = n => String(n).padStart(2, "0");
  const DAY = /^(\d{4})-(\d{2})-(\d{2})$/;

  // The local calendar day, from the local parts (never sliced from a UTC string).
  const localDay = d => `${String(d.getFullYear()).padStart(4, "0")}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const dayNumber = day => { const m = DAY.exec(day); return Date.UTC(+m[1], +m[2] - 1, +m[3]) / 86400000; };
  const pickQuote = (quotes, day) => quotes[((dayNumber(day) % quotes.length) + quotes.length) % quotes.length];
  // The most recent earlier year with activity on this month and day (Feb 29 only matches an earlier Feb 29).
  function onThisDay(dayKeys, today) {
    const m = DAY.exec(today);
    if (!m) return null;
    let best = null;
    for (const k of dayKeys || []) {
      const t = DAY.exec(k);
      if (t && t[2] === m[2] && t[3] === m[3] && +t[1] < +m[1] && (best === null || +t[1] > +best.slice(0, 4))) best = k;
    }
    return best;
  }
  const msToMidnight = now => new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1) - now;
  const longDate = day => { const m = DAY.exec(day); return new Date(+m[1], +m[2] - 1, +m[3]).toLocaleDateString(undefined, {dateStyle: "long"}); };

  function validQuotes(data) {
    if (!data || data.version !== 1 || !Array.isArray(data.quotes) || !data.quotes.length) return null;
    const str = v => typeof v === "string" && v.trim() !== "";
    return data.quotes.every(q => q && str(q.id) && str(q.text) && str(q.author) && str(q.work) && typeof q.source_url === "string")
      ? data.quotes : null;
  }
  function httpsUrl(s) {
    try { const u = new URL(s); return u.protocol === "https:" ? u.href : null; } catch (e) { return null; }
  }

  const root = typeof window !== "undefined" ? window : globalThis;
  root.DailyLight = {localDay, pickQuote, onThisDay, msToMidnight, validQuotes, httpsUrl};
  if (typeof document === "undefined" || !document.getElementById("daily")) return;

  let quotes = null, dayKeys = null, shown = null, timer = null, hideBtn = null, showBtn = null;
  let hidden = false;
  try { hidden = localStorage.getItem(HIDE_KEY) === "1"; } catch (e) {}
  const box = document.getElementById("daily");
  const qSlot = document.createElement("div"), oSlot = document.createElement("div");
  box.append(qSlot, oSlot);

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function button(text, onClick, cls) {
    const b = el("button", cls || "daily-btn", text);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }
  function setHidden(v) {
    hidden = v;
    try { localStorage.setItem(HIDE_KEY, v ? "1" : "0"); } catch (e) {}
  }

  function quoteCard(q, day) {
    const card = el("section", "daily-card daily-quote");
    card.setAttribute("aria-labelledby", "daily-quote-h");
    const h = el("h2", "daily-h", "A little light");
    h.id = "daily-quote-h";
    const fig = el("figure", "daily-fig");
    fig.append(el("blockquote", "daily-text", q.text));
    const cap = el("figcaption", "daily-by", q.author + ", ");
    cap.append(el("cite", "", q.work));
    fig.append(cap);
    const why = el("p", "daily-why", WHY);
    why.id = "daily-why";
    why.hidden = true;
    const whyBtn = button("Why this quote?", () => {
      const open = whyBtn.getAttribute("aria-expanded") !== "true";
      whyBtn.setAttribute("aria-expanded", open ? "true" : "false");
      why.hidden = !open;
    });
    whyBtn.setAttribute("aria-expanded", "false");
    whyBtn.setAttribute("aria-controls", "daily-why");
    hideBtn = button("Hide daily quote", () => { setHidden(true); renderAll(); if (showBtn) showBtn.focus(); });
    const actions = el("div", "daily-actions");
    actions.append(whyBtn);
    const url = httpsUrl(q.source_url);
    if (url) {
      const a = el("a", "daily-link", "Source");
      a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer";
      a.setAttribute("aria-label", "Source (opens external site)");
      actions.append(a);
    }
    actions.append(hideBtn);
    card.append(h, el("p", "daily-date", longDate(day)), fig, actions, why);
    return card;
  }

  function renderQuote() {
    qSlot.replaceChildren();
    hideBtn = showBtn = null;
    if (!quotes) return;
    if (hidden) {
      showBtn = button("Show daily quote", () => { setHidden(false); renderAll(); if (hideBtn) hideBtn.focus(); }, "daily-btn daily-show");
      qSlot.append(showBtn);
      return;
    }
    qSlot.append(quoteCard(pickQuote(quotes, shown), shown));
  }

  function renderOnThisDay() {
    oSlot.replaceChildren();
    const day = dayKeys && onThisDay(dayKeys, shown);
    if (!day) return;
    const card = el("section", "daily-card daily-otd");
    card.setAttribute("aria-labelledby", "daily-otd-h");
    const h = el("h2", "daily-h", "On this day");
    h.id = "daily-otd-h";
    card.append(h, el("p", "daily-text", `Revisit ${longDate(day)}.`),
      button("Explore this day", () => { if (typeof window.exploreDay === "function") window.exploreDay(day); }));
    oSlot.append(card);
  }

  function renderAll() {
    shown = localDay(new Date());
    renderQuote();
    renderOnThisDay();
    const both = qSlot.children.length && oSlot.children.length && !hidden;
    box.className = both ? "daily" : "daily daily-single";
    box.hidden = !qSlot.children.length && !oSlot.children.length;
  }

  function check() {
    if (shown !== null && localDay(new Date()) !== shown) renderAll();
    clearTimeout(timer);
    timer = setTimeout(check, msToMidnight(new Date()) + 1000);
  }

  root.DailyLight.show = days => { dayKeys = Array.isArray(days) ? days : null; renderAll(); };
  root.DailyLight.clear = () => { dayKeys = null; renderAll(); };
  root.DailyLight.check = check;
  window.addEventListener("focus", check);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") check(); });
  box.hidden = true;
  check();

  (async () => {
    try {
      const r = await fetch("/assets/quotes.json", {cache: "no-store"});
      if (!r.ok) return;
      quotes = validQuotes(await r.json());
    } catch (e) { quotes = null; }
    if (quotes) renderAll();
  })();
})();
