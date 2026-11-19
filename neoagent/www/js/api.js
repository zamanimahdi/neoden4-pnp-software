// API helper, control lock, toasts. The browser handles the Basic login (same origin).
const API = (() => {
  let client = sessionStorage.getItem("client");
  if (!client) { client = "web-" + Math.random().toString(36).slice(2, 10); sessionStorage.setItem("client", client); }
  const st = { client, haveControl: false, status: null, listeners: [] };

  async function req(method, path, body, raw) {
    // absolute URL from the origin: works also when the page was opened as http://user:pw@host/
    const r = await fetch(location.origin + path, { method, body, cache: "no-store", credentials: "same-origin" });
    if (raw) { if (!r.ok) throw new Error((await r.text()) || r.statusText); return r; }
    let j;
    try { j = await r.json(); } catch (e) { throw new Error(`${r.status} ${r.statusText}`); }
    if (!r.ok || j.ok === false) throw new Error(j.error || j.report || `${r.status}`);
    return j;
  }
  const qs = (o) => Object.entries(o || {}).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");

  // machine command (needs control)
  async function mc(cmd, params) {
    return req("POST", `/api/mc/${cmd}?client=${st.client}&${qs(params)}`);
  }
  async function take() {
    const r = await req("POST", `/api/mc/lock?client=${st.client}`).catch((e) => ({ ok: false, owner: e.message }));
    st.haveControl = !!r.ok;
    if (!r.ok) toast(`Control is in use on ${r.owner_from || r.owner || "another client"}; it passes to you 10 s after the last command there`, "bad");
    emit();
    return st.haveControl;
  }
  async function release() {
    await req("POST", `/api/mc/unlock?client=${st.client}`).catch(() => {});
    st.haveControl = false; emit();
  }
  setInterval(async () => {               // keep the lock alive (expires after 15 s)
    if (!st.haveControl) return;
    const r = await req("POST", `/api/mc/lock?client=${st.client}`).catch(() => ({ ok: false }));
    if (!r.ok) { st.haveControl = false; toast(`Control lost: taken on ${r.owner_from || "another client"}`, "bad"); emit(); }
  }, 5000);
  window.addEventListener("beforeunload", () => { if (st.haveControl) fetch(location.origin + `/api/mc/unlock?client=${st.client}`, { method: "POST", keepalive: true }); });

  function emit() { st.listeners.forEach((f) => f(st)); }
  function onChange(f) { st.listeners.push(f); }

  // run a machine command with a toast on error; returns the reply or null
  async function run(cmd, params, okMsg) {
    if (!st.haveControl && !(await take())) return null;
    try { const r = await mc(cmd, params); if (okMsg) toast(okMsg, "ok"); return r; }
    catch (e) { toast(`${cmd}: ${e.message}`, "bad"); return null; }
  }

  return { st, req, mc, run, take, release, onChange, qs };
})();

// an error toast is shared: every screen (browsers and the machine's monitor) shows it too
function toast(text, kind, remote) {
  if (kind === "bad" && !remote && typeof API !== "undefined" && API.st && API.st.client) {
    fetch(`/api/event?client=${encodeURIComponent(API.st.client)}`, { method: "POST", body: JSON.stringify({ level: "bad", text: String(text) }) }).catch(() => {});
  }
  if (typeof App !== "undefined" && App.noteEvent) App.noteEvent(text, kind, remote);
  const d = document.createElement("div");
  d.className = "toast " + (kind || "");
  d.textContent = text;
  document.getElementById("toasts").appendChild(d);
  if (kind === "bad") d.setAttribute("role", "alert");          // read out at once by a screen reader
  setTimeout(() => d.remove(), kind === "bad" ? 7000 : 3500);
}

function h(tag, attrs, ...kids) {         // tiny element builder
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "class") e.className = v;
    else if (k === "html") e.innerHTML = v;
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c !== null && c !== undefined) e.append(c.nodeType ? c : document.createTextNode(c));
  // an icon-only button (✕, ⌂ …) says what it does to a screen reader too: its tooltip
  if (tag === "button" && attrs && attrs.title && !attrs["aria-label"] && e.textContent.trim().length <= 2) e.setAttribute("aria-label", attrs.title);
  return e;
}

