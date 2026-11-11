// Setups (calibration), after NeoDen4's frmXYLockFaceUp / frmXYLockFaceDown / frmCamDownCorrect
// (SETUP_SPEC sections 1-7). Frames: machine XY = nozzle 1; tool t over point P <=> head = P + offset(t);
// head camera 112 px/mm, click moves the head +(dx, dy_up)/112; up camera 34.5 px/mm, click moves -(dx, dy_up)/34.5.
const Setups = {
  current: null,

  // [kind, title, what to do, group, Config.ini key it saves]
  list: [
    ["up1", "Nozzle 1 over the up camera", "Centre nozzle 1's tip (or its rotation centre) on the up camera. Do this first.", "nozzles", "posCamLookUpMouse0"],
    ["up2", "Nozzle 2 over the up camera", "Nozzle offsets are measured against nozzle 1.", "nozzles", "posCamLookUpMouse1"],
    ["up3", "Nozzle 3 over the up camera", "Nozzle offsets are measured against nozzle 1.", "nozzles", "posCamLookUpMouse2"],
    ["up4", "Nozzle 4 over the up camera", "Nozzle offsets are measured against nozzle 1.", "nozzles", "posCamLookUpMouse3"],
    ["up0", "All four nozzles in one picture", "Head position for the 4-nozzle shot and each nozzle's point in the picture (Ctrl+click in its quarter).", "nozzles", "dPosSubMouses0..3"],
    ["lens", "Head camera relative to nozzle 1", "Nozzle 1 stamps a mark at (300, 200); centre the head camera on it.", "head", "dPosCamLookDownMouse1"],
    ["trash", "Trash box", "Where rejected parts are blown off.", "places", "posWasteBox"],
    ["stack", "Feeder pick position", "Teach where a stack's parts are picked (also from Settings → Stacks → Teach).", "places", "lstStack…"],
  ],
  groups: [["nozzles", "Nozzles (up-looking camera)"], ["head", "Head camera"], ["places", "Places on the machine"]],

  render(root) {
    if (!Cfg.loaded) { Cfg.load().then(() => App.show()).catch((e) => root.append(h("div", { class: "card" }, "Config.ini: " + e.message))); return; }
    if (this.current) { this.panel(root, this.current); return; }
    const stackSel = h("input", { type: "number", min: 1, max: 98, value: 1 });
    root.append(h("div", { class: "section-title" }, "Calibration values (Config.ini)"), this.valuesTable(),
      h("div", { class: "section-title" }, "Auto-calibration"), this.autoCard(),
      h("div", { class: "section-title" }, "Step by step"));
    root.append(...this.groups.map(([g, gt]) => [h("div", { class: "section-title" }, gt),
      h("div", { class: "grid" }, this.list.filter((l) => l[3] === g).map(([k, t, d, , key]) => h("div", { class: "card tile" },
        h("h3", { style: "margin:0" }, t), h("div", {}, d),
        h("div", { class: "muted mono hint" }, "saves " + key),
        h("div", { class: "row" }, k === "stack" ? [h("label", {}, "Stack "), stackSel] : null,
          h("button", { class: "btn primary", onclick: () => this.open(k, k === "stack" ? stackSel.value - 1 : 0) }, "Open")))))]).flat());
  },

  // ---- the values the calibrations set, with where each one comes from ----
  valuesTable() {
    const pt = (k) => Cfg.pt(k).map((v) => fmt(v, 3)).join(", ");
    const off = (n) => Cfg.offset(n).map((v) => fmt(v, 3)).join(", ");
    const open = (k) => h("button", { class: "btn small", onclick: () => this.open(k) }, "Open");
    const rows = [
      ["Up camera scale", `${fmt(Cfg.pxmm(0), 3)} px/mm`, "auto", null],
      ["Up camera angle", `${fmt(Cfg.get("Default/fAngleCamLookUp", 0), 3)}°`, "auto", null],
      ["Head camera scale", `${fmt(Cfg.pxmm(1), 3)} px/mm`, "Pad alignment, step 1", h("a", { class: "btn small", href: "#padalign" }, "Open")],
      ["Head camera angle", `${fmt(Cfg.get("PadAlign/fHeadCamAngle", 0), 3)}°`, "Pad alignment, step 1", h("a", { class: "btn small", href: "#padalign" }, "Open")],
      ...[1, 2, 3, 4].map((n) => [`Nozzle ${n} over the up camera`, pt(`Position/posCamLookUpMouse${n - 1}`) + (n > 1 ? `   (offset ${off(n)})` : ""), "auto", open(`up${n}`)]),
      ["4-nozzle picture: head position", pt("Position/posCamLookUpMouseMouseCenter"), "step by step", open("up0")],
      ...[0, 1, 2, 3].map((i) => [`Nozzle ${i + 1} point in the 4-nozzle picture`, `${pt(`Position/dPosSubMouses${i}`)} px`, "auto", null]),
      ["Head camera from nozzle 1", pt("Position/dPosCamLookDownMouse1"), "step by step (mark)", open("lens")],
      ["Trash box", pt("Position/posWasteBox"), "step by step", open("trash")],
    ];
    return h("div", { class: "card" }, h("table", {}, h("tr", {}, ["Value", "Now", "Set by", ""].map((t) => h("th", {}, t))),
      rows.map(([l, v, how, b]) => h("tr", {}, h("td", {}, l), h("td", { class: "mono" }, v), h("td", { class: how === "auto" ? "ok" : "muted" }, how), h("td", {}, b)))));
  },

  // ---- auto-calibration: up-camera scale / angle, each nozzle's axis, the 4-nozzle points
  // (neocalib/autocal.py on the server); the results are written only after Apply ----
  autoCard() {
    const chk = (on) => h("input", { type: "checkbox", checked: on });
    const scale = chk(true), noz = [1, 2, 3, 4].map(() => chk(true)), ztilt = chk(true);
    const zmax = h("input", { type: "number", min: 1, max: 9, step: 0.5, style: "width:60px", value: localStorage.getItem("ztiltMax") || 7.5,
      onchange: (e) => localStorage.setItem("ztiltMax", e.target.value) });
    const exp = h("input", { type: "number", min: 1, max: 65535, style: "width:80px", value: localStorage.getItem("holeExp") || 600,
      onchange: (e) => localStorage.setItem("holeExp", e.target.value) });
    const bar = h("div", { class: "bar" }, h("div", { style: "width:0%" }));
    const stepLbl = h("span", { class: "muted" }, "");
    const log = h("pre", { class: "log", style: "height:140px" }, "");
    const results = h("div");
    const startBtn = h("button", { class: "btn primary big", onclick: async () => {
      if (!API.st.haveControl && !(await API.take())) return;
      const nozzles = [1, 2, 3, 4].filter((n, i) => noz[i].checked);
      if (!confirm("The head moves over the up camera and turns the nozzles (about 1-2 minutes). Nothing must be on the nozzles. Start?")) return;
      if (ztilt.checked && !confirm(`Z tilt: each nozzle goes DOWN over the up camera, to ${zmax.value} mm (0, then ${(+zmax.value / 3).toFixed(1)} mm steps). Not yet checked on this machine: watch the first nozzle the first time, and press the red STOP if it gets near the camera window. Go on?`)) return;
      try {
        await API.req("POST", `/api/calib/start?client=${API.st.client}`, JSON.stringify({ scale: scale.checked, nozzles, exposure: +exp.value || 600, step: 3, ztilt: ztilt.checked, zmax: +zmax.value || 7.5 }));
        this.pollCal();
      } catch (e) { toast(e.message, "bad"); }
    } }, "▶ Auto-calibrate");
    const cancelBtn = h("button", { class: "btn danger", onclick: () => API.req("POST", "/api/calib/cancel").catch(() => {}) }, "Cancel");
    this.cal = { bar, stepLbl, log, results, startBtn, cancelBtn };
    this.pollCal();
    return h("div", { class: "card" },
      h("div", { class: "muted", style: "margin-bottom:6px" }, "Repeats the up-camera calibrations: the camera's scale and angle from known head moves, " +
        "each nozzle's real rotation axis (turned 0 / 90 / 180 / -90°; an empty nozzle is skipped), and the nozzle points of the 4-nozzle picture. " +
        "Nothing is saved until you check the results and press Apply."),
      h("div", { class: "row" }, h("label", {}, scale, " up-camera scale and angle"), h("span", { class: "muted" }, "· nozzles:"),
        ...[1, 2, 3, 4].map((n, i) => h("label", {}, noz[i], ` ${n}`)), h("span", { class: "muted" }, "· exposure for the empty nozzles"), exp),
      h("div", { class: "row" }, h("label", { title: "each nozzle lowered over the camera to 0 / 2.5 / 5 / 7.5 mm at four turns: how far its tip moves sideways as it goes down (a tilted Z axis, a bent nozzle); the job corrects the pick and place points by it" },
        ztilt, " Z tilt: the nozzles go down over the camera to"), zmax, h("span", { class: "muted" }, "mm (watch it the first time)")),
      h("div", { class: "row" }, startBtn, cancelBtn, stepLbl), bar, log, results);
  },

  async pollCal() {
    const c = this.cal;
    if (!c || !c.log.isConnected) return;
    const s = await API.req("GET", "/api/calib/status").catch(() => null);
    if (!s) return;
    c.bar.firstChild.style.width = `${Math.round(s.progress * 100)}%`;
    c.stepLbl.textContent = s.state === "running" ? `running: ${s.step}` : s.state === "idle" ? "" : s.state;
    c.log.textContent = s.log.join("\n"); c.log.scrollTop = c.log.scrollHeight;
    c.startBtn.disabled = s.state === "running"; c.cancelBtn.disabled = s.state !== "running";
    if (s.state === "running") { setTimeout(() => this.pollCal(), 700); return; }
    if (s.result && s.result.proposed && s.result.proposed.length && !c.results.dataset.shown) this.showCal(s.result);
  },

  showCal(r) {
    const c = this.cal;
    c.results.dataset.shown = "1";
    const f3 = (a) => (Math.abs(a) > 0 && Math.abs(a) < 0.01 ? fmt(a, 5) : fmt(a, 3));       // Z tilt: mm per mm, small
    const v = (x) => (x === null || x === undefined ? "—" : Array.isArray(x) ? x.map(f3).join(", ") : f3(x));
    const ch = (o, n) => (o === null || o === undefined ? "new" : Array.isArray(n) ? n.map((a, i) => (a - o[i] >= 0 ? "+" : "") + f3(a - o[i])).join(", ") : (n - o >= 0 ? "+" : "") + f3(n - o));
    const boxes = r.proposed.map((p) => h("input", { type: "checkbox", checked: p.default }));
    const nz = Object.entries(r.nozzles || {}).map(([n, x]) => x.ok ? `nozzle ${n}: runout ${fmt(x.runout_mm * 1000, 0)} µm` + (x.runout_mm > 0.1 ? " ← large: bent or worn?" : "") : `nozzle ${n}: ${x.why}`);
    c.results.replaceChildren(
      h("h3", { style: "margin-top:12px" }, "Results"),
      r.scale ? h("div", { class: "muted" }, `fit ${fmt(r.scale.fit_rms_px, 2)} px, skew ${fmt(r.scale.skew_deg, 3)}°` + (r.scale.orientation_ok ? "" : " — ORIENTATION WRONG: do not apply")) : null,
      h("div", { class: "muted" }, nz.join(" · ")),
      h("table", {}, h("tr", {}, ["", "Value", "Now", "Measured", "Change"].map((t) => h("th", {}, t))),
        r.proposed.map((p, i) => h("tr", {}, h("td", {}, boxes[i]), h("td", {}, p.label, p.note ? h("div", { class: "muted", style: "font-size:12px" }, p.note) : null),
          h("td", { class: "mono" }, v(p.old)), h("td", { class: "mono" }, h("b", {}, v(p.new))), h("td", { class: "mono" }, ch(p.old, p.new))))),
      h("div", { class: "row" }, h("button", { class: "btn primary", onclick: async () => {
        let n = 0;
        r.proposed.forEach((p, i) => { if (!boxes[i].checked) return; n++; if (p.type === "pt") Cfg.setPt(p.key, p.new); else Cfg.set(p.key, p.new, "f"); });
        if (!n) return toast("nothing selected", "warn");
        await Settings.save();
        toast(`${n} value(s) written to Config.ini`, "ok");
      } }, "Apply the checked values"), h("span", { class: "muted" }, "Config.ini is backed up before it is written.")));
  },

  open(kind, arg) {
    this.current = { kind, arg: arg || 0 };
    if (location.hash !== "#setups") location.hash = "#setups"; else App.show();
  },
  close() {
    if (this.cam) this.cam.stop();
    if (this.lightOn) { API.run("light", { on: 0 }); this.lightOn = false; }
    API.run("led", { v: 0 });
    this.current = null; App.show();
  },

  pos() { return (API.st.status && API.st.status.pos) || null; },
  async refresh() { await App.poll(); if (this._showPos) this._showPos(); },

  async moveRel(dx, dy) { await API.run("rel", { x: dx.toFixed(4), y: dy.toFixed(4), slow: 1 }); await this.refresh(); this.cam && this.cam.grab(); },
  async moveTo(x, y) {
    x = Math.min(Math.max(x, 0), 416); y = Math.min(Math.max(y, 0), 400);
    await API.run("move", { x: x.toFixed(4), y: y.toFixed(4), slow: 1 }); await this.refresh();
  },

  panel(root, cur) {
    const k = cur.kind;
    const upNozzle = /^up[1-4]$/.test(k) ? +k[2] : 0;
    const isUp = k.startsWith("up");
    const title = k === "stack" ? `Stack ${cur.arg + 1} pick position` : (this.list.find((l) => l[0] === k) || [])[1];
    const posLbl = h("div", { class: "mono" });
    this._showPos = () => { const p = this.pos(); posLbl.textContent = p ? `head X ${fmt(p[0], 3)}  Y ${fmt(p[1], 3)}` + (this.tool !== undefined && !isUp ? `   point under tool: ${fmt(p[0] - Cfg.offset(this.tool)[0], 3)}, ${fmt(p[1] - Cfg.offset(this.tool)[1], 3)}` : "") : "position unknown: home first"; };
    const S = [300, 200];

    // points for the collective shot
    this.points = k === "up0" ? [0, 1, 2, 3].map((i) => Cfg.pt(`Position/dPosSubMouses${i}`)) : null;
    const cam = new CamView(isUp ? 0 : 1, {
      points: this.points,
      onClick: (dx, dyUp, ev) => {
        if (k === "up0" && ev.ctrlKey) {                    // set a nozzle pixel point (zoom-aware)
          const x = cam.size / 2 + dx, y = cam.size / 2 - dyUp;
          const i = y > cam.size / 2 ? (x < cam.size / 2 ? 0 : 1) : (x < cam.size / 2 ? 2 : 3);
          this.points[i] = [Math.round(x), Math.round(y)];
          cam.points = this.points; cam._draw();
          toast(`nozzle ${i + 1} point ${this.points[i].join(", ")}`);
          return;
        }
        if (isUp) this.moveRel(-dx / Cfg.pxmm(0), -dyUp / Cfg.pxmm(0));    // bring the clicked nozzle tip to the centre
        else this.moveRel(dx / Cfg.pxmm(1), dyUp / Cfg.pxmm(1));             // bring the clicked feature to the centre
      },
    });
    this.cam = cam;
    this.tool = isUp ? 1 : 0;

    const left = h("div", { class: "card" }, h("h3", {}, title), cam.el, cam.controls(), posLbl);
    const right = h("div", { class: "card" });
    root.append(h("div", { class: "row" }, h("button", { class: "btn", onclick: () => this.close() }, "← Back")),
      h("div", { class: "grid", style: "grid-template-columns: minmax(320px, 680px) minmax(300px, 1fr)" }, left, right));

    const jog = (dx, dy) => this.moveRel(dx, dy);
    const stepSel = h("select", {}, [0.009525, 0.05, 0.1, 0.5, 1, 5].map((v) => h("option", { value: v, selected: v === 0.1 }, v === 0.009525 ? "1 step" : `${v} mm`)));
    const st = () => +stepSel.value;
    right.append(h("h3", {}, "Move"),
      h("div", { class: "row" }, h("div", { class: "jog" },
        h("span"), h("button", { class: "btn", onclick: () => jog(0, st()) }, "Y+"), h("span"),
        h("button", { class: "btn", onclick: () => jog(-st(), 0) }, "X−"), h("span"), h("button", { class: "btn", onclick: () => jog(st(), 0) }, "X+"),
        h("span"), h("button", { class: "btn", onclick: () => jog(0, -st()) }, "Y−"), h("span")), h("label", {}, "Step "), stepSel),
      h("div", { class: "muted" }, isUp ? "Click the nozzle tip in the image: the head moves it to the crosshair." :
        "Click a feature in the image: the head moves so it is under the crosshair."));

    const save = h("button", { class: "btn primary" }, "Save");
    const go = h("button", { class: "btn" }, "Go to stored position");
    right.append(h("h3", { style: "margin-top:14px" }, "Setup"));

    if (isUp) {
      const key = k === "up0" ? "Position/posCamLookUpMouseMouseCenter" : `Position/posCamLookUpMouse${upNozzle - 1}`;
      const exposure = Math.trunc(50 * (k === "up0" ? Cfg.get("Default/fBrightness4", 1) : Cfg.get("Default/fBrightness", 1)));
      const prep = async () => {
        await API.run("led", { v: 0 });                      // the head lamp dazzles the up camera
        await API.run("light", { on: 1 }); this.lightOn = true;
        await API.req("POST", `/api/cam/set?cam=0&w=1024&h=1024&exposure=${exposure}`).catch((e) => toast(e.message, "bad"));
      };
      go.onclick = async () => { const p = Cfg.pt(key); await prep(); await this.moveTo(p[0], p[1]); cam.grab(); };
      save.onclick = async () => {
        const p = this.pos(); if (!p) return toast("position unknown", "bad");
        Cfg.setPt(key, p);
        if (k === "up0") this.points.forEach((pt, i) => Cfg.setPt(`Position/dPosSubMouses${i}`, pt));
        await Settings.save();
      };
      right.append(h("div", { class: "kv" }, h("div", {}, "Stored"), h("div", { class: "mono" }, Cfg.pt(key).map((v) => fmt(v, 3)).join(", ")),
        h("div", {}, "Exposure"), h("div", {}, `${exposure} (50 × brightness)`)),
        h("div", { class: "row" }, go, upNozzle ? h("button", { class: "btn", onclick: () => this.lensRotation(cam) }, "Lens rotation (average 36 turns)") : null, save),
        h("div", { class: "row" }, h("label", {}, "Exposure for the empty nozzle hole "), this.holeExp = h("input", { type: "number", min: 1, max: 65535, style: "width:80px",
          value: localStorage.getItem("holeExp") || 600, onchange: (e) => localStorage.setItem("holeExp", e.target.value) }),
          h("span", { class: "muted" }, "the job exposure (~150) is too dark for an empty nozzle")),
        h("div", { class: "row" }, h("button", { class: "btn primary", onclick: () => (k === "up0" ? this.autoFour(cam, exposure) : this.autoNozzle(cam, upNozzle, exposure)) },
          k === "up0" ? "Auto-detect the 4 nozzle holes" : "Auto-detect the hole and centre the axis"), this.autoOut = h("div", { class: "mono", style: "white-space:pre-wrap" })),
        k === "up0" ? h("div", { class: "muted" }, "Ctrl+click each nozzle tip in its quarter: nozzle 1 bottom-left, 2 bottom-right, 3 top-left, 4 top-right.") : "");
      prep().then(() => cam.grab());
    } else if (k === "lens") {
      right.append(h("ol", {},
        h("li", {}, "Put something that takes a mark (ink on paper, putty) under (300, 200)."),
        h("li", {}, h("button", { class: "btn", onclick: () => this.trail(S) }, "Trail"), " — nozzle 1 presses 13 mm down and twists ±180° ten times there."),
        h("li", {}, h("button", { class: "btn", onclick: async () => { const d = Cfg.pt("Position/dPosCamLookDownMouse1"); await this.headCam(1); await this.moveTo(S[0] + d[0], S[1] + d[1]); cam.grab(); } }, "Camera over the mark"), " — uses the stored offset; then click the centre of the ring until it is on the crosshair."),
        h("li", {}, "Save: offset = head position − (300, 200).")),
        h("div", { class: "kv" }, h("div", {}, "Stored offset"), h("div", { class: "mono" }, Cfg.pt("Position/dPosCamLookDownMouse1").map((v) => fmt(v, 3)).join(", "))),
        h("div", { class: "row" }, save));
      save.onclick = async () => {
        const p = this.pos(); if (!p) return toast("position unknown", "bad");
        Cfg.setPt("Position/dPosCamLookDownMouse1", [p[0] - S[0], p[1] - S[1]]);
        await Settings.save();
      };
      this.headCam(1).then(() => cam.grab());
    } else {                                                  // trash box / stack: point under a tool
      const key = k === "trash" ? "Position/posWasteBox" : `Stack/lstStack${cur.arg}.pos`;
      const toolBtns = ["Camera", "Nozzle 1", "Nozzle 2", "Nozzle 3", "Nozzle 4"].map((t, i) => h("button", { class: "btn" + (i === 0 ? " on" : ""),
        onclick: async (e) => {                               // keep the same point under the new tool
          const p = this.pos(); if (!p) return;
          const o = Cfg.offset(this.tool), n = Cfg.offset(i);
          this.tool = i;
          [...e.target.parentNode.children].forEach((b) => b.classList.remove("on")); e.target.classList.add("on");
          zRow.style.display = i ? "" : "none";
          await this.moveTo(p[0] - o[0] + n[0], p[1] - o[1] + n[1]); if (!i) cam.grab(); this._showPos();
        } }, t));
      const z = h("input", { type: "range", min: 0, max: 120, value: 0,
        oninput: (e) => { zl.textContent = `height ${(12 - e.target.value / 10).toFixed(1)} mm`; },
        onchange: (e) => API.run("z", { n: this.tool, mm: (e.target.value / 10).toFixed(1), speed: 50 }) });
      const zl = h("span", { class: "muted" }, "height 12.0 mm");
      const zRow = h("div", { class: "row", style: "display:none" }, h("label", {}, "Nozzle down "), z, zl,
        h("button", { class: "btn", onclick: () => { z.value = 0; zl.textContent = "height 12.0 mm"; API.run("z", { n: this.tool, mm: 0, speed: 50 }); } }, "Up"));
      const lamp = h("select", { onchange: (e) => API.run("led", { v: e.target.value }) },
        [["1", "Inner lamp"], ["2", "Outer lamp"], ["3", "Both"], ["0", "Off"]].map(([v, t]) => h("option", { value: v }, t)));
      go.onclick = async () => { const p = Cfg.pt(key), o = Cfg.offset(this.tool); await this.moveTo(p[0] + o[0], p[1] + o[1]); cam.grab(); };
      save.onclick = async () => {
        const p = this.pos(); if (!p) return toast("position unknown", "bad");
        const o = Cfg.offset(this.tool);
        Cfg.setPt(key, [p[0] - o[0], p[1] - o[1]]);
        await Settings.save();
      };
      const feedBtn = k === "stack" && cur.arg < 48 ? h("button", { class: "btn", onclick: async () => { await Settings.stackFeedTest(cur.arg); setTimeout(() => cam.grab(), 800); } }, "Feed") : null;
      right.append(h("div", { class: "row" }, toolBtns), zRow, h("div", { class: "row" }, h("label", {}, "Lamp "), lamp),
        h("div", { class: "kv" }, h("div", {}, "Stored"), h("div", { class: "mono" }, Cfg.pt(key).map((v) => fmt(v, 3)).join(", "))),
        h("div", { class: "row" }, go, feedBtn, save),
        h("div", { class: "muted" }, "Saved value = head position − offset of the selected tool (the point under nozzle 1)."));
      this.headCam(1).then(() => cam.grab());
    }
    this._showPos();
  },

  // full-resolution gray frame from the up camera as {w, h, data}
  async grabGray(cam) {
    const r = await API.req("GET", `/api/cam/grab?cam=${cam}&scale=1&settle=1&t=${Date.now()}`, null, true);
    const bmp = await createImageBitmap(await r.blob());
    const cv = document.createElement("canvas");
    cv.width = bmp.width; cv.height = bmp.height;
    const c = cv.getContext("2d", { willReadFrequently: true });
    c.drawImage(bmp, 0, 0);
    const d = c.getImageData(0, 0, bmp.width, bmp.height).data, g = new Uint8Array(bmp.width * bmp.height);
    for (let i = 0; i < g.length; i++) g[i] = d[4 * i];
    this.cam.frame = { bmp, scale: 1 }; this.cam.size = bmp.width; this.cam._draw();
    return { w: bmp.width, h: bmp.height, data: g };
  },

  // turn nozzle(s) through 360 deg in `steps`, find the hole(s), fit the rotation axis per nozzle
  async spin(nozzles, centres, steps, radius) {
    const pts = nozzles.map(() => []), fails = nozzles.map(() => []);
    for (let i = 0; i < steps; i++) {
      const a = -180 + (360 / steps) * i;
      for (const n of nozzles) await API.run("rotate", { n, deg: a.toFixed(1), speed: 150, wait: 0 });
      for (const n of nozzles) await API.mc("rotate", { n, deg: a.toFixed(1), speed: 150 }).catch(() => {});   // waits until idle
      await new Promise((r) => setTimeout(r, 250));
      const img = await this.grabGray(0);
      nozzles.forEach((n, j) => {
        const r = NozzleCal.findHole(img, centres[j][0], centres[j][1], radius);
        if (r.ok) pts[j].push([r.x, r.y]); else fails[j].push(`${a}°: ${r.why}`);
      });
      if (this.autoOut) this.autoOut.textContent = `turning ${i + 1}/${steps} …`;
    }
    for (const n of nozzles) await API.run("rotate", { n, deg: 0, speed: 150 });
    return nozzles.map((n, j) => ({ n, fit: pts[j].length >= 3 ? NozzleCal.fitCircle(pts[j]) : null, seen: pts[j].length, fails: fails[j] }));
  },

  describe(r, k) {
    if (!r.fit) return `nozzle ${r.n}: hole seen in only ${r.seen} frames\n  ${r.fails.slice(0, 3).join("\n  ")}`;
    const run = r.fit.r / k * 1000;
    return `nozzle ${r.n}: axis ${r.fit.x.toFixed(2)}, ${r.fit.y.toFixed(2)} px   runout ${run.toFixed(0)} µm` +
           (run > 100 ? "  ← large: bent or worn nozzle?" : "") + `   fit rms ${r.fit.rms.toFixed(2)} px   (${r.seen} frames)`;
  },

  // up camera set for the empty nozzle hole (head lamp off, brighter exposure); the returned function
  // puts the job exposure back
  async holeCam(jobExp) {
    await API.run("led", { v: 0 });
    const e = +(this.holeExp && this.holeExp.value) || 600;
    await API.req("POST", `/api/cam/set?cam=0&w=1024&h=1024&exposure=${e}`).catch((x) => toast(x.message, "bad"));
    return () => API.req("POST", `/api/cam/set?cam=0&w=1024&h=1024&exposure=${jobExp}`).catch(() => {});
  },

  // single nozzle over the up camera: find the axis, move it to the image centre and repeat (up to 3 moves)
  // until it is within 30 µm; the repeat also covers a wrong up-camera scale (Vision/fUpPxPerMm)
  async autoNozzle(cam, n, jobExp) {
    if (!(await API.take())) return;
    cam.stop();
    const restore = await this.holeCam(jobExp);
    try {
      const k = Cfg.pxmm(0);
      let [r] = await this.spin([n], [[512, 512]], 12, 300);
      let text = this.describe(r, k);
      this.autoOut.textContent = text;
      if (!r.fit) return;
      let total = [0, 0], res = null, passes = 0;
      for (let pass = 1; pass <= 3; pass++) {
        const mv = NozzleCal.axisToHeadMove(r.fit.x, r.fit.y, 1024, k);
        await this.moveRel(mv[0], mv[1]);
        total = [total[0] + mv[0], total[1] + mv[1]]; passes = pass;
        [r] = await this.spin([n], [[512, 512]], 6, 200);               // check: the axis should now be centred
        res = r.fit ? Math.hypot(r.fit.x - 512, r.fit.y - 512) / k * 1000 : null;
        text += `\npass ${pass}: moved ${mv[0].toFixed(3)}, ${mv[1].toFixed(3)} mm, then ` + (res === null ? "hole not found" : `axis ${res.toFixed(0)} µm from the centre`);
        this.autoOut.textContent = text;
        if (res === null || res <= 30) break;
      }
      text += (res !== null && res <= 30 ? "  ✓" : "  (run again)") + `\ntotal head move ${total[0].toFixed(3)}, ${total[1].toFixed(3)} mm` +
        (passes > 1 ? "\nIt needed more than one pass: the up-camera scale in Settings → Vision (fUpPxPerMm) is probably off " +
          "(measured on this machine: about 32.15 px/mm; NeoDen4 used 34.5)." : "") + "\nSave stores this head position.";
      this.autoOut.textContent = text;
    } finally { await restore(); }
  },

  // collective shot: all four nozzles turn together; each axis becomes its pixel point
  async autoFour(cam, jobExp) {
    if (!(await API.take())) return;
    cam.stop();
    const restore = await this.holeCam(jobExp);
    let res;
    try { res = await this.spin([1, 2, 3, 4], this.points, 12, 150); } finally { await restore(); }
    res.forEach((r, i) => { if (r.fit) this.points[i] = [+r.fit.x.toFixed(1), +r.fit.y.toFixed(1)]; });
    cam.points = this.points; cam._draw();
    this.autoOut.textContent = res.map((r) => this.describe(r, Cfg.pxmm(0))).join("\n") + "\nA nozzle whose hole is not found keeps its old point (empty or removed nozzle).\nSave stores the head position and these 4 points.";
  },

  async headCam(led) {
    await API.req("POST", "/api/cam/set?cam=1&w=1024&h=1024&exposure=25&gain=8").catch((e) => toast(e.message, "bad"));
    if (led !== undefined) await API.run("led", { v: led });
  },

  // frmCamDownCorrect "Trail": 10 x (nozzle 1 down 13 mm, twist -180/+180, up) at S
  async trail(S) {
    if (!(await API.take())) return;
    await this.moveTo(S[0], S[1]);
    for (let i = 0; i < 10; i++) {
      if (!(await API.run("z", { n: 1, mm: 13, speed: 100 }))) return;
      await API.run("rotate", { n: 1, deg: -180, speed: 50 });
      await API.run("rotate", { n: 1, deg: 180, speed: 50 });
      await API.run("z", { n: 1, mm: 0, speed: 100 });
    }
    await API.run("rotate", { n: 1, deg: 0, speed: 50 });
    toast("trail done", "ok");
  },

  // frmXYLockFaceUp "Lens rotation": all nozzles -180..170 in 10° steps, average (÷36, not ÷32), back to 0°
  async lensRotation(cam) {
    if (!(await API.take())) return;
    const n = 1024, acc = new Float32Array(n * n), cv = document.createElement("canvas");
    cv.width = cv.height = n;
    const c = cv.getContext("2d", { willReadFrequently: true });
    let frames = 0;
    for (let a = -180; a <= 170; a += 10) {
      for (let k = 1; k <= 4; k++) await API.run("rotate", { n: k, deg: a, speed: 150, wait: 0 });
      await new Promise((r) => setTimeout(r, 300));
      const r = await API.req("GET", `/api/cam/grab?cam=0&scale=1&t=${Date.now()}`, null, true).catch(() => null);
      if (!r) continue;
      const bmp = await createImageBitmap(await r.blob());
      c.drawImage(bmp, 0, 0, n, n);
      const d = c.getImageData(0, 0, n, n).data;
      for (let i = 0; i < n * n; i++) acc[i] += d[4 * i];
      frames++;
      cam.info.textContent = `lens rotation ${frames}/36`;
    }
    for (let k = 1; k <= 4; k++) await API.run("rotate", { n: k, deg: 0, speed: 150 });
    if (!frames) return;
    const img = c.createImageData(n, n);
    for (let i = 0; i < n * n; i++) { const v = Math.min(255, acc[i] / frames); img.data[4 * i] = img.data[4 * i + 1] = img.data[4 * i + 2] = v; img.data[4 * i + 3] = 255; }
    c.putImageData(img, 0, 0);
    cam.stop();
    cam.frame = { bmp: await createImageBitmap(cv), scale: 1 };
    cam.size = n; cam._draw();
    toast("averaged image: click the centre of the ring", "ok");
  },
};
