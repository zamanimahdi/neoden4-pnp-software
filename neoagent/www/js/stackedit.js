// Feeder settings of one job (NeoDen4 frmStackEdit, the "Feeder Settings" tab of the job editor): the
// feeders in three lists (right bank 1..19, left bank 20..48, special / trays 49..98) and the form of
// the selected one: basic information, pick point (head camera: Align, the tape pockets found in the
// picture), nozzles (allowed, Align = nozzle over the pick point, Height test while held, Pick test),
// heights / delays / vacuum / vision / speed, and the feeder (feed rate, feed torque, peel strength,
// Feed test). It edits JobFile.lines; Save in the job editor writes the file.
//
// stack line fields after the tag (neojob/jobfile.py): 0 id, 1 type (0 tape, 1 tray), 2 nozzles, 3 X,
// 4 Y, 5 angle, 6 footprint, 7 value, 8 pick height, 9 pick delay, 10 place height, 11 place delay,
// 12 vacuum, 13 threshold, 14 vision, 15 speed; tape: 16 feed mm, 17 feed torque, 18 peel strength,
// 19 skip, 20 size check; tray: 16 columns, 17 rows, 18 / 19 right top X / Y, 20 / 21 counters,
// 22 skip, 23 size check.
const StackEdit = {
  sel: null,
  tableView: false,

  issuesFor(jf) {
    const key = jf.path + "|" + (jf.dirty ? "" : jf.lines.length + ":" + jf.lines.join("").length);
    const c = this._issues;
    if (c && c.path === jf.path && (jf.dirty || c.key === key || c.loading)) return c.data;
    this._issues = { path: jf.path, key, loading: true, data: c && c.path === jf.path ? c.data : {} };
    API.req("GET", `/api/job/check?path=${encodeURIComponent(jf.path)}`)
      .then((r) => { this._issues = { path: jf.path, key, data: r.feeders || {} }; App.show(); })
      .catch(() => { this._issues = { path: jf.path, key, data: {} }; });
    return this._issues.data;
  },
  lineNo(jf, id) {
    return jf.lines.findIndex((l) => { const t = l.split(","); return t[0].trim() === "stack" && +t[1] === +id; });
  },
  ids(jf) {
    return jf.lines.map((l) => l.split(",")).filter((t) => t[0].trim() === "stack").map((t) => +t[1]);
  },

  // the board's parts by type (value + footprint): {key, value, footprint, n, stacks}
  groups(jf) {
    const g = new Map();
    jf.lines.forEach((l) => {
      const t = l.split(",");
      if (t[0].trim() !== "comp") return;
      const value = (t[4] || "").trim(), footprint = (t[5] || "").trim(), key = value + "|" + footprint;
      if (!g.has(key)) g.set(key, { key, value, footprint, n: 0, stacks: new Set() });
      const e = g.get(key); e.n++; e.stacks.add(+t[1]);
    });
    return [...g.values()].sort((a, b) => (a.value + a.footprint).localeCompare(b.value + b.footprint, undefined, { numeric: true }));
  },
  // every part of that type onto feeder `to` (the feeders are not fixed: changed in the middle of a job
  // too; the run reads the job again when it continues); the feeder takes the type's value / footprint.
  // A feeder holds one type: the types it had go to their other feeder, else to no feeder (0) — left
  // on it they would not match its new value, and the next Assign would move them anyway.
  // key "" = the feeder emptied.
  moveParts(jf, key, to) {
    const gs = this.groups(jf);
    const g = key ? gs.find((x) => x.key === key) : null;
    if (key && !g) return;
    const kindOf = (t) => (t[4] || "").trim() + "|" + (t[5] || "").trim();
    const off = {};                                         // type -> where its parts on `to` go
    gs.filter((x) => x.stacks.has(to) && x.key !== key).forEach((x) => { off[x.key] = [...x.stacks].find((s) => s !== to && s > 0) || 0; });
    const from = g ? [...g.stacks].filter((s) => s !== to) : [];
    jf.lines = jf.lines.map((l) => {
      const t = l.split(",");
      if (t[0].trim() !== "comp") return l;
      const k = kindOf(t);
      if (g && k === key) t[1] = String(to);
      else if (+t[1] === to && k in off) t[1] = String(off[k]);
      else return l;
      return t.join(",");
    });
    const no = this.lineNo(jf, to);
    if (no >= 0) { jf.setField(no, 6, g ? g.footprint.replace(/,/g, " ") : ""); jf.setField(no, 7, g ? g.value.replace(/,/g, " ") : ""); }
    jf.touch(); App.show();
    const moved = Object.entries(off).map(([k, s]) => `${k.split("|")[0]} → ${s ? "feeder " + s : "no feeder"}`);
    toast((g ? `${g.n} part(s) ${g.value}${g.footprint ? " / " + g.footprint : ""} now on feeder ${to}` + (from.filter((s) => s > 0).length ? ` (were on ${from.filter((s) => s > 0).join(", ")})` : "")
      : `feeder ${to} is empty`) + (moved.length ? `; ${moved.join(", ")}` : "") + ". Save to keep it", "ok");
  },
  ownKind(jf, id) {
    const mine = this.groups(jf).filter((g) => g.stacks.has(+id));
    if (mine.length < 2) return mine[0] || null;
    const no = this.lineNo(jf, +id), t = no >= 0 ? jf.lines[no].split(",") : [];
    const n = (v) => String(v || "").trim().replace(/\s+/g, " ").toLowerCase();
    let fp = n(t[7]), val = n(t[8]);
    if (!fp && val.includes("/")) [fp, val] = [val.slice(0, val.indexOf("/")).trim(), val.slice(val.indexOf("/") + 1).trim()];
    return mine.find((g) => n(g.value) === val && (!fp || n(g.footprint) === fp)) || mine.slice().sort((a, b) => b.n - a.n)[0];
  },
  // the parts to choose from: this feeder's own first, then the same footprint, then parts without a
  // feeder, then the rest
  partOptions(jf, id) {
    const gs = this.groups(jf), own = id ? this.ownKind(jf, id) : null;
    const mine = id ? [own, ...gs.filter((g) => g.stacks.has(+id) && (!own || g.key !== own.key))].filter(Boolean) : [];
    const fp = mine.length ? mine[0].footprint.toLowerCase() : "";
    const rank = (g) => (mine.includes(g) ? 0 : fp && g.footprint.toLowerCase() === fp ? 1 : ![...g.stacks].some((s) => s > 0) ? 2 : 3);
    const sorted = gs.slice().sort((a, b) => rank(a) - rank(b));
    const where = (g) => { const o = [...g.stacks].filter((s) => s !== +id); return o.length ? `, on ${o.map((s) => (s > 0 ? "feeder " + s : "no feeder")).join(", ")}` : ""; };
    return [h("option", { value: "", selected: !mine.length }, mine.length ? "— empty this feeder —" : "— no parts: choose one —"),
      ...sorted.map((g) => h("option", { value: g.key, selected: !!own && g.key === own.key },
        `${g.value || "?"}${g.footprint ? " / " + g.footprint : ""} — ${g.n} part(s)` + where(g)))];
  },
  partsSelect(jf, id) {
    const own = this.ownKind(jf, id), mine = [own, ...this.groups(jf).filter((g) => g.stacks.has(+id) && (!own || g.key !== own.key))].filter(Boolean);
    const sel = h("select", { style: "max-width:420px", onchange: (e) => {
      if (e.target.value) return this.moveParts(jf, e.target.value, +id);
      if (mine.length && confirm(`Empty feeder ${id}? Its parts go to their other feeder, else to no feeder.`)) this.moveParts(jf, "", +id);
      else App.show();
    } }, this.partOptions(jf, id));
    return h("span", { style: "display:inline-flex;gap:6px;align-items:center;flex-wrap:wrap" }, sel,
      mine.length > 1 ? h("span", { class: "warn", style: "font-size:12px" }, `also on it: ${mine.slice(1).map((g) => `${g.value} (${g.n})`).join(", ")} `,
        h("button", { class: "btn small", title: "the other kinds go to their other feeder, else to no feeder", onclick: () => this.moveParts(jf, own.key, +id) }, `Keep only ${own.value}`)) : null,
      h("span", { class: "muted", style: "font-size:12px" }, "a part of the board: all of its kind move to this feeder"));
  },

  render(body, jf) {
    const listOnly = this._listOnly;                          // the list alone (beside a bulk feeder's form)
    if (!listOnly && String(this.sel).startsWith("b")) return this.renderBulk(body, jf);
    const ids = this.ids(jf);
    if (!listOnly && (this.sel === null || !(+this.sel >= 1 && +this.sel <= 98))) this.sel = ids[0] || 1;   // a free number may be chosen (to add it)
    const used = new Set(ids);
    const val = (id) => { const no = this.lineNo(jf, id); return no < 0 ? "" : (jf.lines[no].split(",")[8] || "").trim(); };
    const parts = {};
    jf.lines.forEach((l) => { const t = l.split(","); if (t[0].trim() === "comp") parts[+t[1]] = (parts[+t[1]] || 0) + 1; });
    // what a used feeder still needs (no part height, a pick point the head cannot reach): from the saved
    // file (GET /api/job/check), read again when the file was saved since; marked with a warning sign
    const issues = this.issuesFor(jf);
    const list = (title, from, to) => h("div", { class: "selist" }, h("div", { class: "muted", style: "margin:4px 0 2px" }, title),
      // trays (49..98): the used ones and the next three free numbers, to make a new tray
      ...Array.from({ length: to - from + 1 }, (_, k) => from + k).filter((id) => used.has(id) || id <= 48 ||
        Array.from({ length: to - from + 1 }, (_, k) => from + k).filter((j) => !used.has(j)).slice(0, 3).includes(id)).map((id) =>
        h("div", { class: "seitem" + (id === +this.sel ? " on" : "") + (used.has(id) ? "" : " unused"), onclick: () => { this.sel = id; this.autoGo = used.has(id); App.show(); } },
          h("b", {}, String(id)), h("span", {}, used.has(id) ? val(id) || "—" : ""), parts[id] ? h("span", { class: "muted" }, `${parts[id]}`) : null,
          issues[id] ? h("span", { class: "warn", title: issues[id].join("\n"), style: "font-weight:bold" }, "⚠") : null)));
    const left = h("div", { class: "card selcol" },
      h("div", { class: "row", style: "margin:0" }, h("b", {}, "Feeders"), h("div", { class: "grow" }),
        h("button", { class: "btn small", title: "all feeders in a table", onclick: () => { this.tableView = true; App.show(); } }, "Table")),
      list("Right feeders", 1, 19), list("Left feeders", 20, 48), list("Special feeders (trays)", 49, 98), this.bulkList(jf, parts));
    const wrap = h("div", { class: "stackedit" }, left);
    body.append(wrap);
    if (listOnly) return;
    if (!used.has(+this.sel)) {
      wrap.append(h("div", { class: "card" }, h("h3", {}, `Feeder ${this.sel} is not used in this job`),
        h("button", { class: "btn primary", onclick: () => this.add(jf, +this.sel) }, `Use feeder ${this.sel}`)));
      return;
    }
    this.form(wrap, jf, +this.sel);
  },

  // a new stack line: NeoDen4's defaults, the pick point from the machine's stack table when it has one
  add(jf, id) {
    const p = Cfg.pt(`Stack/lstStack${id - 1}.pos`) || [0, 0];
    const tray = id >= 49;
    const line = tray ? `stack,${id},1,1,${fmt(p[0], 2)},${fmt(p[1], 2)},0.00,,,0.50,100,0.00,100,No,-40,1,100,1,1,${fmt(p[0], 2)},${fmt(p[1], 2)},1,1,No,No,-40,-40,-40,-40,`
      : `stack,${id},0,1,${fmt(p[0], 2)},${fmt(p[1], 2)},0.00,,,0.50,100,0.00,100,No,-40,1,100,4,60,80,No,No,-40,-40,-40,-40,`;
    const nos = jf.lines.map((l, no) => [l, no]).filter(([l]) => l.split(",")[0].trim() === "stack");
    const after = nos.filter(([l]) => +l.split(",")[1] < id).pop();
    const at = after ? after[1] + 1 : nos.length ? nos[0][1] : jf.lines.length;
    jf.lines.splice(at, 0, line);
    jf.touch(); App.show();
  },

  // Feeder ID exchange (frmStackEdit::moveStackTo): the two stacks swap numbers, and so do their parts
  exchange(jf, a, b) {
    if (!(b >= 1 && b <= 98) || a === b) return toast("feeder 1 .. 98", "warn");
    jf.lines = jf.lines.map((l) => {
      const t = l.split(","), tag = t[0].trim();
      if ((tag === "stack" || tag === "comp") && (+t[1] === a || +t[1] === b)) { t[1] = String(+t[1] === a ? b : a); return t.join(","); }
      return l;
    });
    this.sel = b; jf.touch(); App.show();
    toast(`feeder ${a} ↔ ${b} (parts too). Save to keep it`, "ok");
  },

  form(wrap, jf, id) {
    const no = () => this.lineNo(jf, id);
    const get = (i) => (jf.lines[no()].split(",")[i + 1] || "").trim();
    const set = (i, v) => jf.setField(no(), i, String(v));
    const tray = +get(1) === 1;
    const skipI = tray ? 22 : 19, sizeI = tray ? 23 : 20;
    const pocketI = tray ? 28 : 25, partL = tray ? 29 : 26, partW = tray ? 30 : 27;   // ours, after the 4 thresholds
    const padI = tray ? 31 : 28, lookI = tray ? 32 : 29, partH = tray ? 33 : 30, bulkI = tray ? 34 : 31, checkI = tray ? 35 : 32;
    const fid = +Cfg.get(`Stack/lstStack${id - 1}.nFeedId`, id) || id, pid = +Cfg.get(`Stack/lstStack${id - 1}.nPeelId`, id) || id;
    const need = async () => API.st.haveControl || (await API.take());
    const off = (t) => Cfg.offset(t);
    const lamp = () => API.run("led", { v: +Cfg.get("PadAlign/nLed", 3) || 3 });

    // ---- inputs bound to fields ----
    const inputs = {};
    const num = (i, step = "0.01", w = 80, fix = 2) => (inputs[i] = h("input", { type: "number", step, style: `width:${w}px`, value: get(i),
      onchange: (e) => set(i, fix ? (+e.target.value).toFixed(fix) : String(Math.round(+e.target.value))) }));
    const txt = (i, w = 150) => h("input", { type: "text", style: `width:${w}px`, value: get(i), onchange: (e) => set(i, e.target.value.replace(/,/g, " ")) });
    const yn = (i, label) => h("label", {}, h("input", { type: "checkbox", checked: /^(yes|1|true)$/i.test(get(i)), onchange: (e) => set(i, e.target.checked ? "Yes" : "No") }), " " + label);
    const ynOn = (i, label) => h("label", {}, h("input", { type: "checkbox", checked: !/^(no|0|false)$/i.test(get(i)), onchange: (e) => set(i, e.target.checked ? "Yes" : "No") }), " " + label);   // on unless No
    const field = (label, ...el) => h("div", { class: "sefield" }, h("span", {}, label), ...el);
    const setXY = (ix, iy, x, y, why) => {
      set(ix, (+x).toFixed(2)); set(iy, (+y).toFixed(2));
      if (inputs[ix]) inputs[ix].value = (+x).toFixed(2); if (inputs[iy]) inputs[iy].value = (+y).toFixed(2);
      toast(`feeder ${id}: ${fmt(+x, 2)}, ${fmt(+y, 2)} (${why}). Save to keep it`, "ok");
    };

    // ---- head camera, the tape pockets ----
    const cam = Manual.coordCam(1);
    this.cam = cam;
    const camTo = async (ix, iy) => {               // "Align": the head camera over the point
      if (!(await need())) return;
      const o = off(0);
      await lamp();
      if (await API.run("move", { x: (+get(ix) + o[0]).toFixed(3), y: (+get(iy) + o[1]).toFixed(3), slow: 1 })) { await App.poll(); await autoLight(); cam.start(); }
    };
    // the exposure for the tape under the camera (white paper tape burns out at the page's exposure, black
    // plastic is too dark): set by the server from the picture itself
    const lightOut = h("span", { class: "muted", style: "font-size:12px" });
    const autoLight = async () => {
      try {
        const r = await API.req("POST", `/api/cam/auto?cam=1`);
        lightOut.textContent = ` exposure ${r.exp}`;
      } catch (e) { lightOut.textContent = " auto light: " + e.message; }
    };
    const hereXY = (ix, iy) => { const p = (API.st.status || {}).pos; if (!p) return toast("position unknown", "bad"); const o = off(0); setXY(ix, iy, p[0] - o[0], p[1] - o[1], "camera centre"); };
    // the tape pockets, found in every new picture and drawn over it (green: pockets / parts, the thick
    // one nearest the crosshair; blue: sprocket holes). Click "Use pocket" to take the thick one.
    let found = [], busy = false, at = null;
    const info = h("div", { class: "muted", style: "font-size:12px" });
    const pitchBox = h("div", { class: "row", style: "margin:4px 0 0;font-size:12px" });
    const showPitch = () => {
      const k = Cfg.pxmm(1) * (found.size ? found.size / 1024 : 1);
      const pk = found.filter((f) => f.kind === "pocket" && !f.round);
      const d = [];
      for (const a of pk) {                                  // each pocket's nearest neighbour of the same size
        let best = 1e9;
        for (const b of pk) if (b !== a && Math.abs(b.w - a.w) < 0.35 * a.w + 0.2 && Math.abs(b.h - a.h) < 0.35 * a.h + 0.2) best = Math.min(best, Math.hypot(a.px - b.px, a.py - b.py));
        if (best < 1e8) d.push(best / k);
      }
      if (!d.length) { pitchBox.replaceChildren(); return; }
      d.sort((a, b) => a - b);
      const m = d[Math.floor(d.length / 2)];
      const std = [2, 4, 8, 12, 16, 20, 24, 28, 32].reduce((a, b) => (Math.abs(b - m) < Math.abs(a - m) ? b : a));
      const ok = Math.abs(std - m) < 0.15 * std;
      pitchBox.replaceChildren(h("span", {}, `pocket to pocket ${fmt(m, 2)} mm` + (ok ? ` → pitch ${std} mm` : " (not a standard pitch)")),
        tray || !ok ? null : h("button", { class: "btn small" + (+get(16) === std ? "" : " primary"), title: "the feeder's feed rate (mm per part)",
          onclick: () => { set(16, String(std)); if (inputs[16]) inputs[16].value = String(std); toast(`feed rate ${std} mm. Save to keep it`, "ok"); showPitch(); } },
          +get(16) === std ? `feed rate is ${std} mm` : `Feed rate = ${std} mm (now ${get(16) || "—"})`));
    };
    const toMm = (f, size) => {
      const k = Cfg.pxmm(1), o = off(0);
      return [at[0] - o[0] + (f.px - size / 2) / k, at[1] - o[1] - (f.py - size / 2) / k];
    };
    let lastDetect = 0;
    const detect = async () => {
      if (busy || performance.now() - lastDetect < 500) return;   // the search runs on the machine's PC
      busy = true; lastDetect = performance.now();
      try {
        const p = (API.st.status || {}).pos;
        const o0 = off(0);
        const r = await API.req("GET", `/api/cam/pockets?cam=1` + (p ? `&job=${encodeURIComponent(JobFile.path || "")}&x=${(p[0] - o0[0]).toFixed(3)}&y=${(p[1] - o0[1]).toFixed(3)}` : ""));
        at = p; found = r.found; found.size = r.size; found.board = r.board;
        const pk = found.filter((f) => f.kind === "pocket" && f.full !== false);      // pockets holding a part
        found.filter((f) => f.kind === "pocket").forEach((f) => { if (at) f.mm = toMm(f, r.size); });
        if (pk[0] && at) {
          const [x, y] = toMm(pk[0], r.size);
          pk[0].mm = [x, y];
          info.textContent = `pocket ${fmt(pk[0].w, 2)} x ${fmt(pk[0].h, 2)} mm at ${fmt(x, 2)}, ${fmt(y, 2)}` +
            `  (now ${fmt(+get(3), 2)}, ${fmt(+get(4), 2)}: off by ${fmt(x - +get(3), 2)}, ${fmt(y - +get(4), 2)})`;
        } else info.textContent = found.board ? `the board (panel ${found.board.panel}) is in the picture: its Gerber pads are drawn (blue), no tape pockets looked for`
          : found.some((f) => f.kind === "pocket") ? "only empty pockets in the picture" : "no pocket in the picture";
        cam._draw();
        showPitch();
        if (follow.checked && pk[0] && pk[0].mm) centre(pk[0], true);
      } catch (e) { /* no picture yet */ }
      busy = false;
    };
    // a click inside a found pocket takes its centre as the pick point; elsewhere it moves the camera
    const click0 = cam.onClick;
    cam.onClick = (dx, dyUp, ev) => {
      const sz = found.size || cam.size, sc2 = sz / cam.size;
      const px = (cam.size / 2 + dx) * sc2, py = (cam.size / 2 - dyUp) * sc2;
      const k = Cfg.pxmm(1) * sc2;
      const hit = found.find((f) => f.kind === "pocket" && f.mm && Math.abs(px - f.px) < f.w * k / 2 + 6 && Math.abs(py - f.py) < f.h * k / 2 + 6)
        || found.filter((f) => f.kind === "pocket").find((f) => Math.abs(px - f.px) < f.w * k / 2 + 6 && Math.abs(py - f.py) < f.h * k / 2 + 6);
      if (hit && at) {
        const [x, y] = hit.mm || toMm(hit, sz);
        return setXY(3, 4, x, y, `pocket ${fmt(hit.w, 2)} x ${fmt(hit.h, 2)} mm`);
      }
      return click0 && click0(dx, dyUp, ev);
    };
    const grab0 = cam.grab.bind(cam);
    cam.grab = async () => { const ok = await grab0(); if (ok) detect(); return ok; };
    cam.overlay = (g, toC, sc) => {
      if (!found.size) return;
      const k = Cfg.pxmm(1) * cam.size / found.size, f0 = found.find((f) => f.kind === "pocket" && f.full !== false);
      const [ex1] = toC(cam.size / 2 + 1, 0), [ex0] = toC(cam.size / 2, 0), s1 = ex1 - ex0;
      if (found.board) {                                     // the board: its Gerber pads where they really are
        g.strokeStyle = "#4aa0ff"; g.lineWidth = 1.5 * sc;
        for (const poly of found.board.pads) {
          g.beginPath();
          poly.forEach(([x, y], i) => { const [cx, cy] = toC(x * cam.size / found.size, y * cam.size / found.size); i ? g.lineTo(cx, cy) : g.moveTo(cx, cy); });
          g.closePath(); g.stroke();
        }
      }
      for (const f of found) {
        const pocket = f.kind === "pocket", thick = f === f0;
        const [cx, cy] = toC(f.px * cam.size / found.size, f.py * cam.size / found.size);
        if (f.round) {                                        // round things (sprocket holes, round pads, vias): a circle
          g.strokeStyle = f.kind === "hole" ? "#4aa0ff" : f.kind === "round" ? "#ffd84a" : f.full === false ? "#ff9a2e" : "#3ce03c";
          g.lineWidth = (thick ? 4 : 2) * sc;
          g.beginPath(); g.arc(cx, cy, f.r * k * s1, 0, 2 * Math.PI); g.stroke();
          continue;
        }
        const [ex] = toC(cam.size / 2 + 1, 0), [e0] = toC(cam.size / 2, 0), s = ex - e0;   // canvas px per image px
        const w = f.w * k * s, hh = f.h * k * s, a = (-f.angle * Math.PI) / 180;
        g.save(); g.translate(cx, cy); g.rotate(a);
        g.strokeStyle = !pocket ? "#4aa0ff" : f.full === false ? "#ff9a2e" : "#3ce03c";   // blue hole, orange empty, green part g.lineWidth = (thick ? 4 : 2) * sc;
        g.strokeRect(-w / 2, -hh / 2, w, hh);
        g.beginPath(); g.moveTo(-8 * sc, 0); g.lineTo(8 * sc, 0); g.moveTo(0, -8 * sc); g.lineTo(0, 8 * sc); g.stroke();
        g.restore();
      }
    };
    // bring the found pocket's centre under the crosshair (a relative move); "keep centred" does it after
    // every picture until it is within 0.02 mm
    let moving = false;
    const centre = async (f, quiet) => {
      f = f || found.find((q) => q.kind === "pocket" && q.full !== false);
      if (!f || !f.mm || moving) { if (!quiet) toast("no pocket in the picture", "warn"); return; }
      const o = off(0), p = (API.st.status || {}).pos;
      if (!p) return;
      const dx = f.mm[0] - (p[0] - o[0]), dy = f.mm[1] - (p[1] - o[1]);
      if (Math.hypot(dx, dy) < 0.02) return;
      if (!API.st.haveControl && !(await API.take())) return;
      moving = true;
      try {
        if (await API.run("rel", { x: dx.toFixed(4), y: dy.toFixed(4), slow: 1 })) {
          await new Promise((r) => setTimeout(r, 400));        // let the head settle: no chasing a shaking picture
          await App.poll();
          if (!cam.live) cam.grab();
        }
      } finally { setTimeout(() => (moving = false), 400); }
    };
    const follow = h("input", { type: "checkbox", checked: localStorage.getItem("pocketFollow") === "1",
      onchange: (e) => { localStorage.setItem("pocketFollow", e.target.checked ? "1" : "0"); if (e.target.checked) centre(null, true); } });
    const usePocket = () => {
      const f = found.find((q) => q.kind === "pocket" && q.full !== false);
      if (!f || !f.mm) return toast("no pocket in the picture", "warn");
      setXY(3, 4, f.mm[0], f.mm[1], `pocket ${fmt(f.w, 2)} x ${fmt(f.h, 2)} mm`);
    };
    const camCol = h("div", { class: "card secam" }, h("div", { class: "row", style: "margin-top:0" }, h("b", {}, "Head camera"),
      h("span", { class: "muted", style: "font-size:12px" }, "green: pocket with a part, orange: empty · click a pocket: its centre is the pick point · elsewhere: move there · arrows: jog 0.1 mm (Shift 1 mm)")), cam.el,
      h("div", { class: "row toolrow" },
        h("button", { class: "btn", title: tray ? "the head camera over the start (first) cell" : "the head camera over the pick point", onclick: () => camTo(3, 4) }, tray ? "To the start" : "To the pick point"),
        tray ? null : h("button", { class: "btn", title: "move the camera so the pocket's centre is on the crosshair", onclick: () => centre() }, "Centre on pocket"),
        tray ? null : h("label", {}, follow, " keep centred"),
        h("button", { class: "btn", title: "set the camera's exposure for what is under it (also done after Align)", onclick: async () => { await autoLight(); cam.grab(); } }, "Auto light"), lightOut),
      tray ? null : h("div", { class: "row toolrow" },
        h("button", { class: "btn primary", title: "the thick green pocket's centre becomes the pick point", onclick: usePocket }, "Set pick point = this pocket"),
        h("button", { class: "btn", title: "peel, then feed one step (Feed rate below); the picture follows", onclick: () => feedTest() }, "Feed test ▸"),
        ), info, pitchBox);

    // ---- nozzles ----
    // where and how deep a part is picked: the pick point and Pick height; for a bulk feeder (its form shows
    // these tools too) a part of its last scan and the bulk feeder's pick height
    const bp = this._bulkPt || null;
    const ptXY = () => (bp ? bp.xy() : [+get(3), +get(4)]);
    const pickZ = () => (bp ? bp.z() : Math.max(0, 12 - (+get(8) || 0)));
    const ptOk = () => {
      if (!bp) return true;
      if (!bp.xy()) { toast("scan the bulk feeder first (3. Scan)", "warn"); return false; }
      if (!(bp.z() > 0)) { toast("no pick height for this bulk feeder yet: Bulk feeders page → Pick height (type it, or Measure)", "warn"); return false; }
      return true;
    };
    const zDown = (n) => ptOk() && API.run("z", { n, mm: pickZ().toFixed(2), speed: 30, wait: 0 });
    const zUp = (n) => API.run("z", { n, mm: 0, speed: 30, wait: 0 });
    const nozzleTo = async (n) => {                  // "Align" n: nozzle n over the pick point
      if (!ptOk() || !(await need())) return;
      const o = off(n), [px, py] = ptXY();
      await API.run("raise", {});
      return API.run("move", { x: (px + o[0]).toFixed(3), y: (py + o[1]).toFixed(3) });
    };
    const pickTest = async (n) => {                  // CMachine::pickPart: the part stays on the nozzle
      if (!(await nozzleTo(n))) return;
      const spd = Math.max(1, Math.min(255, Math.round((+get(15) || 100) * 150 / 130)));
      await API.run("rotate", { n, deg: 0 });
      await API.run("valve", { ch: n, state: -1 });             // suction first: a loose part may slide away
      await API.run("z", { n, mm: pickZ().toFixed(2), speed: spd });
      await new Promise((r) => setTimeout(r, +get(9) || 0));
      await API.run("z", { n, mm: 0, speed: spd }, `nozzle ${n}: picked (vacuum on). Release drops it`);
    };
    // discardComponent: over the trash box (raw head position, no nozzle offset), blow 100 ms
    const discard = async (n) => {
      if (!(await need())) return;
      const w = Cfg.pt("Position/posWasteBox");
      if (!w || !(w[0] || w[1])) return toast("no trash box position (Machine settings → Setups → Trash box)", "bad");
      await API.run("raise", {});
      if (!(await API.run("move", { x: (+w[0]).toFixed(3), y: (+w[1]).toFixed(3) }))) return;
      await API.run("valve", { ch: n, state: 1 }); await new Promise((r) => setTimeout(r, 100));
      await API.run("valve", { ch: n, state: 0 }, `nozzle ${n}: part dropped in the trash box`);
    };
    const release = async (n) => { if (!(await need())) return; await API.run("valve", { ch: n, state: 1 }); await new Promise((r) => setTimeout(r, 100)); await API.run("valve", { ch: n, state: 0 }); };
    const heightByVacuum = async (n) => {                // POST /api/feedsetup/pick_height: down until it seals
      if (!confirm(`Nozzle ${n} goes down on this feeder's pick point (${fmt(+get(3), 2)}, ${fmt(+get(4), 2)}) with the suction on, in 0.1 mm steps, until it touches the part; the part stays in its pocket. Go?`)) return;
      if (!(await need())) return;
      try {
        const r = await API.req("POST", `/api/feedsetup/pick_height?client=${API.st.client}&job=${encodeURIComponent(jf.path)}&stack=${id}&nozzle=${n}&x=${get(3)}&y=${get(4)}`);
        if (!r.ok) return toast(`feeder ${id}: ${r.text}`, "bad");
        set(8, r.pick_height.toFixed(2)); if (inputs[8]) inputs[8].value = r.pick_height.toFixed(2);
        toast(`feeder ${id}: touched at ${fmt(r.seal, 2)} mm → Pick height ${fmt(r.pick_height, 2)} (was ${fmt(r.was, 2)}). Save to keep it`, "ok");
      } catch (e) { toast(e.message, "bad"); }
    };
    const nozRow = (n) => {
      const allowed = () => new Set([...get(2)].filter((c) => "1234".includes(c)));
      const hold = holdBtn("Height test", () => zDown(n), () => zUp(n), "small",
        { title: "held: the nozzle goes down to the pick height (it stays down while you hold the button)" });
      return h("div", { class: "row", style: "margin:3px 0" },
        h("label", { style: "width:86px" }, h("input", { type: "checkbox", checked: allowed().has(String(n)), onchange: (e) => {
          const cur = allowed();
          if (e.target.checked) cur.add(String(n)); else cur.delete(String(n));
          if (!cur.size) { e.target.checked = true; return toast("at least one nozzle", "warn"); }
          set(2, [...cur].sort().join(""));
        } }), ` Nozzle ${n}`),
        h("button", { class: "btn small", title: `nozzle ${n} over the pick point`, onclick: () => nozzleTo(n) }, "Align"), hold,
        h("button", { class: "btn small", onclick: () => pickTest(n) }, "Pick test"),
        bp ? null : h("button", { class: "btn small", title: "the nozzle goes down on the pick point with the suction on until it touches the part (the pressure falls): Pick height from that", onclick: () => heightByVacuum(n) }, "Measure height"),
        h("button", { class: "btn small", title: "take the part to the trash box and drop it there (unpick)", onclick: () => discard(n) }, "Discard"),
        h("button", { class: "btn small ghost", title: "blow here: drop what the nozzle holds where it is", onclick: () => release(n) }, "Release"));
    };

    // ---- feeder ----
    const pitch = () => +get(16) || 4;
    const peelLen = () => Math.min(64, Math.max(4, Math.max(+Cfg.get(`Peel/lstPeel${pid - 1}.nLoad`, 0) || 0, Math.round(3 * pitch()))));
    const feedTest = async () => {
      if (!(await need())) return;
      await API.run("peel", { n: pid, a: Math.round(+get(18) || 50), b: peelLen() });
      await API.run("feed", { n: fid, p0: Math.round(+get(17) || 60), p1: Math.round(pitch()), mode: 0 }, `feeder ${fid}: fed ${pitch()} mm`);
      await new Promise((r) => setTimeout(r, 700));
      cam.grab();
    };
    const exch = h("input", { type: "number", min: 1, max: 98, style: "width:60px", placeholder: "No." });
    const measureOut = h("span", { class: "muted", style: "font-size:12px" });
    const measurePart = async () => {
      const n = +((get(2) || "1").match(/[1-4]/) || ["1"])[0];
      if (!ptOk()) return;
      const [px, py] = ptXY();
      if (!confirm(`Nozzle ${n} picks a part at ${bp ? "a scanned part of this bulk feeder" : "this feeder's pick point"} (${fmt(px, 2)}, ${fmt(py, 2)}), the bottom camera measures it, and it is put back. Go?`)) return;
      if (!(await need())) return;
      measureOut.textContent = " measuring …";
      try {
        const r = await API.req("POST", `/api/align/measure_part?client=${API.st.client}&x=${px}&y=${py}&z=${pickZ().toFixed(2)}&nozzle=${n}&job=${encodeURIComponent(jf.path || "")}&stack=${id}`);
        set(partL, r.L.toFixed(2)); set(partW, r.W.toFixed(2));
        if (inputs[partL]) inputs[partL].value = r.L.toFixed(2);
        if (inputs[partW]) inputs[partW].value = r.W.toFixed(2);
        measureOut.replaceChildren(` ${fmt(r.L, 2)} x ${fmt(r.W, 2)} mm measured (this job only; Save to keep it) `,
          h("img", { src: "data:image/png;base64," + r.png, style: "width:120px;vertical-align:middle;border-radius:4px" }));
        toast(`${fmt(r.L, 2)} x ${fmt(r.W, 2)} mm: kept in this job's feeder, not in the library`, "ok");
      } catch (e) { measureOut.textContent = ""; toast(e.message, "bad"); }
    };
    const vis = h("select", { onchange: (e) => set(14, e.target.value) }, [["0", "No action"], ["1", "Individual (one nozzle per picture)"], ["2", "Joint (all nozzles in one picture)"], ["3", "Large component"]].map(([v, t]) =>
      h("option", { value: v, selected: get(14) === v }, t)));

    const form = h("div", { class: "card seform" },
      h("div", { class: "row", style: "margin-top:0" }, h("h3", { style: "margin:0" }, `Feeder ${id}`), h("span", { class: "muted" }, tray ? "tray" : `feeder ${fid}, peeler ${pid}`),
        h("div", { class: "grow" }), yn(skipI, "Skip")),
      h("fieldset", {}, h("legend", {}, "Feeder basic information"),
        field("Parts", this.partsSelect(jf, id)),
        field("Footprint", txt(6, 120)), field("Value", txt(7, 170)),
        field("Feeder ID exchange", exch, h("button", { class: "btn small", onclick: () => this.exchange(jf, id, +exch.value) }, "Exchange"))),
      h("fieldset", {}, h("legend", {}, tray ? "Tray" : "Pick position"),
        field(tray ? "Start X / Y" : "Pick X / Y", num(3), num(4),
          h("button", { class: "btn small", title: "head camera over this point", onclick: () => camTo(3, 4) }, "Align"),
          h("button", { class: "btn small", title: "the camera crosshair is on it", onclick: () => hereXY(3, 4) }, "Set here")),
        tray ? field("Right top X / Y", num(18), num(19), h("button", { class: "btn small", onclick: () => camTo(18, 19) }, "Align"),
          h("button", { class: "btn small", onclick: () => hereXY(18, 19) }, "Set here")) : null,
        tray ? field("Columns / Rows", num(16, "1", 60, 0), num(17, "1", 60, 0)) : null,
        tray ? this.trayTools(get, set, camTo, cam, need) : null,
        tray ? this.bulkPick(get, set, bulkI) : null,
        field("Pick angle", num(5, "0.01", 70), ...[-180, -90, 0, 90, 180].map((a) => h("button", { class: "btn small" + (Math.abs(+get(5) - a) < 0.01 ? " on" : ""),
          onclick: (e) => { set(5, a.toFixed(2)); inputs[5].value = a.toFixed(2); e.target.parentNode.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b === e.target)); } },
          (a > 0 ? "+" : "") + a + "°"))),
        tray ? null : field("", yn(pocketI, "find the pocket with the camera before every pick (the pick point follows the tape)"))),
      tray ? null : h("fieldset", {}, h("legend", {}, "Feeder information"),
        field("Feed rate (mm)", num(16, "1", 60, 0)), field("Feed torque", num(17, "1", 60, 0)), field("Peel strength", num(18, "1", 60, 0)),
        h("div", { class: "row" }, h("button", { class: "btn", title: "peel, then feed one step with these values; a new picture after it", onclick: feedTest }, "Feed test"),
          h("span", { class: "muted" }, `peel length ${peelLen()} mm (Settings → Peelers)`))),
      h("fieldset", {}, h("legend", {}, "Nozzle information"), [1, 2, 3, 4].map(nozRow),
        bp ? field("Pick height", h("span", { class: "muted", style: "font-size:12px" }, `the bulk feeder's (${bp.z() > 0 ? fmt(bp.z(), 2) + " mm down" : "not set"}): Bulk feeders page`))
          : field("Pick height (mm)", num(8)), field("Pick delay (ms)", num(9, "1", 80, 0)),
        field("Place height (mm)", num(10)), field("Place delay (ms)", num(11, "1", 80, 0)),
        field("Vacuum", yn(12, "check"), h("span", { class: "muted", style: "font-size:12px" }, "the threshold is automatic: each pick measures the open-air and sealed levels (seal integral)")),
        field("Vision", vis, yn(sizeI, "size check (all its parts; a part can say otherwise: Components → Size check)")),
        field("Second look", ynOn(lookI, "for Joint (4-up) parts: see, turn, see (turned to its place angle, photographed again, turned until it reads right; nothing turns after the last look). Parts shot one by one always get it (Settings → Job)")),
        field("Part size L x W, H (mm)", num(partL, "0.05", 70), num(partW, "0.05", 70), num(partH, "0.05", 60), this.pkgPicker(get, set, inputs, partL, partW, partH),
          h("button", { class: "btn small", title: "picks a part from this feeder, measures it on the bottom camera, puts it back: the size is kept in this job only (not the library)", onclick: measurePart }, "Measure from the part"), measureOut,
          h("span", { class: "muted", style: "font-size:12px" }, "with size check: the up camera measures the part; a part of another size (picked on its side) goes to the trash and another is picked. H: the part's height (the Joint picture's perspective correction). Empty / 0 = by the feeder's footprint in the library")),
        field("Move speed (%)", num(15, "10", 60, 0)),
        field("Pad alignment", yn(padI, "for all its parts: the head camera finds the pads on the board before placing and corrects the place point"),
          h("span", { class: "muted", style: "font-size:12px" }, "(a part can say otherwise: Components → Pad align)")),
        field("After placing", h("select", { onchange: (e) => set(checkI, e.target.value) },
          [["", "no check"], ["vacuum", "vacuum: the part did not come back on the nozzle"], ["photo", "photo: the head camera checks where it landed"], ["both", "both"]]
            .map(([v, t]) => h("option", { value: v, selected: get(checkI).toLowerCase() === v }, t))),
          h("span", { class: "muted", style: "font-size:12px" }, "for all its parts (a part can say otherwise: Components → After placing; all off: Settings → Job)")),
        this.learnt(id)),
      this.testBox(jf, id, get));
    wrap.append(camCol, form);
    if (!Cfg.loaded) Cfg.load().catch(() => {});
    // live picture (and live pocket finding) while this page is open; the lamp on when we have control
    // a feeder chosen in the list: the camera goes to its pick point by itself (lamp on, live picture)
    if (this._embed) return;                                // inside a bulk feeder's form: its camera is the one
    (async () => {
      const go = this.autoGo; this.autoGo = false;
      if (go && JobRun.runState() === "idle" && (API.st.haveControl || (await API.take()))) return camTo(3, 4);
      if (API.st.haveControl && JobRun.runState() === "idle") await lamp();
      cam.start();
    })();
  },

  // the size from the package library: search (with suggestions as you type), the packages that match
  // the feeder's footprint / value as buttons, and the size typed here saved into the library
  pkgPicker(get, set, inputs, iL, iW, iH) {
    const nameLbl = h("span", { class: "muted", style: "font-size:12px" });
    // the library's name for the size now in L / W (the feeder's footprint first, then any same size)
    const showName = () => {
      const l = +inputs[iL].value, w = +inputs[iW].value;
      if (!(l > 0 && w > 0)) { nameLbl.textContent = "no size: the footprint library by the feeder's footprint"; return; }
      const same = (p) => Math.abs(p.l - l) < 0.011 && Math.abs(p.w - w) < 0.011 || Math.abs(p.l - w) < 0.011 && Math.abs(p.w - l) < 0.011;
      const hit = PkgLib.suggest(`${get(6)} ${get(7)}`, 20).find(same) || PkgLib.all().find(same);
      nameLbl.textContent = hit ? `= ${hit.name}${hit.mine ? " (machine library)" : ""}` : "= own size (not in the library)";
    };
    const take = (p) => {
      set(iL, (+p.l).toFixed(2)); set(iW, (+p.w).toFixed(2));
      inputs[iL].value = (+p.l).toFixed(2); inputs[iW].value = (+p.w).toFixed(2);
      if (iH !== undefined && +p.h > 0) { set(iH, (+p.h).toFixed(2)); inputs[iH].value = (+p.h).toFixed(2); }
      showName();
      toast(`${p.name}: ${fmt(p.l, 2)} x ${fmt(p.w, 2)} mm. Save to keep it`, "ok");
    };
    [iL, iW].forEach((i) => inputs[i].addEventListener("change", showName));
    // our own drop-down list (the browser's datalist opens badly in the kiosk)
    const drop = h("div", { class: "pkgdrop", hidden: true });
    let items = [], hi = 0;
    const draw = () => {
      drop.replaceChildren(...items.map((p, k) => h("div", { class: "pkgitem" + (k === hi ? " on" : ""), onmousedown: (e) => { e.preventDefault(); pick(p); } },
        h("b", {}, p.name), h("span", {}, `${fmt(p.l, 2)} x ${fmt(p.w, 2)} mm`), p.mine ? h("span", { class: "muted" }, "machine") : null)));
      drop.hidden = !items.length;
    };
    const fill = () => { const q = search.value.trim(); items = q ? PkgLib.suggest(q, 30) : PkgLib.all(); hi = 0; draw(); };
    const pick = (p) => { take(p); search.value = ""; items = []; draw(); search.blur(); };
    const search = h("input", { type: "search", placeholder: "search the library (0603, SOT-23, QFN …)", style: "width:240px", autocomplete: "off",
      onfocus: fill, oninput: fill, onblur: () => setTimeout(() => { drop.hidden = true; }, 150),
      onkeydown: (e) => {
        if (e.key === "ArrowDown") { e.preventDefault(); hi = Math.min(items.length - 1, hi + 1); draw(); drop.children[hi]?.scrollIntoView({ block: "nearest" }); }
        else if (e.key === "ArrowUp") { e.preventDefault(); hi = Math.max(0, hi - 1); draw(); drop.children[hi]?.scrollIntoView({ block: "nearest" }); }
        else if (e.key === "Enter") { e.preventDefault(); if (items[hi]) pick(items[hi]); }
        else if (e.key === "Escape") { drop.hidden = true; }
      } });
    const chips = h("span", { style: "display:inline-flex;gap:4px;flex-wrap:wrap" });
    const save = h("button", { class: "btn small", title: "save this L x W in the machine's library (Config/pkg.csv)", onclick: async () => {
      const name = prompt("Name in the library", get(6) || (get(7).split("/")[0] || ""));
      if (!name) return;
      try { await PkgLib.save(name, +inputs[iL].value, +inputs[iW].value, iH !== undefined ? +inputs[iH].value || 0 : 0); toast(`${name} saved in the library`, "ok"); showName(); }
      catch (e) { toast(e.message, "bad"); }
    } }, "Save to library");
    PkgLib.load().then(() => {
      const sg = PkgLib.suggest(`${get(6)} ${get(7)}`, 4, 30);         // strong matches only (a name / alias)
      chips.replaceChildren(...sg.map((p) => h("button", { class: "btn small", title: `${p.name}: ${fmt(p.l, 2)} x ${fmt(p.w, 2)} mm`, onclick: () => take(p) }, `${p.name.split(" ")[0]} ${fmt(p.l, 1)}x${fmt(p.w, 1)}`)));
      showName();
    });
    return h("span", { style: "display:inline-flex;gap:6px;flex-wrap:wrap;align-items:center" }, nameLbl,
      h("span", { class: "pkgwrap" }, search, drop), chips, save);
  },

  // the pick point learnt for this feeder (<job>.picklearn.json): shown, and reset
  learnt(id) {
    const out = h("span", { class: "muted", style: "font-size:12px" }, "…");
    const path = (JobFile.path || JobRun.path || "") + ".picklearn.json";
    const show = (d) => {
      const e = (d || {})[String(id)];
      out.replaceChildren(e && (e.dx || e.dy) ? `${fmt(e.dx, 3)}, ${fmt(e.dy, 3)} mm added to the pick point (from ${e.n} parts) ` : "nothing learnt yet ",
        e ? h("button", { class: "btn small", onclick: async () => { delete d[String(id)];
          try { await API.req("PUT", `/api/file?path=${encodeURIComponent(path)}`, JSON.stringify(d, null, 1)); toast("learnt pick point reset", "ok"); show(d); } catch (er) { toast(er.message, "bad"); } } }, "Reset") : null);
    };
    API.req("GET", `/api/file?path=${encodeURIComponent(path)}`).then(show).catch(() => show({}));
    return h("div", { class: "sefield" }, h("span", {}, "Learnt pick point"), out);
  },

  // the bulk feeders (containers of loose parts, Machine settings -> Bulk feeders) in the feeder list:
  // which part each holds (a special feeder, tray type, is made for it: field 34 = the container)
  bulkStack(jf, cid) {
    const t = jf.lines.map((l) => l.split(",")).find((t) => t[0].trim() === "stack" && +t[2] === 1 && +(t[35] || 0) === +cid);
    return t ? +t[1] : 0;
  },
  bulkList(jf, parts) {
    if (!this.bulk) {
      if (!this._bulkLoading) {
        this._bulkLoading = true;
        API.req("GET", "/api/bulk/list").then((d) => { this.bulk = d.containers || []; App.show(); })
          .catch(() => { this.bulk = []; }).finally(() => { this._bulkLoading = false; });
      }
      return h("div", { class: "selist" }, h("div", { class: "muted", style: "margin:4px 0 2px" }, "Bulk feeders …"));
    }
    return h("div", { class: "selist" }, h("div", { class: "muted", style: "margin:4px 0 2px" }, "Bulk feeders"),
      ...this.bulk.map((c) => {
        const sid = this.bulkStack(jf, c.id), on = this.sel === "b" + c.id;
        const g = sid ? this.groups(jf).find((x) => x.stacks.has(sid)) : null;
        return h("div", { class: "seitem" + (on ? " on" : "") + (sid ? "" : " unused"), onclick: () => { this.sel = "b" + c.id; this.autoGo = true; App.show(); } },
          h("b", {}, "B" + c.id), h("span", {}, g ? g.value : c.name || "—"), sid && parts[sid] ? h("span", { class: "muted" }, `${parts[sid]}`) : null);
      }),
      this.bulk.length ? null : h("div", { class: "muted", style: "font-size:12px" }, "none: Machine settings → Bulk feeders"));
  },
  renderBulk(body, jf) {
    const cid = +String(this.sel).slice(1);
    const tmp = h("div");
    this._listOnly = true;
    try { this.render(tmp, jf); } finally { this._listOnly = false; }
    const wrap = h("div", { class: "stackedit" }, tmp.querySelector(".selcol"));
    body.append(wrap);
    const c = (this.bulk || []).find((x) => +x.id === cid);
    if (!c) { wrap.append(h("div", { class: "card" }, `Bulk feeder ${cid} is not defined (Machine settings → Bulk feeders)`)); return; }
    const need = async () => API.st.haveControl || (await API.take());
    const reload = async () => { try { const d = await API.req("GET", "/api/bulk/list"); this.bulk = d.containers || []; } catch (e) { /* keep */ } App.show(); };
    const saveC = async (fields, msg) => {
      if (!(await need())) return false;
      try { await API.req("POST", `/api/bulk/save?client=${API.st.client}`, JSON.stringify({ id: c.id, ...fields })); Object.assign(c, fields); if (msg) toast(msg, "ok"); return true; }
      catch (e) { toast(e.message, "bad"); return false; }
    };
    let sid = this.bulkStack(jf, cid);
    // the same tools as any feeder (nozzles, Height / Pick test, heights and delays, vacuum, vision, size,
    // Second look, Measure from the part, After placing, Placement test), on this block's special feeder;
    // the pick point is the part of the last scan nearest the middle, at the bulk feeder's pick height
    let more = [];
    if (sid) {
      const mid = [(+c.x0 + +c.x1) / 2, (+c.y0 + +c.y1) / 2];
      this._bulkPt = {
        xy: () => { const p = (c.parts || []).slice().sort((a, b) => Math.hypot(a.x - mid[0], a.y - mid[1]) - Math.hypot(b.x - mid[0], b.y - mid[1]))[0]; return p ? [+p.x, +p.y] : null; },
        z: () => (+c.z_pick || +c.z_seal || 0) + 1.0,          // as the job picks: 1 mm into the part's contact
      };
      const keep = [this.sel, this.autoGo, this.cam];
      const tmp2 = h("div");
      this.sel = sid; this._embed = true;
      try { this.render(tmp2, jf); } finally { this.sel = keep[0]; this.autoGo = keep[1]; this.cam = keep[2]; this._embed = false; this._bulkPt = null; }
      more = [...tmp2.querySelectorAll("fieldset")].filter((f) => /^(Nozzle information|Placement test)$/.test((f.querySelector("legend") || {}).textContent || ""));
      more.forEach((f, i) => { const lg = f.querySelector("legend"); lg.textContent = `${7 + i}. ${lg.textContent}`; });
    }
    const makeStack = () => {                                 // the special feeder for this block (tray type)
      const ids = new Set(this.ids(jf));
      let n = 49; while (ids.has(n) && n <= 98) n++;
      if (n > 98) { toast("no free special feeder number (49 .. 98)", "bad"); return 0; }
      this.add(jf, n);
      const no = this.lineNo(jf, n);
      jf.setField(no, 34, String(cid));                       // the bulk feeder
      jf.setField(no, 14, "1");                               // vision: one by one (the underside is checked)
      return n;
    };
    const field = (label, ...el) => h("div", { class: "sefield" }, h("span", {}, label), ...el);
    const note = (t) => h("div", { class: "muted", style: "font-size:12px;margin:2px 0 6px" }, t);
    const cx = (+c.x0 + +c.x1) / 2, cy = (+c.y0 + +c.y1) / 2;
    // Skip, as on any feeder: its special feeder's skip field (tray: 22); none until a part is chosen
    const bulkSkip = () => {
      if (!sid) return null;
      const no = this.lineNo(jf, sid), t = jf.lines[no].split(",");
      return h("label", { title: "the job leaves this bulk feeder's parts unplaced" }, h("input", { type: "checkbox", checked: /^(yes|1|true)$/i.test((t[23] || "").trim()),
        onchange: (e) => { jf.setField(this.lineNo(jf, sid), 22, e.target.checked ? "Yes" : "No"); toast(e.target.checked ? `bulk feeder ${cid} skipped: Save the job` : `bulk feeder ${cid} used: Save the job`, "ok"); } }), " Skip");
    };

    // ---- the part ----
    const partSel = h("select", { style: "max-width:440px", onchange: (e) => {
      if (!e.target.value) return;
      if (!sid) sid = makeStack();
      if (sid) { this.moveParts(jf, e.target.value, sid); this.sel = "b" + cid; App.show(); }
    } }, this.partOptions(jf, sid));

    // ---- the camera on this block: the parts outlined live ----
    const cam = Manual.coordCam(1);
    this.cam = cam;
    let found = [], fsize = 0, busy = false;
    const seen = h("span", { class: "muted" }, "");
    let lastDetect = 0;
    const detect = async () => {
      if (busy || performance.now() - lastDetect < 500) return; busy = true; lastDetect = performance.now();
      try { const r = await API.req("GET", "/api/cam/bulkparts?cam=1"); found = r.found || []; fsize = r.size; seen.textContent = `${found.length} part(s) in the picture`; cam._draw(); }
      catch (e) { /* no picture yet */ }
      busy = false;
    };
    const grab0 = cam.grab.bind(cam);
    cam.grab = async () => { const ok = await grab0(); if (ok) detect(); return ok; };
    cam.overlay = (g, toC, sc) => {
      if (!fsize) return;
      const k = Cfg.pxmm(1) * cam.size / fsize;
      const [ex] = toC(cam.size / 2 + 1, 0), [e0] = toC(cam.size / 2, 0), s = ex - e0;
      for (const f of found) {
        const [px, py] = toC(f.px * cam.size / fsize, f.py * cam.size / fsize);
        g.save(); g.translate(px, py); g.rotate((-f.angle * Math.PI) / 180);
        g.strokeStyle = "#3ce03c"; g.lineWidth = 2 * sc;
        g.strokeRect(-f.w * k * s / 2, -f.h * k * s / 2, f.w * k * s, f.h * k * s);
        g.restore();
      }
    };
    const camTo = async (x, y) => {
      if (!(await need())) return;
      await API.run("led", { v: 3 });
      if (await API.run("move", { x: (x + Cfg.offset(0)[0]).toFixed(3), y: (y + Cfg.offset(0)[1]).toFixed(3), slow: 1 })) { await App.poll(); cam.start(); }
    };
    const big = Math.max(+c.x1 - +c.x0, +c.y1 - +c.y0) > 8;  // more than one picture (8.9 mm)
    const scanOut = h("div");
    const showScan = () => {
      if (!c.parts) { scanOut.replaceChildren(note("not scanned yet")); return; }
      const ok = c.confirmed && +c.confirmed.count === c.parts.length;
      scanOut.replaceChildren(
        h("img", { src: `/api/bulk/mosaic?id=${c.id}&t=${Date.now()}`, style: "width:100%;max-width:420px;border-radius:4px;display:block", onerror: (e) => (e.target.style.display = "none") }),
        h("div", { class: "row" }, h("b", {}, `${c.parts.length} part(s) found`), h("span", { class: "muted" }, c.scanned || ""),
          ok ? h("span", { style: "color:var(--ok)" }, `✓ checked ${c.confirmed.time}`) :
            h("button", { class: "btn small primary", onclick: async () => { if (await saveCnf()) showScan(); } }, `Correct: ${c.parts.length} parts`),
          h("button", { class: "btn small", onclick: scan }, "Not right: scan again")));
    };
    const saveCnf = async () => {
      if (!(await need())) return false;
      try { await API.req("POST", `/api/bulk/confirm?client=${API.st.client}&id=${c.id}&count=${c.parts.length}`); c.confirmed = { count: c.parts.length, time: "now" }; toast("count checked", "ok"); return true; }
      catch (e) { toast(e.message, "bad"); return false; }
    };
    const scan = async () => {
      if (!(await need())) return;
      scanOut.replaceChildren(note("scanning this bulk feeder …"));
      try { await API.req("POST", `/api/bulk/scan?client=${API.st.client}&id=${c.id}`); const d = await API.req("GET", "/api/bulk/list"); this.bulk = d.containers || []; Object.assign(c, this.bulk.find((x) => +x.id === cid) || {}); showScan(); }
      catch (e) { scanOut.replaceChildren(); toast(e.message, "bad"); }
    };
    showScan();

    // ---- the direction ----
    const src = c.dir_source || (c.orient === "fixed" ? "laid" : "top");
    const teachOut = h("div");
    const teach = async () => {
      const p = (c.parts || []).slice().sort((a, b) => Math.hypot(a.x - cx, a.y - cy) - Math.hypot(b.x - cx, b.y - cy))[0];
      if (!p) return toast("scan the bulk feeder first", "warn");
      const name = prompt("Package name (the part must lie the way the Pick angle means)", c.package || (this.groups(jf).find((g) => g.stacks.has(sid)) || {}).footprint || "");
      if (!name || !(await need())) return;
      teachOut.replaceChildren(note("the part is picked, its underside photographed on the bottom camera, then put back …"));
      try {
        const r = await API.req("POST", `/api/bulk/teach?client=${API.st.client}&id=${c.id}&x=${p.x}&y=${p.y}&name=${encodeURIComponent(name)}&nozzle=${c.nozzle || 1}`);
        c.package = r.name;
        teachOut.replaceChildren(h("div", {}, h("b", {}, `${r.name} taught: ${r.bottom_metal} metal pieces underneath`)),
          h("div", { class: "row" }, h("img", { src: "data:image/png;base64," + r.png_top, style: "width:48%" }), h("img", { src: "data:image/png;base64," + r.png_bottom, style: "width:48%" })));
      } catch (e) { teachOut.replaceChildren(); toast(e.message, "bad"); }
    };
    const opt = (v, title, text) => h("label", { class: "bulkopt", style: "display:block;margin:4px 0;padding:6px 8px;border:1px solid var(--line);border-radius:6px" + (src === v ? ";border-color:var(--accent)" : "") },
      h("input", { type: "radio", name: "bulkdir", checked: src === v, onchange: async () => { if (await saveC({ dir_source: v }, "saved on the bulk feeder")) App.show(); } }),
      " ", h("b", {}, title), h("div", { class: "muted", style: "font-size:12px;margin-left:22px" }, text));
    const taught = c.package ? `taught: ${c.package}` : "not taught yet";

    // ---- pick angle (the special feeder's), what to do when a part is not picked ----
    const no = sid ? this.lineNo(jf, sid) : -1;
    const pickAng = h("input", { type: "number", step: "0.01", style: "width:75px", disabled: !sid,
      value: no < 0 ? "" : (jf.lines[no].split(",")[6] || "").trim(),
      onchange: (e) => { jf.setField(this.lineNo(jf, sid), 5, (+e.target.value).toFixed(2)); toast("Save the job to keep it", "ok"); } });
    const angBtns = [-90, 0, 90, 180].map((a) => h("button", { class: "btn small", disabled: !sid, onclick: () => { pickAng.value = a.toFixed(2); pickAng.onchange({ target: pickAng }); } }, (a > 0 ? "+" : "") + a + "°"));
    const fail = h("select", { onchange: (e) => saveC({ on_fail: e.target.value }, "saved on the bulk feeder") },
      [["next", "take the next part (after 3 misses in a row the operator is asked)"], ["retry", "try the same part once more, then the next"],
       ["ask", "stop and ask the operator at once"], ["skip", "skip this component (it stays unplaced)"]]
        .map(([v, t]) => h("option", { value: v, selected: (c.on_fail || "next") === v }, t)));

    wrap.append(h("div", { class: "card seform", style: "grid-column: 2 / -1" },
      h("div", { class: "row", style: "margin-top:0" }, h("h3", { style: "margin:0" }, `Bulk feeder ${c.id}` + (c.row ? ` · row ${c.row}, column ${c.col}` : "")),
        h("span", { class: "muted" }, c.name || ""), h("div", { class: "grow" }), bulkSkip(), h("a", { href: "#bulk", class: "btn small" }, "Bulk feeders page (place, package, height)")),
      h("fieldset", {}, h("legend", {}, "1. Part"),
        field("Part of the board", partSel), note("Every component of this kind is taken from this bulk feeder." + (sid ? ` (Its special feeder is ${sid}.)` : ""))),
      h("div", { style: "display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:10px;align-items:start" },
        h("fieldset", {}, h("legend", {}, "2. Camera on this bulk feeder"), cam.el,
          h("div", { class: "row" }, h("button", { class: "btn small", onclick: () => camTo(cx, cy) }, "Camera to the middle"), seen),
          note("Green outlines: the parts the camera finds in this picture.")),
        h("fieldset", {}, h("legend", {}, "3. Scan " + (big ? "(larger than one picture)" : "")),
          h("button", { class: "btn", onclick: scan }, "Scan this bulk feeder"),
          note("Only this bulk feeder is photographed. Check that the number of parts found is right, then press Correct."), scanOut)),
      h("fieldset", {}, h("legend", {}, "4. Which way round the parts lie"),
        opt("laid", "I lay them all the same way (by hand)", "The Pick angle below says how they lie against the footprint. The camera only measures the small turn of each part; nothing is taught."),
        opt("top", "The machine finds it with the head camera (from above)", `The pin-1 mark / the leads on the top. Needs the package taught (${taught}).`),
        opt("bottom", "The machine finds it with the bottom camera (from below)", `Each part is picked and its leads / pads read on the bottom camera; for parts whose top shows nothing. Needs the underside taught (${taught}).`),
        src !== "laid" ? h("div", { class: "row" }, h("button", { class: "btn small", onclick: teach }, "Teach it from one part"),
          note("A part near the middle is picked, photographed from above and from below, and put back. It must lie the way the Pick angle means.")) : null, teachOut),
      h("fieldset", {}, h("legend", {}, "5. Pick angle"),
        field("Pick angle", pickAng, ...angBtns), note(src === "laid" ? "How the parts, as you lay them, lie against their footprint." : "How the taught part lay against its footprint.")),
      h("fieldset", {}, h("legend", {}, "6. When a part is not picked"), field("Then", fail)),
      ...more,
      sid ? null : note("Choose the part (1.) to get the nozzle, height, vision and test settings here.")));

    // the camera goes to this bulk feeder when it is chosen (the machine idle)
    (async () => {
      const go = this.autoGo; this.autoGo = false;
      if (go && JobRun.runState() === "idle" && (API.st.haveControl || (await API.take()))) return camTo(cx, cy);
      cam.start();
    })();
  },

  // a special feeder that takes its parts from a bulk feeder (Machine settings -> Bulk feeders)
  bulkPick(get, set, i) {
    const sel = h("select", { onchange: (e) => { set(i, e.target.value); toast("Save to keep it", "ok"); } }, h("option", { value: "0" }, "no: a tray"));
    API.req("GET", "/api/bulk/list").then((d) => (d.containers || []).forEach((c) =>
      sel.append(h("option", { value: String(c.id), selected: +get(i) === +c.id }, `${c.id}: ${c.name || ""} (${c.package || "not taught"})`)))).catch(() => {});
    return h("div", { class: "sefield" }, h("span", {}, "Bulk feeder"), sel,
      h("span", { class: "muted", style: "font-size:12px" }, "the parts come loose from that container: found and checked by the cameras, picked by the vacuum; Pick angle = how the taught part lay against the footprint"));
  },

  // a tray (special feeder): Start = the first part (left-bottom), Right top = the last one; the camera to
  // any cell to check the grid; which cell the next part is taken from (the job's counters, fields 20 / 21)
  trayTools(get, set, camTo, cam, need) {
    const cols = () => Math.max(1, +get(16) || 1), rows = () => Math.max(1, +get(17) || 1);
    const cell = (c, r) => {
      const x0 = +get(3), y0 = +get(4), x1 = +get(18), y1 = +get(19);
      return [cols() > 1 ? x0 + (c - 1) * (x1 - x0) / (cols() - 1) : x0, rows() > 1 ? y0 + (r - 1) * (y1 - y0) / (rows() - 1) : y0];
    };
    const cIn = h("input", { type: "number", min: 1, style: "width:55px", value: get(20) || 1 });
    const rIn = h("input", { type: "number", min: 1, style: "width:55px", value: get(21) || 1 });
    const goCell = async () => {
      if (!(await need())) return;
      const [x, y] = cell(+cIn.value || 1, +rIn.value || 1), o = Cfg.offset(0);
      await API.run("led", { v: +Cfg.get("PadAlign/nLed", 3) || 3 });
      if (await API.run("move", { x: (x + o[0]).toFixed(3), y: (y + o[1]).toFixed(3), slow: 1 })) { await App.poll(); cam.start(); }
    };
    return h("div", {},
      h("div", { class: "muted", style: "font-size:12px" }, "A tray is not a real feeder: Start = the camera on the first part (left-bottom cell), Right top = on the last part (right-top cell), then the columns and rows. Check any cell below."),
      h("div", { class: "sefield" }, h("span", {}, "Cell (column / row)"), cIn, rIn,
        h("button", { class: "btn small", title: "the head camera over that cell", onclick: goCell }, "Go"),
        h("button", { class: "btn small", title: "the next part is taken from this cell", onclick: () => { set(20, Math.max(1, Math.min(cols(), +cIn.value || 1))); set(21, Math.max(1, Math.min(rows(), +rIn.value || 1))); toast("the next part comes from this cell. Save to keep it", "ok"); } }, "Next part from here")));
  },

  // placement test: one part of this feeder placed where it belongs (marks, vision, pad alignment, all as in
  // the job), then pictures of it: the head camera over the placed part, the up camera of the pick
  testBox(jf, id, get) {
    const own = this.ownKind(jf, id), seen = {};
    const comps = jf.lines.map((l) => l.split(",")).filter((t) => t[0].trim() === "comp")
      .map((t) => { const nm = (t[3] || "").trim(); seen[nm] = (seen[nm] || 0) + 1; return { t, key: seen[nm] > 1 ? `${nm}#${seen[nm]}` : nm }; })
      .filter(({ t }) => +t[1] === id && (!own || ((t[4] || "").trim() + "|" + (t[5] || "").trim()) === own.key))
      .map(({ t, key }) => ({ name: key, value: (t[4] || "").trim(), x: +t[6], y: +t[7], rot: +t[8] }));
    const fx = +get(3), fy = +get(4);
    comps.sort((a, b) => Math.hypot(a.x - fx, a.y - fy) - Math.hypot(b.x - fx, b.y - fy));   // nearest the feeder first
    const sel = h("select", {}, comps.map((c, i) => h("option", { value: c.name }, `${c.name} ${c.value}  (${fmt(c.x, 1)}, ${fmt(c.y, 1)}, ${fmt(c.rot, 0)}°)${i ? "" : "  nearest"}`)));
    const out = h("div", { class: "muted", style: "font-size:12px;white-space:pre-wrap;max-height:140px;overflow:auto" });
    const imgs = h("div", { style: "display:grid;grid-template-columns:1fr 1fr;gap:6px" });
    const btn = h("button", { class: "btn primary", disabled: !comps.length }, "Test place");
    const pic = async (cam, title) => {
      try {
        const r = await API.req("GET", `/api/job/image?cam=${cam}&scale=1&t=${Date.now()}`, null, true);
        const u = URL.createObjectURL(await r.blob());
        return h("div", {}, h("div", { class: "muted", style: "font-size:12px" }, title), h("img", { src: u, style: "width:100%;border-radius:4px" }));
      } catch (e) { return h("div", { class: "muted" }, `${title}: no picture`); }
    };
    btn.onclick = async () => {
      const ref = sel.value;
      if (!ref) return;
      if (JobRun.runState() !== "idle") return toast("a job is running", "warn");
      if (jf.dirty) await jf.save();
      if (!API.st.haveControl && !(await API.take())) return;
      if (!confirm(`Place ${ref} on the board now (the first panel to be placed; the board marks are found first)?`)) return;
      btn.disabled = true; out.textContent = "starting …"; imgs.replaceChildren();
      try {
        await API.req("POST", `/api/job/start?path=${encodeURIComponent(jf.path)}&resume=0&only=${encodeURIComponent(ref)}&client=${API.st.client}`);
        let since = 0, s;
        do {
          await new Promise((r) => setTimeout(r, 700));
          s = await API.req("GET", `/api/job/status?since=${since}`);
          if (s.log && s.log.length) { out.textContent += "\n" + s.log.join("\n"); out.scrollTop = 1e9; since = s.log_n; }
          if (s.question) {
            const a = confirm(`${s.question.text}\n\nOK = ${s.question.buttons[0]}, Cancel = no`) ? s.question.buttons[0] : "no";
            await API.req("POST", `/api/job/answer?a=${encodeURIComponent(a)}`).catch(() => {});
          }
        } while (s.running);
        imgs.replaceChildren(await pic(1, "head camera: the placed part (is it turned the right way?)"), await pic(0, "up camera: the part on the nozzle"));
      } catch (e) { toast(e.message, "bad"); }
      btn.disabled = false;
    };
    return h("fieldset", {}, h("legend", {}, "Placement test"),
      h("div", { class: "muted", style: "font-size:12px" }, "One part of this feeder is placed where it belongs, as the job would; then the head camera shows it. Wrong turn? Change Pick angle (or the part's rotation) and test again."),
      h("div", { class: "row" }, sel, btn), out, imgs);
  },

  // arrows jog the head (not while typing): 0.1 mm, Shift 1 mm
  key(ev) {
    if (location.hash !== "#jobfile" || JobFile.tab !== "stacks" || this.tableView || !this.cam || !this.cam.el.isConnected) return;
    if (!CamView.keysOn() || /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) return;
    const d = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] }[ev.key];
    if (!d) return;
    ev.preventDefault();
    const s = ev.shiftKey ? 1 : 0.1;
    (async () => { if (API.st.haveControl || (await API.take())) { await API.run("rel", { x: d[0] * s, y: d[1] * s, slow: 1 }); await App.poll(); this.cam.grab(); } })();
  },
};
document.addEventListener("keydown", (e) => StackEdit.key(e));