// a question with named answers (in the page, not the browser's OK / Cancel): resolves the chosen key,
// null on Escape / a click outside
function choose(text, buttons) {
  return new Promise((res) => {
    const done = (v) => { box.remove(); document.removeEventListener("keydown", esc, true); res(v); };
    const esc = (e) => { if (e.key === "Escape") { e.preventDefault(); done(null); } };
    const box = h("div", { class: "choose", onclick: (e) => { if (e.target === box) done(null); } },
      h("div", { class: "card" }, h("div", { style: "white-space:pre-wrap;margin-bottom:10px" }, text),
        h("div", { class: "row" }, ...buttons.map(([k, label, cls]) => h("button", { class: "btn " + (cls || ""), onclick: () => done(k) }, label)))));
    document.addEventListener("keydown", esc, true);
    document.body.append(box);
    const first = box.querySelector(".btn.primary") || box.querySelector(".btn");
    if (first) first.focus();
  });
}

// press-and-hold button: down() on press, up() on release (pointer or keyboard)
function holdBtn(label, down, up, cls, attrs) {
  const b = h("button", Object.assign({ class: "btn hold " + (cls || "") }, attrs || {}), label);   // hold: no touch scroll on it
  let held = false, pending = null;
  // down() goes out after control is held; up() waits for it (a quick tap must not leave a nozzle down)
  const start = (e) => {
    e.preventDefault(); if (held) return;
    held = true; b.classList.add("on");
    try { b.setPointerCapture(e.pointerId); } catch (x) { /* not a pointer event */ }
    pending = (async () => {
      if (!API.st.haveControl && !(await API.take())) { held = false; b.classList.remove("on"); return false; }
      await down();
      return true;
    })();
  };
  const stop = () => {
    if (!held && !pending) return;
    held = false; b.classList.remove("on");
    const p = pending; pending = null;
    if (p) p.then((ok) => { if (ok) up(); }); else up();
  };
  b.addEventListener("pointerdown", start);
  ["pointerup", "pointerleave", "pointercancel", "lostpointercapture"].forEach((ev) => b.addEventListener(ev, stop));
  window.addEventListener("blur", stop);
  return b;
}

// paths of the machine PC: Windows (D:\NeoDen4) on the XP agent, POSIX (/opt/neoden/data) on Ubuntu
const PathU = {
  sep: (p) => (p.includes("/") ? "/" : "\\"),
  join: (dir, name) => { const s = PathU.sep(dir); return dir.endsWith(s) ? dir + name : dir + s + name; },
  parent: (p) => {
    const s = PathU.sep(p), t = p.length > 1 && p.endsWith(s) ? p.slice(0, -1) : p, i = t.lastIndexOf(s);
    if (i < 0) return "";
    if (s === "/") return i === 0 ? (t === "/" ? "" : "/") : t.slice(0, i);
    return i <= 2 ? (t.length > 3 ? t.slice(0, 3) : "") : t.slice(0, i);
  },
};

const fmt = (v, d = 2) => (v === null || v === undefined) ? "–" : Number(v).toFixed(d);

// a help mark beside a field: "?" in a circle; the text shows on hover, on focus and on a tap (the
// machine's touch screen has no hover). help("what this setting does")
function help(text) {
  if (!text) return null;
  const el = h("span", { class: "help", tabindex: 0, role: "note", "aria-label": text, "data-tip": text }, "?");
  el.addEventListener("click", (ev) => { ev.preventDefault(); ev.stopPropagation(); el.classList.toggle("open"); });
  el.addEventListener("blur", () => el.classList.remove("open"));
  return el;
}

// one setting as a row: a short title with its help mark, what it does in small print under it, the
// control beside it. A long label "Title: what it does" (or "Title (what it is)") is split; it is
// translated as a whole first, so the Persian texts keep working. key: the Config.ini key, shown in the help
function setRow(label, ctl, key) {
  const t = I18N.lang === "fa" ? I18N.tr(String(label)) : String(label);
  let title = t, note = "";
  const c = t.indexOf(": ");
  if (c > 2 && c < 70) { title = t.slice(0, c); note = t.slice(c + 2); }
  else { const m = /^(.{3,60}?)\s+\((.+)\)$/s.exec(t); if (m) { title = m[1]; note = m[2]; } }
  if (note) note = note.charAt(0).toUpperCase() + note.slice(1);
  key = key || (ctl && ctl.dataset && ctl.dataset.key) || "";
  const tip = [note, key ? `Config.ini: ${key}` : ""].filter(Boolean).join("\n");
  return h("div", { class: "setrow" },
    h("div", { class: "setlabel" }, h("div", { class: "settitle" }, title, help(tip)), note ? h("div", { class: "setnote" }, note) : null),
    h("div", { class: "setctl" }, ctl));
}
function setRows(rows) { return h("div", { class: "setrows" }, rows.map(([l, f, k]) => setRow(l, f, k))); }

