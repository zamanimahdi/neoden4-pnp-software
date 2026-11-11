// Job file settings (NeoDen4 frmFileEdit, "Edit" in the job list): the stacks, marks, panels and
// components of one Proc/*.csv job, edited in tables and written back line by line. Lines and fields
// this page does not know are kept unchanged (the file is read and written byte for byte).
// `origin,x,y,` (our line): the board's zero point in the part coordinates. With it the panel (mirror)
// positions give where that point is on the machine, so no dummy first part is needed. NeoDen4.exe
// ignores the line (it uses the first part instead), so such a file is read differently there.
function jobSetOrigin(lines, xy) {
  const i = lines.findIndex((l) => l.split(",")[0].trim() === "origin");
  if (!xy) { if (i >= 0) lines.splice(i, 1); return lines; }
  const line = `origin,${(+xy[0]).toFixed(3)},${(+xy[1]).toFixed(3)},`;
  if (i >= 0) { lines[i] = line; return lines; }
  let at = lines.findIndex((l) => /^(mirror_create|mirror|#SMD|comp),/.test(l.trim()));
  if (at < 0) at = lines.length;
  lines.splice(at, 0, line);
  return lines;
}
const JOB_EDIT_NOTE = "Changes made while the job is paused are used when you press Continuous: feeders (heights, delays, vacuum, vision type, speed, skip …) and parts (X, Y, rotation, nozzle, feeder, skip). Marks and panels are used from the next start. Adding, removing or reordering parts: stop and start again.";

const JobFile = {
  tab: "stacks",

  // [tag, title, [[field index after the tag, header, type, width]], filter(tokens)]
  kinds: {
    stacks: ["stack", "Stack setup", [[0, "Feeder ID", "i", 50], [1, "Type (0 tape, 1 tray)", "i", 40], [2, "Nozzles", "s", 50], [3, "X", "f", 70], [4, "Y", "f", 70],
      [5, "Angle", "f", 60], [6, "Footprint", "s", 90], [7, "Value", "s", 140], [8, "Pick height", "f", 60], [9, "Pick delay", "i", 55],
      [10, "Place height", "f", 60], [11, "Place delay", "i", 55], [12, "Vacuum detection", "yn", 60], [13, "Threshold", "i", 55],
      [14, "Vision", "vis", 0], [15, "Speed", "i", 50], [(t) => (t[2] === "1" ? 22 : 19), "Skip", "yn", 0]]],
    comps: ["comp", "Components", [[0, "Feeder", "i", 50], [1, "Nozzle", "i", 45], [2, "Name", "s", 80], [3, "Value", "s", 130], [4, "Footprint", "s", 100],
      [5, "X", "f", 75], [6, "Y", "f", 75], [7, "Rotation", "f", 65], [8, "Skip", "yn", 55], [9, "Pad align", "pa", 0], [10, "Size check", "pa", 0], [11, "After placing", "pc", 0]]],
    panels: ["mirror", "Panels", [[0, "X", "f", 75], [1, "Y", "f", 75], [2, "Rotation (0 / 180)", "i", 60], [3, "Skip", "nya", 70]]],
  },

  async render(root) {
    const st = await API.req("GET", "/api/status").catch(() => ({}));
    const proc = st.neoden_dir ? PathU.join(st.neoden_dir, "Proc") : "";
    const list = await API.req("GET", `/api/files?path=${encodeURIComponent(proc)}`).catch(() => ({ entries: [] }));
    const files = (list.entries || []).filter((e) => !e.dir && /\.csv$/i.test(e.name)).map((e) => PathU.join(proc, e.name)).sort();
    if (!files.includes(JobRun.path)) JobRun.path = files[0] || "";
    const sel = h("select", { onchange: async (e) => {
      if (this.dirty && !confirm("The changes are not saved. Open another file anyway?")) { e.target.value = JobRun.path; return; }
      JobRun.path = e.target.value; localStorage.setItem("jobPath", JobRun.path); this.lines = null; App.show();
    } }, files.map((p) => h("option", { value: p, selected: p === JobRun.path }, p.split(/[\\/]/).pop())));
    if (!JobRun.path) { root.append(h("div", { class: "card muted" }, "No job files in Proc yet. Make one with the setup wizard.")); return; }
    if (!this.lines || this.path !== JobRun.path) await this.load(JobRun.path);
    this.saveBtn = h("button", { class: "btn" + (this.dirty ? " primary" : ""), onclick: () => this.save() }, "Save");
    root.append(h("div", { class: "card" }, h("div", { class: "row", style: "margin:0" }, h("label", {}, "Job file "), sel,
      h("div", { class: "grow" }), this.dirtyLbl = h("span", { class: "muted" }, this.dirty ? "unsaved changes" : ""), this.saveBtn,
      h("button", { class: "btn", title: "feed amount / torque from the machine's feeder table into this job's feeders; each part's feeder and nozzle by footprint / value",
        onclick: () => this.assign() }, "Assign feeders & nozzles"),
      h("button", { class: "btn", onclick: () => { if (!this.dirty || confirm("Discard the changes?")) { this.lines = null; App.show(); } } }, "Reload"),
      h("button", { class: "btn primary", onclick: () => { if (this.dirty && !confirm("The changes are not saved. Mount anyway?")) return; JobRun.mount(JobRun.path); location.hash = "#job"; } }, "Mount ▶"))));
    if (JobRun.mounted && JobRun.path === this.path) root.append(h("div", { class: "card", style: "border-color:var(--accent)" }, h("b", {}, "This job is mounted. "), JOB_EDIT_NOTE));
    const tabs = [["pcb", "PCB info"], ["stacks", "Feeder settings"], ["comps", "Components"], ["board", "Board"], ["panels", "Panels"], ["raw", "Raw file"]];
    root.append(h("div", { class: "tabs" }, tabs.map(([k, t]) => h("button", { class: "btn" + (k === this.tab ? " on" : ""), onclick: () => { this.tab = k; App.show(); } }, t))));
    const body = h("div", { class: "card" });
    root.append(body);
    if (this.tab === "panels") this.panelize(body);
    if (this.tab === "stacks" && !StackEdit.tableView) StackEdit.render(body, this);
    else if (this.kinds[this.tab]) this.table(body, this.kinds[this.tab]);
    else this[this.tab](body);
  },

  async load(path) {
    const r = await API.req("GET", `/api/file?path=${encodeURIComponent(path)}`, null, true);
    this.path = path;
    this.lines = INI.fromBytes(await r.arrayBuffer()).replace(/\r\n?/g, "\n").split("\n");
    while (this.lines.length && this.lines[this.lines.length - 1] === "") this.lines.pop();
    this.dirty = false;
  },
  touch() { this.dirty = true; if (this.saveBtn) this.saveBtn.classList.add("primary"); if (this.dirtyLbl) this.dirtyLbl.textContent = "unsaved changes"; },
  setField(no, i, v) { const t = this.lines[no].split(","); while (t.length <= i + 1) t.push(""); t[i + 1] = v; this.lines[no] = t.join(","); this.touch(); },

  // the same as neojob/assign.py (it also runs at every job start): feeder data the job's stack lines lack
  // from Settings -> Feeders / Peelers (fields 16..18; the job comes first), each part's feeder / nozzle
  async assign() {
    if (!Cfg.loaded) await Cfg.load().catch(() => {});
    const norm = (s) => String(s || "").trim().replace(/\s+/g, " ").toLowerCase();
    const key = (fp, val) => { fp = norm(fp); val = norm(val); if (!fp && val.includes("/")) { const i = val.indexOf("/"); fp = val.slice(0, i).trim(); val = val.slice(i + 1).trim(); } return fp + "|" + val; };
    // a stack without a footprint matches on the value alone; a part on a matching stack stays there
    const fits = (s, fp, val) => { const [sf, sv] = key(s.t[7], s.t[8]).split("|"); return sv === norm(val) && (!sf || sf === norm(fp)); };
    const stacks = {};
    this.lines.forEach((l, no) => { const t = l.split(","); if (t[0].trim() === "stack" && t.length > 15) stacks[+t[1]] = { t, no }; });
    let nfd = 0, nparts = 0; const missing = [], moved = [];
    for (const [sid, s] of Object.entries(stacks)) {
      if (+s.t[2] !== 0) continue;                                         // tape only
      const fid = +Cfg.get(`Stack/lstStack${sid - 1}.nFeedId`, sid) || +sid, pid = +Cfg.get(`Stack/lstStack${sid - 1}.nPeelId`, sid) || +sid;
      let ch = false;
      [[17, `Feed/lstFeed${fid - 1}.nLoad`], [18, `Feed/lstFeed${fid - 1}.nMoment`], [19, `Peel/lstPeel${pid - 1}.nMoment`]].forEach(([i, k]) => {
        const v = Cfg.get(k, null);
        if (v === null || v === "" || !(+v > 0)) return;
        while (s.t.length <= i) s.t.push("");
        if (s.t[i].trim() !== "") return;                              // the job file has it: the job comes first
        s.t[i] = String(parseInt(v)); ch = true;
      });
      if (ch) { this.lines[s.no] = s.t.join(","); nfd++; }
    }
    const skip = (s) => /^yes$/i.test(((+s.t[2] === 1 ? s.t[23] : s.t[20]) || "").trim());
    this.lines.forEach((l, no) => {
      const t = l.split(",");
      if (t[0].trim() !== "comp" || t.length < 10 || /^(yes|1|true)$/i.test(t[9].trim())) return;
      let cur = stacks[+t[1]];
      if (!cur || !fits(cur, t[5], t[4])) {
        const cand = Object.values(stacks).filter((s) => fits(s, t[5], t[4]));
        const pick = cand.find((s) => !skip(s)) || cand[0];
        if (!pick) { if (!cur) missing.push(t[3]); return; }
        moved.push(`${t[3]} ${t[1]} → ${pick.t[1]}`); t[1] = pick.t[1]; cur = pick; nparts++;
      }
      const allowed = [...String(cur.t[3])].filter((c) => "1234".includes(c)).map(Number);
      if (allowed.length && !allowed.includes(+t[2])) { t[2] = String(allowed[0]); nparts++; }
      this.lines[no] = t.join(",");
    });
    this.touch(); App.show();
    toast(`Assign: feeder data on ${nfd} feeder(s), ${nparts} part change(s)` + (moved.length ? `; moved: ${moved.slice(0, 10).join(", ")}` : "") +
      (missing.length ? `; no feeder for ${missing.slice(0, 8).join(", ")}` : "") + ". Save to keep it.", missing.length || moved.length ? "warn" : "ok");
  },

  async save() {
    if (this.tab === "raw" && this.rawTa && this.rawTa.isConnected)        // the text box's edits, not lost
      this.lines = this.rawTa.value.replace(/\r\n?/g, "\n").split("\n");
    try {
      await API.req("PUT", `/api/file?path=${encodeURIComponent(this.path)}`, INI.toBytes(this.lines.join("\r\n") + "\r\n"));
      this.dirty = false; App.show(); toast("File saved.", "ok");
    } catch (e) { toast("Failed to save: " + e.message, "bad"); return; }
    try {                                 // a feeder the head cannot reach: said now, not halfway through a board
      const r = await API.req("GET", `/api/job/check?path=${encodeURIComponent(this.path)}`);
      if (r.problems && r.problems.length)
        alert(`Outside the machine's travel (the job will not start):\n\n${r.problems.slice(0, 12).join("\n")}${r.problems.length > 12 ? "\n..." : ""}\n\nMove these feeders, or skip them.`);
    } catch (e) { /* an older server: no check */ }
  },

  editor(type, value, set, width) {
    if (type === "vis") {
      return h("select", { onchange: (e) => set(e.target.value) }, [["0", "None"], ["1", "Single nozzle"], ["2", "Four nozzles"], ["3", "Large part"]].map(([v, t]) =>
        h("option", { value: v, selected: String(value).trim() === v }, t)));
    }
    if (type === "pa") {                  // pad alignment / size check of one part: as its feeder says, or Yes / No for this part
      const v = String(value || "").trim().toLowerCase();
      return h("select", { title: "Feeder = as the part's feeder says; Yes / No = for this part", onchange: (e) => set(e.target.value) },
        [["", "Feeder"], ["Yes", "Yes"], ["No", "No"]].map(([o, t]) => h("option", { value: o, selected: o.toLowerCase() === v }, t)));
    }
    if (type === "pc") {                  // the after-place check of one part: as its feeder says, or its own
      const v = String(value || "").trim().toLowerCase();
      return h("select", { title: "Feeder = as the part's feeder says; or this part's own check", onchange: (e) => set(e.target.value) },
        [["", "Feeder"], ["none", "None"], ["vacuum", "Vacuum"], ["photo", "Photo"], ["both", "Both"]].map(([o, t]) => h("option", { value: o, selected: o === v }, t)));
    }
    if (type === "yn" || type === "nya") {
      const opts = type === "yn" ? ["No", "Yes"] : ["No", "Yes", "Auto"];
      return h("select", { onchange: (e) => set(e.target.value) }, opts.map((o) => h("option", { selected: o.toLowerCase() === String(value).trim().toLowerCase() }, o)));
    }
    return h("input", { class: "cell" + (type === "s" ? " txt" : ""), style: `width:${width}px;text-align:${type === "s" ? "left" : "right"}`, value: value === undefined ? "" : value,
      type: type === "s" ? "text" : "number", step: type === "f" ? "0.01" : "1",
      onchange: (e) => set(type === "f" ? (+e.target.value).toFixed(2) : e.target.value) });
  },

  table(body, [tag, title, cols]) {
    const rows = this.lines.map((l, no) => [l.split(","), no]).filter(([t]) => t[0].trim() === tag);
    // group edit: tick rows (or all), then Skip / Unskip them, or give one column one value for all of them
    const gs = (this.gsel = this.gsel || {});
    const sel = (gs[tag] = new Set([...(gs[tag] || [])].filter((no) => rows.some(([, n]) => n === no))));
    const skipCol = cols.find((c) => c[1] === "Skip");
    const setAll = (col, v) => {
      if (!sel.size) return toast("tick the rows first", "warn");
      if (v === "" && !["pa", "pc"].includes(col[2])) return toast(`type the value for ${col[1]} first`, "warn");
      for (const [t, no] of rows) if (sel.has(no)) { const fi = typeof col[0] === "function" ? col[0](t) : col[0]; this.setField(no, fi, v); }
      toast(`${col[1]} = ${v || "Feeder"} for ${sel.size} row(s). Save to keep it`, "ok"); App.show();
    };
    let gcol = cols.find((c) => c[1] === this.gcolName) || skipCol || cols[0], gval = "";
    const gEditor = h("span");
    const showEditor = () => {
      gval = ["yn", "nya"].includes(gcol[2]) ? "No" : gcol[2] === "vis" ? "0" : "";      // what the editor shows first
      gEditor.replaceChildren(this.editor(gcol[2], "", (v) => { gval = v; }, gcol[3] || 70));
    };
    showEditor();
    const colSel = h("select", { onchange: (e) => { gcol = cols[+e.target.value]; this.gcolName = gcol[1]; showEditor(); } },
      cols.map((c, k) => h("option", { value: k, selected: c === gcol }, c[1])));
    const count = h("b", {});
    const needSel = [];
    const btn = (text, f) => { const b = h("button", { class: "btn", onclick: f }, text); needSel.push(b); return b; };
    const boxes = [];
    const refresh = () => {                                  // the counter and the buttons, without drawing the table again
      count.textContent = `${sel.size} selected`;
      needSel.forEach((b) => (b.disabled = !sel.size));
      allBox.checked = rows.length > 0 && sel.size === rows.length;
    };
    const allBox = h("input", { type: "checkbox", title: "all rows", onchange: (e) => {
      if (e.target.checked) rows.forEach(([, no]) => sel.add(no)); else sel.clear();
      boxes.forEach(([b, no]) => (b.checked = sel.has(no))); refresh();
    } });
    const group = h("div", { class: "row", style: "margin:4px 0;flex-wrap:wrap;gap:6px;align-items:center" }, count,
      skipCol ? btn("Skip selected", () => setAll(skipCol, "Yes")) : null,
      skipCol ? btn("Unskip selected", () => setAll(skipCol, "No")) : null,
      h("span", { class: "muted" }, "or set"), colSel, gEditor,
      btn("Apply to selected", () => setAll(gcol, gval)),
      btn("Clear selection", () => { sel.clear(); boxes.forEach(([b]) => (b.checked = false)); refresh(); }));
    refresh();
    const rowBox = (no) => { const b = h("input", { type: "checkbox", checked: sel.has(no),
      onchange: (e) => { if (e.target.checked) sel.add(no); else sel.delete(no); refresh(); } }); boxes.push([b, no]); return b; };
    body.append(h("div", { class: "row", style: "margin-top:0" }, h("h3", { style: "margin:0" }, `${title} (${rows.length})`), h("div", { class: "grow" }),
      tag === "stack" ? h("button", { class: "btn", title: "one feeder at a time, with the camera (NeoDen4's Feeder Settings)", onclick: () => { StackEdit.tableView = false; App.show(); } }, "Form view") : null,
      h("button", { class: "btn", onclick: () => this.addRow(tag, cols) }, "+ Add row")), group,
      h("div", { class: "scroll", style: "max-height:66vh" }, h("table", { class: "pick" },
        h("tr", {}, h("th", {}, allBox), h("th", {}), cols.map((c) => h("th", {}, c[1])), h("th", {})),
        rows.map(([t, no], i) => h("tr", {}, h("td", {}, rowBox(no)), h("th", {}, String(i + 1)),
          cols.map(([f, , type, w]) => { const fi = typeof f === "function" ? f(t) : f; return h("td", {}, this.editor(type, t[fi + 1], (v) => this.setField(no, fi, v), w)); }),
          h("td", {}, h("button", { class: "btn ghost", title: "delete this row", onclick: () => { if (confirm("Delete this row?")) { this.lines.splice(no, 1); this.touch(); App.show(); } } }, "✕")))))));
  },

  // Panelize (NeoDen4's panel grid, frmPcbEdit / CLstMirror::createGrid): the job holds ONE board; the
  // panel is columns x rows of it. Panel 1 is the reference (where the board's first part / zero point
  // is on the machine); the others are panel 1 + c * column step + r * row step, row by row (c
  // fastest), as `mirror` lines. The steps are typed (board pitch along X and Y) or taught with the
  // head camera on the same part of the left-bottom, left-top and right-top boards (a turned panel too).
  panelize(body) {
    const tags = (tag) => this.lines.map((l, no) => [l.split(","), no]).filter(([t]) => t[0].trim() === tag);
    const cur = tags("mirror");
    const mc = tags("mirror_create")[0];
    const p1 = cur[0] ? [+cur[0][0][1], +cur[0][0][2]] : [0, 0];
    const st = this.pz || (this.pz = { cols: mc ? +mc[0][1] || 1 : 1, rows: mc ? +mc[0][2] || 1 : 1, dx: 0, dy: 0, lb: null, lt: null, rt: null });
    if (!st.dx && cur.length > 1) st.dx = +(+cur[1][0][1] - p1[0]).toFixed(3);
    const n = (k, w = 70, step = "1") => h("input", { type: "number", step, style: `width:${w}px`, value: st[k], onchange: (e) => { st[k] = +e.target.value; } });
    let inDx, inDy;
    const camPt = () => { const q = (API.st.status || {}).pos; if (!q) return null; const o = Cfg.offset(0); return [+(q[0] - o[0]).toFixed(3), +(q[1] - o[1]).toFixed(3)]; };
    const need = async () => API.st.haveControl || (await API.take());
    const refSel = h("select", {}, h("option", { value: "" }, "— a part —"),
      tags("comp").map(([t]) => (t[3] || "").trim()).filter((r, i, a) => r && a.indexOf(r) === i).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
        .map((r) => h("option", { value: r, selected: r === st.ref }, r)));
    refSel.onchange = () => { st.ref = refSel.value; };
    const result = h("div", { class: "muted", style: "font-size:12px" });
    const showResult = () => {                               // the steps and the panel's turn from the taught points
      const C = Math.max(1, Math.round(st.cols)), Rr = Math.max(1, Math.round(st.rows));
      if (!st.lb) { result.textContent = ""; return; }
      const parts = [];
      if (Rr >= 2 && st.lt) { const r = [(st.lt[0] - st.lb[0]) / (Rr - 1), (st.lt[1] - st.lb[1]) / (Rr - 1)]; parts.push(`row step ${fmt(r[0], 3)}, ${fmt(r[1], 3)} mm`); st.dy = +Math.hypot(...r).toFixed(3); }
      if (C >= 2 && st.rt) { const from = Rr >= 2 && st.lt ? st.lt : st.lb; const c = [(st.rt[0] - from[0]) / (C - 1), (st.rt[1] - from[1]) / (C - 1)];
        parts.push(`column step ${fmt(c[0], 3)}, ${fmt(c[1], 3)} mm`, `panel turned ${fmt(Math.atan2(c[1], c[0]) * 180 / Math.PI, 3)}°`); st.dx = +Math.hypot(...c).toFixed(3); }
      result.textContent = parts.length ? "→ " + parts.join(" · ") + " (used by Make panels)" : "";
      inDx.value = st.dx; inDy.value = st.dy;
    };
    const estimate = (k) => {                                // where that board's part should be (from what is known)
      const C = Math.max(1, Math.round(st.cols)), Rr = Math.max(1, Math.round(st.rows));
      if (!st.lb) return null;
      if (k === "lt") return [st.lb[0], st.lb[1] + (Rr - 1) * (+st.dy || 0)];
      if (k === "rt") { const from = st.lt || [st.lb[0], st.lb[1] + (Rr - 1) * (+st.dy || 0)]; return [from[0] + (C - 1) * (+st.dx || 0), from[1]]; }
      return null;
    };
    const goTo = async (k) => {
      if (!(await need())) return;
      let q = null;
      if (k === "lb") {
        if (!st.ref) return toast("choose a part first", "warn");
        try { const r = await API.req("GET", `/api/align/point?job=${encodeURIComponent(this.path)}&ref=${encodeURIComponent(st.ref)}&panel=0&nominal=1`); q = [r.x, r.y]; } catch (e) { return toast(e.message, "bad"); }
      } else q = st[k] || estimate(k);
      if (!q) return toast("set the left-bottom board first", "warn");
      const o = Cfg.offset(0);
      await API.run("led", { v: 3 });
      if (await API.run("move", { x: (q[0] + o[0]).toFixed(3), y: (q[1] + o[1]).toFixed(3), slow: 1 })) { await App.poll(); pcam.grab(); }
    };
    const teach = (k, label) => {
      const lbl = h("span", { class: "muted", style: "min-width:140px" }, st[k] ? `${fmt(st[k][0], 3)}, ${fmt(st[k][1], 3)}` : "—");
      return h("div", { class: "row", style: "margin:2px 0" }, h("span", { style: "width:230px" }, label),
        h("button", { class: "btn small", title: "the camera to it (the left-bottom: the chosen part; the others: where it should be)", onclick: () => goTo(k) }, "Go"),
        h("button", { class: "btn small primary", title: "the head camera crosshair is on the same part / mark of that board", onclick: () => { const q = camPt(); if (!q) return toast("position unknown (home first)", "bad"); st[k] = q; lbl.textContent = `${fmt(q[0], 3)}, ${fmt(q[1], 3)}`; showResult(); } }, "Set = camera"), lbl);
    };
    const pcam = Manual.coordCam(1);
    this.markCam = pcam;                                      // the arrow keys jog the head on this tab too
    const centreCircle = async () => {
      if (!(await need())) return;
      try { const r = await API.req("POST", `/api/align/circle?client=${API.st.client}`); await App.poll(); pcam.grab(); toast(`centred on the circle (Ø ${fmt(r.d, 2)} mm)`, "ok"); } catch (e) { toast(e.message, "bad"); }
    };
    const build = () => {
      const C = Math.max(1, Math.round(st.cols)), Rr = Math.max(1, Math.round(st.rows));
      let col = [st.dx, 0], row = [0, st.dy], base = p1;
      if (st.lb && (st.rt || st.lt)) {                       // taught: steps from the three points (createGrid)
        if (Rr >= 2 && st.lt) row = [(st.lt[0] - st.lb[0]) / (Rr - 1), (st.lt[1] - st.lb[1]) / (Rr - 1)];
        if (C >= 2) {
          const from = Rr >= 2 && st.lt ? st.lt : st.lb;
          if (st.rt) col = [(st.rt[0] - from[0]) / (C - 1), (st.rt[1] - from[1]) / (C - 1)];
        }
      }
      if (C * Rr > 1 && (Math.hypot(...col) < 1 && C > 1 || Math.hypot(...row) < 1 && Rr > 1)) return toast("set the board steps (or teach the corner boards)", "warn");
      if (cur.length > 1 && !confirm(`Replace the ${cur.length} panels with ${C} x ${Rr} = ${C * Rr}?`)) return;
      const skip = cur[0] ? cur[0][0][4] : "No", rot = cur[0] ? cur[0][0][3] : "0";
      const lines = [];
      for (let r = 0; r < Rr; r++) for (let c = 0; c < C; c++)
        lines.push(`mirror,${(base[0] + c * col[0] + r * row[0]).toFixed(3)},${(base[1] + c * col[1] + r * row[1]).toFixed(3)},${rot || 0},${(skip || "No").trim() || "No"},`);
      const at = cur.length ? cur[0][1] : (tags("comp")[0] ? tags("comp")[0][1] : this.lines.length);
      for (const [, no] of cur.slice().reverse()) this.lines.splice(no, 1);
      this.lines.splice(cur.length ? at : at, 0, ...lines);
      // NeoDen4's grid line (its editor data); the PCB angle (k8) is kept
      const lb = st.lb || base, lt = st.lt || [base[0] + (Rr - 1) * row[0], base[1] + (Rr - 1) * row[1]];
      const rt = st.rt || [lt[0] + (C - 1) * col[0], lt[1] + (C - 1) * col[1]];
      const old = tags("mirror_create")[0];
      const ang = old ? (old[0][9] || "0") : "0";
      const mline = `mirror_create,${C},${Rr},${lb[0].toFixed(3)},${lb[1].toFixed(3)},${lt[0].toFixed(3)},${lt[1].toFixed(3)},${rt[0].toFixed(3)},${rt[1].toFixed(3)},${ang},`;
      if (old) this.lines[old[1]] = mline;
      else this.lines.splice(this.lines.findIndex((l) => l.startsWith("mirror,")), 0, mline);
      if (perBoard.checked) {                                  // the board's own marks, found on each board
        const mk = tags("mark")[0];
        if (mk) this.setField(mk[1], 0, "Mirror");
      }
      this.touch(); App.show();
      toast(`${C * Rr} panels made from panel 1. Save to keep them`, "ok");
    };
    const perBoard = h("input", { type: "checkbox", checked: true });
    body.append(h("div", { class: "card", style: "border-color:var(--accent)" },
      h("h3", { style: "margin-top:0" }, "Panelize"),
      h("div", { class: "muted", style: "font-size:12px" }, "The job is one board; the panel is columns x rows of it. Panel 1 (the first row below) is where the board's first part (or its zero point) is on the machine, on the left-bottom board. The other panels are made from it."),
      h("div", { class: "row" }, h("label", {}, "Columns (along X)"), n("cols", 60), h("label", {}, "Rows (along Y)"), n("rows", 60)),
      h("div", { class: "row" }, h("label", {}, "Board step X (mm)"), (inDx = n("dx", 90, "0.01")), h("label", {}, "Board step Y (mm)"), (inDy = n("dy", 90, "0.01")),
        h("span", { class: "muted", style: "font-size:12px" }, "board size + the gap between boards")),
      h("details", { open: true }, h("summary", {}, h("b", {}, "Teach the panel with the head camera: one part on three corner boards (the steps and a turned panel are worked out)")),
        h("div", { style: "display:grid;grid-template-columns:minmax(0,1fr) minmax(0,0.9fr);gap:12px;align-items:start;margin-top:6px" },
          h("div", {},
            h("div", { class: "muted", style: "font-size:12px" }, "Choose a part, Go takes the camera to it on the left-bottom board; put the crosshair on it (arrow keys 0.1 mm, Shift 1 mm; a click on the picture moves there) and Set. Then the same part on the left-top and right-top boards (Go = where it should be)."),
            h("div", { class: "row" }, h("label", {}, "Part"), refSel, h("button", { class: "btn small", title: "centre on a round mark (a fiducial) under the camera", onclick: centreCircle }, "Centre on the circle")),
            teach("lb", "1. left-bottom board (panel 1)"), teach("lt", "2. left-top board (rows > 1)"), teach("rt", "3. right-top board (columns > 1)"), result),
          pcam.el)),
      h("label", {}, perBoard, " the fiducials are on each board: find them on every panel (marks: Mirror)"),
      h("div", { class: "row" }, h("button", { class: "btn primary", onclick: build }, "Make panels"),
        h("span", { class: "muted" }, `now: ${cur.length} panel(s)`))));
    showResult();
    pcam.start();
  },

  // Board: change a finished job. Turn the board (parts and marks about the board's zero point, so the
  // panel positions stay right), new part coordinates from a pick-and-place file (fitted onto the job,
  // as the wizard placed it, then part by part), new Gerber + P&P for the pad data, panels, the wizard.
  board(body) {
    const tags = (tag) => this.lines.map((l, no) => [l.split(","), no]).filter(([t]) => t[0].trim() === tag);
    const comps = tags("comp");
    const org = tags("origin")[0];
    const anchor = org ? [+org[0][1], +org[0][2]] : comps[0] ? [+comps[0][0][6], +comps[0][0][7]] : [0, 0];
    const turn = (deg) => {
      const t = (deg * Math.PI) / 180, c = Math.cos(t), sn = Math.sin(t);
      const rot = (x, y) => [anchor[0] + c * (x - anchor[0]) - sn * (y - anchor[1]), anchor[1] + sn * (x - anchor[0]) + c * (y - anchor[1])];
      if (!confirm(`Turn the board ${deg > 0 ? "+" : ""}${deg}° (counter-clockwise +) about its zero point ${fmt(anchor[0], 2)}, ${fmt(anchor[1], 2)}? Parts and marks turn; the panels stay where they are.`)) return;
      for (const [t2, no] of comps) {
        const [x, y] = rot(+t2[6], +t2[7]);
        t2[6] = x.toFixed(2); t2[7] = y.toFixed(2);
        t2[8] = ((((+t2[8] + deg) % 360) + 540) % 360 - 180).toFixed(2);
        this.lines[no] = t2.join(",");
      }
      const mk = tags("mark")[0];
      if (mk) {
        const t2 = mk[0];
        for (let i = 3; i + 1 < t2.length && t2[i].trim() !== "" && t2[i + 1].trim() !== ""; i += 2) {
          const [x, y] = rot(+t2[i], +t2[i + 1]);
          t2[i] = x.toFixed(3); t2[i + 1] = y.toFixed(3);
        }
        this.lines[mk[1]] = t2.join(",");
      }
      this.touch(); App.show();
      toast(`board turned ${deg}°. Check it with Vision Align on the mount screen; Save to keep it`, "ok");
    };

    // new P&P: fitted onto the job, then the parts that differ, to apply
    const pnpIn = h("input", { type: "file", accept: ".csv,.txt" });
    const rawBox = h("input", { type: "checkbox" });
    const out = h("div");
    const compare = async () => {
      if (!pnpIn.files[0]) return toast("choose the pick-and-place file (CSV)", "warn");
      if (this.dirty && confirm("Save the job first? (the file is compared with the saved job)")) await this.save();
      try {
        const path = await Wizard.upload(pnpIn.files[0]);
        const r = await API.req("POST", `/api/align/repnp?job=${encodeURIComponent(this.path)}&pnp=${encodeURIComponent(path)}&raw=${rawBox.checked ? 1 : 0}`);
        const f = r.fit;
        const moved = r.parts.filter((q) => Math.hypot(q.dx, q.dy) >= 0.02 || Math.abs(q.drot) >= 0.5);
        const checks = moved.map(() => h("input", { type: "checkbox", checked: true }));
        out.replaceChildren(
          h("div", { class: "muted" }, rawBox.checked ? "used as they are (no fit)" :
            `fit onto the job: turn ${fmt(f.theta, 3)}°, shift ${fmt(f.tx, 3)}, ${fmt(f.ty, 3)} mm${f.mirror ? ", mirrored (bottom side)" : ""}, rms ${fmt(f.rms, 3)} mm on ${f.n} parts`),
          h("div", {}, `${r.parts.length} parts in both; ${moved.length} differ` + (r.missing.length ? `; not in the file: ${r.missing.slice(0, 12).join(", ")}` : "") +
            (r.new.length ? `; not in the job (add them in Components): ${r.new.slice(0, 12).join(", ")}` : "")),
          moved.length ? h("div", { class: "scroll", style: "max-height:300px" }, h("table", { class: "pick" },
            h("tr", {}, h("th", {}), h("th", {}, "Part"), h("th", {}, "X"), h("th", {}, "Y"), h("th", {}, "Rotation"), h("th", {}, "moves by")),
            moved.map((q, i) => h("tr", {}, h("td", {}, checks[i]), h("td", {}, q.name), h("td", {}, fmt(q.x, 2)), h("td", {}, fmt(q.y, 2)), h("td", {}, fmt(q.rot, 1)),
              h("td", {}, `${fmt(q.dx, 3)}, ${fmt(q.dy, 3)} mm` + (Math.abs(q.drot) >= 0.5 ? `, ${q.drot > 0 ? "+" : ""}${fmt(q.drot, 1)}°` : "")))))) : null,
          moved.length ? h("div", { class: "row" }, h("button", { class: "btn primary", onclick: () => {
            const byName = {};
            moved.forEach((q, i) => { if (checks[i].checked) byName[q.name] = q; });
            let n = 0;
            for (const [t2, no] of tags("comp")) {
              const q = byName[t2[3]];
              if (!q) continue;
              t2[6] = (+q.x).toFixed(2); t2[7] = (+q.y).toFixed(2); t2[8] = (+q.rot).toFixed(2);
              this.lines[no] = t2.join(","); n++;
            }
            this.touch(); App.show();
            toast(`${n} part(s) updated. Save to keep them`, "ok");
          } }, "Apply the ticked parts")) : null);
      } catch (e) { toast(e.message, "bad"); }
    };

    // Gerber + P&P -> the pad data (pad alignment, Vision Align outlines)
    const gIn = h("input", { type: "file", accept: ".gbr,.gtl,.gbl,.ger,.art,.pho,.zip,.txt" });
    const pIn = h("input", { type: "file", accept: ".csv,.txt" });
    const padsOut = h("div", { class: "muted" });
    const rebuildPads = async () => {
      if (!gIn.files[0] || !pIn.files[0]) return toast("choose the copper Gerber and the pick-and-place file", "warn");
      if (this.dirty && confirm("Save the job first?")) await this.save();
      try {
        const g = await Wizard.upload(gIn.files[0]), pp = await Wizard.upload(pIn.files[0]);
        const r = await API.req("POST", `/api/align/pads?client=${API.st.client}&job=${encodeURIComponent(this.path)}&pnp=${encodeURIComponent(pp)}&gerber=${encodeURIComponent(g)}`);
        padsOut.textContent = `pad data: ${r.parts} parts, ${r.pads} pads (${r.layer}), fit rms ${fmt(r.fit.rms, 3)} mm`;
        toast("pad data rebuilt", "ok");
      } catch (e) { toast(e.message, "bad"); }
    };

    body.append(this.remapCard(anchor, !!org),
      h("div", { class: "card" }, h("h3", { style: "margin-top:0" }, "Turn the board"),
        h("div", { class: "muted", style: "font-size:12px" }, `Parts and marks turn about the board's zero point (${org ? "the origin line" : "the first part"}: ${fmt(anchor[0], 2)}, ${fmt(anchor[1], 2)}), so the panel positions stay right.`),
        h("div", { class: "row" }, [-90, 90, 180].map((d) => h("button", { class: "btn", onclick: () => turn(d) }, `${d > 0 ? "+" : ""}${d}°`)))),
      h("div", { class: "card" }, h("h3", { style: "margin-top:0" }, "New part coordinates (pick-and-place file)"),
        h("div", { class: "muted", style: "font-size:12px" }, "The file (Altium / KiCad CSV; save an Excel sheet as CSV) is fitted onto the job's parts as a whole, then each part that differs is listed; tick the ones to take."),
        h("div", { class: "row" }, pnpIn, h("label", {}, rawBox, " already in the job's coordinates (no fit)"), h("button", { class: "btn primary", onclick: compare }, "Compare")), out),
      h("div", { class: "card" }, h("h3", { style: "margin-top:0" }, "New Gerber + pick-and-place (pad data)"),
        h("div", { class: "muted", style: "font-size:12px" }, "The copper layer (top, or bottom for a bottom-side board) and the P&P file: the pads used by pad alignment and drawn by Vision Align."),
        h("div", { class: "row" }, h("label", {}, "Gerber"), gIn, h("label", {}, "P&P"), pIn, h("button", { class: "btn primary", onclick: rebuildPads }, "Rebuild pad data")), padsOut),
      h("div", { class: "card" }, h("h3", { style: "margin-top:0" }, "More"),
        h("div", { class: "row" },
          h("button", { class: "btn", onclick: () => { this.tab = "panels"; App.show(); } }, "Panels / Panelize →"),
          h("button", { class: "btn", title: "make the job again from the Gerber and the P&P file", onclick: () => { location.hash = "#wizard"; } }, "Make it again with the setup wizard →"))));
    if (!Wizard.jobs) API.req("GET", "/api/status").then((st) => { if (st.neoden_dir) { Wizard.jobs = PathU.join(st.neoden_dir, "Jobs"); Wizard.proc = PathU.join(st.neoden_dir, "Proc"); } }).catch(() => {});
  },

  // Remap with the camera: parts proposed far apart (one part = an offset only); the camera goes where
  // each should be, the operator brings the crosshair onto its real centre and sets it; the fit
  // (shift, or turn + shift) is applied to all the parts. Through the board marks when they are found
  // (then only the parts move: the file's parts vs its marks), else through panel 1 (marks move too).
  remapCard(anchor, hasOrigin) {
    const R = this.rm || (this.rm = { n: 3, parts: null, marks: false });
    const comps = this.lines.map((l) => l.split(",")).filter((t) => t[0].trim() === "comp" && !/^(yes|1|true)$/i.test((t[9] || "").trim()))
      .map((t) => ({ name: t[3], x: +t[6], y: +t[7] })).filter((c) => !/^(fid|mark)/i.test(c.name));
    const suggest = (n) => {
      if (comps.length < n) return comps.slice();
      if (n === 1) { const cx = comps.reduce((a, c) => a + c.x, 0) / comps.length, cy = comps.reduce((a, c) => a + c.y, 0) / comps.length;
        return [comps.slice().sort((a, b) => Math.hypot(a.x - cx, a.y - cy) - Math.hypot(b.x - cx, b.y - cy))[0]]; }
      let best = [comps[0], comps[1]], dm = -1;
      for (const a of comps) for (const b of comps) { const d = Math.hypot(a.x - b.x, a.y - b.y); if (d > dm) { dm = d; best = [a, b]; } }
      if (n === 2) return best;
      const [a, b] = best, L = Math.hypot(b.x - a.x, b.y - a.y) || 1;
      const c = comps.slice().sort((p, q) => Math.abs((b.x - a.x) * (a.y - q.y) - (a.x - q.x) * (b.y - a.y)) - Math.abs((b.x - a.x) * (a.y - p.y) - (a.x - p.x) * (b.y - a.y)))[0];
      return [a, c, b].filter((v, i, arr) => arr.indexOf(v) === i);
    };
    if (!R.parts) R.parts = suggest(R.n).map((c) => ({ ...c, meas: null }));
    const panel1 = () => { const t = this.lines.map((l) => l.split(",")).find((t) => t[0].trim() === "mirror"); return t ? [+t[1], +t[2]] : [0, 0]; };
    const cam = Manual.coordCam(1);
    this.rmCam = cam;
    setTimeout(() => { if (cam.el.isConnected) cam.start(); }, 0);   // live: the card is redrawn after each Set
    const off = () => Cfg.offset(0);
    const need = async () => API.st.haveControl || (await API.take());
    const info = h("div", { class: "muted" });
    const where = async (c) => {                       // machine point where the part should be
      if (R.marks) { const q = await API.req("GET", `/api/align/point?job=${encodeURIComponent(this.path)}&` + (c.point ? `x=${c.x}&y=${c.y}` : `ref=${encodeURIComponent(c.name)}`)); return [q.x, q.y]; }
      const P = panel1();
      return [P[0] + c.x - anchor[0], P[1] + c.y - anchor[1]];
    };
    const design = async (c, m) => {                   // machine point -> design point
      if (R.marks) { const q = await API.req("GET", `/api/align/design?job=${encodeURIComponent(this.path)}&x=${m[0]}&y=${m[1]}&x0=${c.x}&y0=${c.y}`); return [q.x, q.y]; }
      const P = panel1();
      return [anchor[0] + m[0] - P[0], anchor[1] + m[1] - P[1]];
    };
    const go = async (c) => {
      if (!(await need())) return;
      try {
        const m = await where(c), o = off();
        await API.run("led", { v: +Cfg.get("PadAlign/nLed", 3) || 3 });
        if (await API.run("move", { x: (m[0] + o[0]).toFixed(3), y: (m[1] + o[1]).toFixed(3), slow: 1 })) { await App.poll(); cam.start(); }
        info.textContent = `${c.name}: bring the crosshair onto its real centre (click the picture, arrows 0.1 mm, Shift 1 mm), then Set`;
      } catch (e) { toast(e.message, "bad"); }
    };
    const ready = (c) => !c.point || (c.x !== "" && c.y !== "" && isFinite(c.x) && isFinite(c.y));
    const setHere = async (c) => {
      if (!ready(c)) return toast("type the point's X, Y on the board first", "warn");
      const pos = (API.st.status || {}).pos;
      if (!pos) return toast("position unknown", "bad");
      const o = off();
      try { c.meas = await design(c, [pos[0] - o[0], pos[1] - o[1]]); } catch (e) { return toast(e.message, "bad"); }
      toast(`${c.name}: ${fmt(c.meas[0] - c.x, 3)}, ${fmt(c.meas[1] - c.y, 3)} mm off`, "ok");
      App.show();
    };
    const byName = (n) => comps.find((c) => c.name.trim().toLowerCase() === String(n).trim().toLowerCase());
    const listId = "rmParts";
    const names = h("datalist", { id: listId }, comps.map((c) => h("option", { value: c.name })));
    const choose = (i, v) => {
      const c = byName(v);
      if (!c) return toast(`no part ${v} in the job`, "bad");
      if (R.parts.some((p, k) => k !== i && p.name === c.name)) { App.show(); return toast(`${c.name} is already in the list`, "warn"); }
      R.parts[i] = { ...c, meas: null }; App.show();
    };
    const nearOut = h("span", {});
    const near = async () => {                         // the parts nearest to the crosshair (design frame)
      const pos = (API.st.status || {}).pos;
      if (!pos) return toast("position unknown", "bad");
      const o = off();
      let d;
      try { d = await design({ x: anchor[0], y: anchor[1] }, [pos[0] - o[0], pos[1] - o[1]]); } catch (e) { return toast(e.message, "bad"); }
      const list = comps.map((c) => ({ c, d: Math.hypot(c.x - d[0], c.y - d[1]) })).sort((a, b) => a.d - b.d).slice(0, 8);
      const sel = h("select", {}, list.map((q, i) => h("option", { value: i }, `${q.c.name}  (${fmt(q.d, 2)} mm away)`)));
      nearOut.replaceChildren(sel, " ", h("button", { class: "btn small primary", title: "the crosshair is on this part's real centre: set it with this part", onclick: async () => {
        const c = list[+sel.value].c;
        let i = R.parts.findIndex((p) => p.name === c.name);
        if (i < 0) { R.parts.push({ ...c, meas: null }); i = R.parts.length - 1; }
        await setHere(R.parts[i]);
      } }, "Set with this part"));
    };
    const circleOut = h("span", { class: "muted", style: "font-size:12px" });
    const circle = async () => {
      if (!(await need())) return null;
      try {
        const r = await API.req("POST", `/api/align/circle?client=${API.st.client}`);
        await App.poll(); cam.grab();
        circleOut.replaceChildren(h("img", { src: "data:image/png;base64," + r.png, style: "width:80px;vertical-align:middle;border-radius:4px" }), ` centred: ${fmt(r.x, 3)}, ${fmt(r.y, 3)} · Ø ${fmt(r.d, 2)} mm`);
        return r;
      } catch (e) { toast(e.message, "bad"); return null; }
    };
    const circleSet = async (c) => { if (!ready(c)) return toast("type the point's X, Y on the board first", "warn"); if (await circle()) await setHere(c); };
    const ptIn = (c, k) => h("input", { type: "number", step: "0.01", value: c[k], style: "width:78px", placeholder: k.toUpperCase(),
      onchange: (e) => { c[k] = e.target.value === "" ? "" : +e.target.value; c.meas = null; App.show(); } });
    const findMarks = async () => {
      if (!(await need())) return;
      if (this.dirty && confirm("Save the job first? (the marks are taken from the saved file)")) await this.save();
      try {
        const m = await API.req("POST", `/api/align/marks?client=${API.st.client}&job=${encodeURIComponent(this.path)}`);
        R.marks = true; toast(`marks found (${m.fit})`, "ok"); App.show();
      } catch (e) { R.marks = false; toast("marks: " + e.message + " (the parts are mapped through panel 1 then)", "warn"); App.show(); }
    };
    const apply = () => {
      const pr = R.parts.filter((c) => c.meas && ready(c));
      if (!pr.length) return toast("set at least one part", "warn");
      let th = 0, cx = 0, cy = 0, tx, ty;
      const ca = [pr.reduce((a, c) => a + c.x, 0) / pr.length, pr.reduce((a, c) => a + c.y, 0) / pr.length];
      const cb = [pr.reduce((a, c) => a + c.meas[0], 0) / pr.length, pr.reduce((a, c) => a + c.meas[1], 0) / pr.length];
      if (pr.length >= 2) {                            // rigid fit (turn + shift) about the centroids
        let sxx = 0, sxy = 0;
        for (const c of pr) { const ax = c.x - ca[0], ay = c.y - ca[1], bx = c.meas[0] - cb[0], by = c.meas[1] - cb[1]; sxx += ax * bx + ay * by; sxy += ax * by - ay * bx; }
        th = Math.atan2(sxy, sxx);
      }
      const co = Math.cos(th), si = Math.sin(th);
      const map = (x, y) => [cb[0] + co * (x - ca[0]) - si * (y - ca[1]), cb[1] + si * (x - ca[0]) + co * (y - ca[1])];
      const res = pr.map((c) => { const [x, y] = map(c.x, c.y); return Math.hypot(x - c.meas[0], y - c.meas[1]); });
      const sh = map(ca[0], ca[1]);
      if (!confirm(`Remap all the parts: turn ${fmt(th * 180 / Math.PI, 3)}°, shift ${fmt(sh[0] - ca[0], 3)}, ${fmt(sh[1] - ca[1], 3)} mm` +
        (pr.length >= 2 ? `, left over at the parts ${res.map((r) => fmt(r, 3)).join(" ")} mm` : "") + (R.marks ? " (the marks stay)" : " (the marks move too)") + "?")) return;
      if (!hasOrigin) this.lines = jobSetOrigin(this.lines, anchor);   // the panels keep referring to the same point
      const deg = th * 180 / Math.PI;
      this.lines = this.lines.map((l) => {
        const t = l.split(",");
        if (t[0].trim() === "comp" && t.length > 8) {
          const [x, y] = map(+t[6], +t[7]);
          t[6] = x.toFixed(2); t[7] = y.toFixed(2);
          if (Math.abs(deg) > 0.005) t[8] = ((((+t[8] + deg) % 360) + 540) % 360 - 180).toFixed(2);
          return t.join(",");
        }
        if (t[0].trim() === "mark" && !R.marks) {
          for (let i = 3; i + 1 < t.length && t[i].trim() !== "" && t[i + 1].trim() !== ""; i += 2) { const [x, y] = map(+t[i], +t[i + 1]); t[i] = x.toFixed(3); t[i + 1] = y.toFixed(3); }
          return t.join(",");
        }
        return l;
      });
      R.parts = null; R.marks = false;
      this.touch(); App.show();
      toast("remapped. Save to keep it", "ok");
    };
    const nSel = h("select", { onchange: (e) => { R.n = +e.target.value; R.parts = null; App.show(); } },
      [[1, "1 part (offset only)"], [2, "2 parts (offset + turn)"], [3, "3 parts (offset + turn, checked)"]].map(([v, t]) => h("option", { value: v, selected: v === R.n }, t)));
    return h("div", { class: "card", style: "border-color:var(--accent)" }, h("h3", { style: "margin-top:0" }, "Remap with the camera (offset, or after turning the board)"),
      h("div", { class: "muted", style: "font-size:12px" }, "Find the marks first when the board has them (then the parts are corrected against the marks); without marks the parts are taken through panel 1. Go = the camera to where the part should be; put the crosshair on its real centre; Set. Any part can be used: type its name in Part, or put the crosshair on a part you see and ask which part is under it. Or a board point: + board point, type its X, Y on the board. ◎ Set = centre on the round mark, then set."),
      h("div", { class: "row" }, nSel, h("button", { class: "btn", onclick: findMarks }, R.marks ? "Marks found ✓ (again)" : "Find the marks"),
        h("button", { class: "btn", title: "other parts", onclick: () => { R.parts = null; App.show(); } }, "Suggest again")),
      h("div", { style: "display:grid;grid-template-columns:minmax(0,420px) minmax(0,1fr);gap:10px;align-items:start" },
        cam.el,
        h("div", {}, h("table", { class: "pick" }, h("tr", {}, h("th", {}, "Part"), h("th", {}, "X / Y in the job"), h("th", {}), h("th", {}, "measured off by")),
          R.parts.map((c, i) => h("tr", {}, h("td", {}, c.point ? h("span", { title: "a point of the board, its X, Y typed in" }, c.name)
              : h("input", { value: c.name, list: listId, style: "width:90px", title: "any part of the job: type its name", onchange: (e) => choose(i, e.target.value) })),
            h("td", {}, c.point ? h("span", {}, ptIn(c, "x"), " ", ptIn(c, "y")) : `${fmt(c.x, 2)}, ${fmt(c.y, 2)}`),
            h("td", {}, h("button", { class: "btn small", onclick: () => { if (!ready(c)) return toast("type the point's X, Y on the board first", "warn"); go(c); } }, "Go"), " ",
              h("button", { class: "btn small primary", title: "the crosshair is on it: set", onclick: () => setHere(c) }, "Set"), " ",
              h("button", { class: "btn small", title: "centre the camera on the round mark under it, then set", onclick: () => circleSet(c) }, "◎ Set"), " ",
              h("button", { class: "btn small ghost", title: "leave this part out", onclick: () => { R.parts.splice(i, 1); App.show(); } }, "✕")),
            h("td", {}, c.meas ? `${fmt(c.meas[0] - c.x, 3)}, ${fmt(c.meas[1] - c.y, 3)} mm` : "—")))),
          names,
          h("div", { class: "row" }, h("button", { class: "btn small", title: "a point of the board instead of a part: type its X, Y on the board (a fiducial, a hole, a pad), put the camera on it, set",
              onclick: () => { R.parts.push({ name: `point ${R.parts.filter((p) => p.point).length + 1}`, point: true, x: "", y: "", meas: null }); App.show(); } }, "+ board point"),
            h("button", { class: "btn small", title: "one more part (type its name in the new row)", onclick: () => { const c = comps.find((q) => !R.parts.some((p) => p.name === q.name)); if (c) { R.parts.push({ ...c, meas: null }); App.show(); } } }, "+ part"),
            h("button", { class: "btn small", title: "the parts nearest to the camera now: pick the one under the crosshair and set it", onclick: near }, "Which part is under the crosshair?"), nearOut),
          h("div", { class: "row" }, h("button", { class: "btn small", title: "centre the camera on the round mark under it (fiducial, hole)", onclick: circle }, "Centre on the circle"), circleOut),
          info, h("div", { class: "row" }, h("button", { class: "btn primary", onclick: apply }, "Apply to all the parts")))));
  },

  addRow(tag, cols) {
    const last = this.lines.map((l, no) => [l, no]).filter(([l]) => l.split(",")[0].trim() === tag).pop();
    const t = last ? last[0].split(",") : [tag, ...cols.map(() => "")];
    if (tag === "stack") t[1] = String(Math.max(0, ...this.lines.filter((l) => l.startsWith("stack,")).map((l) => +l.split(",")[1] || 0)) + 1);
    const at = last ? last[1] + 1 : this.lines.length;
    this.lines.splice(at, 0, t.join(","));
    this.touch(); App.show();
  },

  // PCB info: run mode, marks (fiducials) with their search settings, the bad-mark point, test run
  pcb(body) {
    const find = (tag) => this.lines.findIndex((l) => l.split(",")[0].trim() === tag);
    const pcb = find("pcb"), mark = find("mark"), test = find("test");
    const t = (no) => (no >= 0 ? this.lines[no].split(",") : []);
    const kv = h("div", { class: "kv", style: "align-items:center" });
    if (pcb >= 0) {
      const p = t(pcb);
      kv.append(h("div", {}, "Board mode"), h("div", {}, p[2] === "Lock" ? "Lock (fixed table)" : `${p[2]} (rail; not used on this machine)`),
        h("div", {}, "Bad-mark point X / Y"), h("div", { class: "row", style: "margin:0" },
          this.editor("f", p[8], (v) => this.setField(pcb, 7, v), 80), this.editor("f", p[9], (v) => this.setField(pcb, 8, v), 80)),
        h("div", {}, "Bad-mark exposure"), h("div", {}, this.editor("i", p[10], (v) => this.setField(pcb, 9, v), 80)));
    }
    if (test >= 0) kv.append(h("div", {}, "Test run (no parts placed)"), h("div", {}, this.editor("yn", t(test)[1], (v) => this.setField(test, 0, v))));
    const oi = find("origin"), o = oi >= 0 ? t(oi) : null;
    const ox = h("input", { type: "number", step: "0.001", style: "width:90px", value: o ? o[1] : "" });
    const oy = h("input", { type: "number", step: "0.001", style: "width:90px", value: o ? o[2] : "" });
    const setO = () => { if (ox.value === "" || oy.value === "") return; jobSetOrigin(this.lines, [ox.value, oy.value]); this.touch(); };
    ox.onchange = setO; oy.onchange = setO;
    kv.append(h("div", {}, "Board zero point (optional)"), h("div", {},
      h("div", { class: "row", style: "margin:0" }, h("label", {}, "X"), ox, h("label", {}, "Y"), oy,
        h("button", { class: "btn ghost", onclick: () => { jobSetOrigin(this.lines, null); this.touch(); App.show(); } }, "Clear")),
      h("div", { class: "muted", style: "font-size:12px;max-width:560px" }, "In the part coordinates. When it is set, the panel X / Y mean where this point is on the machine " +
        "(no dummy first part needed). Empty: as NeoDen4, the first part is the reference. NeoDen4.exe itself ignores this setting.")));
    body.append(h("h3", {}, "PCB"), kv);
    body.append(this.marksCard(t, mark));
    body.append(this.zmapCard(this.path, async () => {
      if (this.dirty && !confirm("Save the job first? (the parts are read from the saved job)")) return false;
      if (this.dirty) await this.save();
      return true;
    }));
  },

  // the board's height map (neojob.zmap): nozzle 1 comes down slowly with the suction on at a grid of bare
  // spots until the pressure falls; between the points the height is linear (triangles), and each part's
  // place depth follows the board there; nozzles 2 .. 4 are measured against nozzle 1, and the board is a
  // floor no place depth goes below (POST /api/zmap/measure, /api/zmap/on, GET /api/zmap/get). Also on the
  // wizard's last step (ready(): false = do not measure)
  // the map in 3D: its triangles shaded by height (blue low, red high), the heights stretched so the
  // differences show; drag to turn it, the wheel to stretch the heights more or less
  zmap3d(m, odd) {
    const W = 520, H = 320, cv = h("canvas", { width: W, height: H, style: "max-width:100%;touch-action:none;cursor:grab;border-radius:6px;background:var(--card2, #1b1f24)" });
    const P = m.points, xs = P.map((p) => p.x), ys = P.map((p) => p.y), zs = P.map((p) => p.z);
    const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
    const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys), 1);
    const z0 = Math.min(...zs), z1 = Math.max(...zs), zr = Math.max(z1 - z0, 0.01), zm = (z0 + z1) / 2;
    let az = -0.6, el = 0.4, ex = 0.35 * span / zr;        // turn, tilt, height stretch (the spread: a third of the board)
    const col = (z) => { const t = (z - z0) / zr; return `hsl(${Math.round(240 - 240 * t)},70%,${45 + 10 * (1 - Math.abs(t - 0.5) * 2)}%)`; };
    const proj = (x, y, z) => {                              // board X right, Y away; depth z: deeper = lower
      const X = x - cx, Y = y - cy, Z = -(z - zm) * ex;
      const u = X * Math.cos(az) - Y * Math.sin(az), v = X * Math.sin(az) + Y * Math.cos(az);
      const k = 0.8 * Math.min(W, H) / span;
      return [W / 2 + k * u, H / 2 - k * (Z * Math.cos(el) + v * Math.sin(el)), v * Math.cos(el) - Z * Math.sin(el)];
    };
    const draw = () => {
      const g = cv.getContext("2d");
      g.clearRect(0, 0, W, H);
      const S = P.map((p) => proj(p.x, p.y, p.z));
      const tris = (m.tri || []).map((t) => ({ t, d: (S[t[0]][2] + S[t[1]][2] + S[t[2]][2]) / 3 })).sort((a, b) => b.d - a.d);
      g.strokeStyle = "rgba(160,160,160,.5)"; g.setLineDash([3, 3]);   // each point's stem down to the lowest
      P.forEach((p, i) => { const b = proj(p.x, p.y, z1); g.beginPath(); g.moveTo(b[0], b[1]); g.lineTo(S[i][0], S[i][1]); g.stroke(); });
      g.setLineDash([]);
      for (const { t } of tris) {                            // far ones first
        g.beginPath(); g.moveTo(S[t[0]][0], S[t[0]][1]); g.lineTo(S[t[1]][0], S[t[1]][1]); g.lineTo(S[t[2]][0], S[t[2]][1]); g.closePath();
        g.fillStyle = col((zs[t[0]] + zs[t[1]] + zs[t[2]]) / 3); g.globalAlpha = 0.85; g.fill();
        g.globalAlpha = 1; g.strokeStyle = "rgba(255,255,255,.55)"; g.lineWidth = 1; g.stroke();
      }
      g.font = "12px sans-serif";
      P.forEach((p, i) => {
        const [u, v] = S[i];
        g.fillStyle = odd.has(i) ? "#ff5252" : "#fff"; g.beginPath(); g.arc(u, v, 3.5, 0, 7); g.fill();
        g.fillText(`${i + 1}  ${p.z.toFixed(2)}`, u + 6, v - 5);
      });
      g.fillStyle = "#aaa";
      g.fillText(`${z0.toFixed(2)} .. ${z1.toFixed(2)} mm, heights x${(ex).toFixed(0)} · drag to turn, wheel: height stretch`, 8, H - 8);
    };
    let drag = null;
    cv.onpointerdown = (e) => { drag = [e.clientX, e.clientY, az, el]; cv.setPointerCapture(e.pointerId); };
    cv.onpointermove = (e) => { if (!drag) return; az = drag[2] + (e.clientX - drag[0]) / 150; el = Math.max(0.05, Math.min(1.5, drag[3] + (e.clientY - drag[1]) / 150)); draw(); };
    cv.onpointerup = () => { drag = null; };
    cv.onwheel = (e) => { e.preventDefault(); ex *= e.deltaY < 0 ? 1.25 : 0.8; draw(); };
    draw();
    return cv;
  },

  zmapCard(path, ready) {
    const q = () => `job=${encodeURIComponent(path)}`;
    const card = h("div", { class: "card", style: "margin-top:14px" });
    const nx = h("input", { type: "number", min: 2, max: 8, value: 4, style: "width:56px" });
    const ny = h("input", { type: "number", min: 2, max: 8, value: 3, style: "width:56px" });
    const out = h("div", { class: "muted", style: "font-size:12px" }), list = h("div"), view = h("div");
    const bout = h("span", { class: "muted", style: "font-size:12px" });
    const use = h("input", { type: "checkbox" });
    const noz = h("input", { type: "text", value: "1,2,3,4", style: "width:70px", title: "the nozzles to measure (the ones with a nozzle on, empty)" });
    const show = (r) => {
      const m = r.machine, b = r.board;
      bout.textContent = b && b.board_z != null ? ` this board: nozzle 1 meets it at ${fmt(b.board_z, 3)} mm (${b.time || ""}, ${b.how || ""})` : " this board: not measured (measured at the job's start)";
      list.textContent = ""; view.textContent = "";
      if (!m) { out.textContent = "no machine height map yet: Calibrate (place depths as the feeders set them)"; use.checked = false; use.disabled = true; return; }
      use.disabled = false; use.checked = m.on !== false;
      const odd = new Map((m.odd || []).map(([i, e]) => [i, e]));
      out.textContent = `machine map: ${m.points.length} points, ${m.time || ""} on ${m.board || "?"} · spread ${fmt(m.spread, 3)} mm, off one plane ${fmt(m.tilt_rms, 3)}` +
        (m.missed && m.missed.length ? ` · ${m.missed.length} without a touch` : "") + (odd.size ? ` · ${odd.size} odd` : "") +
        (m.nozzles && Object.keys(m.nozzles).length ? ` · against nozzle 1: ${Object.entries(m.nozzles).map(([k, v]) => `N${k} ${v >= 0 ? "+" : ""}${fmt(v, 3)}`).join(", ")}` : "");
      view.append(this.zmap3d(m, odd));
      list.append(h("details", {}, h("summary", {}, "Points"), h("table", { class: "pick", style: "width:auto" },
        h("tr", {}, h("th", {}, "#"), h("th", {}, "machine X"), h("th", {}, "machine Y"), h("th", {}, "touch Z"), h("th", {}, "vs middle")),
        m.points.map((p, i) => h("tr", { style: odd.has(i) ? "color:var(--bad, #c33)" : "" }, h("td", {}, i + 1), h("td", {}, fmt(p.x, 2)), h("td", {}, fmt(p.y, 2)),
          h("td", {}, fmt(p.z, 3)), h("td", {}, (p.z - m.ref >= 0 ? "+" : "") + fmt(p.z - m.ref, 3) + (odd.has(i) ? ` (off its neighbours ${fmt(odd.get(i), 3)})` : "")))))));
    };
    const load = async () => { try { show(await API.req("GET", `/api/zmap/get?${q()}`)); } catch (e) { out.textContent = e.message; } };
    const run = async (what, text, args) => {
      if (ready && !(await ready())) return;
      if (!confirm(text)) return;
      if (!(API.st.haveControl || (await API.take()))) return;
      const was = out.textContent; out.textContent = "measuring … (about 20 s a point)";
      try { await API.req("POST", `/api/zmap/${what}?client=${API.st.client}&${q()}${args || ""}`); await load(); toast("saved", "ok"); }
      catch (e) { out.textContent = was; toast(e.message, "bad"); }
    };
    const calibrate = () => run("calibrate", `Machine calibration on this board: nozzles ${noz.value} must be empty. They come down slowly on bare spots, with the suction on. Start?`,
      `&nx=${+nx.value || 4}&ny=${+ny.value || 3}&nozzles=${encodeURIComponent(noz.value)}`);
    const board = () => run("board", "This board's height: nozzle 1 (empty) touches it at 3 bare spots. Start?");
    use.onchange = async () => {
      try { await API.req("POST", `/api/zmap/on?client=${API.st.client}&on=${use.checked ? 1 : 0}`); toast(use.checked ? "place depths follow the machine map" : "machine map not used", "ok"); }
      catch (e) { toast(e.message, "bad"); load(); }
    };
    card.append(h("h3", { style: "margin-top:0" }, "Z height map"),
      h("div", { class: "row" }, h("b", { style: "font-size:13px" }, "Machine (every job)"), "Points", nx, "×", ny, "Nozzles", noz,
        h("button", { class: "btn", title: "the machine's own heights over the table, measured on this board: a calibration, used by every job", onclick: calibrate }, "Calibrate machine heights"),
        h("label", {}, use, " use when placing")),
      out, view, list,
      h("div", { class: "row" }, h("b", { style: "font-size:13px" }, "This board"),
        h("button", { class: "btn small", title: "nozzle 1 touches this board at 3 spots: its height (the machine's own shape taken off)", onclick: board }, "Measure board height"), bout),
      h("div", { class: "muted", style: "font-size:12px;max-width:620px" }, "The machine map is the axes' and the table's own heights (linear between the points, machine coordinates), " +
        "with each nozzle's difference from nozzle 1; it applies to every job. The board height is this job's: measured at its start when it has none, then kept. " +
        "With both, a part is placed at the board there less its height plus 0.1 mm, and no nozzle goes deeper than the board (less the part, plus 0.3 mm)."));
    load();
    return card;
  },

  // the head camera's scale for this board (it is not telecentric: ~115.2 px/mm on the BOARD-A, ~116.8 on the
  // BOARD-B): a `headscale,<px/mm>,<how>` line, measured from a row of pads (POST /api/feedsetup/headscale)
  headScaleRow() {
    const no = () => this.lines.findIndex((l) => l.split(",")[0].trim() === "headscale");
    const cur = () => { const i = no(); return i < 0 ? null : this.lines[i].split(",").slice(1); };
    const out = h("span", { class: "muted", style: "font-size:12px" });
    const show = () => {
      const c = cur();
      out.textContent = c ? ` this board: ${c[0]} px/mm (${c[1] || ""}) · Config ${fmt(Cfg.pxmm(1), 2)}` : ` Config's ${fmt(Cfg.pxmm(1), 2)} px/mm (not measured on this board)`;
    };
    const put = (v, how) => {
      const line = `headscale,${(+v).toFixed(3)},${how.replace(/,/g, " ")},`;
      const i = no();
      if (i >= 0) this.lines[i] = line;
      else { const pcb = this.lines.findIndex((l) => l.split(",")[0].trim() === "pcb"); this.lines.splice(pcb >= 0 ? pcb + 1 : 0, 0, line); }
      this.touch(); show(); toast(`head camera ${fmt(v, 3)} px/mm for this job. Save to keep it`, "ok");
    };
    const measure = async () => {
      if (this.dirty && !confirm("Save the job first? (the pads are read from the saved job)")) return;
      if (this.dirty) await this.save();
      const ref = prompt("Measured on the pads of (empty: the part with the longest regular row of pads)", "");
      if (ref === null) return;
      if (!(API.st.haveControl || (await API.take()))) return;
      out.textContent = " measuring …";
      try {
        const r = await API.req("POST", `/api/feedsetup/headscale?client=${API.st.client}&job=${encodeURIComponent(this.path)}` + (ref ? `&ref=${encodeURIComponent(ref)}` : ""));
        const rows = r.rows.map((q) => `${q.pads} pads along ${q.along}: ${fmt(q.px_per_mm, 2)}`).join(", ");
        if (confirm(`${r.ref}: ${fmt(r.px_per_mm, 3)} px/mm (${rows}; Config ${fmt(r.config, 2)}). Use it for this job?`)) put(r.px_per_mm, `${r.ref} ${new Date().toISOString().slice(0, 10)}`);
        else show();
      } catch (e) { show(); toast(e.message, "bad"); }
    };
    show();
    return h("div", { class: "row" }, h("b", { style: "font-size:13px" }, "Head camera scale"),
      h("button", { class: "btn small", title: "the head camera over a part's row of pads: its scale on this board's surface", onclick: measure }, "Measure on this board"),
      h("button", { class: "btn small ghost", title: "use Config's scale again", onclick: () => { const i = no(); if (i >= 0) { this.lines.splice(i, 1); this.touch(); show(); } } }, "Clear"), out);
  },

  // the marks (fiducials): edit, add, delete; the head camera live on this page, centred on a round mark
  // with one button; a new mark is written in the board's frame: the job's marks are read first, so the
  // new one does not come out shifted by where the board lies today
  marksCard(t, mark) {
    const path = this.path;
    const need = async () => API.st.haveControl || (await API.take());
    const card = h("div", { class: "card", style: "margin-top:14px" });
    const ensure = () => {                                  // a mark line when the job has none
      let no = this.lines.findIndex((l) => l.split(",")[0].trim() === "mark");
      if (no < 0) {
        const pcb = this.lines.findIndex((l) => l.split(",")[0].trim() === "pcb");
        this.lines.splice(pcb >= 0 ? pcb + 1 : 0, 0, "mark,Whole,Auto,");
        no = pcb >= 0 ? pcb + 1 : 0;
      }
      return no;
    };
    const pairs = () => { const no = this.lines.findIndex((l) => l.split(",")[0].trim() === "mark"); if (no < 0) return []; const m = t(no), out = []; for (let i = 3; i + 1 < m.length && m[i] !== "" && m[i + 1] !== undefined && m[i + 1] !== ""; i += 2) out.push([+m[i], +m[i + 1]]); return out; };
    const writePairs = (ps, ext) => {
      const no = ensure(), m = t(no);
      this.lines[no] = ["mark", m[1] || "Whole", m[2] || "Auto", ...ps.flatMap(([x, y]) => [(+x).toFixed(3), (+y).toFixed(3)]), ""].join(",");
      // markext lines (search size, lamp, exposure per mark): renumbered with the marks
      const exts = {};
      this.lines.forEach((l) => { const q = l.split(","); if (q[0].trim() === "markext") exts[+q[1]] = q; });
      this.lines = this.lines.filter((l) => l.split(",")[0].trim() !== "markext");
      const at = this.lines.findIndex((l) => l.split(",")[0].trim() === "mark") + 1;
      const lines = [];
      ext.forEach((old, i) => { const q = old === "new" ? null : exts[old]; if (q) { q[1] = String(i); lines.push(q.join(",")); } else if (old && old.new) lines.push(`markext,${i},${old.dmin},${old.dmax},3,0`); });
      this.lines.splice(at, 0, ...lines);
      this.touch(); App.show();
    };
    const del = (i) => { const ps = pairs(); if (!confirm(`Delete mark ${i + 1}?`)) return; ps.splice(i, 1); writePairs(ps, pairs().map((_, k) => k).filter((k) => k !== i)); };
    const toDesign = async (x, y) => {
      const r = await API.req("GET", `/api/align/design?job=${encodeURIComponent(path)}&x=${x}&y=${y}&nominal=1`);
      if (!r.measured && pairs().length && !confirm("The job's marks were not read since the board was put on the table: the new mark may come out shifted. Use it anyway? (Cancel, then 'Read the marks now')")) return null;
      return r;
    };
    const camPt = () => { const p = (API.st.status || {}).pos; if (!p) { toast("position unknown", "bad"); return null; } const o = Cfg.offset(0); return [p[0] - o[0], p[1] - o[1]]; };
    const shot = h("span");
    const centre = async () => {
      if (!(await need())) return null;
      try {
        const r = await API.req("POST", `/api/align/circle?client=${API.st.client}`);
        await App.poll(); cam.grab();
        shot.replaceChildren(h("img", { src: "data:image/png;base64," + r.png, style: "width:110px;vertical-align:middle;border-radius:4px" }), ` centred: ${fmt(r.x, 3)}, ${fmt(r.y, 3)} · Ø ${fmt(r.d, 2)} mm`);
        return r;
      } catch (e) { toast(e.message, "bad"); return null; }
    };
    // the marks from the Gerber: its round copper pads of no part, each looked for by the head camera
    // (POST /api/align/automarks); the found ones farthest apart are suggested as the mark line
    const autoOut = h("div");
    const auto = async () => {
      if (this.dirty && !confirm("Save the job first? The marks are looked for from the saved file.")) return;
      if (this.dirty) await this.save();
      if (!(await need())) return;
      autoOut.replaceChildren(h("div", { class: "muted" }, "looking for the Gerber's round pads with the camera …"));
      let r;
      try { r = await API.req("POST", `/api/align/automarks?client=${API.st.client}&job=${encodeURIComponent(path)}`); }
      catch (e) { autoOut.replaceChildren(h("div", { class: "bad" }, e.message)); return; }
      await App.poll(); cam.grab();
      const cs = r.candidates || [], sg = r.suggest || [];
      const use = () => {
        const pick = sg.map((i) => cs[i]);
        if (pairs().length && !confirm(`Replace the ${pairs().length} mark(s) by the ${pick.length} suggested?`)) return;
        writePairs(pick.map((c) => c.design), pick.map((c) => ({ new: true, dmin: (0.7 * c.d).toFixed(2), dmax: (1.3 * c.d).toFixed(2) })));
        toast(`${pick.length} marks from the Gerber. Save the job to keep them`, "ok");
      };
      autoOut.replaceChildren(
        cs.length ? h("table", { class: "pick", style: "width:auto;margin-top:6px" },
          h("tr", {}, ["", "Design X, Y", "Ø mm", "Camera", ""].map((t) => h("th", {}, t))),
          cs.map((c, i) => h("tr", {}, h("td", {}, sg.includes(i) ? h("b", {}, "✓") : ""), h("td", {}, `${fmt(c.design[0], 3)}, ${fmt(c.design[1], 3)}`), h("td", {}, fmt(c.d, 2)),
            h("td", { class: c.found ? "ok" : "bad" }, c.found ? `found, ${fmt(c.off_mm, 3)} mm from where expected` : "not found"),
            h("td", {}, h("button", { class: "btn small", title: "the camera to it", onclick: async () => {
              if (!(await need())) return;
              const o = Cfg.offset(0), [x, y] = c.measured || c.expected;
              await API.run("led", { v: 3 });
              if (await API.run("move", { x: (x + o[0]).toFixed(3), y: (y + o[1]).toFixed(3), slow: 1 })) { await App.poll(); cam.grab(); }
            } }, "Go"))))) : null,
        r.note ? h("div", { class: "muted", style: "font-size:12px" }, r.note) : null,
        sg.length >= 2 ? h("div", { class: "row" }, h("button", { class: "btn primary", onclick: use }, `Use the suggested (${sg.length})`),
          h("span", { class: "muted", style: "font-size:12px" }, "✓ = the found ones farthest apart")) : h("div", { class: "muted", style: "font-size:12px" }, "fewer than 2 found: take the marks with the camera (below)"));
    };
    const add = async () => {
      const c = await centre(); if (!c) return;
      const d = await toDesign(c.x, c.y); if (!d) return;
      const ps = pairs(); ps.push([d.x, d.y]);
      writePairs(ps, [...pairs().map((_, k) => k), { new: true, dmin: Math.max(0.5, c.d * 0.8).toFixed(2), dmax: (c.d * 1.25).toFixed(2) }]);
      toast(`mark ${ps.length} added at ${fmt(d.x, 3)}, ${fmt(d.y, 3)} (board frame). Save to keep it`, "ok");
    };
    const update = async (i) => {
      const c = await centre(); if (!c) return;
      const d = await toDesign(c.x, c.y); if (!d) return;
      const ps = pairs(); ps[i] = [d.x, d.y];
      writePairs(ps, ps.map((_, k) => k));
      toast(`mark ${i + 1} moved to ${fmt(d.x, 3)}, ${fmt(d.y, 3)}. Save to keep it`, "ok");
    };
    const go = async (i) => {
      if (!(await need())) return;
      const [x, y] = pairs()[i];
      try {
        const r = await API.req("GET", `/api/align/point?job=${encodeURIComponent(path)}&x=${x}&y=${y}&nominal=1`);
        const o = Cfg.offset(0);
        await API.run("led", { v: 3 });
        if (await API.run("move", { x: (r.x + o[0]).toFixed(3), y: (r.y + o[1]).toFixed(3), slow: 1 })) { await App.poll(); cam.grab(); }
      } catch (e) { toast(e.message, "bad"); }
    };
    const readOut = h("span", { class: "muted", style: "font-size:12px" });
    const read = async () => {
      if (this.dirty && !confirm("Save the job first? The marks are read from the saved file.")) return;
      if (this.dirty) await this.save();
      if (!(await need())) return;
      readOut.textContent = " reading the marks …";
      try {
        const r = await API.req("POST", `/api/align/marks?client=${API.st.client}&job=${encodeURIComponent(path)}`);
        readOut.textContent = ` ${r.marks.length} mark(s) read (${r.fit})` + (r.residuals_mm && r.residuals_mm.length ? `, residuals ${r.residuals_mm.map((v) => fmt(v, 3)).join(" / ")} mm` : "") + ": new marks are now in the board's frame";
        await App.poll(); cam.grab();
      } catch (e) { readOut.textContent = ""; toast(e.message, "bad"); }
    };
    const cam = Manual.coordCam(1);
    this.markCam = cam;
    const no = mark;
    const m = no >= 0 ? t(no) : ["mark", "Whole", "Auto"];
    const ps = pairs();
    card.append(h("h3", { style: "margin-top:0" }, "Marks (fiducials)"),
      h("div", { class: "kv", style: "align-items:center" },
        h("div", {}, "Marks for"), h("div", {}, no >= 0 ? this.editor("s", m[1], (v) => this.setField(no, 0, v), 90) : "Whole", h("span", { class: "muted" }, "  Whole or Mirror (each panel)")),
        h("div", {}, "Find marks"), h("div", {}, no >= 0 ? this.editor("s", m[2], (v) => this.setField(no, 1, v), 90) : "Auto", h("span", { class: "muted" }, "  Auto or Manual"))),
      h("div", { style: "display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px;align-items:start;margin-top:8px" },
        h("div", {},
          h("table", { class: "pick", style: "width:auto" }, h("tr", {}, h("th", {}), h("th", {}, "X"), h("th", {}, "Y"), h("th", {})),
            ps.map(([x, y], k) => h("tr", {}, h("th", {}, `Mark ${k + 1}`),
              h("td", {}, this.editor("f", x.toFixed(3), (v) => { const q = pairs(); q[k][0] = +v; writePairs(q, q.map((_, i) => i)); }, 85)),
              h("td", {}, this.editor("f", y.toFixed(3), (v) => { const q = pairs(); q[k][1] = +v; writePairs(q, q.map((_, i) => i)); }, 85)),
              h("td", {}, h("button", { class: "btn small", title: "the camera to this mark", onclick: () => go(k) }, "Go"), " ",
                h("button", { class: "btn small", title: "centre on the round mark under the camera and put this mark there", onclick: () => update(k) }, "Update"), " ",
                h("button", { class: "btn small ghost", title: "delete this mark", onclick: () => del(k) }, "✕"))))),
          ps.length ? null : h("div", { class: "muted" }, "no marks yet"),
          h("div", { class: "muted", style: "font-size:12px;margin:8px 0" },
            "To add or move a mark: 1. Read the marks now (the board's frame today), 2. bring the camera near the round mark (arrow keys, or a click on the picture), 3. Add as a new mark (it is centred on the circle first) or Update on a mark's row. Save the job."),
          h("div", { class: "row" }, h("button", { class: "btn", title: "the Gerber's round copper pads of no part, looked for by the head camera; the found ones farthest apart become the marks", onclick: auto }, "Find marks from the Gerber")), autoOut,
          h("div", { class: "row" }, h("button", { class: "btn", onclick: read }, "Read the marks now"), readOut),
          this.headScaleRow(),
          h("div", { class: "row" }, h("button", { class: "btn", onclick: centre }, "Centre on the circle"),
            h("button", { class: "btn primary", onclick: add }, "Add as a new mark"), shot)),
        cam.el));
    cam.start();
    return card;
  },

  raw(body) {
    const ta = h("textarea", { rows: 30, oninput: () => this.touch() }, this.lines.join("\n"));
    this.rawTa = ta;
    body.append(h("div", { class: "muted" }, "The whole file as text. Save writes it (Apply only takes it into the other tabs)."), ta,
      h("div", { class: "row" }, h("button", { class: "btn", onclick: () => { this.lines = ta.value.replace(/\r\n?/g, "\n").split("\n"); this.touch(); toast("applied; Save to write the file"); } }, "Apply")));
  },
};
// Board tab, Remap: the arrows jog the head (0.1 mm, Shift 1 mm) when not typing
document.addEventListener("keydown", (ev) => {
  const pcbCam = location.hash === "#jobfile" && JobFile.markCam && JobFile.markCam.el.isConnected;
  if (pcbCam && CamView.keysOn() && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) {
    const d = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] }[ev.key];
    if (d) {
      ev.preventDefault();
      const st = ev.shiftKey ? 1 : 0.1;
      (async () => { if (API.st.haveControl || (await API.take())) { await API.run("rel", { x: d[0] * st, y: d[1] * st, slow: 1 }); await App.poll(); JobFile.markCam.grab(); } })();
      return;
    }
  }
  if (location.hash !== "#jobfile" || JobFile.tab !== "board" || !JobFile.rmCam || !JobFile.rmCam.el.isConnected) return;
  if (!CamView.keysOn() || /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) return;
  const d = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] }[ev.key];
  if (!d) return;
  ev.preventDefault();
  const st = ev.shiftKey ? 1 : 0.1;
  (async () => { if (API.st.haveControl || (await API.take())) { await API.run("rel", { x: d[0] * st, y: d[1] * st, slow: 1 }); await App.poll(); if (!JobFile.rmCam.live) JobFile.rmCam.grab(); } })();
});
