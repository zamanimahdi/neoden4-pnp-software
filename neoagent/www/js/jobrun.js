// Work page, laid out like NeoDen4.exe:
//  - job list ("P&P Programming", frmFileLoad): the Proc/*.csv files with Import, Export, Delete, Copy,
//    New, Edit and Mount;
//  - run screen ("Mount", frmFilePaist): file name, panel table (Skip), component table, Save, << / Vision
//    Align / >>, log, camera image, nozzle pressures, speed slider, Vibration feeder, Start/Pause/Continuous,
//    Step, Stop, Exit. Added: live numbers (elapsed time, real rate now, average, placed / total) and
//    "Continue where it stopped". Fixed table only: no rail, Continued, Auto-Eject or Eject PCB.
// The job runs in neojob through /api/job/...; this page reads the job file itself for the tables.
const JobRun = {
  path: localStorage.getItem("jobPath") || "",
  mounted: localStorage.getItem("jobMounted") === "1",          // the run screen stays open over a page reload
  logN: 0,
  lines: [],
  timer: null,
  sel: 0,                                   // selected component row (0-based)

  wantLive(cam) {                                         // the Mount screen's live choice per camera
    let v = null;
    try { v = localStorage.getItem("mountLive" + cam); } catch (e) { /* private window */ }
    return v === null ? cam === 1 : v === "1";            // the head camera live unless switched off
  },

  async render(root) {
    // the server just started (or homes, or opens the cameras) may not answer yet: say so and try again,
    // instead of an empty page
    const wait = h("div", { class: "card muted" }, "Loading the jobs …");
    root.append(wait);
    const st = await API.req("GET", "/api/status").catch(() => ({}));
    if (!st.neoden_dir) {
      wait.textContent = "The machine server is not answering yet (starting, homing?): trying again …";
      clearTimeout(this.retry);
      this.retry = setTimeout(() => { if (wait.isConnected) App.show(); }, 2000);
      return;
    }
    wait.remove();
    this.proc = PathU.join(st.neoden_dir, "Proc");
    const js = await API.req("GET", "/api/job/status?since=0").catch(() => null);
    if (js && js.running && js.job) { this.path = js.job; this.mounted = true; }     // a run is going on: show it
    clearInterval(this.timer);
    if (this.mounted && this.path) return this.runScreen(root);
    return this.listScreen(root);
  },

  // ---------------------------------------------------------------- job list
  async listScreen(root) {
    const list = await API.req("GET", `/api/files?path=${encodeURIComponent(this.proc)}`).catch(() => ({ entries: [] }));
    const files = (list.entries || []).filter((e) => !e.dir && /\.csv$/i.test(e.name)).sort((a, b) => a.name.localeCompare(b.name));
    let cur = files.find((e) => PathU.join(this.proc, e.name) === this.path) ? this.path : (files[0] ? PathU.join(this.proc, files[0].name) : "");
    const full = (e) => PathU.join(this.proc, e.name);
    // parts on the board (to place / all), panels, last run: from the server (it reads every job)
    const info = {};
    ((await API.req("GET", `/api/job/list?path=${encodeURIComponent(this.proc)}`).catch(() => ({ jobs: [] }))).jobs || []).forEach((j) => (info[j.name] = j));
    const rows = files.map((e) => {
      const tr = h("tr", { class: full(e) === cur ? "sel" : "", onclick: () => { cur = full(e); [...tbl.querySelectorAll("tr")].forEach((r) => r.classList.remove("sel")); tr.classList.add("sel"); upd(); },
        ondblclick: () => this.mount(cur) }, h("td", {}, e.name.replace(/\.csv$/i, "")),
        h("td", { class: "num", title: "parts to place (not skipped) / all the parts of one board" }, info[e.name] ? `${info[e.name].to_place} / ${info[e.name].parts}` : ""),
        h("td", { class: "num" }, info[e.name] ? String(info[e.name].panels) : ""),
        h("td", {}, info[e.name] && info[e.name].last_used ? info[e.name].last_used : h("span", { class: "muted" }, "—")),
        h("td", { class: "muted" }, e.mtime));
      return tr;
    });
    const tbl = h("table", { class: "pick" }, h("tr", {}, h("th", {}, "File Name"), h("th", { class: "num" }, "Parts per board"), h("th", { class: "num" }, "Panels"), h("th", {}, "Last used"), h("th", {}, "Changed")), rows);
    const btn = (label, fn, cls) => h("button", { class: "btn " + (cls || ""), onclick: fn }, label);
    const needSel = [];
    const sb = (label, fn, cls) => { const b = btn(label, fn, cls); needSel.push(b); return b; };
    const upd = () => needSel.forEach((b) => (b.disabled = !cur));
    const upl = h("input", { type: "file", accept: ".csv", style: "display:none", onchange: async (e) => {
      for (const f of e.target.files) {
        try { await API.req("PUT", `/api/file?path=${encodeURIComponent(PathU.join(this.proc, f.name))}`, await f.arrayBuffer()); toast(`imported ${f.name}`, "ok"); }
        catch (x) { toast(`${f.name}: ${x.message}`, "bad"); }
      }
      App.show();
    } });
    const name = () => cur.split(/[\\/]/).pop();
    root.append(h("div", { class: "card" },
      h("h3", {}, "P&P Programming: job files in Proc"),
      files.length ? h("div", { class: "scroll", style: "max-height:62vh" }, tbl) : h("div", { class: "muted" }, `No job files in ${this.proc || "Proc"}. Make one with New (the setup wizard) or Import a .csv.`),
      h("div", { class: "row", style: "margin-top:10px" },
        btn("Import…", () => upl.click()), upl,
        sb("Export", () => { location.href = `/api/file?path=${encodeURIComponent(cur)}`; }),
        sb("Delete", async () => {
          if (!confirm(`Delete ${name()}? (it is backed up first)`)) return;
          try { await API.req("POST", `/api/delete?path=${encodeURIComponent(cur)}`); toast("deleted", "ok"); App.show(); } catch (x) { toast(x.message, "bad"); }
        }),
        sb("Copy", async () => {
          const n = prompt("Name of the copy", name().replace(/\.csv$/i, "") + " copy");
          if (!n) return;
          try {
            const r = await API.req("GET", `/api/file?path=${encodeURIComponent(cur)}`, null, true);
            await API.req("PUT", `/api/file?path=${encodeURIComponent(PathU.join(this.proc, n.replace(/\.csv$/i, "") + ".csv"))}`, await r.arrayBuffer());
            toast("copied", "ok"); App.show();
          } catch (x) { toast(x.message, "bad"); }
        }),
        btn("New", () => { location.hash = "#wizard"; }),
        sb("Edit", () => { this.path = cur; localStorage.setItem("jobPath", cur); location.hash = "#jobfile"; }),
        h("div", { class: "grow" }),
        sb("Mount ▶", () => this.mount(cur), "primary big"))));
    upd();
  },

  mount(path) {
    if (!path) return;
    this.path = path; localStorage.setItem("jobPath", path);
    this.mounted = true; this.sel = 0; localStorage.setItem("jobMounted", "1");
    App.show();
  },

  // ---------------------------------------------------------------- run screen
  async runScreen(root) {
    try { await this.loadJob(); }
    catch (e) {                         // the open job's file is gone (deleted, renamed): back to the list
      toast(`${this.path.split(/[\\/]/).pop()}: cannot be read (${e.message}); choose a job`, "warn");
      this.mounted = false; localStorage.setItem("jobMounted", "0");
      return this.listScreen(root);
    }
    if (!Cfg.loaded) await Cfg.load().catch(() => {});
    const name = this.path.split(/[\\/]/).pop().replace(/\.csv$/i, "");
    this.statsBox = h("div", { class: "stats" });
    this.samples = [];
    this.resumeBox = h("div", { class: "muted" });
    this.qBox = h("div");
    this.logBox = h("pre", { class: "log", style: "height:100%;margin:0" });

    // panel table: "PanelN | Skip" (No / Yes / Auto)
    this.panelSel = Math.min(this.panelSel || 0, Math.max(0, this.job.panels.length - 1));
    const pickPanel = (i) => { this.panelSel = i; this.panelRows.forEach((r, k) => r.classList.toggle("sel", k === i)); if (this.aim) this.goto(this.sel); };
    this.panelRows = this.job.panels.map((p, i) => h("tr", { class: i === this.panelSel ? "sel" : "", title: "click: Vision Align on this panel", onclick: (e) => { if (e.target.tagName !== "SELECT") pickPanel(i); } },
      h("th", { title: `Panel ${i + 1}` }, `P${i + 1}`),
      h("td", {}, h("select", { onchange: (e) => { p.skip = +e.target.value; this.dirty = true; this.updSave(); } },
        ["No", "Yes", "Auto"].map((t, k) => h("option", { value: k, selected: k === p.skip }, t))))));
    const panelMenu = (v) => () => {
      this.job.panels.forEach((p) => (p.skip = v));
      this.panelRows.forEach((r) => { const s = r.querySelector("select"); if (s) s.value = String(v); });
      this.dirty = true; this.updSave();
    };
    const panels = h("div", { class: "card tablecard panelcard" }, h("h3", { title: "Panelized: skip a panel (No / Yes / Auto = bad-mark check)" }, "Panels"),
      h("div", { class: "scroll", style: "max-height:none;flex:1" }, h("table", { class: "pick" }, h("tr", {}, h("th", {}), h("th", {}, "Skip")), this.panelRows)),
      h("details", {}, h("summary", {}, "All"), h("div", { class: "row" },
        h("button", { class: "btn", onclick: panelMenu(1) }, "Skip all"), h("button", { class: "btn", onclick: panelMenu(0) }, "Place all"),
        h("button", { class: "btn", onclick: panelMenu(2) }, "Auto all"))));

    // component table: Feeder, Nozzle, Name, Value, Footprint, X, Y, Rotation. X / Y are not typed in here
    // (a touch must not change a part's place): the job editor's Components tab, or the camera on the
    // part and "Set X/Y here"
    const cols = ["Feeder", "Nozzle", "Name", "Value", "Footprint", "X", "Y", "Rotation"];      // skipped parts are not listed
    const num = (c, k) => h("span", { class: "xy" }, c[k].toFixed(2));
    this.compRows = this.visible.map((c, i) => h("tr", { onclick: () => this.select(i) },
      h("th", {}, String(c.i + 1)), h("td", {}, c.stack), h("td", {}, c.nozzle), h("td", {}, c.name), h("td", {}, c.value), h("td", {}, c.footprint),
      h("td", { class: "num" }, num(c, "x")), h("td", { class: "num" }, num(c, "y")), h("td", { class: "num" }, c.rot.toFixed(2))));
    this.compBox = h("div", { class: "scroll", style: "max-height:none;flex:1" },
      h("table", { class: "pick" }, h("tr", {}, h("th", {}), cols.map((c) => h("th", {}, c))), this.compRows));
    this.saveBtn = h("button", { class: "btn", onclick: () => this.save() }, "Save");
    this.navBtns = [h("button", { class: "btn", onclick: () => this.goto(this.sel - 1) }, "<<"),
      h("button", { class: "btn", title: "the head camera over the point where this part lands (through the board marks)", onclick: () => this.goto(this.sel) }, "Vision Align"),
      h("button", { class: "btn", onclick: () => this.goto(this.sel + 1) }, ">>"),
      h("button", { class: "btn", title: "jog the camera onto the part's real centre (click the picture), then: its X / Y in the job (through the marks); Save to keep. Keys: Up / Down = part, Space = this", onclick: () => this.setHere() }, "Set X/Y here (Space)")];
    const hidden = this.job.comps.length - this.visible.length;
    const comps = h("div", { class: "card tablecard" }, h("h3", {}, `Component List (${this.visible.length} to place` + (hidden ? `, ${hidden} skipped and hidden)` : ")")), this.compBox,
      h("div", { class: "row" }, this.saveBtn, h("div", { class: "grow" }), this.navBtns));

    // both cameras side by side, pressures, speed, vibration feeder, the big buttons
    // head camera: a click moves the head so that point comes to the centre (then Space = Set X/Y here)
    this.camHead = new CamView(1, { onClick: async (dx, dyUp) => {
      if (this.runState() === "running" && !this.markQ()) return;
      const k = Cfg.pxmm(1);
      if (await API.run("rel", { x: (dx / k).toFixed(4), y: (dyUp / k).toFixed(4), slow: 1 })) {
        await App.poll(); this.camHead.grab();
      }
    } });
    this.camUp = new CamView(0);
    this.camHead.labelBelow = this.camUp.labelBelow = true;        // the texts under the pictures, not on them
    this.camUp.zoom = +localStorage.getItem("camUpZoom") || 4;     // the part on the nozzle, not the whole 32 mm field
    this.camHead.zoom = +localStorage.getItem("camHeadZoom") || 1;
    const lightFor = async (v) => {                       // head lamp / up light on with its camera, unless a job runs
      if (this.runState() !== "idle") return;
      if (v.cam === 1) await API.run("led", { v: +Cfg.get("PadAlign/nLed", 3) || 3 });
      else await API.run("light", { on: 1 });
    };
    const camBox = (v, title) => {
      const live = h("input", { type: "checkbox", checked: this.wantLive(v.cam), onchange: async (e) => {
        try { localStorage.setItem("mountLive" + v.cam, e.target.checked ? "1" : "0"); } catch (x) { /* private window */ }
        if (e.target.checked) { if (this.runState() === "idle") { await lightFor(v); v.start(); } } else v.stop();
      } });
      v.liveBox = live;
      return h("div", { class: "camcell" }, h("div", { class: "camhead" }, h("b", {}, title), h("div", { class: "grow" }),
        v.zoomSel = h("select", { class: "small", title: "zoom", onchange: (e) => { v.zoom = +e.target.value; localStorage.setItem(v.cam ? "camHeadZoom" : "camUpZoom", v.zoom); v._draw(); } },
          [1, 2, 3, 4, 6, 8].map((z) => h("option", { value: z, selected: z === v.zoom }, `${z}x`))),
        h("label", {}, live, " live"), h("button", { class: "btn small", onclick: async () => { await lightFor(v); v.grab(); } }, "Grab")), v.el);
    };
    this.vac = [1, 2, 3, 4].map(() => h("progress", { max: 100, value: 0 }));
    const speed0 = +Cfg.get("Default/nRunSpeed", 100) || 100;
    this.speedLbl = h("label", {}, `Current Speed (${speed0}%)`);
    this.speed = h("input", { type: "range", min: 10, max: 130, step: 10, value: speed0,
      oninput: (e) => (this.speedLbl.textContent = `Current Speed (${e.target.value}%)`),
      onchange: (e) => API.run("xy_speed", { v: e.target.value, force: 9 }) });
    this.startBtn = h("button", { class: "btn primary bigbtn", onclick: () => this.startPause() }, "Start");
    this.stepBtn = h("button", { class: "btn bigbtn", onclick: () => this.stepOnce() }, "Step");
    this.stopBtn = h("button", { class: "btn danger bigbtn", onclick: () => this.ctl("stop") }, "Stop");
    this.exitBtn = h("button", { class: "btn bigbtn", onclick: () => this.exit() }, "Exit");
    this.resumeBtn = h("button", { class: "btn", title: "continue where the job stopped", onclick: () => this.start(true) }, "Continue ▶");
    const right = h("div", { class: "runright" },
      h("div", { class: "card cams" }, camBox(this.camHead, "Head camera"), camBox(this.camUp, "Up camera")),
      h("div", { class: "card" },
        h("div", { class: "vacgrid" }, this.vac.flatMap((p, i) => [h("label", {}, `Pressure${i + 1}`), p])),
        h("div", { class: "row" }, this.speedLbl, this.speed),
        h("div", { class: "row" }, h("label", {}, h("input", { type: "checkbox", onchange: (e) => API.run("vib_feeder", { on: e.target.checked ? 1 : 0,
          v: Cfg.get("Default/nVibCurrent", 30), p: Cfg.get("Default/nVibFreq", 100) }) }), " Vibration feeder")),
        h("div", { class: "bigrow" }, this.startBtn, this.stepBtn, this.stopBtn, this.exitBtn),
        h("div", { class: "btnrow4" },
          h("button", { class: "btn", title: "this job's feeders (skip, vision type, heights, speed), components and marks", onclick: () => this.openSettings("#jobfile") }, "⚙ Job settings"),
          h("button", { class: "btn", title: "Config.ini: vision, positions, feeders, ...", onclick: () => this.openSettings("#settings") }, "⚙ Machine settings"),
          h("button", { class: "btn", onclick: () => this.openSettings("#manual") }, "Manual control"),
          this.resumeBtn),
        this.resumeBox,
        h("details", {}, h("summary", {}, "Editing during a pause"), h("div", { class: "muted", style: "font-size:12px" }, JOB_EDIT_NOTE))),
      this.qBox,
      h("div", { class: "card logcard" }, this.logBox));

    root.replaceChildren(h("div", { class: "runwrap" },
      h("div", { class: "runhead" }, this.backBtn = h("button", { class: "btn", title: "back to the job list (Exit)", onclick: () => this.exit() }, "◀ Job list"),
        h("div", { class: "filename" }, h("span", { class: "muted" }, "File: "), name), this.statsBox),
      h("div", { class: "runscreen" },
        h("div", { class: "runleft" }, h("div", { class: "tables" }, panels, comps)),
        right)));
    if (JobFile.dirty && JobFile.path === this.path) {     // changed in the job editor, not saved: not used here
      root.prepend(h("div", { class: "card alert row", style: "margin:0 0 8px" },
        h("b", {}, "The job editor has unsaved changes to this job (feeders, parts …): this screen and the run use the saved file."),
        h("div", { class: "grow" }),
        h("button", { class: "btn primary", onclick: async () => { await JobFile.save(); location.hash = "#job"; App.show(); } }, "Save them now"),
        h("a", { class: "btn", href: "#jobfile" }, "Back to the editor")));
    }
    this.camHead.grab(); this.camUp.grab();
    this.liveNow = (async () => {                        // live as chosen (head camera by default), not while a job runs
      for (const v of [this.camHead, this.camUp]) {
        if (this.wantLive(v.cam) && this.runState() === "idle") {
          if (API.st.haveControl) await lightFor(v);      // never takes control by itself (another screen may have it)
          v.start(); if (v.liveBox) v.liveBox.checked = true;
        }
      }
    });
    this.liveNow();
    this.select(this.sel, true);
    this.logN = 0; this.lines = [];
    this.dirty = false; this.updSave();
    this.showResume();
    // the job's status: every 0.7 s while it runs, every ~2 s while idle, not while the page is hidden
    // (one timer only: an overlapping redraw must not leave a second one polling)
    clearInterval(this.timer);
    let ticks = 0;
    this.timer = setInterval(() => {
      if (location.hash !== "#job" || !this.mounted) { clearInterval(this.timer); return; }
      if (document.hidden) return;
      if (this.runState() === "idle" && ticks++ % 3) return;
      this.poll();
    }, 700);
    this.poll();
    this.showStats(null);
  },

  // the job file, read here for the tables (the runner reads it again itself)
  async loadJob() {
    const r = await API.req("GET", `/api/file?path=${encodeURIComponent(this.path)}`, null, true);
    this.text = INI.fromBytes(await r.arrayBuffer());                 // bytes 1:1, written back unchanged
    this.jobLines = this.text.replace(/\r\n?/g, "\n").split("\n");
    const job = { panels: [], comps: [], stacks: {} };
    this.jobLines.forEach((line, no) => {
      const t = line.split(","), tag = t[0].trim(), k = t.slice(1);
      if (tag === "stack" && k.length >= 14) job.stacks[+k[0]] = { type: +k[1], vision: +k[14], skip: /^yes$/i.test((k[+k[1] === 1 ? 22 : 19] || "").trim()) };
      else if (tag === "mirror" && k.length >= 4) job.panels.push({ x: +k[0], y: +k[1], angle: +k[2], skip: Math.max(0, ["No", "Yes", "Auto"].indexOf(k[3].trim())), no });
      else if (tag === "comp" && k.length >= 9) job.comps.push({ stack: +k[0], nozzle: +k[1], name: k[2], value: k[3], footprint: k[4], x: +k[5], y: +k[6], rot: +k[7], skip: /^(yes|1|true)$/i.test(k[8].trim()), no });
    });
    job.comps.forEach((c, i) => { c.i = i; const st = job.stacks[c.stack]; c.hidden = c.skip || !!(st && st.skip); });
    this.job = job;
    this.visible = job.comps.filter((c) => !c.hidden);          // the parts that will really be placed
  },

  // leave the run screen for the settings and come back with "Back to mount" (the job stays mounted;
  // pause first so the changes can be used when it continues)
  openSettings(hash) {
    if (this.runState() === "running" && !confirm("The job is running. Pause it first? (OK = pause and open the settings)")) return;
    if (this.runState() === "running") this.ctl("pause");
    if (this.dirty && confirm("Save the changes in the component list first?")) this.save();
    JobFile.lines = null;
    location.hash = hash;
  },

  updSave() { if (this.saveBtn) this.saveBtn.classList.toggle("primary", !!this.dirty); },

  // Save: edited X / Y and the panel skips back into the file (CProcFile::save keeps the rest)
  async save() {
    // what changes, said by name: parts placed elsewhere (Set X/Y here) and panel skips
    const moved = this.job.comps.filter((c) => { const t = this.jobLines[c.no].split(","); return Math.abs(+t[6] - c.x) > 0.004 || Math.abs(+t[7] - c.y) > 0.004; });
    const skips = this.job.panels.filter((p) => (this.jobLines[p.no].split(",")[4] || "").trim() !== ["No", "Yes", "Auto"][p.skip]);
    const what = [moved.length ? `${moved.length} part position(s): ${moved.slice(0, 6).map((c) => c.name).join(", ")}${moved.length > 6 ? " …" : ""}` : "",
      skips.length ? `${skips.length} panel skip(s)` : ""].filter(Boolean).join("; ");
    if (!what) { toast("nothing changed"); this.dirty = false; this.updSave(); return; }
    if (!confirm(`Save into ${this.path.split(/[\\/]/).pop()}: ${what}?`)) return;
    const L = this.jobLines.slice();
    for (const p of this.job.panels) { const t = L[p.no].split(","); t[4] = ["No", "Yes", "Auto"][p.skip]; L[p.no] = t.join(","); }
    for (const c of this.job.comps) { const t = L[c.no].split(","); t[6] = c.x.toFixed(2); t[7] = c.y.toFixed(2); L[c.no] = t.join(","); }
    while (L.length && L[L.length - 1] === "") L.pop();
    try {
      await API.req("PUT", `/api/file?path=${encodeURIComponent(this.path)}`, INI.toBytes(L.join("\r\n") + "\r\n"));
      this.jobLines = L; this.dirty = false; this.updSave(); toast("File saved.", "ok");
    } catch (e) { toast("Failed to save: " + e.message, "bad"); }
  },

  select(i, scroll) {
    if (!this.compRows || !this.compRows.length) return;
    i = Math.max(0, Math.min(this.compRows.length - 1, i));
    this.compRows.forEach((r, k) => r.classList.toggle("sel", k === i));
    this.sel = i;
    if (scroll !== false) this.compRows[i].scrollIntoView({ block: "nearest" });
  },

  // << / Vision Align / >>: put the head camera over the part and show the picture (gotoComponent;
  // design coordinates, no mark transform here)
  // the head camera over the point where the part really lands: the part's X / Y through the board marks
  // (shift, turn, stretch: the same transform the job uses); its Gerber pads and outline drawn over the picture
  async goto(i) {
    this.select(i);
    const c = this.visible[this.sel];
    if (!c) return;
    if (!API.st.haveControl && !(await API.take())) return;
    this.marksOk = this.marksOk || {};
    if (!this.marksOk[this.path]) {
      if (!confirm("Find the board's fiducials first? The head camera visits each mark (once per mounted job), so the part is shown where it really lands, board shift and turn included.")) return;
      const cam0 = this.camHead, wasLive = cam0.live;
      cam0.overlay = null; cam0.label = () => ["finding the board marks …"];
      if (!wasLive) cam0.start();                         // watch the head camera while the server looks for the marks
      try {
        const m = await API.req("POST", `/api/align/marks?client=${API.st.client}&job=${encodeURIComponent(this.path)}`).finally(() => { if (!wasLive) cam0.stop(); });
        this.marksOk[this.path] = m;
        toast(`marks found (${m.fit}${m.residuals_mm && m.residuals_mm.length ? `, residuals ${m.residuals_mm.map((v) => fmt(v, 3)).join(" ")} mm` : ""})`, "ok");
      } catch (e) { return toast("marks: " + e.message, "bad"); }
    }
    let p;
    try { p = await API.req("GET", `/api/align/point?job=${encodeURIComponent(this.path)}&ref=${encodeURIComponent(c.name)}&panel=${this.panelSel || 0}`); }
    catch (e) { return toast(e.message, "bad"); }
    const off = Cfg.offset(0);
    const x = Math.min(Math.max(p.x + off[0], 0), 416), y = Math.min(Math.max(p.y + off[1], 0), 400);
    await API.run("led", { v: +Cfg.get("PadAlign/nLed", 3) || 3 });
    if (!(await API.run("move", { x: x.toFixed(3), y: y.toFixed(3), slow: 1 }))) return;
    this.aim = { name: c.name, p, head: [x, y], t: Date.now() };
    const k = Cfg.pxmm(1), cam = this.camHead;
    cam.overlay = (g, toC, sc = 1) => {
      const a = this.aim;
      if (!a || cam.frame === null) return;
      // where the head is: the status, once it reports a time after this move; until then the target
      const st = API.st.status || {};
      const pos = st.pos && (API.st.statusAt || 0) > a.t + 300 ? st.pos : a.head;
      const cx = pos[0] - off[0], cy = pos[1] - off[1];                  // point under the camera now
      const px = (X, Y) => toC(cam.size / 2 + (X - cx) * k, cam.size / 2 - (Y - cy) * k);
      const poly = (pts, col, w) => { if (!pts) return; g.strokeStyle = col; g.lineWidth = w; g.beginPath(); pts.forEach((q, j) => { const [u, v] = px(q[0], q[1]); j ? g.lineTo(u, v) : g.moveTo(u, v); }); g.closePath(); g.stroke(); };
      // each pad: a filled box with its own border; the part's outline dashed 0.25 mm outside the pads,
      // so the two are not drawn on top of each other
      (a.p.pads || []).forEach((q) => {
        g.fillStyle = "rgba(255,210,0,.22)"; g.beginPath(); q.forEach((r, j) => { const [u, v] = px(r[0], r[1]); j ? g.lineTo(u, v) : g.moveTo(u, v); }); g.closePath(); g.fill();
        poly(q, "rgba(255,210,0,1)", 2 * sc);
      });
      if (a.p.outline) {
        const o = a.p.outline, e = 0.25;                   // the rectangle grown by e on every side (along its edges)
        const unit = (p, q) => { const d = Math.hypot(q[0] - p[0], q[1] - p[1]) || 1; return [(q[0] - p[0]) / d, (q[1] - p[1]) / d]; };
        const u = unit(o[0], o[1]), v = unit(o[0], o[3]);
        const sgn = [[-1, -1], [1, -1], [1, 1], [-1, 1]];
        const big = o.map((q, k) => [q[0] + e * (sgn[k][0] * u[0] + sgn[k][1] * v[0]), q[1] + e * (sgn[k][0] * u[1] + sgn[k][1] * v[1])]);
        g.setLineDash([8 * sc, 5 * sc]); poly(big, "rgba(40,255,100,1)", 2 * sc); g.setLineDash([]);
      }
      const [u, v] = px(a.p.x, a.p.y), r = 16 * sc;
      g.strokeStyle = "#ff2a2a"; g.lineWidth = 2.5 * sc; g.beginPath(); g.moveTo(u - r, v); g.lineTo(u + r, v); g.moveTo(u, v - r); g.lineTo(u, v + r); g.stroke();
      const fs = Math.round(14 * sc);
      g.font = `bold ${fs}px system-ui`;
      const tag = [`P${(a.p.panel || 0) + 1} ${a.name}`, c.footprint, c.value].filter(Boolean).join(" · ");
      const tw = g.measureText(tag).width;
      g.fillStyle = "rgba(0,0,0,.65)"; g.fillRect(u + 6 * sc, v - 8 * sc - fs, tw + 8 * sc, fs + 6 * sc);
      g.fillStyle = "#ff5050"; g.fillText(tag, u + 10 * sc, v - 8 * sc);
    };
    cam.label = () => [`P${(p.panel || 0) + 1} ${c.name} lands at ${fmt(p.x, 3)}, ${fmt(p.y, 3)}  turn ${fmt(p.angle, 2)}°`, p.pads && p.pads.length ? "yellow: pads (mask size), green dashed: the part's outline (0.25 mm outside)" : "no Gerber pads for this part"];
    // the head camera back to viewing settings: finding the marks leaves its own (an 800 window and a low
    // automatic exposure for shiny copper), which made this picture dark
    const cs = (window.Cameras && Cameras.state && Cameras.state[1]) || {};
    const exp = +Cfg.get("PadAlign/nExposure", 0) || cs.exp || 25, gain = cs.gain ?? 8;
    await API.req("POST", `/api/cam/set?cam=1&w=1024&h=1024&exposure=${exp}&gain=${gain}`).catch(() => {});
    await API.run("led", { v: +Cfg.get("PadAlign/nLed", 3) || 3 });
    await new Promise((r) => setTimeout(r, 250));
    await App.poll();                                     // the head position for the overlay, fresh
    cam.grab();
  },

  // keys on the mount screen (not while typing, not while a job runs): Up / Down = previous / next part
  // with Vision Align, Space = Set X/Y here
  markQ() { const s = this.last && this.last.s; return !!(s && s.question && /^Mark/.test(s.question.text)); },
  key(ev) {
    if (location.hash !== "#job" || !this.mounted || /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) return;
    const jog = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] }[ev.key];
    if (jog && this.markQ()) {                          // a mark asked for by hand: the arrows jog (0.1 mm, Shift 1 mm)
      ev.preventDefault();
      const st = ev.shiftKey ? 1 : 0.1;
      API.run("rel", { x: jog[0] * st, y: jog[1] * st, slow: 1 }).then(() => App.poll()).then(() => { if (!this.camHead.live) this.camHead.grab(); });
      return;
    }
    if (this.runState() === "running") return;
    if (ev.key === "ArrowUp") { ev.preventDefault(); this.goto(this.sel - 1); }
    else if (ev.key === "ArrowDown") { ev.preventDefault(); this.goto(this.sel + 1); }
    else if (ev.key === " ") { ev.preventDefault(); this.setHere(); }
  },

  // the camera crosshair is on the part's real centre: write it back as the part's design X / Y
  async setHere() {
    const c = this.visible[this.sel], s = API.st.status || {};
    if (!c || !s.pos) return toast("position unknown", "bad");
    if (!(this.marksOk || {})[this.path]) return toast("press Vision Align first (it finds the marks)", "warn");
    const off = Cfg.offset(0);
    const mx = s.pos[0] - off[0], my = s.pos[1] - off[1];
    let d;
    try { d = await API.req("GET", `/api/align/design?job=${encodeURIComponent(this.path)}&x=${mx}&y=${my}&x0=${c.x}&y0=${c.y}&panel=${this.panelSel || 0}`); }
    catch (e) { return toast(e.message, "bad"); }
    const was = [c.x, c.y];
    c.x = +(+d.x).toFixed(2); c.y = +(+d.y).toFixed(2);
    const row = this.compRows[this.sel], ins = row.querySelectorAll("span.xy");
    if (ins[0]) { ins[0].textContent = c.x.toFixed(2); ins[0].classList.add("changed"); }
    if (ins[1]) { ins[1].textContent = c.y.toFixed(2); ins[1].classList.add("changed"); }
    this.dirty = true; this.updSave();
    toast(`${c.name}: ${fmt(was[0], 2)}, ${fmt(was[1], 2)} → ${fmt(c.x, 2)}, ${fmt(c.y, 2)} (change ${fmt(c.x - was[0], 3)}, ${fmt(c.y - was[1], 3)} mm). Save to keep it`, "ok");
    this.goto(this.sel);
  },

  async showResume() {
    if (!this.path) return;
    const r = await API.req("GET", `/api/job/resume?path=${encodeURIComponent(this.path)}`).catch(() => ({}));
    if (r.resume && r.resume.finished) r.resume = null;   // the board was finished: a new one starts from the first part
    this.canResume = !!r.resume;
    this.resumeBox.textContent = r.resume ? `stopped at panel ${r.resume.panel + 1}, component ${r.resume.comp + 1}; ${r.resume.done.length} placed (${r.resume.time})` : "";
    this.resumeBtn.disabled = !r.resume || (this.last && this.last.s && this.last.s.running);
  },

  async start(resume, step) {
    if (!this.path) return toast("choose a job file", "warn");
    if (this.dirty && confirm("Save the changes before starting?")) await this.save();
    if (!API.st.haveControl && !(await API.take())) return;
    try {
      await API.req("POST", `/api/job/start?path=${encodeURIComponent(this.path)}&resume=${resume ? 1 : 0}&client=${API.st.client}`);
      if (step) await API.req("POST", "/api/job/step").catch(() => {});
      toast(resume ? "job continues" : "job started", "ok");
    } catch (e) { toast(e.message, "bad"); }
  },
  ctl(what) { return API.req("POST", `/api/job/${what}`).catch((e) => toast(e.message, "bad")); },
  runState() {
    const s = this.last && this.last.s;
    if (!s || !s.running) return "idle";
    return /pause|step|wait/i.test(s.state || "") ? "paused" : "running";
  },
  // Start after a Stop goes on where the job stopped (the parts placed are not placed again); from
  // the first part only when the operator says so
  async startFromIdle(step) {
    await this.showResume();
    let resume = false;
    if (this.canResume) {
      const a = await choose(`${this.resumeBox.textContent}.`, [["resume", "Continue from there", "primary"],
        ["new", "New board: from the first part"], ["cancel", "Cancel"]]);
      if (!a || a === "cancel") return;
      resume = a === "resume";
    }
    return this.start(resume, step);
  },
  startPause() {
    const r = this.runState();
    if (r === "idle") return this.startFromIdle(false);
    return this.ctl(r === "running" ? "pause" : "continue");
  },
  stepOnce() { return this.runState() === "idle" ? this.startFromIdle(true) : this.ctl("step"); },
  exit() {
    if (this.runState() !== "idle") return alert("Please stop the job before exiting.");
    if (this.dirty && !confirm("The changes are not saved. Exit anyway?")) return;
    clearInterval(this.timer); this.mounted = false; localStorage.setItem("jobMounted", "0"); App.show();
  },

  // live numbers: elapsed time, the real placement rate now (parts in the last 60 s), the average since
  // the start, progress and the time left at the current rate. The rate is measured here from the
  // placed count over time, so it shows what the machine really does (pauses and errors included).
  showStats(s) {
    const now = performance.now() / 1000;
    if (s) {
      const last = this.samples[this.samples.length - 1];
      if (!s.running || (last && (s.placed < last.placed || s.job !== this.statJob))) this.samples = [];
      this.statJob = s.job;
      if (s.running) this.samples.push({ t: now, placed: s.placed });
      this.samples = this.samples.filter((x) => now - x.t <= 120);
      this.last = { s, t: now };
    }
    const L = this.last && this.last.s;
    const run = L && L.running && !/pause|step/i.test(L.state || "") && !L.question;
    const el = L && L.elapsed_s ? L.elapsed_s + (run ? now - this.last.t : 0) : 0;
    const hms = (x) => { x = Math.max(0, Math.round(x)); return `${Math.floor(x / 3600)}:${String(Math.floor(x / 60) % 60).padStart(2, "0")}:${String(x % 60).padStart(2, "0")}`; };
    const win = this.samples.filter((x) => now - x.t <= 60);
    let rate = null;
    if (win.length > 1 && win[win.length - 1].t - win[0].t >= 5) rate = (win[win.length - 1].placed - win[0].placed) / (win[win.length - 1].t - win[0].t) * 3600;
    const avg = L && L.elapsed_s > 5 && L.placed ? L.placed / L.elapsed_s * 3600 : null;
    const total = this.visible ? this.visible.length * Math.max(1, this.job.panels.filter((p) => p.skip !== 1).length) : (L && L.comps) || null, placed = L ? L.placed || 0 : 0;
    const pct = total ? Math.min(100, placed / total * 100) : 0;
    const left = total && rate > 0 ? (total - placed) / rate * 3600 : null;
    const tile = (label, value, sub, cls) => h("div", { class: "stat " + (cls || "") }, h("div", { class: "label" }, label), h("div", { class: "value" }, value), h("div", { class: "sub" }, sub || ""));
    const key = [L && L.elapsed_s ? hms(el) : "", L && L.state, rate !== null ? Math.round(rate) : "", avg !== null ? Math.round(avg) : "", placed, total, left !== null ? hms(left) : ""].join("|");
    if (key === this.statsKey && this.statsBox.firstChild) { clearTimeout(this.tick); if (run && location.hash === "#job") this.tick = setTimeout(() => this.showStats(null), 1000); return; }
    this.statsKey = key;                                  // the tiles only when a number changed
    this.statsBox.replaceChildren(
      tile("Elapsed", L && L.elapsed_s ? hms(el) : "–", L ? (L.state || "") : ""),
      tile("Rate now", rate !== null ? `${Math.round(rate).toLocaleString()} CPH` : "–", "parts per hour, last 60 s", rate !== null ? "accent" : ""),
      tile("Average", avg !== null ? `${Math.round(avg).toLocaleString()} CPH` : "–", "since the start"),
      h("div", { class: "stat wide" }, h("div", { class: "label" }, "Placed"),
        h("div", { class: "value" }, total ? `${placed} / ${total}` : String(placed || "–")),
        h("div", { class: "bar" }, h("div", { style: `width:${pct}%` })),
        h("div", { class: "sub" }, total ? `${pct.toFixed(0)} %` + (left !== null ? ` · about ${hms(left)} left at this rate` : "") : "")));
    clearTimeout(this.tick);
    if (run && location.hash === "#job") this.tick = setTimeout(() => this.showStats(null), 1000);
  },

  async poll() {
    const s = await API.req("GET", `/api/job/status?since=${this.logN}`).catch(() => null);
    if (!s) return;
    if (s.log_n < this.logN) { this.logN = 0; this.lines = []; this.logBox.textContent = ""; }
    this.logN = s.log_n;
    if (s.log && s.log.length) {                         // only new lines: no re-layout of the whole log
      this.lines.push(...s.log);
      if (this.lines.length > 400 || !this.logBox.firstChild) { this.lines = this.lines.slice(-300); this.logBox.textContent = this.lines.join("\n"); }
      else this.logBox.append("\n" + s.log.join("\n"));
      this.logBox.scrollTop = this.logBox.scrollHeight;
    }
    this.showStats(s);
    // buttons like setRunState: idle Start / running Pause / paused or stepping Continuous
    const r = this.runState();
    // the job file saved from another screen (Feeder settings, the job editor on the machine's monitor
    // or a browser) while this one is open: read it again, so skipped feeders / parts drop out of the list
    if (r !== "running" && !this.dirty && (this.fileTick = (this.fileTick || 0) + 1) % 4 === 0) {
      const f = await API.req("GET", `/api/file?path=${encodeURIComponent(this.path)}`, null, true).catch(() => null);
      const text = f && INI.fromBytes(await f.arrayBuffer());
      if (text && this.text && text !== this.text && !this.dirty) {
        toast("The job file was changed: the list is read again", "ok");
        App.show();                                        // runScreen reads the file and draws the tables again
        return;
      }
    }
    this.startBtn.textContent = r === "idle" ? "Start" : r === "running" ? "Pause" : "Continuous";
    this.stopBtn.disabled = r === "idle";
    this.exitBtn.disabled = r !== "idle";
    if (this.backBtn) this.backBtn.disabled = r !== "idle";
    this.resumeBtn.disabled = r !== "idle" || !this.canResume;
    this.navBtns.forEach((b) => (b.disabled = r === "running"));
    if (r !== "idle" && s.panels) {                     // the current panel and part are the selected rows
      this.panelRows.forEach((row, k) => row.classList.toggle("sel", k === s.panel - 1));
      const v = this.visible.findIndex((c) => c.i >= s.comp - 1);           // the next part that is placed
      if (v >= 0 && v !== this.sel) this.select(v);
    }
    if (this.wasRunning && r === "idle") this.showResume();
    // the pictures the job's vision takes, as they come (up camera: the parts on the nozzles; head: marks, pads)
    const seq = s.img_seq || {};
    this.imgSeq = this.imgSeq || {};
    for (const [k, cam] of [["0", this.camUp], ["1", this.camHead]]) {
      if (seq[k] && seq[k] !== this.imgSeq[k] && !cam.live) {
        this.imgSeq[k] = seq[k];
        const inf = (s.img_info || {})[k];
        cam.overlay = null; cam.label = () => ["job picture"];
        if (inf && inf.four && inf.seq === seq[k]) {     // the 4-up shot: every part, its nozzle's axis, its turn
          cam.label = () => [inf.parts.map((q) => `${q.name} (${q.nozzle}): ${q.valid ? `${q.angle > 0 ? "+" : ""}${fmt(q.angle, 1)}°` : "not recognised"}`).join("   ")];
          cam.overlay = (g, toC, sc) => {
            for (const q of inf.parts) {
              const [au, av] = toC(q.axis[0], q.axis[1]);
              g.strokeStyle = "#00e5ff"; g.lineWidth = 2 * sc;
              g.beginPath(); g.moveTo(au - 7 * sc, av); g.lineTo(au + 7 * sc, av); g.moveTo(au, av - 7 * sc); g.lineTo(au, av + 7 * sc); g.stroke();
              if (!q.rect) continue;
              const [cx, cy, w, hh, a] = q.rect, [u, v] = toC(cx, cy), [u1] = toC(cx + 1, cy), f = u1 - u;
              g.save(); g.translate(u, v); g.rotate((-a * Math.PI) / 180);
              g.strokeStyle = q.valid ? "#ffd400" : "#ff4040"; g.lineWidth = 2.5 * sc;
              g.strokeRect((-w / 2) * f, (-hh / 2) * f, w * f, hh * f);
              g.beginPath(); g.moveTo(0, 0); g.lineTo((w / 2 + 10) * f, 0); g.stroke();
              g.restore();
              g.strokeStyle = "#ff3bff"; g.lineWidth = 2 * sc; g.beginPath(); g.moveTo(au, av); g.lineTo(u, v); g.stroke();
              g.fillStyle = "#ffd400"; g.font = `bold ${13 * sc}px system-ui`;
              g.fillText(`${q.name} ${q.valid ? (q.angle > 0 ? "+" : "") + fmt(q.angle, 1) + "°" : "?"}`, u + 8 * sc, v - 8 * sc);
            }
          };
        } else if (inf && inf.seq === seq[k]) {          // what vision found: the part's outline, offset and turn
          cam.label = () => [inf.valid ? `${inf.name} (nozzle ${inf.nozzle}): turned ${inf.angle > 0 ? "+" : ""}${fmt(inf.angle, 2)}°` : `${inf.name} (nozzle ${inf.nozzle}): ${inf.empty ? "nozzle empty" : "not recognised"}`,
            inf.valid ? `offset ${fmt(inf.x, 3)}, ${fmt(inf.y, 3)} mm  size ${fmt(inf.w, 2)} x ${fmt(inf.h, 2)} mm` : ""];
          cam.overlay = (g, toC, sc) => {
            let c0 = cam.size / 2, d0 = cam.size / 2;                                      // the nozzle: its hole's circle when found
            if (inf.tip) {
              const [tu, tv] = toC(inf.tip[0], inf.tip[1]), [tu1] = toC(inf.tip[0] + inf.tip[2], inf.tip[1]);
              g.strokeStyle = "#00e5ff"; g.lineWidth = 2 * sc; g.beginPath(); g.arc(tu, tv, Math.max(3, tu1 - tu), 0, 2 * Math.PI); g.stroke();
              g.beginPath(); g.moveTo(tu - 6 * sc, tv); g.lineTo(tu + 6 * sc, tv); g.moveTo(tu, tv - 6 * sc); g.lineTo(tu, tv + 6 * sc); g.stroke();
              [c0, d0] = inf.tip;
            }
            if (!inf.rect) return;
            const [cx, cy, w, hh, a] = inf.rect;
            const [u, v] = toC(cx, cy), [u1] = toC(cx + 1, cy), q = u1 - u;
            g.save(); g.translate(u, v); g.rotate((-a * Math.PI) / 180);
            g.strokeStyle = inf.valid ? "#ffd400" : "#ff4040"; g.lineWidth = 2.5 * sc;
            g.strokeRect((-w / 2) * q, (-hh / 2) * q, w * q, hh * q);
            g.beginPath(); g.moveTo(0, 0); g.lineTo((w / 2 + 12) * q, 0); g.stroke();     // the part's own X axis
            g.restore();
            const [nu, nv] = toC(c0, d0);                                                   // offset: nozzle centre -> part centre
            g.strokeStyle = "#ff3bff"; g.lineWidth = 2 * sc; g.beginPath(); g.moveTo(nu, nv); g.lineTo(u, v); g.stroke();
          };
        }
        const inf0 = (s.img_info || {})[k];
        const four = inf0 && inf0.four && inf0.seq === seq[k];
        if (four) { if (cam.zoom !== 1) cam.zoomWas = cam.zoom; cam.zoom = 1; }       // the 4-up shot: all the nozzles in view
        else if (cam.zoomWas) { cam.zoom = cam.zoomWas; cam.zoomWas = 0; }
        if (cam.zoomSel) cam.zoomSel.value = String(cam.zoom);
        const full = cam.zoom >= 2 ? 1 : 2;              // zoomed in: the full picture, else half
        cam.showUrl(`/api/job/image?cam=${k}&s=${seq[k]}&scale=${full}`, full);
      }
    }
    // a job starts: the live views give way to the job's own pictures; it ends: live again as chosen
    if (r !== "idle" && !this.wasRunning) [this.camHead, this.camUp].forEach((v) => { if (v.live) v.stop(); if (v.liveBox) v.liveBox.checked = this.wantLive(v.cam); });
    if (r === "idle" && this.wasRunning && this.liveNow) setTimeout(() => this.liveNow(), 1500);
    this.wasRunning = r !== "idle";
    // nozzle pressures (vacuum, live)
    const vac = (API.st.status && API.st.status.vacuum) || [];
    this.vac.forEach((p, i) => (p.value = Math.max(0, Math.min(100, -(vac[i] || 0)))));
    const q = s.question;
    if (q && !this.qBox.dataset.q) {
      this.qBox.dataset.q = q.text;
      this.qBox.replaceChildren(h("div", { class: "card alert" }, h("b", {}, "Attention!"), h("p", {}, q.text),
        h("div", { class: "row" }, q.buttons.map((b) => h("button", { class: "btn" + (b === "yes" ? " primary" : b === "no" ? " danger" : ""), onclick: async () => {
          await API.req("POST", `/api/job/answer?a=${b}`).catch(() => {}); this.qBox.dataset.q = ""; this.qBox.replaceChildren();
        } }, (/^Mark/.test(q.text) ? { yes: "Yes: the crosshair is on the mark", no: "No (stop the job)", ignore: "Search again" }
          : { yes: "Yes (try again)", no: "No (stop the job)", ignore: "Ignore (skip)" })[b])))));
      if (/^Mark/.test(q.text)) {                       // the camera live with its lamp, to bring the crosshair onto the mark
        API.run("led", { v: +Cfg.get("PadAlign/nLed", 3) || 3 }).then(() => this.camHead.start());
      }
    } else if (!q && this.qBox.dataset.q) { this.qBox.dataset.q = ""; this.qBox.replaceChildren(); if (this.camHead.live) this.camHead.stop(); }
  },
};
document.addEventListener("keydown", (e) => JobRun.key(e));
