// Pad alignment and lead vision, tried live on a test board (docs/PADALIGN.md, "Calibrating on a
// test board"). Every step calls /api/align/... (neoserver/align.py) and shows the annotated picture:
// blue + expected pad, yellow found pad, green + fitted pad, red + camera centre.
const PadAlign = {
  ref: "", nozzle: 1, parts: [],

  async render(root) {
    if (!Cfg.loaded) await Cfg.load().catch(() => {});
    const st = await API.req("GET", "/api/status").catch(() => ({}));
    const proc = st.neoden_dir ? PathU.join(st.neoden_dir, "Proc") : "";
    const list = await API.req("GET", `/api/files?path=${encodeURIComponent(proc)}`).catch(() => ({ entries: [] }));
    const jobs = (list.entries || []).filter((e) => !e.dir && /\.csv$/i.test(e.name)).map((e) => PathU.join(proc, e.name));
    if (!this.job || !jobs.includes(this.job)) this.job = jobs.includes(JobRun.path) ? JobRun.path : jobs[0] || "";
    const jobsDir = st.neoden_dir ? PathU.join(st.neoden_dir, "Jobs") : "";

    // pictures on top: the live head camera and the last result
    this.live = Manual.coordCam(1);
    this.result = h("img", { class: "resimg", alt: "" });
    this.resultText = h("div", { class: "mono", style: "white-space:pre-wrap" }, "Results appear here.");
    this.views = h("div", { class: "row" });
    const num = (v, w = 70, step = "any") => h("input", { type: "number", value: v, step, style: `width:${w}px` });

    // step inputs
    const P = (k, d) => Cfg.get("PadAlign/" + k, d);
    const led = h("select", {}, [[1, "inner"], [2, "outer"], [3, "both"]].map(([v, t]) => h("option", { value: v, selected: +P("nLed", 1) === v }, `lamp ${t}`)));
    const exp = num(P("nExposure", 0)), thr = num(P("nThreshold", 0), 60), mn = num(P("fMinPadMm2", 0.05)), mx = num(P("fMaxPadMm2", 25));
    const inv = h("input", { type: "checkbox", checked: !!P("bInvert", false) });
    const camStep = num(1, 60);
    const jobSel = h("select", { onchange: (e) => { this.job = e.target.value; this.parts = []; App.show(); } }, jobs.map((p) => h("option", { value: p, selected: p === this.job }, p.split(/[\\/]/).pop())));
    const pnp = h("input", { placeholder: "Pick & Place .csv on the machine", value: this.pnp || jobsDir, style: "width:100%", onchange: (e) => (this.pnp = e.target.value) });
    const ger = h("input", { placeholder: "Gerber folder or .zip on the machine", value: this.ger || jobsDir, style: "width:100%", onchange: (e) => (this.ger = e.target.value) });
    const refSel = h("select", { onchange: (e) => (this.ref = e.target.value) }, h("option", { value: "" }, "– part –"),
      this.parts.map((p) => h("option", { value: p.ref, selected: p.ref === this.ref }, `${p.ref}  ${p.footprint}  (${p.pads} pads)`)));
    const noz = h("select", { onchange: (e) => (this.nozzle = +e.target.value) }, [1, 2, 3, 4].map((n) => h("option", { value: n, selected: n === this.nozzle }, `nozzle ${n}`)));
    const sang = h("input", { type: "number", placeholder: "stack angle", style: "width:100px" });
    const lthr = num(Cfg.get("LeadVision/nThreshold", 0), 60), lexp = num(Cfg.get("LeadVision/nExposure", 0));
    this.partsBox = h("div", { class: "scroll", style: "max-height:180px" });
    this.showParts();

    const step = (n, title, hint, ...body) => h("div", { class: "card" }, h("h3", {}, `${n}. ${title}`), hint ? h("div", { class: "muted", style: "margin-bottom:6px" }, hint) : null, ...body);
    const go = (label, fn, cls) => h("button", { class: "btn " + (cls || "primary"), onclick: fn }, label);
    const padq = () => ({ led: led.value, exp: exp.value, thr: thr.value, invert: inv.checked ? 1 : 0, min: mn.value, max: mx.value });

    root.append(
      h("div", { class: "padtop" },
        h("div", { class: "card" }, h("div", { class: "row", style: "margin-top:0" }, h("b", {}, "Head camera"), this.live.controls()), this.live.el),
        h("div", { class: "card" }, h("div", { class: "row", style: "margin-top:0" }, h("b", {}, "Result")), this.result, this.views, this.resultText),
        Manual.moveCard()),
      h("div", { class: "grid", style: "margin-top:12px" },
        step(1, "Head camera scale and angle", "Put the camera over a group of pads (Move, arrow keys, or click the picture), then measure. Repeat until the values stay the same.",
          h("div", { class: "row" }, h("label", {}, "step mm"), camStep, go("Measure", () => this.call("camcal", { step: camStep.value }, (r) => {
            this.cam = r;
            return `px/mm ${r.px_per_mm} (X ${r.px_per_mm_x}, Y ${r.px_per_mm_y})\ncamera angle ${r.cam_angle_deg}°, skew ${r.skew_deg}°\npads followed ${r.pads_followed.join(" / ")}\nnow: ${r.now.fHeadPxPerMm} px/mm, ${r.now.fHeadCamAngle}°`;
          })), go("Write to settings", () => this.writeCam(), ""))),
        step(2, "Pad detection", "Over different pad types: change the lamp, exposure, threshold and pad size until every pad is found and nothing else.",
          h("div", { class: "row" }, led, h("label", {}, "exposure"), exp, h("label", {}, "threshold"), thr, h("label", {}, inv, " darker pads")),
          h("div", { class: "row" }, h("label", {}, "pad mm² from"), mn, h("label", {}, "to"), mx),
          h("div", { class: "row" }, go("Detect here", () => this.call("detect", padq(), (r) => `${r.blobs.length} pads, threshold ${r.threshold}, mean grey ${r.mean}` + (r.hint ? `\n${r.hint}` : ""))),
            go("Save these values", () => this.writePad(led, exp, thr, inv, mn, mx), ""))),
        step(3, "Pads file", "From the board's Pick & Place file and Gerber (paths on the machine).",
          h("div", { class: "row" }, h("label", {}, "job"), jobSel),
          pnp, h("div", { style: "height:6px" }), ger,
          h("div", { class: "row" }, go("Build pads file", () => this.call("pads", { job: this.job, pnp: pnp.value, gerber: ger.value }, (r) =>
            `${r.parts} parts, ${r.pads} pads (layer ${r.layer})\nfit: ${JSON.stringify(r.fit)}`, false).then(() => this.loadParts())),
            go("List parts", () => this.loadParts(), "")), this.partsBox),
        step(4, "Board transform (marks)", "Finds the job's fiducials. With 3 or more marks the residuals should be a few hundredths of a mm.",
          h("div", { class: "row" }, go("Find the marks", () => this.call("marks", { job: this.job }, (r) =>
            `fit ${r.fit}\n` + r.marks.map((m, i) => `mark ${i + 1}: design ${m.design.join(", ")} → measured ${m.measured.join(", ")}`).join("\n") +
            (r.residuals_mm.length ? `\nresiduals mm: ${r.residuals_mm.join("  ")}` : ""))))),
        step(5, "One part", "Run it a few times: the correction should repeat within about 0.01 mm.",
          h("div", { class: "row" }, refSel, go("Align on the pads", () => this.call("part", { job: this.job, ref: this.ref }, (r) =>
            `${r.aligned ? "OK" : "FAILED"}  ${r.message || ""}\ndx ${fmt(r.dx, 4)}  dy ${fmt(r.dy, 4)} mm   turn ${fmt(r.dtheta, 3)}°\nrms ${fmt(r.rms, 4)} mm, ${r.matched} of ${r.expected} pads\n` +
            (r.within_limits ? "within the limits" : `outside the limits (${r.limits.fMaxShiftMm} mm / ${r.limits.fMaxTurnDeg}°): the job would ask first`))))),
        step(6, "Lead vision (up camera)", "Empty nozzle first (background), then pick the part by hand on the Manual page and measure its leads.",
          h("div", { class: "row" }, noz, go("Empty nozzle (background)", () => this.call("leads", { nozzle: this.nozzle, bg: 1 }, (r) => `background of nozzle ${r.nozzle} kept (mean grey ${r.mean})`), "")),
          h("div", { class: "row" }, h("label", {}, "threshold"), lthr, h("label", {}, "exposure"), lexp, sang),
          h("div", { class: "row" }, go("Measure the leads", () => this.call("leads", { nozzle: this.nozzle, job: this.job, ref: this.ref, thr: lthr.value, exp: lexp.value, stack_angle: sang.value }, (r) =>
            `${r.valid ? "OK" : "FAILED"}  ${r.message || ""}\ncentre ${fmt(r.x, 4)}, ${fmt(r.y, 4)} mm   angle ${fmt(r.angle, 3)}°\n${r.matched} of ${r.expected} leads, rms ${fmt(r.rms, 4)} mm\n` +
            `body outline (NeoDen): ${r.body && r.body.valid ? `${fmt(r.body.x, 3)}, ${fmt(r.body.y, 3)} mm, ${fmt(r.body.angle, 2)}°` : "not found"}` +
            (r.leads_vs_body_mm !== null && r.leads_vs_body_mm !== undefined ? `\nleads vs. body: ${r.leads_vs_body_mm} mm` : ""))),
            h("span", { class: "muted" }, "uses the part chosen in step 5")))));
    this.live.grab();
  },

  // POST /api/align/<what>; moving calls need the control lock
  async call(what, params, show, moves = true) {
    if (moves && !API.st.haveControl && !(await API.take())) return null;
    this.resultText.textContent = `${what}…`;
    try {
      const r = await API.req("POST", `/api/align/${what}?client=${API.st.client}&${API.qs(params)}`);
      if (r.png) this.result.src = "data:image/png;base64," + r.png;
      this.views.replaceChildren(...(r.views || []).map((v) => h("img", { class: "resthumb", src: "data:image/png;base64," + v.png, title: `at ${(v.at || []).join(", ")}`,
        onclick: (e) => (this.result.src = e.target.src) })));
      if (r.views && r.views[0] && !r.png) this.result.src = "data:image/png;base64," + r.views[0].png;
      this.resultText.textContent = show ? show(r) : JSON.stringify(r, null, 1);
      this.live.grab();
      return r;
    } catch (e) { this.resultText.textContent = `${what}: ${e.message}`; toast(e.message, "bad"); return null; }
  },

  async loadParts() {
    const r = await API.req("GET", `/api/align/parts?job=${encodeURIComponent(this.job)}`).catch((e) => ({ parts: [], note: e.message }));
    this.parts = r.parts || [];
    if (!this.parts.length) { this.partsBox.replaceChildren(h("div", { class: "muted" }, r.note || "no parts")); return; }
    App.show();
  },
  showParts() {
    if (!this.parts.length) return;
    this.partsBox.replaceChildren(h("table", { class: "pick" }, h("tr", {}, ["Part", "Footprint", "Pads", "In job"].map((t) => h("th", {}, t))),
      this.parts.map((p) => h("tr", { class: p.ref === this.ref ? "sel" : "", onclick: () => { this.ref = p.ref; App.show(); } },
        h("td", {}, p.ref), h("td", {}, p.footprint), h("td", {}, p.pads), h("td", {}, p.in_job ? "yes" : "no")))));
  },

  async writeCam() {
    if (!this.cam) return toast("measure first", "warn");
    Cfg.set("Vision/fHeadPxPerMm", this.cam.px_per_mm, "f");
    Cfg.set("PadAlign/fHeadCamAngle", this.cam.cam_angle_deg, "f");
    try { await Cfg.save(); toast(`saved: ${this.cam.px_per_mm} px/mm, ${this.cam.cam_angle_deg}°`, "ok"); } catch (e) { toast(e.message, "bad"); }
  },
  async writePad(led, exp, thr, inv, mn, mx) {
    Cfg.set("PadAlign/nLed", +led.value, "i"); Cfg.set("PadAlign/nExposure", +exp.value, "i"); Cfg.set("PadAlign/nThreshold", +thr.value, "i");
    Cfg.set("PadAlign/bInvert", inv.checked, "b"); Cfg.set("PadAlign/fMinPadMm2", +mn.value, "f"); Cfg.set("PadAlign/fMaxPadMm2", +mx.value, "f");
    try { await Cfg.save(); toast("pad detection values saved", "ok"); } catch (e) { toast(e.message, "bad"); }
  },
};
