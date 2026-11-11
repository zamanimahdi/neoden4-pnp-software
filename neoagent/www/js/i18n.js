// Language (English / Persian) and theme (light / dark), switched from the top bar (فا/EN, ◐).
// Both are per screen (localStorage): a browser and the machine's monitor can differ.
// Persian: the pages stay written in English; this translates what they put on the screen
// (text nodes, title / placeholder attributes) from the dictionary in fa.js, as it appears,
// and turns the page right-to-left. Texts with ${...} parts are patterns ("{0} placed").
// A text not in the dictionary stays English (tools/i18n_extract.py lists the missing ones).
const I18N = (() => {
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode: this page only */ } },
  };
  const root = document.documentElement;
  // ?lang=fa / ?theme=dark in the address (e.g. the kiosk's url): stored as this screen's choice,
  // then taken out of the address so the buttons can change it again
  const q = new URLSearchParams(location.search);
  if (q.has("lang") || q.has("theme")) {
    if (q.has("lang")) store.set("lang", q.get("lang") === "fa" ? "fa" : "en");
    if (q.has("theme")) store.set("theme", q.get("theme") === "dark" ? "dark" : "light");
    q.delete("lang"); q.delete("theme");
    history.replaceState(null, "", location.pathname + (q.toString() ? "?" + q : "") + location.hash);
  }

  // ---- theme: data-theme on <html>; style.css has the colours for both ----
  const realMM = window.matchMedia.bind(window);
  const sysDark = realMM("(prefers-color-scheme: dark)");
  let theme = store.get("theme");                  // "light", "dark" or null: as the system
  // the machine's own screen (neokiosk tells window.KIOSK.theme): kiosk.ini decides, so a reload or a
  // restart never loses it; elsewhere each browser keeps its own choice
  const kiosk = window.KIOSK && typeof window.KIOSK === "object";
  if (kiosk) theme = window.KIOSK.theme === "dark" || window.KIOSK.theme === "light" ? window.KIOSK.theme : null;
  const dark = () => (theme ? theme === "dark" : sysDark.matches);
  function applyTheme() { root.dataset.theme = dark() ? "dark" : "light"; }
  // the canvas code (board map, manual map) asks matchMedia for dark: answer with the theme
  window.matchMedia = (q) => (/prefers-color-scheme:\s*dark/.test(q)
    ? { matches: dark(), media: q, onchange: null, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }
    : realMM(q));
  if (sysDark.addEventListener) sysDark.addEventListener("change", applyTheme);
  applyTheme();

  // ---- language ----
  const lang = store.get("lang") === "fa" ? "fa" : "en";
  const exact = new Map(), pats = [];
  for (const [key, val] of Object.entries(window.FA || {})) {
    const en = key.trim(), fa = val.trim();           // the screen's text is matched trimmed
    if (!/\{\d+\}/.test(en)) { exact.set(en, fa); continue; }
    const order = [];
    const src = en.split(/(\{\d+\})/).map((p) => {
      const m = /^\{(\d+)\}$/.exec(p);
      if (m) { order.push(+m[1]); return "(.+?)"; }
      return p.replace(/[.*+?^$()|[\]\\{}]/g, "\\$&");
    }).join("");
    const lits = en.split(/\{\d+\}/), lead = lits[0];
    // starting and ending in words, it can also start a longer text built from pieces
    // ("A (" + n + " to place" + ...); one starting with a {0} would match the start of anything
    const head = /\{\d+\}$/.test(en) || lead.trim().length < 3 ? null : new RegExp("^" + src, "s");
    pats.push({ re: new RegExp("^" + src + "$", "s"), head, fa, order, lead, lits: lits.filter((l) => l.trim()) });
  }
  // every fixed part of the pattern in the text, in order: before any regex runs
  const fits = (p, t) => { let i = 0; for (const l of p.lits) { i = t.indexOf(l, i); if (i < 0) return false; i += l.length; } return true; };
  let budget = 0;                                       // steps of one lookup, against slow texts
  const heads = [...exact.keys()].filter((k) => k.length > 3).sort((a, b) => b.length - a.length);
  const cache = new Map();
  const fill = (p, m) => p.fa.replace(/\{(\d+)\}/g, (_, i) => {
    const g = m[p.order.indexOf(+i) + 1];
    return g === undefined ? "" : (exact.get(g.trim()) ?? g);
  });
  // the rest of a text after a translated start: translated too, or kept when it has no words
  function rest(r, depth) {
    if (!/[A-Za-z]{2}/.test(r)) return r;
    const t = r.trim();
    const f = look(t, depth + 1);
    return f === null ? null : r.slice(0, r.indexOf(t)) + f + r.slice(r.indexOf(t) + t.length);
  }
  function look(t, depth) {
    let out = exact.get(t);
    if (out !== undefined) return out;
    if (t.length > 500 || --budget < 0) return null;
    for (const p of pats) {
      if ((p.lead && !t.startsWith(p.lead)) || !fits(p, t)) continue;
      const m = p.re.exec(t);
      if (m) return fill(p, m);
    }
    if (depth >= 3) return null;
    // a log line: "11:55:43 job: R7: no vision result (nozzle 3)": time and tag kept
    const lg = /^((?:\d{1,2}:\d\d(?::\d\d)?\s+)?(?:[A-Za-z][A-Za-z ]{0,14}: )?)(\S[\s\S]*)$/.exec(t);
    if (lg && lg[1]) {
      const r = rest(lg[2], depth);
      if (r !== null) return lg[1] + r;
    }
    for (const p of pats) {                              // a pattern, then more
      if (!p.head || !t.startsWith(p.lead)) continue;
      const m = p.head.exec(t);
      if (!m) continue;
      const r = rest(t.slice(m[0].length), depth);
      if (r !== null) return fill(p, m) + r;
    }
    for (const k of heads) {                             // a plain text, then more
      if (!t.startsWith(k) || /[A-Za-z]/.test(t[k.length] || "")) continue;
      const r = rest(t.slice(k.length), depth);
      if (r !== null) return exact.get(k) + r;
    }
    return null;
  }
  function tr(t) {
    if (cache.has(t)) return cache.get(t);
    budget = 60;
    const out = look(t, 0);
    if (cache.size > 4000) cache.clear();
    cache.set(t, out);
    return out;
  }
  // text with its spaces around kept
  function trText(s) {
    const t = s.trim();
    if (!t || !/[A-Za-z]/.test(t)) return null;
    const f = tr(t);
    if (f === null || f === t) return null;          // unchanged: writing it would be a mutation again
    return s.slice(0, s.indexOf(t)) + f + s.slice(s.indexOf(t) + t.length);
  }
  const SKIP = new Set(["SCRIPT", "STYLE", "TEXTAREA", "CODE", "INPUT"]);
  function skipped(el) {
    for (let e = el; e && e !== document.body; e = e.parentElement) {
      if (SKIP.has(e.tagName) || e.isContentEditable || (e.dataset && "notr" in e.dataset)) return true;
    }
    return false;
  }
  function textNode(n) {
    const p = n.parentElement;
    if (!p || skipped(p)) return;
    if (p.closest("pre, .log")) {                 // logs and reports: line by line
      if (!n.data.includes("\n")) { const f = trText(n.data); if (f !== null) n.data = f; return; }
      let any = false;
      const out = n.data.split("\n").map((l) => { const f = trText(l); if (f === null) return l; any = true; return f; });
      if (any) n.data = out.join("\n");
      return;
    }
    const f = trText(n.data);
    if (f === null) return;
    // an <option> without value= takes its text as value: keep the English value
    if (p.tagName === "OPTION" && !p.hasAttribute("value")) p.setAttribute("value", p.value);
    n.data = f;
  }
  const ATTRS = ["title", "placeholder", "aria-label"];
  function attrs(el) {
    for (const a of ATTRS) {
      const v = el.getAttribute(a);
      if (v) { const f = trText(v); if (f !== null) el.setAttribute(a, f); }
    }
  }
  function walk(node) {
    if (node.nodeType === 3) { textNode(node); return; }
    if (node.nodeType !== 1 || SKIP.has(node.tagName)) return;
    attrs(node);
    const w = document.createTreeWalker(node, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      if (n.nodeType === 3) textNode(n); else attrs(n);
    }
  }

  function startFa() {
    root.lang = "fa";
    root.dir = "rtl";
    walk(document.body);
    if (document.title) { const f = trText(document.title); if (f) document.title = f; }
    new MutationObserver((list) => {
      for (const m of list) {
        if (m.type === "characterData") textNode(m.target);
        else if (m.type === "attributes") attrs(m.target);
        else m.addedNodes.forEach(walk);
      }
    }).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });
    // dialogs from the pages
    for (const k of ["alert", "confirm", "prompt"]) {
      const f = window[k].bind(window);
      window[k] = (msg, ...rest) => f(msg == null ? msg : (trText(String(msg)) ?? msg), ...rest);
    }
  }

  // ---- the two buttons in the top bar ----
  function buttons() {
    const lb = document.getElementById("langbtn"), tb = document.getElementById("themebtn");
    if (lb) {
      lb.textContent = lang === "fa" ? "EN" : "فا";
      lb.title = lang === "fa" ? "English" : "فارسی";
      lb.dataset.notr = "";
      lb.onclick = () => { store.set("lang", lang === "fa" ? "en" : "fa"); location.reload(); };
    }
    if (tb) {
      tb.dataset.notr = "";
      const show = () => { tb.textContent = dark() ? "☀" : "☾"; tb.title = dark() ? (lang === "fa" ? "تم روشن" : "Light theme") : (lang === "fa" ? "تم تیره" : "Dark theme"); };
      tb.onclick = () => {
        theme = dark() ? "light" : "dark"; store.set("theme", theme); applyTheme(); show();
        if (kiosk) {                                  // kept for this screen: kiosk.ini, no restart
          window.KIOSK.theme = theme;
          fetch(location.origin + `/api/kiosk?theme=${theme}&restart=0`, { method: "POST", credentials: "same-origin" }).catch(() => {});
        }
      };
      show();
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    buttons();
    if (lang === "fa") startFa();
  });
  return { lang, tr: (t) => tr(t) ?? t, dark };
})();
