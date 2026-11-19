// Job setup wizard: Altium Pick & Place CSV -> NeoDen machine file, on the machine through
// neosetup (the KFG tool as a library; docs/SETUP_WIZARD.md). Every step calls the server:
// POST /api/setup/call {sid, method, args, kwargs}. One tool project per browser tab (sid).
const Wizard = {
  step: 0,
  sid: sessionStorage.getItem("setupSid") || (() => { const s = "wz-" + Math.random().toString(36).slice(2, 10); sessionStorage.setItem("setupSid", s); return s; })(),
  jobs: "",            // folder for uploads and outputs: <neoden_dir>/Jobs
  proc: "",            // NeoDen job folder: <neoden_dir>/Proc
  name: localStorage.getItem("wzName") || "",
  log: [],
  al: { pairs: [], pick: 0, meas: {}, plan: null, mode: "parts", pt: {}, applied: false },   // alignment step state
  steps: [["files", "1 Files"], ["options", "2 Options"], ["parts", "3 Parts"], ["board", "4 Board"], ["align", "5 Alignment"],
          ["marks", "6 Fiducials"], ["feeders", "7 Feeders"], ["order", "8 Order"], ["speed", "9 Speed"], ["output", "10 Output"]],

  stepIcon: { files: "file", options: "sliders", parts: "chip", board: "board", align: "target", marks: "mark", feeders: "reel", order: "list", speed: "gauge", output: "out" },

  // ---- server calls ----
  async call(method, args, kwargs, quiet) {
    const r = await fetch(location.origin + "/api/setup/call", { method: "POST", cache: "no-store", credentials: "same-origin",
      body: JSON.stringify({ sid: this.sid, method, args: args || [], kwargs: kwargs || {} }) });
    let j;
    try { j = await r.json(); } catch (e) { throw new Error(`${method}: ${r.status} ${r.statusText}`); }
    this.messages(j.messages || [], quiet);
    if (!j.ok) { const m = j.title ? `${j.title}: ${j.text}` : j.error; toast(m, "bad"); throw new Error(m); }
    return j.result;
  },
  messages(list, quiet) {
    for (const m of list) {
      this.log.unshift(m);
      if (!quiet && m.kind !== "check") toast(`${m.title}: ${String(m.text).split("\n")[0]}`, m.kind === "error" ? "bad" : m.kind === "warning" ? "warn" : "ok");
    }
    this.log = this.log.slice(0, 200);
  },
  async run(method, args, kwargs) {                   // mutating call, then redraw the step
    try { const r = await this.call(method, args, kwargs); App.show(); return r; } catch (e) { return null; }
  },
  async ask(text, method, args, kwargs) { if (confirm(text)) return this.run(method, args, kwargs); return null; },

  // ---- page ----
  async render(root) {
    if (!this.jobs) {
      const s = await API.req("GET", "/api/status").catch(() => ({}));
      if (s.neoden_dir) { this.jobs = PathU.join(s.neoden_dir, "Jobs"); this.proc = PathU.join(s.neoden_dir, "Proc"); }
    }
    const k = this.steps[this.step][0];
    root.append(h("div", { class: "tabs steps" }, this.steps.map(([, t], i) =>
      h("button", { class: "btn" + (i === this.step ? " on" : i < this.step ? " done" : ""), onclick: () => { this.step = i; App.show(); } },
        icon(this.stepIcon[this.steps[i][0]]), t))));
    const status = h("div", { class: "muted" });
    root.append(status);
    this.call("status", [], {}, true).then((st) => status.replaceChildren(...(st || []).map(([t]) => h("span", { style: "margin-right:14px" }, t)))).catch(() => {});
    const body = h("div", { class: "card" });
    root.append(body);
    try { await this[k](body); } catch (e) { body.append(h("div", { class: "bad" }, e.message)); }
    root.append(h("div", { class: "footbar" },
      h("button", { class: "btn", disabled: this.step === 0, onclick: () => { this.step--; App.show(); } }, "← Back"),
      h("button", { class: "btn primary", disabled: this.step === this.steps.length - 1, onclick: () => { this.step++; App.show(); } },
        this.step < this.steps.length - 1 ? `Next: ${this.steps[this.step + 1][1].replace(/^\d+ /, "")} →` : "Next →"),
      h("span", { class: "muted" }, `Step ${this.step + 1} of ${this.steps.length}`)),
      h("details", {}, h("summary", {}, `Tool messages (${this.log.length})`),
        h("div", { class: "scroll" }, this.log.map((m) => h("div", { class: "mono", style: "white-space:pre-wrap;margin:4px 0" }, `[${m.kind}] ${m.title}\n${m.text}`)))));
  },

  table(cols, rows, cells) {
    return h("div", { class: "scroll" }, h("table", {}, h("tr", {}, cols.map((c) => h("th", {}, c))), rows.map((r, i) => h("tr", {}, cells(r, i).map((c) => h("td", {}, c))))));
  },
  num(v, w) { return h("input", { type: "number", step: "any", value: v ?? "", style: `width:${w || 80}px` }); },

  // upload a local file into the jobs folder, return its path on the machine
  async upload(file) {
    const p = PathU.join(this.jobs, file.name);
    await API.req("PUT", `/api/file?path=${encodeURIComponent(p)}`, await file.arrayBuffer());
    return p;
  },
  filePick(label, accept, then) {
    const inp = h("input", { type: "file", accept });
    return h("div", { class: "filepick" }, h("label", {}, label), inp,
      h("button", { class: "btn", onclick: async () => {
        if (!inp.files[0]) return toast("choose a file first", "warn");
        try { await then(await this.upload(inp.files[0])); } catch (e) { toast(e.message, "bad"); }
      } }, "Load"));
  },

  zeroBL() { return localStorage.getItem("wzZeroBL") !== "0"; },
  async autoZero() {
    if (!this.zeroBL()) return;
    const z = await this.call("zero_bottom_left", [], {}, true).catch(() => null);
    if (z) toast(`0, 0 moved to the board's bottom-left (${z.source}): shift ${fmt(z.dx, 3)}, ${fmt(z.dy, 3)} mm`, "ok");
  },

  // ---- 1 files ----
  async files(b) {
    const proc = await API.req("GET", `/api/files?path=${encodeURIComponent(this.proc)}`).catch(() => ({ entries: [] }));
    const tmpl = h("select", {}, h("option", { value: "" }, "(none)"),
      (proc.entries || []).filter((e) => !e.dir && /\.csv$/i.test(e.name)).map((e) => h("option", { value: PathU.join(this.proc, e.name) }, e.name)));
    const name = h("input", { value: this.name, placeholder: "job name, e.g. GPS-MAIN-2" });
    name.onchange = () => { this.name = name.value.trim(); localStorage.setItem("wzName", this.name); };
    b.append(h("h3", {}, "Files"),
      h("div", { class: "grpgrid" },
        this.grp("Job",
          h("div", { class: "fields" }, h("label", {}, "Job name", name)),
          h("label", { class: "checkline" }, h("input", { type: "checkbox", checked: this.zeroBL(),
            onchange: (e) => localStorage.setItem("wzZeroBL", e.target.checked ? "1" : "0") }),
            " Put 0, 0 on the board's bottom-left corner after loading"),
          h("div", { class: "hint" }, "From the Gerber outline, else from the parts, whatever origin Altium used.")),
        this.grp("Design files",
          this.filePick("Pick & Place CSV (Altium)", ".csv,.txt", async (p) => {
            const r = await this.call("import_pnp", [p]);
            toast(`${r.components} components, ${r.placeable} placeable`, "ok");
            await this.autoZero();
            if (!this.name) { this.name = p.split(/[\\/]/).pop().replace(/\.[^.]*$/, ""); localStorage.setItem("wzName", this.name); }
            App.show();
          }),
          this.filePick("Gerber (.zip)", ".zip", async (p) => {
            const g = await this.call("load_gerber", [p]); toast(`Gerber: ${g.match[0]} of ${g.match[1]} matched`, "ok");
            await this.autoZero(); this.shapes = null; App.show();
          }),
          h("div", { class: "hint" }, "A .rar does not work on the machine: pack the Gerber files as .zip.")),
        this.grp("Start from",
          h("div", { class: "filepick" }, h("label", {}, "Template: a machine file from Proc"), tmpl,
            h("button", { class: "btn", onclick: () => tmpl.value && this.run("import_machine_file", [tmpl.value]) }, "Use as template")),
          this.filePick("A saved tool project (.kfg)", ".kfg,.json", async (p) => {
            if (!(await this.call("is_dirty", [], {}, true)) || confirm("Discard the unsaved work?")) { await this.call("load_project", [p]); App.show(); }
          })),
        this.grp("Project",
          h("div", { class: "btnrow" },
            h("button", { class: "btn", onclick: () => this.name ? this.run("save_project", [PathU.join(this.jobs, this.name + ".kfg")]) : toast("job name first", "warn") }, icon("save"), "Save project (.kfg) in Jobs"),
            h("button", { class: "btn", onclick: async () => { if (confirm("Start a new, empty project?")) { await API.req("POST", `/api/setup/close?sid=${this.sid}`); this.log = []; App.show(); } } }, icon("plus"), "New project")),
          h("div", { class: "hint" }, `Uploads and outputs go to ${this.jobs || "(Jobs folder)"}; NeoDen job files are in ${this.proc || "Proc"}.`))));
    const st = await this.call("stats", [], {}, true).catch(() => null);
    if (st) b.append(h("div", { class: "statstrip" }, Object.entries(st).map(([k, v]) => h("div", {}, h("b", {}, String(v)), h("span", {}, k.replace(/_/g, " "))))));
  },

  // ---- 2 options ----
  async options(b) {
    const o = await this.call("options", [], {}, true);
    const ins = {};
    b.append(h("h3", {}, "Machine options (the tool's Options dialog)"),
      h("table", {}, o.fields.map((f) => h("tr", {}, h("td", {}, f.label), h("td", {}, ins[f.key] = h("input", { value: f.value }))))),
      h("div", { class: "row" },
        h("button", { class: "btn primary", onclick: () => {
          const values = {}; for (const f of o.fields) if (ins[f.key].value !== String(f.value)) values[f.key] = ins[f.key].value;
          this.run("set_options", [], { values });
        } }, "Save options")),
      h("div", { class: "muted" }, `Taught slots: ${Object.keys(o.slots || {}).length}. Saved in neosetup-settings.json on the machine.`));
    const proc = await API.req("GET", `/api/files?path=${encodeURIComponent(this.proc)}`).catch(() => ({ entries: [] }));
    const f = h("select", {}, (proc.entries || []).filter((e) => !e.dir && /\.csv$/i.test(e.name)).map((e) => h("option", { value: PathU.join(this.proc, e.name) }, e.name)));
    b.append(h("div", { class: "row" }, h("label", {}, "Learn slots from a machine file "), f,
      h("button", { class: "btn", onclick: () => f.value && this.run("learn_slots", [f.value]) }, "Learn")));
  },

  // ---- 3 parts ----
  async parts(b) {
    const [bom, fps, sim] = await Promise.all([this.call("bom", [], {}, true), this.call("footprints", [], {}, true), this.call("similar_name_groups", [], {}, true)]);
    b.append(h("h3", {}, "Parts"));
    if (sim.length) b.append(h("div", { class: "notice warnbox" }, h("div", {}, h("b", {}, `${sim.length} part name(s) spelled more than one way`),
      h("div", { class: "hint" }, sim.map((g) => Object.keys(g.spellings).join(" / ")).join(";  "))),
      h("button", { class: "btn", onclick: () => this.run("merge_similar_names") }, "Merge all")));
    b.append(h("div", { class: "grpstack" },
      this.grp(`Parts (${bom.length})`,
        h("div", { class: "hint" }, "A part's height decides how deep it is placed. Set it here when the footprint's height below is not this part's."),
        this.table(["Part", "Qty", "Footprints", "Height", "Flag", "Set height (mm)", ""], bom, (p) => {
          const mm = this.num("", 70);
          return [p.part, p.qty, (p.footprints || []).join(" "), p.height_label, p.flag || "",
            h("div", { class: "btnrow tight" }, mm, h("button", { class: "btn", onclick: () => this.run("set_part_height", [p.part, mm.value === "" ? null : Number(mm.value)]) }, "Set")),
            h("button", { class: "btn ghost", title: `delete every ${p.part} from the list`, onclick: () => this.ask(`Delete every ${p.part}?`, "remove_part", [p.part]) }, "Delete")];
        })),
      this.grp(`Footprints (${fps.length})`,
        h("div", { class: "hint" }, "The height of every part with this footprint, unless the part has its own above."),
        this.table(["Footprint", "Qty", "Height", "Source", "Note", "Set height (mm)", ""], fps, (f) => {
          const mm = this.num(f.mm, 70);
          return [f.footprint, f.qty, f.height_label, f.source, f.note || "", mm,
            h("button", { class: "btn", onclick: () => this.run("set_footprint_height", [f.footprint, Number(mm.value)]) }, "Set")];
        }))));
  },

  // ---- 4 board ----
  grp(title, ...kids) { return h("fieldset", { class: "grp" }, h("legend", {}, title), ...kids); },
  async board(b) {
    const bd = await this.call("board", [], {}, true), side = await this.call("board_side", [], {}, true);
    const w = this.num(bd.width, 76), ht = this.num(bd.height, 76), ko = this.num(bd.keepout, 76);
    const fx = this.num("", 86), fy = this.num("", 86);
    const comps = await this.call("components", [], {}, true);
    const map = BoardMap.make({ sid: this.sid, comps, onPick: (x, y) => { fx.value = x.toFixed(3); fy.value = y.toFixed(3); } });
    b.classList.add("wz2");
    b.append(h("div", { class: "wzside" },
      h("h3", {}, "Board"),
      this.grp("Zero point",
        h("button", { class: "btn primary wide", onclick: async () => {
          const z = await this.run("zero_bottom_left");
          if (z) toast(`0, 0 is now the board's bottom-left (${z.source}); board ${fmt(z.size[0], 2)} × ${fmt(z.size[1], 2)} mm`, "ok");
        } }, "Put 0, 0 on the bottom-left corner"),
        h("div", { class: "hint" }, "From the Gerber outline when it is loaded, else from the parts.")),
      this.grp("Turn and flip",
        h("div", { class: "btnrow" },
          [-90, 90, 180].map((d) => h("button", { class: "btn", onclick: () => this.run("rotate_board_quarter", [d]) }, `${d > 0 ? "+" : ""}${d}°`)),
          h("button", { class: "btn", onclick: () => this.run("flip_x") }, "Flip X"), h("button", { class: "btn", onclick: () => this.run("flip_y") }, "Flip Y"))),
      this.grp("Size (mm)",
        h("div", { class: "fields" }, h("label", {}, "Width", w), h("label", {}, "Height", ht), h("label", {}, "Keep-out", ko)),
        h("button", { class: "btn", onclick: () => this.run("set_board", [], { width: Number(w.value) || null, height: Number(ht.value) || null, keepout: ko.value === "" ? null : Number(ko.value) }) }, "Set size")),
      this.grp("Fiducial of the design",
        h("div", { class: "fields" }, h("label", {}, "X", fx), h("label", {}, "Y", fy)),
        h("div", { class: "btnrow" },
          h("button", { class: "btn", onclick: () => this.run("add_fiducial", [Number(fx.value), Number(fy.value)]) }, "Add fiducial"),
          h("button", { class: "btn", onclick: () => this.run("apply_offset", [Number(fx.value), Number(fy.value)]) }, "Move first fiducial there")),
        h("div", { class: "hint" }, "A click on the board fills X, Y. The marks the machine reads are set with the camera in step 6 (Fiducials).")),
      h("div", { class: "hint" }, `Side: ${side[0]} (${side[1]})` + (bd.extent ? ` · parts from ${fmt(bd.extent[0], 2)}, ${fmt(bd.extent[1], 2)} to ${fmt(bd.extent[2], 2)}, ${fmt(bd.extent[3], 2)} mm` : "") + (bd.mirrored ? " · seen from the bottom (mirrored)" : ""))),
      h("div", { class: "wzmain" }, map.el));
  },

  map(comps, bd) {                                   // plain 2D view from the data (the tool's map is not an API)
    const cv = h("canvas", { width: 640, height: 400, style: "background:#111;max-width:100%;margin-top:8px" });
    const c = cv.getContext("2d");
    const xs = comps.map((p) => p.x), ys = comps.map((p) => p.y);
    if (!xs.length) return cv;
    const x0 = Math.min(0, ...xs), x1 = Math.max(bd.width || 0, ...xs), y0 = Math.min(0, ...ys), y1 = Math.max(bd.height || 0, ...ys);
    const k = Math.min(620 / Math.max(1, x1 - x0), 380 / Math.max(1, y1 - y0));
    const X = (x) => 10 + (x - x0) * k, Y = (y) => 390 - (y - y0) * k;
    if (bd.width && bd.height) { c.strokeStyle = "#4a4"; c.strokeRect(X(0), Y(bd.height), bd.width * k, bd.height * k); }
    for (const p of comps) { c.fillStyle = p.fiducial ? "#ff0" : p.layer && /bottom/i.test(p.layer) ? "#58f" : "#e66"; c.fillRect(X(p.x) - 2, Y(p.y) - 2, 4, 4); }
    return cv;
  },

  // ---- 5 alignment (head camera) ----
  async align(b) {
    const A = this.al;
    if (!A.pairs.length) A.pairs = await this.call("suggest_alignment", [6], {}, true).catch(() => []);
    const pair = A.pairs[A.pick];
    const camPt = () => { const p = API.st.status && API.st.status.pos; if (!p) return null; const o = Cfg.offset(0); return [p[0] - o[0], p[1] - o[1]]; };
    this.alCam = Manual.coordCam(1);
    this.alShot = h("canvas", { width: 512, height: 512, class: "resimg" });
    this.alShotText = h("div", { class: "muted", style: "font-size:12px" }, "Find the part centre: the pads under the camera are found and boxed; the box centre is recorded.");
    const radius = this.num(localStorage.getItem("wzPartR") || 3, 60);
    radius.onchange = () => localStorage.setItem("wzPartR", radius.value);
    this.alRadius = radius;
    b.append(h("div", { class: "padtop", style: "margin-bottom:12px" },
      h("div", { class: "card" }, h("div", { class: "row", style: "margin-top:0" }, h("b", {}, "Head camera"), this.alCam.controls()), this.alCam.el),
      h("div", { class: "card" }, h("div", { class: "row", style: "margin-top:0" }, h("b", {}, "Pads found"), h("label", {}, "part size up to ±"), radius, h("label", {}, "mm")),
        this.alShot, this.alShotText),
      Manual.moveCard()));
    this.alCam.grab();
    b.append(h("h3", {}, "Alignment: put the head camera on each part (Move, arrow keys, click the picture), then record it"),
      h("div", { class: "row" }, h("label", {}, "Pair "), h("select", { onchange: (e) => { A.pick = Number(e.target.value); A.meas = {}; A.plan = null; App.show(); } },
        A.pairs.map((p, i) => h("option", { value: i, selected: i === A.pick }, `${p.a.designator} – ${p.b.designator}${p.c ? " (+" + p.c.designator + ")" : ""}  baseline ${fmt(p.baseline, 1)} mm`))),
        h("button", { class: "btn", onclick: async () => { A.pairs = []; A.meas = {}; A.plan = null; App.show(); } }, "Suggest again")));
    const fids = await this.call("fiducials", [], {}, true).catch(() => []);
    const pointsMode = A.mode === "points";
    b.append(h("div", { class: "row" }, h("label", {}, "Align by "),
      h("select", { onchange: (e) => { A.mode = e.target.value; A.meas = {}; A.plan = null; App.show(); } },
        h("option", { value: "parts", selected: !pointsMode }, "parts (a suggested pair)"),
        h("option", { value: "points", selected: pointsMode }, "board points (design X, Y typed in)")),
      h("span", { class: "muted", style: "font-size:12px" }, pointsMode ? "type a point of the board (a fiducial, a hole, a pad), put the camera on it, record it" : "")));
    const recBtns = (key) => h("div", { class: "row", style: "margin:0;flex-wrap:nowrap" },
      pointsMode ? h("button", { class: "btn primary", title: "centre the camera on the round mark under it and record that", onclick: () => this.circlePt(key) }, "Centre on the circle")
        : h("button", { class: "btn primary", title: "find the pads under the camera, box them and record the box centre", onclick: () => this.findCentre(key) }, "Find part centre"),
      h("button", { class: "btn", title: "record the camera crosshair as this point", onclick: () => { const p = camPt(); if (!p) return toast("no machine position (home first)", "bad"); A.meas[key] = p; A.plan = null; App.show(); } }, "Record crosshair"));
    const measTxt = (key) => (A.meas[key] ? `${fmt(A.meas[key][0], 3)}, ${fmt(A.meas[key][1], 3)}` : "–");
    let args, kw;
    const side = h("select", {}, ["auto", "top", "bottom"].map((s) => h("option", { value: s }, s)));
    if (pointsMode) {
      const ptIn = (key, i) => {
        const v = A.pt[key] ? A.pt[key][i] : "";
        const e = this.num(v === undefined || v === null ? "" : v, 80);
        e.onchange = () => { A.pt[key] = A.pt[key] || ["", ""]; A.pt[key][i] = e.value === "" ? "" : Number(e.value); A.plan = null; };
        return e;
      };
      const fromFid = (key) => fids.length ? h("select", { title: "take a fiducial's design X, Y", onchange: (e) => { const f = fids[+e.target.value]; if (f) { A.pt[key] = [f.x, f.y]; A.plan = null; App.show(); } } },
        h("option", { value: "" }, "fiducial …"), fids.map((f, i) => h("option", { value: i }, `${f.designator} ${fmt(f.x, 3)}, ${fmt(f.y, 3)}`))) : null;
      b.append(this.table(["Point", "Design X", "Design Y", "", "Measured X, Y", ""], ["a", "b", "c"], (key) => [key.toUpperCase() + (key === "c" ? " (check, optional)" : ""),
        ptIn(key, 0), ptIn(key, 1), fromFid(key), measTxt(key), recBtns(key)]));
      const ok = (key) => !!A.pt[key] && A.pt[key][0] !== "" && A.pt[key][1] !== "";
      args = () => [A.pt.a, A.pt.b, A.meas.a, A.meas.b];
      kw = () => Object.assign({ side: side.value }, ok("c") && A.meas.c ? { c: A.pt.c, measured_c: A.meas.c } : {});
      A.ready = () => ok("a") && ok("b");
    } else {
      if (!pair) return b.append(h("div", { class: "muted" }, "No pairs: load a Pick & Place file first."));
      const pts = [["a", pair.a], ["b", pair.b]].concat(pair.c ? [["c", pair.c]] : []);
      b.append(this.table(["Point", "Part", "Design X, Y", "Measured X, Y", ""], pts, ([key, comp]) => [key.toUpperCase(), `${comp.designator} ${comp.part} (${comp.footprint})`,
        `${fmt(comp.x, 3)}, ${fmt(comp.y, 3)}`, measTxt(key), recBtns(key)]));
      args = () => [pair.a.designator, pair.b.designator, A.meas.a, A.meas.b];
      kw = () => Object.assign({ side: side.value }, pair.c && A.meas.c ? { c: pair.c.designator, measured_c: A.meas.c } : {});
      A.ready = () => true;
    }
    b.append(h("div", { class: "row" }, h("label", {}, "Side "), side,
      h("button", { class: "btn", onclick: async () => { if (!A.ready()) return toast("type the design X, Y of A and B first", "warn"); if (!A.meas.a || !A.meas.b) return toast("record A and B first", "warn"); A.plan = await this.call("plan_alignment", args(), kw()).catch(() => null); App.show(); } }, "Compute"),
      h("button", { class: "btn primary", onclick: async () => {
        if (!A.plan) return toast("compute first", "warn");
        const r = await this.call("apply_alignment", args(), kw()).catch(() => null);
        if (r) { toast(`applied: ${fmt(r.applied_theta, 3)}°; camera on ${r.check_b ? r.check_b.designator : "B"} should read ${fmt(r.check_b && r.check_b.x, 3)}, ${fmt(r.check_b && r.check_b.y, 3)}`, "ok"); A.plan = null; A.pairs = []; A.applied = true; A.meas = {}; App.show(); }
      } }, "Apply")));
    if (A.plan) b.append(h("pre", { class: "mono", style: "white-space:pre-wrap" }, (A.plan.report || []).join("\n") +
      `\n\nθ ${fmt(A.plan.theta, 4)}°  scale ${fmt(A.plan.scale, 5)}  side ${A.plan.side} (${A.plan.decided_by})  fit ${A.plan.fit}` + (A.plan.rms !== undefined ? `  rms ${fmt(A.plan.rms, 3)}` : "")));
    b.append(h("div", { class: "hint", style: "margin-top:10px" }, A.applied ? "Aligned in this session. Next: step 6, the marks (fiducials) the machine reads before placing."
      : "After Apply the board's coordinates are the machine's. Then step 6: the marks (fiducials)."));
  },

  // the round mark under the head camera: centred on (POST /api/align/circle, the head moves onto it);
  // its centre is in the nozzle-1 frame, as the crosshair reading
  async circleAt() {
    if (!API.st.haveControl && !(await API.take())) return null;
    try {
      const r = await API.req("POST", `/api/align/circle?client=${API.st.client}`);
      await App.poll(); this.alCam.grab();
      this.alShotText.textContent = `round mark Ø ${fmt(r.d, 2)} mm centred: ${fmt(r.x, 3)}, ${fmt(r.y, 3)}`;
      const img = new Image();
      img.onload = () => this.alShot.getContext("2d").drawImage(img, 0, 0, this.alShot.width, this.alShot.height);
      img.src = "data:image/png;base64," + r.png;
      return r;
    } catch (e) { toast(e.message, "bad"); return null; }
  },
  async circlePt(key) {
    const r = await this.circleAt(); if (!r) return;
    this.al.meas[key] = [r.x, r.y]; this.al.plan = null; App.show();
  },

  // ---- 6 fiducials: the marks the machine reads before placing, set with the head camera on the board's picture ----
  // After Apply (step 5) the board's coordinates are the machine's: a click on the Gerber sends the camera
  // there, its live picture is drawn on the board where it looks, and the round mark under it is centred on
  // and added at what the camera reads. They become the program's mark line (one panel at the first part).
  async marks(b) {
    const A = this.al;
    const [marks, comps] = await Promise.all([this.call("marks", [], {}, true).catch(() => []), this.call("components", [], {}, true)]);
    const sure = () => A.applied || confirm("The alignment was not applied in this session: the board's coordinates may not be the machine's yet, so the camera may go to the wrong place and a mark may come out wrong. Go on?");
    const camPt = () => { const p = API.st.status && API.st.status.pos; if (!p) return null; const o = Cfg.offset(0); return [p[0] - o[0], p[1] - o[1]]; };
    this.alCam = Manual.coordCam(1);
    this.alShot = h("canvas", { width: 256, height: 256, class: "resimg small" });
    this.alShotText = h("div", { class: "hint" }, "Centre on the circle: the round mark nearest the picture's middle is found and the camera moved onto it.");
    const go = async ([x, y]) => {
      if (!sure()) return;
      if (!API.st.haveControl && !(await API.take())) return;
      const o = Cfg.offset(0);
      await API.run("led", { v: 3 });
      if (await API.run("move", { x: (x + o[0]).toFixed(3), y: (y + o[1]).toFixed(3), slow: 1 })) { await App.poll(); this.alCam.grab(); }
    };
    const add = async () => { if (!sure()) return; const r = await this.circleAt(); if (r) await this.run("add_mark", [r.x, r.y]); };
    const update = async (i) => { if (!sure()) return; const r = await this.circleAt(); if (r) await this.run("set_mark", [i, r.x, r.y]); };
    const map = BoardMap.make({ sid: this.sid, comps, marks,
      cam: () => { const p = camPt(); if (!p) return null; const f = this.alCam.frame; return { x: p[0], y: p[1], size: this.alCam.size / Cfg.pxmm(1), bmp: f && f.bmp }; },
      onPick: (x, y) => go([x, y]) });
    const tick = setInterval(() => { if (!map.live()) return clearInterval(tick); map.draw(); }, 400);     // the camera's frame follows the head
    b.classList.add("wz2");
    b.append(h("div", { class: "wzside wide" },
      h("h3", {}, "Fiducials (marks)"),
      h("div", { class: "hint" }, A.applied ? "Aligned in this session." : h("span", { class: "warn" }, "Not aligned yet in this session: do step 5 first.")),
      h("ol", { class: "steps-how" },
        h("li", {}, "Click the round mark on the board (right): the camera goes there."),
        h("li", {}, "Press Add mark here: the camera centres on the circle and the mark is added."),
        h("li", {}, "3 or 4 marks far apart (the board's corners).")),
      h("div", { class: "btnrow" },
        h("button", { class: "btn primary", title: "centre the camera on the round mark under it and add a mark there", onclick: add }, "Add mark here"),
        h("button", { class: "btn", title: "only centre the camera on the round mark under it", onclick: () => this.circleAt() }, "Centre on the circle")),
      h("div", { class: "camcard" }, h("div", { class: "row", style: "margin-top:0" }, h("b", {}, "Head camera"), this.alCam.controls()), this.alCam.el),
      this.alShotText,
      this.grp(`Marks (${marks.length})`,
        marks.length ? this.table(["", "X", "Y", ""], marks, (m, i) => [`M${i + 1}`, fmt(m[0], 3), fmt(m[1], 3),
          h("div", { class: "btnrow tight" },
            h("button", { class: "btn small", title: "the camera to this mark", onclick: () => go(m) }, "Go"),
            h("button", { class: "btn small", title: "centre on the round mark under the camera and move this mark there", onclick: () => update(i) }, "Update"),
            h("button", { class: "btn small ghost", title: "delete this mark", onclick: () => this.ask(`Delete mark ${i + 1}?`, "delete_mark", [i]) }, "✕"))])
          : h("div", { class: "hint" }, "No marks yet.")),
      h("details", {}, h("summary", {}, "Move the head by hand"), Manual.moveCard())),
      h("div", { class: "wzmain" }, map.el, h("div", { class: "row" }, h("span", { class: "muted" }, "Last centring:"), this.alShot)));
    this.alCam.grab();
  },

  // the part under the head camera: pads found by /api/align/detect (Pad alignment settings), the pads
  // within the part size around the pad nearest the centre are boxed; the box centre is the part centre
  async findCentre(key) {
    if (!API.st.haveControl && !(await API.take())) return;
    const P = (k, d) => Cfg.get("PadAlign/" + k, d);
    let r;
    try {
      r = await API.req("POST", `/api/align/detect?client=${API.st.client}&led=${P("nLed", 1)}&exp=${P("nExposure", 0)}&thr=${P("nThreshold", 0)}&invert=${P("bInvert", false) ? 1 : 0}&min=${P("fMinPadMm2", 0.05)}&max=${P("fMaxPadMm2", 25)}`);
    } catch (e) { this.alShotText.textContent = "pad detection: " + e.message; return toast(e.message, "bad"); }
    const at = r.at, R = Math.max(0.2, +this.alRadius.value || 3);
    const blobs = r.blobs || [];
    if (!blobs.length) { this.alShotText.textContent = `no pads found (mean grey ${r.mean}${r.hint ? ", " + r.hint : ""}): change the lamp / exposure in Settings → Pad alignment`; return; }
    const d = (b) => Math.hypot(b.x - at[0], b.y - at[1]);
    const near = blobs.reduce((m, b) => (d(b) < d(m) ? b : m));
    const sel = blobs.filter((b) => Math.hypot(b.x - near.x, b.y - near.y) <= 2 * R);
    const x0 = Math.min(...sel.map((b) => b.x - b.w / 2)), x1 = Math.max(...sel.map((b) => b.x + b.w / 2));
    const y0 = Math.min(...sel.map((b) => b.y - b.h / 2)), y1 = Math.max(...sel.map((b) => b.y + b.h / 2));
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    // picture with the box (pixel box from the pads' pixel centres and sizes)
    const k = Cfg.pxmm(1);
    const px0 = Math.min(...sel.map((b) => b.px - b.w * k / 2)), px1 = Math.max(...sel.map((b) => b.px + b.w * k / 2));
    const py0 = Math.min(...sel.map((b) => b.py - b.h * k / 2)), py1 = Math.max(...sel.map((b) => b.py + b.h * k / 2));
    const img = new Image();
    img.onload = () => {
      const c = this.alShot.getContext("2d"), W = this.alShot.width, s = W / img.width;
      c.drawImage(img, 0, 0, W, W);
      c.strokeStyle = "#22c55e"; c.lineWidth = 3; c.strokeRect(px0 * s, py0 * s, (px1 - px0) * s, (py1 - py0) * s);
      const mx = (px0 + px1) / 2 * s, my = (py0 + py1) / 2 * s;
      c.beginPath(); c.moveTo(mx - 12, my); c.lineTo(mx + 12, my); c.moveTo(mx, my - 12); c.lineTo(mx, my + 12); c.stroke();
    };
    img.src = "data:image/png;base64," + r.png;
    this.alShotText.textContent = `${sel.length} pads of ${blobs.length}; box ${fmt(x1 - x0, 2)} × ${fmt(y1 - y0, 2)} mm, centre ${fmt(cx, 3)}, ${fmt(cy, 3)}` +
      ` (${fmt(cx - at[0], 3)}, ${fmt(cy - at[1], 3)} from the crosshair) → recorded for ${key.toUpperCase()}`;
    this.al.meas[key] = [cx, cy]; this.al.plan = null;
    const row = [...document.querySelectorAll("td")].find((td) => td.textContent === key.toUpperCase());
    if (row) row.parentNode.children[3].textContent = `${fmt(cx, 3)}, ${fmt(cy, 3)}`;
    this.alCam.grab();
  },

  // ---- 7 feeders ----
  async feeders(b) {
    const prog = await this.call("program", [], {}, true);
    if (!prog.loaded) {
      const pb = await this.call("prepare_build", [], { top_only: true }, true);
      b.append(h("h3", {}, "Feeders"),
        this.grp("Build the machine program",
          h("div", { class: "hint" }, (pb.has_template ? "The loaded machine file is the template: its feeders are kept." : "No template: the feeder slots come from the tool's defaults (step 2, Options).")
            + " Change a feeder number or its nozzles in the row, then Build."),
          this.table(["Footprint", "Value", "Qty", "Feeder", "Nozzles", "Pick", "Place", "Speed", ""], pb.parts, (p) => {
            const fd = h("input", { value: p.feeder, style: "width:50px" }), nz = h("input", { value: Array.isArray(p.nozzles) ? p.nozzles.join("") : p.nozzles, style: "width:50px" });
            return [p.footprint, p.value, p.qty, fd, nz, p.pick, p.place, p.speed,
              h("button", { class: "btn", onclick: () => this.run("edit_build_part", [p.index], { feeder: fd.value, nozzles: nz.value }) }, "Set")];
          }),
          h("div", { class: "btnrow left" }, h("button", { class: "btn primary", onclick: () => this.run("build_machine", [], { top_only: true }) }, icon("check"), "Build the program"))));
      return;
    }
    const fs = await this.call("feeders", [], {}, true);
    const cols = ["id", "part", "footprint", "nozzles", "x", "y", "angle", "pick_height", "place_height", "vision", "speed"];
    const head = { id: "Feeder", part: "Part", footprint: "Footprint", nozzles: "Nozzles", x: "X", y: "Y", angle: "Angle", pick_height: "Pick height", place_height: "Place height", vision: "Vision", speed: "Speed" };
    const over = fs.filter((f) => f.overlapping).length;
    b.append(h("h3", {}, "Feeders"),
      over ? h("div", { class: "notice warnbox" }, h("b", {}, `${over} feeder(s) overlap their neighbour (red): move them or use Arrange.`)) : "",
      this.grp(`Feeders (${fs.length})`,
        h("div", { class: "btnrow left" },
          h("button", { class: "btn", onclick: () => this.run("arrange_feeders") }, icon("list"), "Arrange by the rules"),
          h("button", { class: "btn", onclick: () => this.ask("Build the program again from the list? Feeder edits are replaced.", "build_machine", [], { top_only: true }) }, "Build again")),
        h("div", { class: "hint" }, "Change a value in the row and press Save in that row. The pick point, heights and tests with the cameras: Job file settings → Feeder settings, after step 10."),
        this.table(cols.map((c) => head[c]).concat([""]), fs, (f) => {
          const ins = {};
          const cell = (k) => (k === "id" || k === "part" || k === "footprint") ? h("span", { class: f.overlapping ? "bad" : "" }, f[k]) : (ins[k] = h("input", { value: f[k], style: "width:60px" }));
          return cols.map(cell).concat([h("button", { class: "btn", onclick: () => {
            const d = {}; for (const k in ins) if (ins[k].value !== f[k]) d[k] = ins[k].value;
            if (Object.keys(d).length) this.run("set_feeder_fields", [f.id, d]);
          } }, "Save")]);
        })));
  },

  // ---- 8 order ----
  async order(b) {
    const pl = await this.call("placements", [], {}, true);
    const skipped = pl.filter((p) => p.skip).length;
    b.append(h("h3", {}, "Placement order"),
      this.grp(`Placements (${pl.length}${skipped ? `, ${skipped} skipped` : ""})`,
        h("div", { class: "btnrow left" }, h("button", { class: "btn primary", onclick: () => this.run("auto_sort") }, icon("list"), "Auto sort (heights, footprints, route)")),
        h("div", { class: "hint" }, "Low parts first, tall parts last. ↑ ↓ move one part; Skip leaves it out of the program."),
        this.table(["#", "Designator", "Value", "Footprint", "Feeder", "Nozzle", "X", "Y", "Rot", "Skip", "Move"], pl, (p) => [p.position, p.designator, p.value, p.footprint, p.feeder, p.nozzle,
          fmt(p.x), fmt(p.y), p.rotation, h("input", { type: "checkbox", checked: !!p.skip, disabled: !!p.fiducial, onchange: (e) => this.run("set_skip", [[p.designator]], { skip: e.target.checked }) }),
          p.fiducial ? "" : h("div", { class: "btnrow tight" }, h("button", { class: "btn", title: "one place earlier", onclick: () => this.run("move", [[p.designator], -1]) }, "↑"), h("button", { class: "btn", title: "one place later", onclick: () => this.run("move", [[p.designator], 1]) }, "↓"))])));
  },

  // ---- 9 speed ----
  async speed(b) {
    const sp = await this.call("speed_plan", [], {}, true).catch(() => null);
    b.append(h("h3", {}, "Speed"));
    if (!sp) return b.append(h("div", { class: "notice" }, "Build the program first (step 7, Feeders)."));
    const apply = { both: "the order and the feeders", order: "the order only", feeders: "the feeders only" };
    b.append(h("div", { class: "grpgrid" },
      this.grp("What the plan would do", h("pre", { class: "mono planbox" }, [].concat(sp.summary || []).join("\n"))),
      this.grp("Apply the plan",
        h("div", { class: "btnrow" }, ["both", "order", "feeders"].map((m) => h("button", { class: "btn" + (m === "both" ? " primary" : ""), title: `apply the plan to ${apply[m]}`, onclick: async () => {
          const r = await this.run("apply_speed_plan", [m]);
          if (r && r.changed_side && r.changed_side.length) toast(`check the angle of: ${r.changed_side.join(", ")}`, "warn");
        } }, m === "both" ? "Order and feeders" : m === "order" ? "Order only" : "Feeders only"))),
        h("div", { class: "hint" }, "Order: the placement order for the shortest way. Feeders: the reels moved to slots that save head travel."))),
      this.grp(`Parts (${(sp.parts || []).length})`,
        this.table(["Part", "Qty", "Height", "Tape", "Now", "Suggested", "Status"], sp.parts || [], (p) => [p.part, p.qty, p.height, p.tape, p.now, p.suggested, p.status])),
      (sp.loading_list || []).length ? h("details", {}, h("summary", {}, "Loading list"), h("pre", { class: "mono" }, JSON.stringify(sp.loading_list, null, 1))) : "");
  },

  // ---- 10 output ----
  async output(b) {
    const name = this.name || "job";
    const target = PathU.join(this.proc, name + ".csv");
    const boards = this.num(1, 60), spare = this.num(0, 60);
    const ox = h("input", { type: "number", step: "0.001", style: "width:90px", value: this.originXY ? this.originXY[0] : "", onchange: () => (this.originXY = ox.value !== "" && oy.value !== "" ? [ox.value, oy.value] : null) });
    const oy = h("input", { type: "number", step: "0.001", style: "width:90px", value: this.originXY ? this.originXY[1] : "", onchange: () => (this.originXY = ox.value !== "" && oy.value !== "" ? [ox.value, oy.value] : null) });
    const kitBox = h("div");
    const write = async () => {
      const ex = await API.req("GET", `/api/files?path=${encodeURIComponent(this.proc)}`).catch(() => ({ entries: [] }));
      if ((ex.entries || []).some((e) => e.name.toLowerCase() === (name + ".csv").toLowerCase()) && !confirm(`${name}.csv exists in Proc. Overwrite? (the old one is backed up)`)) return;
      if (!(await this.run("write_machine_file", [target]))) return;
      if (this.originXY) {                                  // our `origin` line, written into the file afterwards
        try {
          const r = await API.req("GET", `/api/file?path=${encodeURIComponent(target)}`, null, true);
          const lines = jobSetOrigin(INI.fromBytes(await r.arrayBuffer()).replace(/\r\n?/g, "\n").replace(/\n$/, "").split("\n"), this.originXY);
          await API.req("PUT", `/api/file?path=${encodeURIComponent(target)}`, INI.toBytes(lines.join("\r\n") + "\r\n"));
        } catch (e) { toast("board zero point: " + e.message, "bad"); }
      }
      toast(`written ${target}`, "ok");
    };
    b.append(h("h3", {}, "Output"),
      this.name ? "" : h("div", { class: "notice warnbox" }, "No job name yet (step 1, Files): the file would be called job.csv."),
      h("div", { class: "grpgrid" },
        this.grp("Machine file",
          h("button", { class: "btn primary wide", onclick: write }, icon("out"), `Write Proc/${name}.csv`),
          h("div", { class: "hint" }, "The job the machine runs. An older file of that name is backed up."),
          h("div", { class: "fields" }, h("label", {}, "Board zero X", ox), h("label", {}, "Board zero Y", oy)),
          h("div", { class: "hint" }, "Optional, in part coordinates: the panel positions then refer to this point instead of the first part (NeoDen4.exe ignores it)."),
          h("button", { class: "btn", title: "the job is one board: make the panel (columns x rows of it) in the job editor", onclick: () => {
            JobRun.path = target; localStorage.setItem("jobPath", target); JobFile.lines = null; JobFile.tab = "panels"; JobFile.pz = null; location.hash = "#jobfile";
          } }, icon("panel"), "Panelize →")),
        this.grp("Other files (in Jobs)",
          h("div", { class: "btncol" },
            h("button", { class: "btn", onclick: () => this.run("save_project", [PathU.join(this.jobs, name + ".kfg")]) }, icon("save"), "Save the project (.kfg)"),
            h("button", { class: "btn", onclick: () => this.run("export_stages", [PathU.join(this.jobs, name)]) }, "Export the stages"),
            h("button", { class: "btn", onclick: () => this.run("export_pnp_csv", [PathU.join(this.jobs, name + "-pnp.csv")]) }, "Export the P&P CSV in table order")),
          h("div", { class: "hint" }, "Stages: one machine file per reel change, when the parts do not all fit on the machine at once.")),
        this.grp("Parts kit",
          h("div", { class: "fields" }, h("label", {}, "Boards", boards), h("label", {}, "Spare %", spare)),
          h("div", { class: "btnrow" },
            h("button", { class: "btn", onclick: async () => {
              const k = await this.call("parts_kit", [], { boards: Number(boards.value), spare_pct: Number(spare.value) }, true).catch(() => null);
              if (k) kitBox.replaceChildren(h("div", { class: "hint" }, [].concat(k.summary || []).join(" ")),
                this.table(["Part", "Value", "Footprint", "Side", "Per board", "Total", "Spare", "Feeder"], k.lines, (l) => [l.part, l.value, l.footprint, l.side, l.per, l.total, l.spare, l.feeder]));
            } }, "Show the kit"),
            h("button", { class: "btn", onclick: () => this.run("export_kit", [PathU.join(this.jobs, name + "-kit.csv"), Number(boards.value), Number(spare.value), true, true]) }, "Export kit CSV")),
          h("div", { class: "hint" }, "How many of each part to prepare for this many boards."))),
      kitBox,
      // the board's surface: after the machine file is written, with the board on the table
      JobFile.zmapCard(target, async () => {
        const ex = await API.req("GET", `/api/files?path=${encodeURIComponent(this.proc)}`).catch(() => ({ entries: [] }));
        if (!(ex.entries || []).some((e) => e.name.toLowerCase() === (name + ".csv").toLowerCase())) { toast("write the machine file first", "bad"); return false; }
        return true;
      }),
      h("details", {}, h("summary", {}, "Machine file preview"), h("pre", { class: "mono scroll" }, await this.call("machine_file_text", [], {}, true).catch((e) => e.message))));
  },
};