// line icons drawn here (no font, no files): icon("gear") -> <span class="i"><svg ...></span>; the paths are
// on a 24 x 24 grid, drawn with the text's colour
const ICONS = {
  power: "M12 3v9M6.3 6.3a8 8 0 1011.4 0", restart: "M4 12a8 8 0 1014-5.3M18 3v4h-4",
  play: "M7 5l12 7-12 7z", plus: "M12 5v14M5 12h14", stop: "M6 6h12v12H6z", check: "M5 12l5 5 9-10",
  home: "M4 11l8-7 8 7M6 10v10h12V10", save: "M5 4h11l3 3v13H5zM8 4v5h7V4M8 20v-6h8v6",
  gear: "M12 9a3 3 0 100 6 3 3 0 000-6zM19 12a7 7 0 00-.1-1.3l2-1.5-2-3.4-2.3.9a7 7 0 00-2.2-1.3L14 3h-4l-.4 2.4a7 7 0 00-2.2 1.3l-2.3-.9-2 3.4 2 1.5A7 7 0 005 12c0 .4 0 .9.1 1.3l-2 1.5 2 3.4 2.3-.9a7 7 0 002.2 1.3L10 21h4l.4-2.4a7 7 0 002.2-1.3l2.3.9 2-3.4-2-1.5c.1-.4.1-.9.1-1.3z",
  file: "M6 3h8l4 4v14H6zM14 3v4h4", sliders: "M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M14 4v4M8 10v4M16 16v4",
  chip: "M7 7h10v10H7zM10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4",
  board: "M3 5h18v14H3zM7 9h4v3H7zM14 9h3M14 12h3M7 15h10",
  target: "M12 5a7 7 0 100 14 7 7 0 000-14zM12 2v5M12 17v5M2 12h5M17 12h5",
  mark: "M12 9a3 3 0 100 6 3 3 0 000-6zM12 3v4M12 17v4M3 12h4M17 12h4",
  reel: "M12 4a8 8 0 100 16 8 8 0 000-16zM12 10a2 2 0 100 4 2 2 0 000-4zM12 20h9",
  list: "M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01", gauge: "M4 17a8 8 0 1116 0M12 17l4-6",
  out: "M12 4v10M8 10l4 4 4-4M5 19h14", camera: "M4 8h3l2-2h6l2 2h3v11H4zM12 11a3 3 0 100 6 3 3 0 000-6z",
  move: "M12 3v18M3 12h18M9 6l3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3M18 9l3 3-3 3",
  ruler: "M4 15L15 4l5 5L9 20zM8 13l2 2M11 10l2 2M14 7l2 2", grid: "M4 4h16v16H4zM4 10h16M4 15h16M10 4v16M15 4v16",
  box: "M4 8l8-4 8 4v8l-8 4-8-4zM4 8l8 4 8-4M12 12v8", eye: "M2 12s4-6 10-6 10 6 10 6-4 6-10 6S2 12 2 12zM12 9a3 3 0 100 6 3 3 0 000-6z",
  wifi: "M4 10a12 12 0 0116 0M7 13.5a7.5 7.5 0 0110 0M10 17a3 3 0 014 0", chart: "M5 20V10M10 20V4M15 20v-8M20 20v-5",
  pads: "M5 7h5v4H5zM14 7h5v4h-5zM5 14h5v4H5zM14 14h5v4h-5z", wand: "M5 19L16 8M14 6l4 4M18 2v4M16 4h4M6 4v3M4.5 5.5h3",
  panel: "M3 4h8v7H3zM13 4h8v7h-8zM3 13h8v7H3zM13 13h8v7h-8z", code: "M8 7l-5 5 5 5M16 7l5 5-5 5M14 4l-4 16",
  screen: "M3 5h18v12H3zM9 21h6M12 17v4", leads: "M8 6h8v12H8zM4 9h4M4 15h4M16 9h4M16 15h4", pin: "M12 21s6-6 6-11a6 6 0 10-12 0c0 5 6 11 6 11zM12 8a2 2 0 100 4 2 2 0 000-4z",
};
function icon(name) {
  const d = ICONS[name];
  if (!d) return null;
  const s = document.createElement("span");
  s.className = "i";
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="${d}"/></svg>`;
  return s;
}
