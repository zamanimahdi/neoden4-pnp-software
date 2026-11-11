// Settings page: every NeoDen4 Config.ini setting (SETTINGS_MANUAL_SPEC section 1), saved in the
// same format NeoDen4.exe reads (the agent backs up the old file on every save).
const Settings = {
  tab: "general",

  async render(root) {
    const pathIn = h("input", { value: Cfg.path, style: "width:340px" });
    const bar = h("div", { class: "card" }, h("div", { class: "row" },
      h("label", {}, "Config.ini "), pathIn,
      h("button", { class: "btn", onclick: async () => { Cfg.path = pathIn.value; localStorage.setItem("cfgPath", Cfg.path); await this.reload(root); } }, "Load"),
      h("div", { class: "grow" }),
      this.dirtyLbl = h("span", { class: "muted" }, ""),
      h("button", { class: "btn primary", onclick: () => this.save(root) }, "Save")));
    root.append(bar);
    if (!Cfg.loaded) {
      try { await Cfg.load(); } catch (e) { root.append(h("div", { class: "card" }, `Cannot read ${Cfg.path}: ${e.message}`)); return; }
    }
    const tabs = [["general", "General"], ["vision", "Vision"], ["job", "Job"], ["padalign", "Pad alignment"], ["leadvision", "Lead vision"], ["positions", "Positions"], ["stacks", "Stacks"], ["feeders", "Feeders"], ["peelers", "Peelers"], ["kiosk", "This PC's screen"], ["raw", "Raw file"]];
    root.append(h("div", { class: "tabs" }, tabs.map(([k, t]) =>
      h("button", { class: "btn" + (k === this.tab ? " on" : ""), onclick: () => { this.tab = k; App.show(); } }, t))));
    const body = h("div", { class: "card" });
    root.append(body);
    this[this.tab](body);
    this.updDirty();
  },

  async reload(root) { await Cfg.load(); App.show(); },
  updDirty() { if (this.dirtyLbl) this.dirtyLbl.textContent = Cfg.dirty() ? `${Cfg.dirty()} unsaved change(s)` : ""; },

  async save() {
    if (!Cfg.dirty()) { toast("nothing to save"); return; }
    try { const r = await Cfg.save(); toast(`saved${r.backup ? " (backup " + r.backup + ")" : ""}`, "ok"); App.show(); }
    catch (e) { toast("save failed: " + e.message, "bad"); }
  },

  // select / text bound to a string Config key
  choice(key, def, opts) {
    return h("select", { onchange: (e) => { Cfg.set(key, e.target.value, "s"); this.updDirty(); } },
      opts.map(([v, t]) => h("option", { value: v, selected: String(Cfg.get(key, def)) === v }, t)));
  },
  text(key, placeholder) {
    return h("input", { value: Cfg.get(key, ""), placeholder, style: "width:320px", onchange: (e) => { Cfg.set(key, e.target.value.trim(), "s"); this.updDirty(); } });
  },
  table(b, rows, note) {
    b.append(h("table", {}, rows.map(([l, f]) => h("tr", {}, h("td", {}, l), h("td", {}, f)))));
    if (note) b.append(h("div", { class: "muted", style: "margin-top:8px" }, note));
  },

  // [PadAlign]: the head camera looks at the part's pads just before placing (docs/PADALIGN.md)
  padalign(b) {
    const f = (k, d, o) => this.field("PadAlign/" + k, k[0] === "b" ? "b" : k[0] === "f" ? "f" : "i", Object.assign({ def: d }, o || {}));
    this.table(b, [
      ["Use pad alignment", f("bEnabled", false)],
      ["Footprints (comma separated; * = every part)", this.text("PadAlign/sFootprints", "0402, 0603, SOIC8 …")],
      ["Designators", this.text("PadAlign/sRefs", "U1, U3 …")],
      ["Head lamp (1 inner, 2 outer, 3 both)", f("nLed", 1, { choices: [1, 2, 3] })],
      ["Exposure (0 = automatic)", f("nExposure", 0, { min: 0, max: 65535 })],
      ["Threshold (0 = automatic)", f("nThreshold", 0, { min: 0, max: 255 })],
      ["Pads darker than the board", f("bInvert", false)],
      ["Smallest pad (mm²)", f("fMinPadMm2", 0.05, { min: 0, max: 10, step: 0.01, dec: 3 })],
      ["Largest pad (mm²)", f("fMaxPadMm2", 25, { min: 0.1, max: 200, step: 0.1, dec: 2 })],
      ["Pad search radius (mm)", f("fMatchRadiusMm", 0.6, { min: 0.05, max: 3, step: 0.05, dec: 2 })],
      ["Share of the pads that must be found", f("fMinMatched", 0.6, { min: 0.1, max: 1, step: 0.05, dec: 2 })],
      ["Largest fit error, rms (mm)", f("fMaxRmsMm", 0.08, { min: 0.005, max: 1, step: 0.005, dec: 3 })],
      ["Pad spacing vs. Gerber, largest error (share)", f("fMaxScaleErr", 0.04, { min: 0.005, max: 0.5, step: 0.005, dec: 3 })],
      ["Ask before a shift larger than (mm)", f("fMaxShiftMm", 0.5, { min: 0.01, max: 5, step: 0.05, dec: 2 })],
      ["Ask before a turn larger than (°)", f("fMaxTurnDeg", 2, { min: 0.1, max: 45, step: 0.1, dec: 1 })],
      ["Head camera angle (° ; from the camera check)", f("fHeadCamAngle", 0, { min: -10, max: 10, step: 0.01, dec: 3 })],
    ], "Test it first on the Pad alignment page (Machine settings → Pad alignment), then switch it on for the parts that need it.");
  },

  // [LeadVision]: the up camera measures the leads and fits them to the part's pads
  leadvision(b) {
    const f = (k, d, o) => this.field("LeadVision/" + k, k[0] === "b" ? "b" : k[0] === "f" ? "f" : "i", Object.assign({ def: d }, o || {}));
    this.table(b, [
      ["Use lead vision", f("bEnabled", false)],
      ["Footprints (comma separated; * = every part with pads)", this.text("LeadVision/sFootprints", "SOT23, SOIC8, QFP64 …")],
      ["Designators", this.text("LeadVision/sRefs", "U1 …")],
      ["Exposure (0 = the job's up-camera exposure)", f("nExposure", 0, { min: 0, max: 65535 })],
      ["Threshold (0 = automatic)", f("nThreshold", 0, { min: 0, max: 255 })],
      ["Smallest lead (mm²)", f("fMinLeadMm2", 0.01, { min: 0, max: 5, step: 0.005, dec: 3 })],
      ["Largest lead (mm²)", f("fMaxLeadMm2", 4, { min: 0.05, max: 50, step: 0.05, dec: 2 })],
      ["Lead search radius (mm)", f("fMatchRadiusMm", 0.4, { min: 0.05, max: 3, step: 0.05, dec: 2 })],
      ["Share of the leads that must be found", f("fMinMatched", 0.7, { min: 0.1, max: 1, step: 0.05, dec: 2 })],
      ["Largest fit error, rms (mm)", f("fMaxRmsMm", 0.05, { min: 0.005, max: 1, step: 0.005, dec: 3 })],
      ["Leads not found: use the body outline instead of asking", f("bBodyFallback", false)],
    ], "For parts whose leads matter more than their body (SOT-23, SOIC, QFP, connectors). Test it on the Pad alignment page, step 6.");
  },

  // input bound to a Config key
  field(key, type, opts = {}) {
    const v = Cfg.get(key, opts.def ?? 0);
    let el;
    const set = (val) => { Cfg.set(key, type === "f" ? parseFloat(val) : type === "b" ? !!val : parseInt(val, 10), type); this.updDirty(); };
    if (opts.choices) {
      el = h("select", { onchange: (e) => set(opts.scale ? e.target.value / opts.scale : e.target.value) },
        opts.choices.map((c) => h("option", { value: c, selected: Math.abs((opts.scale ? v * opts.scale : v) - c) < 1e-6 }, String(c) + (opts.unit || ""))));
    } else if (type === "b") {
      el = h("input", { type: "checkbox", checked: !!v, onchange: (e) => set(e.target.checked) });
    } else {
      el = h("input", { type: "number", value: type === "f" ? +Number(v).toFixed(opts.dec ?? 3) : v, min: opts.min, max: opts.max,
        step: opts.step || (type === "f" ? 0.01 : 1), onchange: (e) => set(e.target.value) });
    }
    return el;
  },

  general(b) {
    const rows = [
      ["Placement speed (XY speed byte)", this.field("Default/nRunSpeed", "i", { choices: [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130] })],
      ["Vibration feeder frequency", this.field("Default/nVibFreq", "i", { min: 100, max: 250 })],
      ["Vibration feeder strength", this.field("Default/nVibCurrent", "i", { min: 0, max: 50 })],
      ["Up-camera angle (added to every placement, °)", this.field("Default/fAngleCamLookUp", "f", { min: -180, max: 180, step: 0.1, dec: 1 })],
      ["Up-camera brightness, one nozzle (exposure = 50 × value)", this.field("Default/fBrightness", "f", { choices: [10, 20, 30, 40, 50, 75, 100, 150, 200, 250, 300], scale: 100, unit: " %" })],
      ["Up-camera brightness, four nozzles", this.field("Default/fBrightness4", "f", { choices: [10, 20, 30, 40, 50, 75, 100, 150, 200, 250, 300], scale: 100, unit: " %" })],
    ];
    b.append(h("table", {}, rows.map(([l, f]) => h("tr", {}, h("td", {}, l), h("td", {}, f)))),
      h("div", { class: "row" },
        h("button", { class: "btn", onclick: () => API.run("xy_speed", { v: Cfg.get("Default/nRunSpeed", 80), force: 9 }, "XY speed sent") }, "Send speed to the machine")));
  },

  // settings of the new software ([Vision] group; NeoDen4.exe ignores it). Defaults = neocam/config.py
  vision(b) {
    const sel = (key, def, opts) => h("select", { onchange: (e) => { Cfg.set(key, e.target.value, "s"); this.updDirty(); } },
      opts.map(([v, t]) => h("option", { value: v, selected: Cfg.get(key, def) === v }, t)));
    const rows = [
      ["Check the part size after vision", this.field("Vision/bSizeCheck", "b", { def: true })],
      ["Size ratio minimum (measured / package)", this.field("Vision/fSizeMin", "f", { def: 0.8, min: 0.1, max: 1, step: 0.01, dec: 2 })],
      ["Size ratio maximum", this.field("Vision/fSizeMax", "f", { def: 1.25, min: 1, max: 5, step: 0.01, dec: 2 })],
      ["Width / height pairing", sel("Vision/sSizeOrientation", "stack", [["stack", "by the stack angle (like NeoDen4)"], ["auto", "automatic (closest fit)"]])],
      ["Verify the angle correction with a second image", this.field("Vision/bVerifyAngle", "b", { def: true })],
      ["Angle tolerance (°)", this.field("Vision/fVerifyAngleTol", "f", { def: 1.0, min: 0.1, max: 10, step: 0.1, dec: 1 })],
      ["Offset tolerance (mm)", this.field("Vision/fVerifyOffsetTol", "f", { def: 0.15, min: 0.01, max: 2, step: 0.01, dec: 2 })],
      ["Correction tries before rejecting", this.field("Vision/nVerifyTries", "i", { def: 3, min: 1, max: 10 })],
      ["Up camera scale (px per mm; NeoDen4: 34.5)", this.field("Vision/fUpPxPerMm", "f", { def: 34.5, min: 10, max: 100, step: 0.01, dec: 3 })],
      ["Head camera scale (px per mm; NeoDen4: 112)", this.field("Vision/fHeadPxPerMm", "f", { def: 112, min: 30, max: 300, step: 0.01, dec: 3 })],
    ];
    b.append(h("table", {}, rows.map(([l, f]) => h("tr", {}, h("td", {}, l), h("td", {}, f)))),
      h("div", { class: "muted", style: "margin-top:8px" },
        "NeoDen4 used fixed 0.8–1.25 and paired width/height only for exact 0/180 or ±90° stacks; here the stack angle is rounded to the nearest 90°. " +
        "Angle check: after turning the nozzle by the measured angle, a new up-camera image must show the part square within the tolerance and at the expected offset; " +
        "otherwise it is corrected again, and rejected after the given number of tries. Below about 1° a 2 mm part shows no measurable tilt at 34.5 px/mm."));
  },

  // [Job] group of the new software. Defaults = neocam/config.py JOB_DEFAULTS
  job(b) {
    const rows = [
      ["Sound (all beeps; off = silent machine)", this.field("Job/bSound", "b", { def: true })],
      ["Beep at the end of the job", this.field("Job/bBeepJobEnd", "b", { def: true })],
      ["Beep when the machine needs the operator (mark, pick, vision)", this.field("Job/bBeepError", "b", { def: true })],
      ["Wait until the camera image is still after a move", this.field("Job/bSettle", "b", { def: true })],
      ["Still: largest shift between frames (px)", this.field("Job/fSettleTolPx", "f", { def: 0.3, min: 0.05, max: 5, step: 0.05, dec: 2 })],
      ["Still: give up after (s)", this.field("Job/fSettleMaxS", "f", { def: 1.0, min: 0.1, max: 5, step: 0.1, dec: 1 })],
      ["See, turn, see: every part shot on its own is looked at again after turning, until its turn reads right (all feeders)", this.field("Job/bSeeTurnSee", "b", { def: true })],
      ["Assign plans the nozzles so up to four parts are picked together (each part a nozzle its feeder allows, not yet used in the cycle)", this.field("Job/bPlanNozzles", "b", { def: true })],
      ["Fill a pick cycle from up to this many parts ahead when the next part's nozzle is taken (0: strictly in order)", this.field("Job/nCycleLookahead", "i", { def: 12 })],
      ["Feeders of different speeds share a pick cycle (at the slowest of them)", this.field("Job/bCycleMixSpeeds", "b", { def: true })],
      ["See, turn, see: looks at most", this.field("Job/nSeeTurnLooks", "i", { def: 5, min: 1, max: 10, step: 1 })],
      ["See, turn, see: turn left that is good enough (°)", this.field("Job/fSeeTurnTolDeg", "f", { def: 0.1, min: 0.05, max: 2, step: 0.05, dec: 2 })],
      ["Every nozzle turn ends from the same side: this much short first (°, 0 = off; the rotation's backlash)", this.field("Job/fTurnApproachDeg", "f", { def: 1.5, min: 0, max: 5, step: 0.5, dec: 1 })],
      ["Learn each feeder's pick point from where its parts sit on the nozzle (bottom camera)", this.field("Job/bPickLearn", "b", { def: true })],
      ["Learning: correct after this many parts (their mean offset)", this.field("Job/nPickLearnSamples", "i", { def: 5, min: 1, max: 50, step: 1 })],
      ["Learning: never further than (mm) from the taught point", this.field("Job/fPickLearnMaxMm", "f", { def: 1.0, min: 0.1, max: 3, step: 0.1, dec: 1 })],
      ["Count every pick, reject and placement (Work → Statistics)", this.field("Job/bStats", "b", { def: true })],
      ["Warn when a nozzle / feeder fails this often (%)", this.field("Job/fStatsWarnPct", "f", { def: 15, min: 1, max: 100, step: 1, dec: 0 })],
      ["After-place checks (set per feeder or per part; off = none of them)", this.field("Job/bPlaceCheck", "b", { def: true })],
      ["After-place photo: on the first panel of a run only (first article)", this.field("Job/bPlaceCheckFirstOnly", "b", { def: false })],
      ["After-place photo: largest offset (mm)", this.field("Job/fPlaceCheckMm", "f", { def: 0.15, min: 0.02, max: 2, step: 0.01, dec: 2 })],
      ["After-place photo: largest turn (°)", this.field("Job/fPlaceCheckDeg", "f", { def: 3, min: 0.5, max: 45, step: 0.5, dec: 1 })],
      ["Save vision images and results (Vision log; for calibrating the vision)", this.field("Job/bVisionLog", "b", { def: false })],
      ["Vision log: largest size (MB)", this.field("Job/nVisionLogMaxMB", "i", { def: 2000, min: 50, max: 100000, step: 50 })],
      ["Board transform from the fiducials", this.choice("Job/sMarkFit", "affine", [["neoden", "2 marks: shift + turn (NeoDen4)"], ["affine", "3+ marks: also scale and skew"], ["bilinear", "4+ marks at the corners: also a warped board"]])],
    ];
    b.append(h("table", {}, rows.map(([l, f]) => h("tr", {}, h("td", {}, l), h("td", {}, f)))),
      h("div", { class: "muted", style: "margin-top:8px" },
        "NeoDen4 always beeped at the end of a job and always waited 250 ms before an image. " +
        "The manual Buzzer button works even with the sound off."));
  },

  positions(b) {
    const rows = [
      ["Collective nozzle alignment (4-nozzle shot)", "Position/posCamLookUpMouseMouseCenter", "up0"],
      ["Nozzle 1 over the up camera", "Position/posCamLookUpMouse0", "up1"],
      ["Nozzle 2 over the up camera", "Position/posCamLookUpMouse1", "up2"],
      ["Nozzle 3 over the up camera", "Position/posCamLookUpMouse2", "up3"],
      ["Nozzle 4 over the up camera", "Position/posCamLookUpMouse3", "up4"],
      ["Head camera relative to nozzle 1", "Position/dPosCamLookDownMouse1", "lens"],
      ["Trash box", "Position/posWasteBox", "trash"],
    ];
    b.append(h("table", {}, h("tr", {}, h("th", {}, "Position"), h("th", {}, "X"), h("th", {}, "Y"), h("th", {})),
      rows.map(([l, key, setup]) => h("tr", {}, h("td", {}, l),
        h("td", {}, this.field(key + ".x", "f", { min: -20, max: 416 })), h("td", {}, this.field(key + ".y", "f", { min: 0, max: 416 })),
        h("td", {}, h("button", { class: "btn", onclick: () => Setups.open(setup) }, "Align…"))))),
      h("h3", { style: "margin-top:12px" }, "Nozzle pixel points in the 4-nozzle up-camera image"),
      h("table", {}, [0, 1, 2, 3].map((i) => h("tr", {}, h("td", {}, `Nozzle ${i + 1}`),
        h("td", {}, this.field(`Position/dPosSubMouses${i}.x`, "f", { min: 0, max: 1024, step: 1, dec: 0 })),
        h("td", {}, this.field(`Position/dPosSubMouses${i}.y`, "f", { min: 0, max: 1024, step: 1, dec: 0 }))))));
  },

  stacks(b) {
    b.append(h("div", { class: "muted" }, "Stacks 1–19 right bank, 20–48 left bank, 49–98 without feeder. Positions are the point under nozzle 1."),
      h("div", { class: "scroll" }, h("table", {},
        h("tr", {}, ["Stack", "X", "Y", "Feeder ID", "Peeler ID", ""].map((t) => h("th", {}, t))),
        Array.from({ length: 98 }, (_, s) => h("tr", {},
          h("td", {}, String(s + 1)),
          h("td", {}, this.field(`Stack/lstStack${s}.pos.x`, "f", { min: 0, max: 416 })),
          h("td", {}, this.field(`Stack/lstStack${s}.pos.y`, "f", { min: 0, max: 416 })),
          h("td", {}, s < 48 ? this.field(`Stack/lstStack${s}.nFeedId`, "i", { min: 1, max: 50 }) : ""),
          h("td", {}, s < 48 ? this.field(`Stack/lstStack${s}.nPeelId`, "i", { min: 1, max: 48 }) : ""),
          h("td", {}, h("button", { class: "btn", onclick: () => Setups.open("stack", s) }, "Teach…"),
            s < 48 ? h("button", { class: "btn", onclick: () => this.stackFeedTest(s) }, "Feed test") : ""))))));
  },

  async stackFeedTest(s) {               // CMachine::feedConfiguredStack: peel, then feed
    const fid = Cfg.get(`Stack/lstStack${s}.nFeedId`), pid = Cfg.get(`Stack/lstStack${s}.nPeelId`);
    if (await API.run("peel", { n: pid, a: Cfg.get(`Peel/lstPeel${pid - 1}.nMoment`, 80), b: Cfg.get(`Peel/lstPeel${pid - 1}.nLoad`, 16) }))
      await API.run("feed", { n: fid, p0: Cfg.get(`Feed/lstFeed${fid - 1}.nMoment`, 50), p1: Cfg.get(`Feed/lstFeed${fid - 1}.nLoad`, 4), mode: 0 }, `stack ${s + 1}: peel + feed sent`);
  },

  feeders(b) {
    const from = h("select", {}, [...Array.from({ length: 50 }, (_, i) => h("option", { value: i + 1, selected: i === 49 }, `Feeder ${i + 1}`)), h("option", { value: 51 }, "All feeders (1–48)")]);
    const to = h("select", {}, Array.from({ length: 50 }, (_, i) => h("option", { value: i + 1 }, `Feeder ${i + 1}`)));
    b.append(h("div", { class: "scroll" }, h("table", {},
      h("tr", {}, ["Feeder", "Feeding rate (tape pitch)", "Strength", ""].map((t) => h("th", {}, t))),
      Array.from({ length: 48 }, (_, k) => h("tr", {}, h("td", {}, String(k + 1)),
        h("td", {}, this.field(`Feed/lstFeed${k}.nLoad`, "i", { choices: [2, 4, 8, 12, 16, 24, 32], unit: " mm" })),
        h("td", {}, this.field(`Feed/lstFeed${k}.nMoment`, "i", { choices: [10, 20, 30, 40, 50, 60, 70, 80, 90, 100] })),
        h("td", {},
          h("button", { class: "btn", onclick: () => API.run("feed", { n: k + 1, p0: Cfg.get(`Feed/lstFeed${k}.nMoment`, 50), p1: Cfg.get(`Feed/lstFeed${k}.nLoad`, 4), mode: 0 }, `feeder ${k + 1} test sent`) }, "Test"),
          h("button", { class: "btn", onclick: () => API.run("feed", { n: k + 1, mode: 2 }, `feeder ${k + 1} calibration sent`) }, "Calibrate")))))),
      h("h3", { style: "margin-top:12px" }, "Change a feeder's ID (connect only that feeder)"),
      h("div", { class: "row" }, from, h("span", {}, "→"), to,
        h("button", { class: "btn", onclick: async () => {
          const f = +from.value, t = +to.value;
          if (f === 51) {
            if (!confirm("This changes the ID of every feeder 1–48. Continue?")) return;
            for (let id = 1; id <= 48; id++) await API.run("feed", { n: id, p0: t, mode: 1 });
            toast("update finished", "ok");
          } else await API.run("feed", { n: f, p0: t, mode: 1 }, `feeder ${f} → ${t}`);
        } }, "Change ID")));
  },

  peelers(b) {
    b.append(h("div", { class: "scroll" }, h("table", {},
      h("tr", {}, ["Peeler", "Peel length", "Strength", ""].map((t) => h("th", {}, t))),
      Array.from({ length: 48 }, (_, k) => h("tr", {}, h("td", {}, String(k + 1)),
        h("td", {}, this.field(`Peel/lstPeel${k}.nLoad`, "i", { min: 4, max: 64 })),
        h("td", {}, this.field(`Peel/lstPeel${k}.nMoment`, "i", { choices: [10, 20, 30, 40, 50, 60, 70, 80, 90, 100] })),
        h("td", {}, h("button", { class: "btn", onclick: () => API.run("peel", { n: k + 1, a: Cfg.get(`Peel/lstPeel${k}.nMoment`, 80), b: Cfg.get(`Peel/lstPeel${k}.nLoad`, 16) }, `peeler ${k + 1} test sent`) }, "Test")))))));
  },

  // the machine PC's own screen (neokiosk, /etc/neoden/kiosk.ini): mouse pointer speed and page zoom;
  // Apply writes them and restarts the kiosk (the screen goes dark for a few seconds)
  async kiosk(b) {
    let k;
    try { k = await API.req("GET", "/api/kiosk"); } catch (e) { b.append(h("div", { class: "muted" }, "kiosk settings: " + e.message)); return; }
    if (!k.installed) b.append(h("div", { class: "warn", style: "margin-bottom:8px" }, "The kiosk is not installed on this PC (neokiosk/install.sh): these take effect once it is."));
    const num = (v, min, max, step) => h("input", { type: "number", value: v, min, max, step, style: "width:80px" });
    const speed = num(k.mouse_speed, 0.2, 5, 0.1), zoom = num(k.zoom, 0.4, 2, 0.05);
    const theme = h("select", {}, [["dark", "Dark"], ["light", "Light"], ["auto", "As the system (light)"]].map(([v, t]) => h("option", { value: v, selected: (k.theme || "auto") === v }, t)));
    const step = (inp, f) => h("button", { class: "btn small", onclick: () => { inp.value = (Math.round(+inp.value * f * 20) / 20).toFixed(2); } }, f > 1 ? "faster" : "slower");
    b.append(h("div", { class: "kv", style: "align-items:center" },
      h("div", {}, "Mouse speed"), h("div", { class: "row", style: "margin:0" }, speed, step(speed, 0.8), step(speed, 1.25),
        h("span", { class: "muted", style: "font-size:12px" }, "1 = as the mouse is, 2 = twice as fast, 0.5 = half")),
      h("div", {}, "Theme"), h("div", { class: "row", style: "margin:0" }, theme,
        h("span", { class: "muted", style: "font-size:12px" }, "the machine's screen; its ☾ / ☀ button changes it too")),
      h("div", {}, "Page zoom"), h("div", { class: "row", style: "margin:0" }, zoom,
        h("span", { class: "muted", style: "font-size:12px" }, "the machine monitor: 0.85 at 1280 x 1024 (Ctrl + / Ctrl − change it until the next restart)"))),
      h("div", { class: "row" }, h("button", { class: "btn primary", onclick: async () => {
        if (!confirm("Write these and restart the machine's screen (dark for a few seconds)?")) return;
        try { await API.req("POST", `/api/kiosk?mouse_speed=${+speed.value}&zoom=${+zoom.value}&theme=${theme.value}`); toast("saved: the machine's screen restarts", "ok"); }
        catch (e) { toast(e.message, "bad"); }
      } }, "Apply (restarts the screen)"),
        h("span", { class: "muted", style: "font-size:12px" }, "only the machine PC's own screen; a laptop or phone uses its own mouse settings")));
  },

  raw(b) {
    const text = INI.serialize(Cfg.doc, Cfg.changes);
    const ta = h("textarea", { rows: 30 }, text);
    b.append(h("div", { class: "muted" }, "The whole file as it would be saved. Editing here replaces it as typed."),
      ta, h("div", { class: "row" }, h("button", { class: "btn primary", onclick: async () => {
        try { const r = await API.req("PUT", `/api/file?path=${encodeURIComponent(Cfg.path)}`, INI.toBytes(ta.value));
              toast(`saved (backup ${r.backup || "none"})`, "ok"); await Cfg.load(); App.show(); }
        catch (e) { toast(e.message, "bad"); }
      } }, "Save raw file")));
  },
};
