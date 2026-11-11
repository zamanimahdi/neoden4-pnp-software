// Board points: go over a board with the head camera (or a nozzle), record the machine coordinates
// of features, compare with their nominal coordinates and fit the machine's axis errors.
// Point = head XY - offset(tool) (SETUP_SPEC section 6): with the camera, the point under the crosshair.
const Points = {
  list: JSON.parse(localStorage.getItem("boardPoints") || "[]"),   // {name, x, y, nx, ny}
  tool: 0,

  store() { localStorage.setItem("boardPoints", JSON.stringify(this.list)); },

  card() {
    const cam = new CamView(1, { onClick: (dx, dyUp) => this.moveRel(dx / Cfg.pxmm(1), dyUp / Cfg.pxmm(1)) });
    this.cam = cam;
    const toolBtns = ["Camera", "Nozzle 1", "Nozzle 2", "Nozzle 3", "Nozzle 4"].map((t, i) => h("button", {
      class: "btn" + (i === this.tool ? " on" : ""),
      onclick: async (e) => {                         // keep the same point under the new tool
        if (!Cfg.loaded) await Cfg.load().catch(() => {});
        const p = this.head(); if (!p) return toast("home first", "bad");
        const o = Cfg.offset(this.tool), n = Cfg.offset(i);
        this.tool = i;
        [...e.target.parentNode.children].forEach((b) => b.classList.remove("on")); e.target.classList.add("on");
        await API.run("move", { x: (p[0] - o[0] + n[0]).toFixed(4), y: (p[1] - o[1] + n[1]).toFixed(4), slow: 1 });
        await App.poll(); this.show();
      } }, t));
    const lamp = h("select", { onchange: (e) => API.run("led", { v: e.target.value }) },
      [["1", "Inner lamp"], ["2", "Outer lamp"], ["3", "Both"], ["0", "Off"]].map(([v, t]) => h("option", { value: v }, t)));
    this.here = h("div", { class: "mono" });
    this.tbl = h("div", { class: "scroll" });
    this.fit = h("div");
    const csvIn = h("input", { type: "file", accept: ".csv", style: "display:none", onchange: async (e) => {
      if (e.target.files[0]) { this.fromCsv(await e.target.files[0].text()); e.target.value = ""; }
    } });
    const card = h("div", { class: "card", style: "grid-column: 1 / -1" },
      h("h3", {}, "Board points (measure coordinates for calibration)"),
      h("div", { class: "grid", style: "grid-template-columns: minmax(300px, 560px) minmax(320px, 1fr)" },
        h("div", {}, cam.el, cam.controls(),
          h("div", { class: "row" }, h("label", {}, "Lamp "), lamp,
            h("button", { class: "btn", onclick: async () => { await API.req("POST", "/api/cam/set?cam=1&w=1024&h=1024&exposure=25&gain=8").catch((e) => toast(e.message, "bad")); cam.grab(); } }, "Head camera 1024²")),
          h("div", { class: "muted" }, "Click a feature to bring it under the crosshair; jog with the XY pad or the keyboard.")),
        h("div", {},
          h("div", { class: "row" }, toolBtns),
          this.here,
          h("div", { class: "row" },
            h("button", { class: "btn primary", onclick: () => this.record() }, "Record point"),
            h("button", { class: "btn", onclick: () => this.save() }, "Save to machine"),
            h("button", { class: "btn", onclick: () => this.load() }, "Load"),
            h("button", { class: "btn", onclick: () => this.download() }, "Download CSV"),
            h("button", { class: "btn", onclick: () => csvIn.click() }, "Import CSV"), csvIn,
            h("button", { class: "btn", onclick: () => { if (confirm("Delete all points?")) { this.list = []; this.store(); this.show(); } } }, "Clear")),
          this.fit)),
      this.tbl);
    this.show();
    return card;
  },

  head() { return (API.st.status && API.st.status.pos) || null; },
  point() { const p = this.head(); if (!p) return null; const o = Cfg.offset(this.tool); return [p[0] - o[0], p[1] - o[1]]; },

  async moveRel(dx, dy) {
    await API.run("rel", { x: dx.toFixed(4), y: dy.toFixed(4), slow: 1 });
    await App.poll(); this.show(); if (this.tool === 0) this.cam.grab();
  },

  async record() {
    if (!Cfg.loaded) await Cfg.load().catch(() => {});
    await App.poll();
    const p = this.point();
    if (!p) return toast("position unknown: home first", "bad");
    this.list.push({ name: `P${this.list.length + 1}`, x: +p[0].toFixed(4), y: +p[1].toFixed(4), nx: "", ny: "", tool: this.tool });
    this.store(); this.show();
    toast(`recorded ${p[0].toFixed(3)}, ${p[1].toFixed(3)}`, "ok");
  },

  async goTo(i) {
    if (!Cfg.loaded) await Cfg.load().catch(() => {});
    const o = Cfg.offset(this.tool), q = this.list[i];
    await API.run("move", { x: (q.x + o[0]).toFixed(4), y: (q.y + o[1]).toFixed(4), slow: 1 });
    await App.poll(); this.show(); if (this.tool === 0) this.cam.grab();
  },

  show() {
    const p = this.point();
    if (this.here) this.here.textContent = p ? `point under the ${this.tool ? "nozzle " + this.tool : "camera"}: X ${p[0].toFixed(3)}  Y ${p[1].toFixed(3)}` : "position unknown: home first";
    if (!this.tbl) return;
    const fit = this.fitNow();
    const num = (i, k) => h("input", { type: "number", step: "0.001", value: this.list[i][k], style: "width:90px",
      onchange: (e) => { this.list[i][k] = e.target.value === "" ? "" : +e.target.value; this.store(); this.show(); } });
    this.tbl.replaceChildren(h("table", {},
      h("tr", {}, ["#", "Name", "Measured X", "Measured Y", "Nominal X", "Nominal Y", "Residual", "Step from previous", ""].map((t) => h("th", {}, t))),
      this.list.map((q, i) => {
        const prev = this.list[i - 1];
        const d = prev ? Math.hypot(q.x - prev.x, q.y - prev.y) : null;
        const r = fit && fit.res[i];
        return h("tr", {}, h("td", {}, String(i + 1)),
          h("td", {}, h("input", { value: q.name, style: "width:70px", onchange: (e) => { q.name = e.target.value; this.store(); } })),
          h("td", { class: "mono" }, q.x.toFixed(3)), h("td", { class: "mono" }, q.y.toFixed(3)),
          h("td", {}, num(i, "nx")), h("td", {}, num(i, "ny")),
          h("td", { class: "mono" }, r ? `${(r[0] * 1000).toFixed(0)}, ${(r[1] * 1000).toFixed(0)} µm` : ""),
          h("td", { class: "mono" }, d === null ? "" : `${d.toFixed(3)} mm`),
          h("td", {}, h("button", { class: "btn", onclick: () => this.goTo(i) }, "Go to"),
            h("button", { class: "btn", onclick: () => { this.list.splice(i, 1); this.store(); this.show(); } }, "✕")));
      })));
    this.fit.replaceChildren(fit ? h("div", { class: "card", style: "margin-top:8px" }, h("h3", {}, `Fit of ${fit.n} points (nominal → measured)`),
      h("div", { class: "kv" },
        h("div", {}, "Model"), h("div", {}, fit.model),
        h("div", {}, "Scale X"), h("div", { class: "mono" }, `${fit.sx.toFixed(6)}  (${((fit.sx - 1) * 100).toFixed(3)} %, ${((fit.sx - 1) * 100).toFixed(3)} mm per 100 mm)`),
        h("div", {}, "Scale Y"), h("div", { class: "mono" }, `${fit.sy.toFixed(6)}  (${((fit.sy - 1) * 100).toFixed(3)} %)`),
        h("div", {}, "Rotation"), h("div", { class: "mono" }, `${fit.rot.toFixed(4)}°`),
        h("div", {}, "X/Y skew (non-square)"), h("div", { class: "mono" }, fit.skew === null ? "– (needs 3+ points)" : `${fit.skew.toFixed(4)}°`),
        h("div", {}, "Offset"), h("div", { class: "mono" }, `${fit.tx.toFixed(3)}, ${fit.ty.toFixed(3)} mm`),
        h("div", {}, "Residual RMS / max"), h("div", { class: "mono" }, `${(fit.rms * 1000).toFixed(1)} / ${(fit.max * 1000).toFixed(1)} µm`)),
      h("div", { class: "muted" }, "Enter the nominal coordinates (from the board drawing) of at least 2 points (3+ for scale per axis and skew). " +
        "Scale ≠ 1 means the axis moves more or less than commanded; skew is the error of the 90° between X and Y."))
      : h("div", { class: "muted" }, "Enter nominal X/Y for 2 or more points to see the axis errors."));
  },

  // least squares: affine (3+ points) or similarity (2 points), nominal (u,v) -> measured (x,y)
  fitNow() {
    const P = this.list.map((q, i) => ({ i, u: +q.nx, v: +q.ny, x: q.x, y: q.y })).filter((q) => q.u !== "" && this.list[q.i].nx !== "" && this.list[q.i].ny !== "");
    const n = P.length;
    if (n < 2) return null;
    let a, b, c, d, e, f, model;
    if (n === 2) {                                       // x + iy = s e^{iθ} (u + iv) + t
      const [p, q] = P;
      const du = q.u - p.u, dv = q.v - p.v, dx = q.x - p.x, dy = q.y - p.y;
      const den = du * du + dv * dv; if (!den) return null;
      const re = (dx * du + dy * dv) / den, im = (dy * du - dx * dv) / den;
      a = re; b = -im; d = im; e = re;
      c = p.x - (a * p.u + b * p.v); f = p.y - (d * p.u + e * p.v);
      model = "similarity (2 points: one scale for both axes, rotation, offset)";
    } else {
      const solve = (t) => {                          // normal equations for t = a u + b v + c
        let S = [[0, 0, 0], [0, 0, 0], [0, 0, 0]], r = [0, 0, 0];
        for (const q of P) { const row = [q.u, q.v, 1]; for (let j = 0; j < 3; j++) { r[j] += row[j] * t(q); for (let k = 0; k < 3; k++) S[j][k] += row[j] * row[k]; } }
        return gauss(S, r);
      };
      const X = solve((q) => q.x), Y = solve((q) => q.y);
      if (!X || !Y) return null;
      [a, b, c] = X; [d, e, f] = Y;
      model = "affine (scale per axis, rotation, skew, offset)";
    }
    const res = {};
    let ss = 0, mx = 0;
    for (const q of P) {
      const rx = q.x - (a * q.u + b * q.v + c), ry = q.y - (d * q.u + e * q.v + f);
      res[q.i] = [rx, ry];
      const m = Math.hypot(rx, ry); ss += m * m; mx = Math.max(mx, m);
    }
    const deg = 180 / Math.PI, ax = Math.atan2(d, a), ay = Math.atan2(-b, e);
    return { n, model, sx: Math.hypot(a, d), sy: Math.hypot(b, e), rot: ax * deg, skew: n >= 3 ? (ay - ax) * deg : null,
             tx: c, ty: f, res, rms: Math.sqrt(ss / n), max: mx, m: [a, b, c, d, e, f] };
  },

  csv() {
    return "name,x,y,nominal_x,nominal_y,tool\n" + this.list.map((q) => [q.name, q.x, q.y, q.nx, q.ny, q.tool ?? 0].join(",")).join("\n") + "\n";
  },
  fromCsv(text) {
    const lines = text.trim().split(/\r?\n/).slice(1);
    this.list = lines.map((l) => { const [name, x, y, nx, ny, tool] = l.split(","); return { name, x: +x, y: +y, nx: nx === "" ? "" : +nx, ny: ny === "" ? "" : +ny, tool: +tool || 0 }; });
    this.store(); this.show(); toast(`${this.list.length} points`, "ok");
  },
  dir() { return PathU.parent(Cfg.path); },
  async save() {
    try { const r = await API.req("PUT", `/api/file?path=${encodeURIComponent(PathU.join(this.dir(), "points.csv"))}`, this.csv()); toast(`saved ${PathU.join(this.dir(), "points.csv")}`, "ok"); }
    catch (e) { toast(e.message, "bad"); }
  },
  async load() {
    try { const r = await API.req("GET", `/api/file?path=${encodeURIComponent(PathU.join(this.dir(), "points.csv"))}`, null, true); this.fromCsv(await r.text()); }
    catch (e) { toast(e.message, "bad"); }
  },
  download() {
    const a = h("a", { href: URL.createObjectURL(new Blob([this.csv()], { type: "text/csv" })), download: "points.csv" });
    a.click();
  },
};

function gauss(A, b) {                                 // 3x3 linear solve with partial pivoting
  const M = A.map((r, i) => [...r, b[i]]), n = 3;
  for (let i = 0; i < n; i++) {
    let p = i; for (let r = i + 1; r < n; r++) if (Math.abs(M[r][i]) > Math.abs(M[p][i])) p = r;
    if (Math.abs(M[p][i]) < 1e-12) return null;
    [M[i], M[p]] = [M[p], M[i]];
    for (let r = 0; r < n; r++) if (r !== i) { const k = M[r][i] / M[i][i]; for (let c = i; c <= n; c++) M[r][c] -= k * M[i][c]; }
  }
  return M.map((r, i) => r[n] / r[i]);
}
