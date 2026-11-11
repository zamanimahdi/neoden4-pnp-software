// Manual control page (NeoDen4 frmMacManual + keyboard jog, SETTINGS_MANUAL_SPEC section 2).
const Manual = {
  nozzle: 1, step: 0.1, led: 0,

  render(root) {
    const s = () => API.st.status || {};

    const nozzleBtns = [1, 2, 3, 4].map((n) => h("button", { class: "btn" + (n === this.nozzle ? " on" : ""),
      onclick: (e) => { this.nozzle = n; [...e.target.parentNode.children].forEach((b) => b.classList.remove("on")); e.target.classList.add("on"); } }, `Nozzle ${n}`));
    const zIn = h("input", { type: "number", min: 0, max: 12, step: 0.1, value: 0 });
    const aIn = h("input", { type: "number", min: -180, max: 180, step: 1, value: 0 });
    const N = () => this.nozzle;

    const status = h("div", { class: "kv" });
    const showStatus = () => {
      const t = s();
      status.replaceChildren(
        h("div", {}, "XY"), h("div", {}, t.xy_state_name || t.error || "–"),
        h("div", {}, "Position"), h("div", {}, t.pos ? `X ${fmt(t.pos[0])}  Y ${fmt(t.pos[1])}` : "unknown (home first)"),
        h("div", {}, "Nozzles idle"), h("div", {}, (t.idle || []).map((v) => (v ? "●" : "○")).join(" ")),
        h("div", {}, "Vacuum 1..4"), h("div", { class: "mono" }, (t.vacuum || []).join("  ")),
        h("div", {}, "Z 1..4 / angle"), h("div", { class: "mono" }, (t.z || []).map((v) => fmt(v, 1)).join(" ") + "  /  " + (t.angle || []).map((v) => fmt(v, 0)).join(" ")),
        h("div", {}, "Start button"), h("div", {}, t.input0 ? "on" : "off"),
        h("div", {}, "Step losses"), h("div", { class: t.step_losses ? "bad" : "" }, t.step_losses === undefined ? "–" :
          t.step_losses ? `${t.step_losses} (last: ${t.last_step_loss.state} → ${(t.last_step_loss.target || []).map((v) => fmt(v)).join(", ")}); repeated losses mean a crash` : "none"),
      );
    };
    this._showStatus = () => { showStatus(); if (this.cam && !this.cam.live && this.cam.el.isConnected) this.cam._draw(); };
    showStatus();

    const feeders = (from, to) => h("div", { class: "feeders" }, Array.from({ length: to - from + 1 }, (_, i) => from + i).map((id) =>
      h("button", { class: "btn", title: `feed ${id}`, onclick: () => this.feed(id) }, String(id))));
    const peelers = (from, to) => h("div", { class: "feeders" }, Array.from({ length: to - from + 1 }, (_, i) => from + i).map((id) =>
      h("button", { class: "btn", title: `peel ${id}`, onclick: () => this.peel(id) }, String(id))));
    this.peelSync = h("input", { type: "checkbox" });

    // camera on top: head camera (or up camera) with the coordinates in the picture; a click moves the
    // clicked point to the centre (like the calibration pages); arrow keys jog
    const mkCam = (n) => Manual.coordCam(n);
    this.cam = mkCam(this.camN || 1);
    const camHost = h("div", {}, this.cam.el), ctlHost = h("div", {}, this.cam.controls());
    const camSel = h("select", { onchange: (e) => { this.cam.stop(); this.camN = +e.target.value; this.cam = mkCam(this.camN); camHost.replaceChildren(this.cam.el); ctlHost.replaceChildren(this.cam.controls()); this.cam.start(); } },
      h("option", { value: 1, selected: (this.camN || 1) === 1 }, "Head camera"), h("option", { value: 0, selected: this.camN === 0 }, "Up camera"));

    root.append(h("div", { class: "camtop" },
      h("div", { class: "card" }, h("div", { class: "row", style: "margin-top:0" }, camSel, ctlHost,
        h("button", { class: "btn", onclick: () => { this.led = (this.led + 1) % 4; API.run("led", { v: this.led }, ["Head lamp off", "Inner lamp", "Outer lamp", "Both lamps"][this.led]); } }, "Head lamp"),
        h("button", { class: "btn", onclick: () => API.run("light", { on: s().light ? 0 : 1 }) }, "Up light"),
        h("button", { class: "btn", title: "head camera: centre on the round mark (fiducial, hole) nearest the picture's middle", onclick: async () => {
          if (!API.st.haveControl && !(await API.take())) return;
          try {
            const r = await API.req("POST", `/api/align/circle?client=${API.st.client}`);
            await App.poll(); this.cam.grab();
            toast(`centred on the circle: ${fmt(r.x, 3)}, ${fmt(r.y, 3)} (Ø ${fmt(r.d, 2)} mm)`, "ok");
          } catch (e) { toast(e.message, "bad"); }
        } }, "Centre on the circle")),
        camHost, h("div", { class: "muted" }, "Click in the picture: that point moves to the centre. Keyboard: arrows / WASD jog (Shift 1 mm, Ctrl one step).")),
      h("div", { class: "stack" },
        Manual.moveCard(),
        h("div", { class: "card" }, h("h3", {}, "Status"), status))));
    this.cam.start();

    root.append(h("div", { class: "cols3", style: "margin-top:12px" },
      h("div", { class: "stack" },
      h("div", { class: "card" }, h("h3", {}, "Lights and outputs"),
        h("div", { class: "row" },
          h("button", { class: "btn", onclick: () => { this.led = (this.led + 1) % 4; API.run("led", { v: this.led }, ["Head lamp off", "Inner lamp", "Outer lamp", "Both lamps"][this.led]); } }, "Head lamp"),
          h("button", { class: "btn", onclick: () => API.run("light", { on: s().light ? 0 : 1 }) }, "Up-camera light"),
          holdBtn("Buzzer", () => API.run("beep", { on: 1 }), () => API.run("beep", { on: 0 }))),
        h("div", { class: "row" },
          h("label", {}, h("input", { type: "checkbox", onchange: (e) => API.run("vib_feeder", { on: e.target.checked ? 1 : 0,
            v: Cfg.get("Default/nVibCurrent", 30), p: Cfg.get("Default/nVibFreq", 100) }) }), " Vibration feeder")))),

      h("div", { class: "card" }, h("h3", {}, "Head"),
        h("div", { class: "muted" }, "Keyboard: Q / E nozzle 1 up / down 0.1 mm, Z = full down."),
        h("div", { class: "row" }, nozzleBtns),
        h("div", { class: "row" },
          holdBtn("Nozzle down", () => API.run("z", { n: N(), mm: 12, speed: 100, wait: 0 }), () => API.run("z", { n: N(), mm: 0, speed: 100 })),
          holdBtn("Suck", () => API.run("valve", { ch: N(), state: -1 }), () => API.run("valve", { ch: N(), state: 0 })),
          holdBtn("Blow", () => API.run("valve", { ch: N(), state: 1 }), () => API.run("valve", { ch: N(), state: 0 }))),
        h("div", { class: "row" },
          holdBtn("Turn left", () => API.run("rotate", { n: N(), deg: 180, speed: 50, wait: 0 }), () => API.run("rotate", { n: N(), deg: 0, speed: 50 })),
          holdBtn("Turn right", () => API.run("rotate", { n: N(), deg: -180, speed: 50, wait: 0 }), () => API.run("rotate", { n: N(), deg: 0, speed: 50 })),
          h("button", { class: "btn", onclick: () => API.run("raise", {}) }, "All up")),
        h("div", { class: "row" }, h("label", {}, "Z mm"), zIn, h("button", { class: "btn", onclick: () => API.run("z", { n: N(), mm: zIn.value, speed: 50 }) }, "Set"),
          h("label", {}, "Angle"), aIn, h("button", { class: "btn", onclick: () => API.run("rotate", { n: N(), deg: aIn.value, speed: 150 }) }, "Set")))),

      h("div", { class: "section-title" }, "Feeders"),
      h("details", { class: "card", open: this.feedOpen, ontoggle: (e) => (this.feedOpen = e.target.open) },
        h("summary", {}, "Feed or peel one feeder (click = feed with the Config.ini strength / rate)"),
        h("div", { class: "row" }, h("label", {}, this.peelSync, " Peel first (Feed Peel Synchronous)")),
        h("div", { class: "muted" }, "Right bank 1..19"), feeders(1, 19),
        h("div", { class: "muted" }, "Left bank 20..48"), feeders(20, 48),
        h("div", { class: "muted", style: "margin-top:10px" }, "Peelers 1..48"), peelers(1, 48)),

      h("div", { class: "section-title" }, "Measure"),
      Points.card(),
    );
    if (!Cfg.loaded) Cfg.load().catch(() => {});
  },

  // camera with the coordinates in the picture; a click brings the clicked point to the centre
  coordCam(n) {
    const s = () => API.st.status || {};
    const v = new CamView(n, { onClick: (dx, dyUp) => {
      const k = Cfg.pxmm(n), sg = n === 1 ? 1 : -1;
      return API.run("rel", { x: (sg * dx / k).toFixed(4), y: (sg * dyUp / k).toFixed(4), slow: 1 });
    } });
    v.label = () => {
      const p = s().pos;
      if (!p) return ["position unknown: Home first"];
      if (n === 0) return [`head    X ${fmt(p[0], 3)}  Y ${fmt(p[1], 3)}`];
      const o = Cfg.offset(0);
      return [`camera  X ${fmt(p[0] - o[0], 3)}  Y ${fmt(p[1] - o[1], 3)}`, `nozzle1 X ${fmt(p[0], 3)}  Y ${fmt(p[1], 3)}`];
    };
    (this.coordCams = (this.coordCams || []).filter((c) => c.el.isConnected)).push(v);
    return v;
  },

  // the Move card (Manual control, Pad alignment): Home, jog, step, Go to a camera / nozzle-1 point, Here, speed
  moveCard() {
    const s = () => API.st.status || {};
    const stepSel = h("select", { onchange: (e) => (this.step = +e.target.value) },
      [["0.009525", "1 step"], ["0.1", "0.1 mm"], ["1", "1 mm"], ["10", "10 mm"]].map(([v, t]) =>
        h("option", { value: v, selected: +v === this.step }, t)));
    const jog = (dx, dy) => API.run("rel", { x: dx * this.step, y: dy * this.step });
    const gx = h("input", { type: "number", step: "0.01", value: "200" });
    const gy = h("input", { type: "number", step: "0.01", value: "200" });
    const slow = h("input", { type: "checkbox", checked: true });
    const speed = h("input", { type: "range", min: 10, max: 130, step: 10, value: 80,
      onchange: (e) => API.run("xy_speed", { v: e.target.value, force: 9 }, `XY speed ${e.target.value}`) });
    const gmode = h("select", {}, h("option", { value: "cam" }, "head camera at"), h("option", { value: "n1" }, "nozzle 1 at"));
    const goTo = () => {
      let x = +gx.value, y = +gy.value;
      if (gmode.value === "cam") { const o = Cfg.offset(0); x += o[0]; y += o[1]; }
      return API.run("move", { x: x.toFixed(3), y: y.toFixed(3), slow: slow.checked ? 1 : 0 });
    };
    const here = () => { const p = s().pos; if (!p) return; const o = gmode.value === "cam" ? Cfg.offset(0) : [0, 0]; gx.value = (p[0] - o[0]).toFixed(3); gy.value = (p[1] - o[1]).toFixed(3); };
    const map = this.tableMap((x, y) => { gx.value = x.toFixed(2); gy.value = y.toFixed(2); goTo(); }, () => gmode.value);
    return h("div", { class: "card" }, h("h3", {}, "Move"), map,
      h("div", { class: "row" },
        h("button", { class: "btn primary", onclick: () => API.run("home", {}, "XY homed") }, "Home"),
        h("button", { class: "btn", onclick: () => API.run("correct", {}, "XY corrected") }, "Clear step loss"),
        h("label", {}, "Step "), stepSel),
      h("div", { class: "row" },
        h("div", { class: "jog" },
          h("span"), h("button", { class: "btn", onclick: () => jog(0, 1) }, "Y+"), h("span"),
          h("button", { class: "btn", onclick: () => jog(-1, 0) }, "X−"), h("span", { class: "muted", style: "text-align:center;align-self:center" }, "jog"),
          h("button", { class: "btn", onclick: () => jog(1, 0) }, "X+"),
          h("span"), h("button", { class: "btn", onclick: () => jog(0, -1) }, "Y−"), h("span")),
        h("div", { class: "muted", style: "max-width:150px;font-size:12px" },
          "Keys: arrows / WASD jog by the step (Shift 1 mm, Ctrl one motor step), Q / E nozzle 1 up / down. Click the picture: that point moves to the centre.")),
      h("div", { class: "row" }, h("label", {}, "Go: "), gmode),
      h("div", { class: "row" }, h("label", {}, "X"), gx, h("label", {}, "Y"), gy),
      h("div", { class: "row" }, h("button", { class: "btn primary", onclick: goTo }, "Go"), h("button", { class: "btn", onclick: here }, "Here"), h("label", {}, slow, " slow")),
      h("div", { class: "row" }, h("label", {}, "Speed 10..130 "), speed));
  },

  // the whole machine table (X 0..416, Y 0..400 mm, Y up): click = go there (camera or nozzle 1, as the
  // Go setting); shows nozzle 1 (white), the head camera (green) and the last target (orange)
  tableMap(onPick, mode) {
    const XM = 416, YM = 400, W = 416, H = 400;
    const c = h("canvas", { width: W, height: H, class: "tablemap", title: "click: go there" });
    const tip = h("div", { class: "maptip" }, "");
    const box = h("div", { class: "mapbox" }, c, tip);
    const g = c.getContext("2d");
    const toPx = (x, y) => [x / XM * W, H - y / YM * H];
    const fromEv = (ev) => { const r = c.getBoundingClientRect(); return [(ev.clientX - r.left) / r.width * XM, (1 - (ev.clientY - r.top) / r.height) * YM]; };
    let target = null, hover = null;
    const draw = () => {
      if (!c.isConnected) return;
      const dark = matchMedia("(prefers-color-scheme: dark)").matches;
      g.fillStyle = dark ? "#11151c" : "#f4f6fa"; g.fillRect(0, 0, W, H);
      g.lineWidth = 1;
      for (let v = 0; v <= 416; v += 10) {
        g.strokeStyle = v % 50 ? (dark ? "#1b212b" : "#e8ebf1") : (dark ? "#2c3442" : "#cfd5df");
        const [px] = toPx(v, 0); g.beginPath(); g.moveTo(px + 0.5, 0); g.lineTo(px + 0.5, H); g.stroke();
        if (v <= 400) { const [, py] = toPx(0, v); g.beginPath(); g.moveTo(0, py + 0.5); g.lineTo(W, py + 0.5); g.stroke(); }
      }
      g.fillStyle = dark ? "#6b7385" : "#8a92a3"; g.font = "10px system-ui";
      for (let v = 0; v <= 400; v += 100) { const [px] = toPx(v, 0); g.fillText(String(v), px + 2, H - 3); const [, py] = toPx(0, v); if (v) g.fillText(String(v), 2, py - 2); }
      const dot = (x, y, col, r) => { const [px, py] = toPx(x, y); g.fillStyle = col; g.beginPath(); g.arc(px, py, r, 0, 7); g.fill(); };
      const cross = (x, y, col) => { const [px, py] = toPx(x, y); g.strokeStyle = col; g.lineWidth = 2; g.beginPath(); g.moveTo(px - 8, py); g.lineTo(px + 8, py); g.moveTo(px, py - 8); g.lineTo(px, py + 8); g.stroke(); g.lineWidth = 1; };
      if (target) cross(target[0], target[1], "#f59e0b");
      const p = (API.st.status || {}).pos;
      if (p) {
        const o = Cfg.offset(0);
        dot(p[0] - o[0], p[1] - o[1], "#22c55e", 5);            // head camera
        dot(p[0], p[1], dark ? "#ffffff" : "#1d2330", 4);        // nozzle 1
      }
      tip.textContent = hover ? `X ${hover[0].toFixed(1)}  Y ${hover[1].toFixed(1)}  (${mode() === "cam" ? "camera" : "nozzle 1"})` :
        p ? `nozzle 1 ${p[0].toFixed(2)}, ${p[1].toFixed(2)}` : "position unknown: Home first";
    };
    c.addEventListener("mousemove", (ev) => { hover = fromEv(ev); draw(); });
    c.addEventListener("mouseleave", () => { hover = null; draw(); });
    c.addEventListener("click", (ev) => {
      let [x, y] = fromEv(ev);
      x = Math.min(Math.max(x, 0), XM); y = Math.min(Math.max(y, 0), YM);
      target = [x, y]; draw(); onPick(x, y);
    });
    (this.maps = (this.maps || []).filter((m) => m.c.isConnected)).push({ c, draw });
    draw();
    return box;
  },

  // CFunNodeFeed::feedStack (SETTINGS_MANUAL_SPEC 3.4)
  async feed(id) {
    if (!Cfg.loaded) await Cfg.load().catch(() => {});
    if (this.peelSync.checked) {
      let pid = null;
      for (let s = 0; s < 98; s++) if (Cfg.get(`Stack/lstStack${s}.nFeedId`) === id) { pid = Cfg.get(`Stack/lstStack${s}.nPeelId`); break; }
      if (pid && !(await this.peel(pid))) return;
    }
    const k = id - 1;
    if (!(await API.run("feed", { n: id, p0: Cfg.get(`Feed/lstFeed${k}.nMoment`, 50), p1: Cfg.get(`Feed/lstFeed${k}.nLoad`, 4), mode: 0 }))) return;
    for (let t = 0; t < 40; t++) {                       // waitFeedDone, 2 s, resend on failure (6 times)
      await new Promise((r) => setTimeout(r, 50));
      const st = await API.mc("feeder", { n: id }).catch(() => null);
      if (st && st.done) { toast(`feeder ${id} done`, "ok"); return; }
      if (st && st.failed) { toast(`feeder ${id}: failed, ${st.remaining} left`, "bad"); return; }
    }
    toast(`feeder ${id}: no done flag within 2 s`, "bad");
  },
  async peel(id) {
    if (!Cfg.loaded) await Cfg.load().catch(() => {});
    const k = id - 1;
    return API.run("peel", { n: id, a: Cfg.get(`Peel/lstPeel${k}.nMoment`, 80), b: Cfg.get(`Peel/lstPeel${k}.nLoad`, 16) });
  },

  // keyboard jog (frmXYKeyFaceDown): only while the Manual page is open and no input has focus
  key(ev) {
    if (!["#manual", "#padalign"].includes(location.hash) || /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) return;
    const step = ev.ctrlKey && !ev.shiftKey ? 0.009525 : ev.shiftKey && !ev.ctrlKey ? 1 : 0.1;
    const k = ev.key.toLowerCase();
    const d = { arrowup: [0, 1], w: [0, 1], arrowdown: [0, -1], s: [0, -1], arrowright: [1, 0], d: [1, 0], arrowleft: [-1, 0], a: [-1, 0] }[k];
    this.kz = this.kz || 0;
    if (d) { ev.preventDefault(); API.run("rel", { x: d[0] * step, y: d[1] * step }); }
    else if (k === "q" || k === "pageup") { this.kz = Math.max(0, this.kz - 0.1); API.run("z", { n: 1, mm: this.kz.toFixed(1), speed: 100 }); }
    else if (k === "e" || k === "pagedown") { this.kz = Math.min(12, this.kz + 0.1); API.run("z", { n: 1, mm: this.kz.toFixed(1), speed: 100 }); }
    else if ((k === "z" || k === "end") && ev.shiftKey) { this.kz = 12; API.run("z", { n: 1, mm: 12, speed: 100 }); }   // all the way down: Shift + Z / End
  },
};
document.addEventListener("keydown", (e) => Manual.key(e));
