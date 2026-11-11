// Setup view: every part of one board (a panel: its first board) drawn on its Gerber pads the way it will be
// placed: the feeder's part as the bottom camera saw it on the nozzle (nozzle angle 0), turned by the turn the
// job gives it (rotation - Pick angle + the bottom camera's angle, less the part's own skew the vision removes),
// so a part turned 90 or 180 degrees wrong shows at a glance. The Pick angle can be changed here (Save the job).
const SetupView = {
  only: "",
  async render(root) {
    Checks.head(root, "Setup view", "Each part of one board on its Gerber pads (green), with its feeder's part on top as it will be placed: the picture the bottom camera took (Take picture, or Measure from the part / Check feeders), turned by rotation − Pick angle + camera angle. Check the leads and pin 1 against the pads; change a feeder's Pick angle here and Save the job.");
    const jf = await Checks.job();
    if (!jf) return;
    let d;
    try { d = await API.req("GET", `/api/align/setupview?job=${encodeURIComponent(jf.path)}`); }
    catch (e) { root.append(h("div", { class: "card bad" }, e.message)); return; }
    if (!d.pads_file) root.append(h("div", { class: "card warn" }, "No Gerber pads for this job (Job file → Board → build the pad data): the parts are drawn without their pads."));
    const out = h("div");
    const filter = h("input", { type: "text", value: this.only, placeholder: "only these (designators, footprints or feeder numbers, comma separated)", style: "width:360px",
      oninput: () => { this.only = filter.value; draw(); } });
    const noPic = h("input", { type: "checkbox" });
    noPic.onchange = () => draw();
    const saveBtn = h("button", { class: "btn", onclick: async () => { await jf.save(); saveBtn.classList.remove("primary"); } }, "Save the job");
    const walkBox = h("div");
    const walkBtn = h("button", { class: "btn primary", title: "feeder by feeder: one part picked and shown on the bottom camera with what was measured; you confirm or fix, then the next" }, "Go feeder by feeder");
    walkBtn.onclick = () => walk();
    root.append(h("div", { class: "card" }, h("div", { class: "row", style: "margin-top:0" }, walkBtn, filter, h("label", {}, noPic, " only feeders without a picture"), h("div", { class: "grow" }), saveBtn)), walkBox, out);

    const lineOf = (sid) => jf.lines.findIndex((l) => { const t = l.split(","); return t[0].trim() === "stack" && +t[1] === +sid; });
    const pickAngle = (sid) => { const no = lineOf(sid); return no < 0 ? 0 : +(jf.lines[no].split(",")[6] || 0); };
    const imgs = {};                                         // feeder -> Image of its part (bottom camera)
    const load = (sid) => {
      if (imgs[sid] !== undefined) return imgs[sid];
      imgs[sid] = null;
      const im = new Image();
      im.onload = () => { imgs[sid] = im; draw(); };
      im.src = `/api/align/partpic?job=${encodeURIComponent(jf.path)}&stack=${sid}&t=${Date.now()}`;
      return null;
    };

    const card = (p, f) => {
      const W = 190, cv = h("canvas", { width: W, height: W, style: "background:#111;border-radius:4px;display:block" });
      const g = cv.getContext("2d");
      const turn = (((p.rot - pickAngle(p.stack) + d.cam_angle) % 360) + 540) % 360 - 180;
      const pic = f.pic;
      const ext = Math.max(1.2, ...p.pads.map(([x, y, w, hh]) => Math.max(Math.abs(x) + w / 2, Math.abs(y) + hh / 2)),
        pic ? Math.max(pic.L, pic.W) / 2 + 0.3 : 0, f.size ? Math.max(...f.size) / 2 + 0.3 : 0) + 0.4;
      const s = W / 2 / ext;                               // canvas px per mm
      g.save(); g.translate(W / 2, W / 2);
      if (pic && imgs[p.stack]) {                         // the part, turned as placed (machine CCW = canvas CW)
        const im = imgs[p.stack], half = pic.half_mm;
        const a = turn - (pic.angle || 0);
        g.save(); g.rotate(-a * Math.PI / 180);
        g.globalAlpha = 0.85;
        g.drawImage(im, (-half - (pic.dx || 0)) * s, (-half + (pic.dy || 0)) * s, 2 * half * s, 2 * half * s);
        g.restore();
      } else if (f.size) {                                // no picture: the outline at the part's rotation only
        g.save(); g.rotate(-p.rot * Math.PI / 180);
        g.strokeStyle = "#888"; g.setLineDash([4, 3]); g.lineWidth = 1;
        g.strokeRect(-f.size[0] / 2 * s, -f.size[1] / 2 * s, f.size[0] * s, f.size[1] * s);
        g.restore();
      }
      g.strokeStyle = "#3ce03c"; g.lineWidth = 1.5;        // its Gerber pads (a turned pad as itself, not its box)
      p.pads.forEach(([x, y, w, hh], i) => {
        const sh = (p.shapes || [])[i];
        if (sh && Math.abs(sh[0]) > 0.5) {
          g.save(); g.translate(x * s, -y * s); g.rotate(-sh[0] * Math.PI / 180);
          g.strokeRect(-sh[1] / 2 * s, -sh[2] / 2 * s, sh[1] * s, sh[2] * s);
          g.restore();
        } else g.strokeRect((x - w / 2) * s, (-y - hh / 2) * s, w * s, hh * s);
      });
      g.restore();
      return h("div", { class: "card", style: "padding:6px;margin:0;width:202px" }, cv,
        h("div", { style: "font-size:12px;margin-top:4px" }, h("b", {}, p.key), " ", h("span", { class: "muted" }, p.footprint || "")),
        h("div", { class: "muted", style: "font-size:11px" }, `rot ${fmt(p.rot, 2)}° − pick ${fmt(pickAngle(p.stack), 0)}° + cam ${fmt(d.cam_angle, 2)}° → turn ${fmt(turn, 2)}°`),
        h("div", { class: "row", style: "margin:4px 0 0;gap:4px" }, h("span", { class: "muted", style: "font-size:11px" }, "this part"),
          ...[[-90, "−90°"], [90, "+90°"], [180, "180°"]].map(([a, t]) => h("button", { class: "btn small", title: `turn ${p.key.split("#")[0]} by ${t} (all its board copies; Save the job to keep it)`, onclick: () => turnPart(p, a) }, t))));
    };

    const takePic = async (f, btn) => {
      if (f.bulk) return toast("a bulk feeder: Feeder settings → its Measure from the part (a scanned part is picked)", "warn");
      const n = +((String(f.nozzles || "1").match(/[1-4]/) || ["1"])[0]);
      if (!confirm(`Nozzle ${n} picks a part of feeder ${f.id} at its pick point, the bottom camera photographs it, and it is put back. Go?`)) return;
      if (!(await Checks.need())) return;
      btn.disabled = true; btn.textContent = "picking …";
      try {
        await API.req("POST", `/api/align/measure_part?client=${API.st.client}&x=${f.x}&y=${f.y}&z=${f.pick_z}&nozzle=${n}&job=${encodeURIComponent(jf.path)}&stack=${f.id}`);
        const r = await API.req("GET", `/api/align/setupview?job=${encodeURIComponent(jf.path)}`);
        d.feeders = r.feeders; delete imgs[f.id];
        toast(`feeder ${f.id}: picture taken`, "ok");
      } catch (e) { toast(e.message, "bad"); }
      draw();
    };
    // one part's own rotation (a footprint drawn another way than the others of its feeder): every board
    // copy of the designator turns with it, as the job places them all alike
    const turnPart = (p, a) => {
      const name = p.key.split("#")[0];
      let n = 0;
      jf.lines.forEach((l, no) => {
        const t = l.split(",");
        if (t[0].trim() !== "comp" || (t[3] || "").trim() !== name) return;
        jf.setField(no, 7, ((((+t[8] + a) % 360) + 540) % 360 - 180).toFixed(2));
        n++;
      });
      p.rot = ((((p.rot + a) % 360) + 540) % 360 - 180);
      saveBtn.classList.add("primary");
      toast(`${name}: turned ${a > 0 ? "+" : ""}${a}° (${n} on the panel). Save the job to keep it`, "ok");
      draw();
    };
    const setAngle = (sid, a) => {
      const no = lineOf(sid);
      if (no < 0) return;
      jf.setField(no, 5, (((a % 360) + 540) % 360 - 180).toFixed(2));
      saveBtn.classList.add("primary");
      draw();
    };

    // ---- feeder by feeder: pick one part (Check feeders: the pocket, the part on the bottom camera, its size,
    // along / across its pads, suggestions), show it with the parts it goes on, wait for the operator ----
    let ws = null;                                           // the walk: { sid, i, n, r, err, res }
    const stackOf = (sid) => Checks.stacks(jf).find((x) => x.id === +sid) || {};
    const applySg = (sid, sg, btn) => {
      const no = lineOf(sid), tray = stackOf(sid).tray;
      if (no < 0) return;
      if (sg.kind === "pick_point") { jf.setField(no, 3, (+sg.x).toFixed(2)); jf.setField(no, 4, (+sg.y).toFixed(2)); }
      else if (sg.kind === "size") { jf.setField(no, tray ? 29 : 26, (+sg.L).toFixed(2)); jf.setField(no, tray ? 30 : 27, (+sg.W).toFixed(2)); }
      else if (sg.kind === "pick_angle") jf.setField(no, 5, (+sg.angle).toFixed(2));
      btn.disabled = true; btn.textContent = "applied";
      saveBtn.classList.add("primary");
      draw();
    };
    const renderWalk = () => {
      if (!ws) { walkBox.replaceChildren(); return; }
      const f = d.feeders[ws.sid] || { id: ws.sid }, r = ws.r;
      if (f.pic) load(ws.sid);
      const pa = pickAngle(ws.sid);
      const img = (b64, t) => h("div", {}, h("div", { class: "muted", style: "font-size:12px" }, t), h("img", { src: "data:image/png;base64," + b64, style: "width:100%;max-width:240px;border-radius:4px;display:block" }));
      const act = (a, label, cls) => h("button", { class: "btn " + (cls || ""), disabled: !ws.res, onclick: () => { const done = ws.res; ws.res = null; done(a); } }, label);
      const parts = d.parts.filter((p) => p.stack === ws.sid);
      walkBox.replaceChildren(h("div", { class: "card", style: "border-color:var(--accent)" },
        h("div", { class: "row", style: "margin-top:0" }, h("b", {}, `Feeder ${ws.sid}  (${ws.i + 1} of ${ws.n})`), h("span", {}, f.value || ""),
          h("div", { class: "grow" }), h("span", {}, `Pick angle ${fmt(pa, 0)}°`),
          ...[-90, 90, 180].map((a) => h("button", { class: "btn small", onclick: () => setAngle(ws.sid, pa + a) }, `${a > 0 ? "+" : ""}${a}°`))),
        !r && !ws.err ? h("div", { class: "muted" }, "picking a part, the bottom camera looks at it, it goes back …") : null,
        ws.err ? h("div", { class: "bad" }, ws.err) : null,
        r ? h("div", { class: "split3" },
          r.pocket_png ? img(r.pocket_png, r.pocket ? `pocket ${fmt(r.pocket.w, 2)} x ${fmt(r.pocket.h, 2)} mm, ${fmt(r.pocket.off, 2)} mm from the pick point` : "no pocket found") : h("div", { class: "muted" }, "tray: no pocket"),
          r.part_png ? img(r.part_png, "on the bottom camera (nozzle at 0°)") : h("div", { class: "bad" }, "nothing on the nozzle"),
          h("div", {},
            r.part ? h("div", { style: r.part.size_ok ? "" : "color:var(--bad);font-weight:600" }, `measured ${fmt(r.part.L, 2)} x ${fmt(r.part.W, 2)} mm` + (r.part.expected ? `, expected ${fmt(r.part.expected[0], 2)} x ${fmt(r.part.expected[1], 2)}` : ", no size known")) : null,
            r.orient && r.orient.checked ? h("div", { class: r.orient.across.length ? "warn" : "ok" }, r.orient.across.length ? `${r.orient.across.length} of ${r.orient.checked} would sit across their pads` : `all ${r.orient.checked} along their pads`) : null,
            ...(r.suggest.length ? r.suggest.map((sg) => h("div", { style: "margin:4px 0" }, sg.kind === "info" ? "ℹ " : "⚠ ", sg.text, " ",
              sg.kind !== "info" ? (() => { const b = h("button", { class: "btn small primary" }, "Apply"); b.onclick = () => applySg(ws.sid, sg, b); return b; })() : null))
              : [h("div", { class: "ok" }, "✓ nothing to change")]))) : null,
        parts.length ? h("div", { style: "display:flex;flex-wrap:wrap;gap:8px;margin-top:8px" }, ...parts.map((p) => card(p, f))) : null,
        h("div", { class: "row" }, act("ok", "OK, next ▶", "primary"), act("again", "Again (after a fix)"), act("skip", "Skip"), act("stop", "Stop"),
          h("span", { class: "muted", style: "font-size:12px" }, "OK and Skip save the job; Again picks once more with what you changed"))));
    };
    const walk = async () => {
      if (ws) return;
      const want = new Set(d.parts.map((p) => p.stack));
      const ids = Object.values(d.feeders).filter((f) => want.has(f.id) && !f.bulk && (!noPic.checked || !f.pic)).map((f) => f.id).sort((a, b) => a - b);
      if (!ids.length) return toast("no feeder to go through (bulk feeders: their own page)", "warn");
      if (!(await Checks.need())) return;
      walkBtn.disabled = true;
      for (let i = 0; i < ids.length;) {
        ws = { sid: ids[i], i, n: ids.length, r: null, err: null, res: null };
        renderWalk(); walkBox.scrollIntoView({ block: "start" });
        try { ws.r = await API.req("POST", `/api/align/check_feeder?client=${API.st.client}&job=${encodeURIComponent(jf.path)}&stack=${ws.sid}&pick=1`); }
        catch (e) { ws.err = e.message; }
        try { d.feeders = (await API.req("GET", `/api/align/setupview?job=${encodeURIComponent(jf.path)}`)).feeders; delete imgs[ws.sid]; } catch (e) { /* keep */ }
        const a = await new Promise((res) => { ws.res = res; draw(); });
        if (a === "stop") break;
        if (jf.dirty) { await jf.save(); saveBtn.classList.remove("primary"); }
        if (a !== "again") i++;
      }
      ws = null; walkBtn.disabled = false;
      await API.run("raise", {}).catch(() => {});
      draw();
      toast("feeder by feeder: done", "ok");
    };

    const draw = () => {
      const want = this.only.split(",").map((t) => t.trim().toLowerCase()).filter(Boolean);
      const hit = (p) => !want.length || want.some((w) => p.key.toLowerCase() === w || (p.footprint || "").toLowerCase().includes(w) || String(p.stack) === w);
      const by = {};
      d.parts.filter(hit).forEach((p) => (by[p.stack] = by[p.stack] || []).push(p));
      const ids = Object.keys(by).map(Number).sort((a, b) => a - b).filter((sid) => !noPic.checked || !(d.feeders[sid] || {}).pic);
      out.replaceChildren(...ids.map((sid) => {
        const f = d.feeders[sid] || { id: sid };
        if (f.pic) load(sid);
        const pa = pickAngle(sid);
        const btn = h("button", { class: "btn small" + (f.pic ? "" : " primary"), title: "pick one part, photograph it on the bottom camera, put it back" }, f.pic ? "Take again" : "Take picture");
        btn.onclick = () => takePic(f, btn);
        return h("div", { class: "card" },
          h("div", { class: "row", style: "margin-top:0" }, h("b", {}, `Feeder ${sid}${f.bulk ? ` (bulk ${f.bulk})` : ""}`), h("span", {}, f.value || ""),
            h("span", { class: "muted" }, f.pic ? `picture ${f.pic.time}, ${fmt(f.pic.L, 2)} x ${fmt(f.pic.W, 2)} mm` : "no picture of its part yet"), btn,
            h("div", { class: "grow" }), h("span", {}, `Pick angle ${fmt(pa, 0)}°`),
            ...[-90, 90, 180].map((a) => h("button", { class: "btn small", title: `Pick angle ${a > 0 ? "+" : ""}${a}° (Save the job to keep it)`, onclick: () => setAngle(sid, pa + a) }, `${a > 0 ? "+" : ""}${a}°`))),
          h("div", { style: "display:flex;flex-wrap:wrap;gap:8px" }, ...by[sid].map((p) => card(p, f))));
      }));
      if (!ids.length) out.append(h("div", { class: "muted" }, "no parts"));
      renderWalk();
    };
    draw();
  },
};
