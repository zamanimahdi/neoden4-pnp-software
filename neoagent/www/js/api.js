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
