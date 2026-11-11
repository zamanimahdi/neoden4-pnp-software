// Bulk feeders (neojob/bulkfeed.py, neoserver/bulk.py): up to 30 containers of loose parts on the table.
// Layout: the containers are one matrix of blocks (columns x rows, a block's size, the step from one
// block to the next), set with the head camera on block 1's corners and on the last block of the first
// row / column; a drawing shows what is set. Each block: the package its parts are (taught from one part:
// the top, then picked by the vacuum, its underside, put back), the pin-1 test, the up-camera exposure,
// the pick height. Scan = the block photographed and stitched, the parts found; click one on the
// picture: Look, Measure height, Teach as. A job takes its parts from a block: Job file -> Feeder settings.
const Bulk = {
  sel: null,
  async render(root) {
    if (!Cfg.loaded) await Cfg.load().catch(() => {});
    let d;
    try { d = await API.req("GET", "/api/bulk/list"); } catch (e) { root.append(h("div", { class: "card bad" }, e.message)); return; }
    const cs = d.containers || [];
    this.templates = d.templates || [];
    if (this.sel === null && cs.length) this.sel = cs[0].id;
    const need = async () => API.st.haveControl || (await API.take());
    const cam = Manual.coordCam(1);
    this.cam = cam;
    root.append(this.layout(d, cs, cam, need));
    const list = h("div", { class: "card" }, h("div", { class: "row", style: "margin-top:0" }, h("h3", { style: "margin:0" }, `Bulk feeders (${cs.length} of ${d.max})`),
      h("div", { class: "grow" }),
      h("button", { class: "btn", title: "one more block outside the matrix (its own corners)", onclick: () => { const used = new Set(cs.map((c) => +c.id)); let n = 1; while (used.has(n) && n <= d.max) n++; if (n > d.max) return toast("all 30 are defined", "warn"); this.sel = n; this.draft = { id: n, name: `Bulk ${n}`, pin1: true, exp_bottom: 300, nozzle: 1 }; App.show(); } }, "+ Single block")),
      h("table", { class: "pick" }, h("tr", {}, ["#", "Row / col", "Name", "Package", "Parts", "Scanned", "Direction"].map((t) => h("th", {}, t))),
        cs.map((c) => h("tr", { class: +c.id === +this.sel ? "sel" : "", onclick: () => { this.sel = c.id; this.draft = null; App.show(); } },
          h("td", {}, String(c.id)), h("td", { class: "muted" }, c.row ? `${c.row} / ${c.col}` : "own"), h("td", {}, c.name || ""),
          h("td", {}, c.package || h("span", { class: "muted" }, "not taught")),
          h("td", {}, c.count === 0 ? h("b", { style: "color:var(--bad)" }, "EMPTY: refill") : String(c.count ?? "—")),
          h("td", { class: "muted" }, c.scanned || "—"), h("td", { class: "muted" }, Bulk.dirName(c))))));
    root.append(list);
    const c = this.draft && +this.draft.id === +this.sel ? this.draft : cs.find((x) => +x.id === +this.sel);
    if (c) root.append(this.block(c, need));
    cam.start();
  },

  dirName(c) {
    const s = c.dir_source || (c.orient === "fixed" ? "laid" : "top");
    return { laid: "laid by hand", top: "head camera", bottom: "bottom camera" }[s] || s;
  },

  // ---- the layout: a matrix of blocks, set with the head camera, and its drawing ----
  layout(d, cs, cam, need) {
    const g = Object.assign({ cols: 3, rows: 2, x0: 0, y0: 0, w: 40, h: 25, px: 45, py: 30 }, d.grid || {});
    const camPos = () => { const p = (API.st.status || {}).pos; if (!p) { toast("position unknown", "bad"); return null; } const o = Cfg.offset(0); return [p[0] - o[0], p[1] - o[1]]; };
    const inp = {};
    const num = (k, step, w = 75) => (inp[k] = h("input", { type: "number", step, style: `width:${w}px`, value: g[k], oninput: () => { read(); draw(); } }));
    const read = () => { for (const k in inp) g[k] = +inp[k].value; };
    const put = (k, v) => { g[k] = +(+v).toFixed(k === "cols" || k === "rows" ? 0 : 3); inp[k].value = g[k]; };
    const gapX = h("span", { class: "muted" }), gapY = h("span", { class: "muted" });
    const setCorner1 = () => { const p = camPos(); if (p) { put("x0", p[0]); put("y0", p[1]); draw(); toast("block 1: lower-left corner", "ok"); } };
    const setCorner2 = () => { const p = camPos(); if (!p) return; put("w", p[0] - g.x0); put("h", p[1] - g.y0); if (g.w <= 0 || g.h <= 0) toast("the upper-right corner must be up and right of the lower-left", "warn"); draw(); };
    const setRowEnd = () => { const p = camPos(); if (!p) return; if (g.cols < 2) return toast("one column: no step in X", "warn"); put("px", (p[0] - g.x0) / (g.cols - 1)); draw(); };
    const setColEnd = () => { const p = camPos(); if (!p) return; if (g.rows < 2) return toast("one row: no step in Y", "warn"); put("py", (p[1] - g.y0) / (g.rows - 1)); draw(); };
    const go = async (x, y) => { if (!(await need())) return; await API.run("led", { v: 3 }); if (await API.run("move", { x: (x + Cfg.offset(0)[0]).toFixed(3), y: (y + Cfg.offset(0)[1]).toFixed(3), slow: 1 })) { await App.poll(); cam.start(); } };
    const apply = async () => {
      read();
      if (g.cols * g.rows > d.max) return toast(`at most ${d.max} blocks`, "warn");
      const over = cs.filter((c) => c.row && +c.id > g.cols * g.rows).length;
      if (!confirm(`Make ${g.cols * g.rows} bulk feeders (${g.cols} columns x ${g.rows} rows), numbered row by row from block 1?` +
        (over ? ` ${over} block(s) beyond that are removed.` : "") + " A block that moves loses its last scan.")) return;
      if (!(await need())) return;
      try { const r = await API.req("POST", `/api/bulk/grid?client=${API.st.client}`, JSON.stringify(g)); toast(`${r.containers} bulk feeders`, "ok"); App.show(); }
      catch (e) { toast(e.message, "bad"); }
    };
    // the drawing: the table (machine X right, Y up), the blocks, the camera
    const W = 520, H = 360, X1 = 420, Y1 = 404;
    const sx = (x) => (x / X1) * W, sy = (y) => H - (y / Y1) * H;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("style", "width:100%;background:#12161b;border-radius:6px;cursor:crosshair");
    const el = (tag, a, txt, parent = svg) => { const e = document.createElementNS("http://www.w3.org/2000/svg", tag); for (const k in a) e.setAttribute(k, a[k]); if (txt) e.textContent = txt; parent.append(e); return e; };
    let camMark = null;
    const draw = () => {
      svg.replaceChildren();
      el("rect", { x: 0, y: 0, width: W, height: H, fill: "none", stroke: "#2c343e" });
      for (let x = 0; x <= 400; x += 50) el("text", { x: sx(x) + 2, y: H - 3, fill: "#4b5663", "font-size": 9 }, String(x));
      for (let y = 50; y <= 400; y += 50) el("text", { x: 2, y: sy(y) + 3, fill: "#4b5663", "font-size": 9 }, String(y));
      cs.forEach((c) => {                                       // the bulk feeders now (blue; a click selects one)
        const r = el("rect", { x: sx(c.x0), y: sy(c.y1), width: sx(c.x1) - sx(c.x0), height: sy(c.y0) - sy(c.y1), "data-id": c.id,
          fill: +c.id === +this.sel ? "rgba(90,140,255,.4)" : "rgba(90,140,255,.12)", stroke: "#5a8cff", "stroke-width": 1, style: "cursor:pointer" });
        r.addEventListener("click", (ev) => { ev.stopPropagation(); this.sel = c.id; this.draft = null; App.show(); });
        el("text", { x: (sx(c.x0) + sx(c.x1)) / 2, y: (sy(c.y0) + sy(c.y1)) / 2 + 4, fill: "#cfe0ff", "font-size": 11, "text-anchor": "middle", "pointer-events": "none" }, "B" + c.id);
      });
      for (let r = 0; r < Math.max(1, g.rows); r++) for (let c = 0; c < Math.max(1, g.cols); c++) {   // the matrix being set (orange dashed)
        const x = g.x0 + c * g.px, y = g.y0 + r * g.py;
        el("rect", { x: sx(x), y: sy(y + g.h), width: Math.max(0, sx(x + g.w) - sx(x)), height: Math.max(0, sy(y) - sy(y + g.h)), fill: "none", stroke: "#ffb648", "stroke-dasharray": "4 3", "pointer-events": "none" });
        el("text", { x: sx(x) + 3, y: sy(y + g.h) + 10, fill: "#ffb648", "font-size": 9, "pointer-events": "none" }, String(r * g.cols + c + 1));
      }
      camMark = el("g", { "pointer-events": "none" });
      gapX.textContent = g.cols > 1 ? ` gap ${fmt(g.px - g.w, 2)} mm` : "";
      gapY.textContent = g.rows > 1 ? ` gap ${fmt(g.py - g.h, 2)} mm` : "";
      moveCam();
    };
    const moveCam = () => {
      const p = (API.st.status || {}).pos; if (!p || !camMark) return;
      const o = Cfg.offset(0), x = sx(p[0] - o[0]), y = sy(p[1] - o[1]);
      camMark.replaceChildren();
      el("line", { x1: x - 7, y1: y, x2: x + 7, y2: y, stroke: "#ff4d4d", "stroke-width": 2 }, null, camMark);
      el("line", { x1: x, y1: y - 7, x2: x, y2: y + 7, stroke: "#ff4d4d", "stroke-width": 2 }, null, camMark);
    };
    clearInterval(this.camTimer);
    this.camTimer = setInterval(() => { if (!svg.isConnected) return clearInterval(this.camTimer); moveCam(); }, 700);
    svg.addEventListener("click", (ev) => {                 // a click on the drawing: the camera goes there
      const b = svg.getBoundingClientRect();
      go(((ev.clientX - b.left) / b.width) * X1, (1 - (ev.clientY - b.top) / b.height) * Y1);
    });
    const row = (label, ...e) => h("div", { class: "sefield" }, h("span", {}, label), ...e);
    const camBtn = (f, tip) => h("button", { class: "btn small", title: tip, onclick: f }, "Set = camera");
    const form = h("div", { class: "seform" },
      h("div", { class: "muted", style: "font-size:12px;margin-bottom:6px" },
        "The containers stand in a matrix. Put the camera's crosshair on each point (arrow keys jog 0.1 mm, Shift 1 mm; a click on the drawing moves the camera there) and press its button. The blocks are numbered row by row from block 1 (lower left)."),
      row("Columns x rows", num("cols", "1", 55), " x ", num("rows", "1", 55), h("span", { class: "muted" }, ` (at most ${d.max})`)),
      row("1. Block 1: lower-left corner", camBtn(setCorner1, "the crosshair on the inside lower-left corner of block 1"), num("x0", "0.01"), num("y0", "0.01")),
      row("2. Block 1: upper-right corner", camBtn(setCorner2, "the crosshair on the inside upper-right corner of block 1: the block's size"),
        h("span", { class: "muted" }, "size "), num("w", "0.01", 65), " x ", num("h", "0.01", 65), " mm"),
      row("3. Last block of row 1: lower-left", camBtn(setRowEnd, "the same corner on the right-most block of the first row: the step in X"),
        h("span", { class: "muted" }, "step X "), num("px", "0.01", 65), gapX),
      row("4. Top block of column 1: lower-left", camBtn(setColEnd, "the same corner on the top block of the first column: the step in Y"),
        h("span", { class: "muted" }, "step Y "), num("py", "0.01", 65), gapY),
      h("div", { class: "row" }, h("button", { class: "btn primary", onclick: apply }, "Apply: make the bulk feeders"),
        h("button", { class: "btn small", onclick: () => { read(); go(g.x0, g.y0); } }, "Camera to block 1")),
      h("div", { class: "muted", style: "font-size:12px" }, "Drawing: blue = the bulk feeders now (click one to edit it), orange dashed = the matrix above before Apply, red cross = the camera."));
    draw();
    return h("div", { class: "card" }, h("h3", { style: "margin-top:0" }, "Layout"),
      h("div", { style: "display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr) minmax(0,1fr);gap:12px;align-items:start" }, form, svg, cam.el));
  },

  // ---- one block: its settings, the last scan ----
  block(c, need) {
    const num = (key, step = "0.01", w = 80) => h("input", { type: "number", step, style: `width:${w}px`, value: c[key] ?? "", onchange: (e) => (c[key] = e.target.value === "" ? null : +e.target.value) });
    const inputs = { x0: num("x0"), y0: num("y0"), x1: num("x1"), y1: num("y1") };
    const here = (kx, ky) => { const p = (API.st.status || {}).pos; if (!p) return toast("position unknown", "bad"); const o = Cfg.offset(0); c[kx] = +(p[0] - o[0]).toFixed(3); c[ky] = +(p[1] - o[1]).toFixed(3); inputs[kx].value = c[kx]; inputs[ky].value = c[ky]; };
    const pkg = h("select", { onchange: (e) => (c.package = e.target.value) }, h("option", { value: "" }, "(teach one: Teach as on the scan)"),
      (this.templates || []).map((t) => h("option", { value: t, selected: t === c.package }, t)));
    const pin1 = h("input", { type: "checkbox", checked: c.pin1 !== false, onchange: (e) => (c.pin1 = e.target.checked) });
    const save = async () => {
      if (!(await need())) return;
      try { await API.req("POST", `/api/bulk/save?client=${API.st.client}`, JSON.stringify(c)); this.draft = null; toast("saved", "ok"); App.show(); } catch (e) { toast(e.message, "bad"); }
    };
    const out = h("div");
    const scan = async () => {
      if (!(await need())) return;
      if (c.x0 === undefined || c.x1 === undefined) return toast("set the block's place first", "warn");
      await save();
      out.replaceChildren(h("div", { class: "muted" }, "scanning …"));
      try { const r = await API.req("POST", `/api/bulk/scan?client=${API.st.client}&id=${c.id}`); toast(`${r.count} parts seen`, r.count ? "ok" : "warn"); App.show(); }
      catch (e) { out.replaceChildren(); toast(e.message, "bad"); }
    };
    const teachName = h("input", { type: "text", placeholder: "package name, e.g. SOIC-8", style: "width:170px", value: c.package || "" });
    const act = async (what, x, y) => {
      if (!(await need())) return;
      try {
        if (what === "teach") {
          if (!teachName.value.trim()) return toast("a package name first", "warn");
          if (!confirm(`Teach the part at ${fmt(x, 2)}, ${fmt(y, 2)} as "${teachName.value.trim()}"? It must lie the right way round (as the feeder's Pick angle means). It is picked, its underside photographed, then put back.`)) return;
          out.replaceChildren(h("div", { class: "muted" }, "teaching: the top, the pick, the underside, back …"));
          const r = await API.req("POST", `/api/bulk/teach?client=${API.st.client}&id=${c.id}&x=${x}&y=${y}&name=${encodeURIComponent(teachName.value.trim())}&nozzle=${c.nozzle || 1}`);
          out.replaceChildren(h("div", { class: "row" }, h("b", {}, `taught ${r.name}: ${r.bottom_metal} metal underneath, picked at ${fmt(r.seal_z, 1)} mm`)),
            h("div", { class: "row" }, h("img", { src: "data:image/png;base64," + r.png_top, style: "width:48%" }), h("img", { src: "data:image/png;base64," + r.png_bottom, style: "width:48%" })));
        } else if (what === "measure") {
          if (!confirm(`Read the pick height on the part at ${fmt(x, 2)}, ${fmt(y, 2)}? The nozzle goes down slowly until the pressure falls, then puts the part back.`)) return;
          out.replaceChildren(h("div", { class: "muted" }, "measuring the pick height …"));
          const r = await API.req("POST", `/api/bulk/measure?client=${API.st.client}&id=${c.id}&x=${x}&y=${y}`); out.replaceChildren(); toast(`pick height ${fmt(r.z_pick, 2)} mm`, "ok"); App.show();
        } else {
          out.replaceChildren(h("div", { class: "muted" }, "looking …"));
          const r = await API.req("POST", `/api/bulk/look?client=${API.st.client}&id=${c.id}&x=${x}&y=${y}`);
          out.replaceChildren(h("div", { class: "row" }, h("b", {}, r.package ? `${r.package}: ${r.is_it ? "yes" : "NO"} (distance ${fmt(r.distance, 2)}), turned ${fmt(r.turn, 1)}° by ${r.by}` : (r.why || "looked"))),
            h("img", { src: "data:image/png;base64," + r.png, style: "width:60%" }));
        }
      } catch (e) { out.replaceChildren(); toast(e.message, "bad"); }
    };
    const mos = h("img", { style: "width:100%;cursor:crosshair;border-radius:4px", src: `/api/bulk/mosaic?id=${c.id}&t=${Date.now()}`, onerror: (e) => (e.target.style.display = "none") });
    const partsSel = h("div", { class: "muted", style: "font-size:12px" }, c.parts ? `${c.parts.length} parts in the last scan: click one` : "not scanned yet");
    mos.onclick = (e) => {
      const m = c.mosaic, b = mos.getBoundingClientRect();
      if (!m || !c.parts || !c.parts.length) return;
      const X = m.x0 + (e.clientX - b.left) * mos.naturalWidth / b.width / m.k, Y = m.y1 - (e.clientY - b.top) * mos.naturalHeight / b.height / m.k;
      const ch = c.parts.slice().sort((a, b_) => Math.hypot(a.x - X, a.y - Y) - Math.hypot(b_.x - X, b_.y - Y))[0];
      partsSel.replaceChildren(`part at ${fmt(ch.x, 2)}, ${fmt(ch.y, 2)} (${fmt(ch.L, 1)} x ${fmt(ch.W, 1)} mm) `,
        h("button", { class: "btn small", onclick: () => act("look", ch.x, ch.y) }, "Look"), " ",
        h("button", { class: "btn small", onclick: () => act("measure", ch.x, ch.y) }, "Measure height"), " ",
        h("button", { class: "btn small primary", onclick: () => act("teach", ch.x, ch.y) }, "Teach as"), " ", teachName);
    };
    const f = (label, ...el) => h("div", { class: "sefield" }, h("span", {}, label), ...el);
    const tip = (t) => h("span", { class: "muted", style: "font-size:12px" }, t);
    return h("div", { class: "stackedit", style: "grid-template-columns:minmax(0,1fr) minmax(0,1.2fr)" },
      h("div", { class: "card seform" }, h("h3", { style: "margin-top:0" }, `Bulk feeder ${c.id}` + (c.row ? ` (row ${c.row}, column ${c.col})` : "")),
        f("Name", h("input", { type: "text", value: c.name || "", onchange: (e) => (c.name = e.target.value) })),
        h("fieldset", {}, h("legend", {}, c.row ? "Place (from the layout: change it there)" : "Place (head camera)"),
          f("Lower-left X / Y", inputs.x0, inputs.y0, c.row ? null : h("button", { class: "btn small", onclick: () => here("x0", "y0") }, "Set = camera")),
          f("Upper-right X / Y", inputs.x1, inputs.y1, c.row ? null : h("button", { class: "btn small", onclick: () => here("x1", "y1") }, "Set = camera"))),
        h("fieldset", {}, h("legend", {}, "Parts"),
          f("Package", pkg, tip("taught from one part: click it on the scan, Teach as")),
          f("Pin-1 mark", h("label", {}, pin1, " use it"), tip("off when the camera cannot see it well: the leads / the picture decide")),
          f("Underside exposure", num("exp_bottom", "50", 70), tip("bottom camera (300 on this machine)")),
          f("Nozzle", num("nozzle", "1", 50)),
          f("Pick height (mm)", num("z_pick", "0.05", 70), tip("where the nozzle meets the parts; empty = read once by the pressure sensor (Measure height)"))),
        tip("The part, its direction and what to do when a part is not picked: Job file → Feeder settings → this bulk feeder."),
        h("div", { class: "row" }, h("button", { class: "btn primary", onclick: save }, "Save"), h("button", { class: "btn", onclick: scan }, "Scan"),
          h("button", { class: "btn ghost", onclick: async () => { if (!confirm(`Delete bulk feeder ${c.id}?`) || !(await need())) return; await API.req("POST", `/api/bulk/delete?client=${API.st.client}&id=${c.id}`); this.sel = null; App.show(); } }, "Delete"))),
      h("div", { class: "card" }, h("h3", { style: "margin-top:0" }, "Last scan"), mos, partsSel, out));
  },
};
// the arrows jog the head on this page (0.1 mm, Shift 1 mm)
document.addEventListener("keydown", (ev) => {
  if (location.hash !== "#bulk" || !Bulk.cam || !Bulk.cam.el.isConnected || !CamView.keysOn() || /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) return;
  const d = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] }[ev.key];
  if (!d) return;
  ev.preventDefault();
  const st = ev.shiftKey ? 1 : 0.1;
  (async () => { if (API.st.haveControl || (await API.take())) { await API.run("rel", { x: d[0] * st, y: d[1] * st, slow: 1 }); await App.poll(); } })();
});
