// Two checks of a job with the cameras, each giving suggestions to apply to the job (ticked by the operator):
// Check feeders (after the setup): each feeder's pocket at its pick point (the point off?), one part picked,
//   measured on the bottom camera and put back (a wrong reel? the way it lies -> along or across its pads).
// Check board: the marks read, then each part's pads read by the head camera on the chosen panels (move /
//   turn the part onto its pads), along-or-across from the feeder check, vision tips per part.
const Checks = {
  // ---- the job: its feeders and parts (JobFile's lines, saved with JobFile.save) ----
  async job() {
    if (!JobRun.path) return null;
    if (!JobFile.lines || JobFile.path !== JobRun.path) await JobFile.load(JobRun.path);
    return JobFile;
  },
  stacks(jf) {
    return jf.lines.map((l, no) => [l.split(","), no]).filter(([t]) => t[0].trim() === "stack").map(([t, no]) => ({
      id: +t[1], type: +t[2], value: (t[8] || "").trim(), footprint: (t[7] || "").trim(), no, tray: +t[2] === 1,
      bulk: +t[2] === 1 ? +(t[35] || 0) : 0, skip: /^(yes|1|true)$/i.test(((+t[2] === 1 ? t[23] : t[20]) || "").trim()) }));
  },
  comps(jf) {
    return jf.lines.map((l, no) => [l.split(","), no]).filter(([t]) => t[0].trim() === "comp").map(([t, no]) => ({
      stack: +t[1], ref: (t[3] || "").trim(), value: (t[4] || "").trim(), footprint: (t[5] || "").trim(), x: +t[6], y: +t[7], rot: +t[8], no }));
  },
  head(root, title, text) {
    root.append(h("div", { class: "card" }, h("div", { class: "row", style: "margin-top:0" }, h("b", {}, title), h("span", { class: "muted" }, JobRun.path ? JobRun.path.split(/[\\/]/).pop() : "no job chosen (Work → choose a job)")),
      h("div", { class: "muted", style: "font-size:12px" }, text)));
  },
  need: async () => API.st.haveControl || (await API.take()),

  // ======================================================================================== feeders
  async feeders(root) {
    this.head(root, "Check feeders", "After the setup: the camera goes to each feeder's pick point and finds the pocket; with 'one part on the bottom camera' a part is picked, measured and put back: its size against the expected one (a wrong reel?) and the way it lies on the nozzle (will it sit along or across its pads?). Apply what is right, then Save.");
    const jf = await this.job();
    if (!jf) return;
    const st = this.stacks(jf).filter((s) => !s.skip && !s.bulk && this.comps(jf).some((c) => c.stack === s.id));
    const pick = h("input", { type: "checkbox", checked: true });
    const rows = {};
    let stop = false;
    const table = h("table", { class: "pick" }, h("tr", {}, ["Feeder", "Part", "Pocket", "On the bottom camera", "Suggestions"].map((t) => h("th", {}, t))),
      st.map((s) => (rows[s.id] = { tr: h("tr", {}, h("td", {}, h("b", {}, String(s.id))), h("td", {}, `${s.value} ${s.footprint}`), h("td", { class: "muted" }, "—"), h("td", { class: "muted" }, "—"), h("td", {})) }).tr));
    const apply = async (s, sg, btn) => {
      const no = jf.lines.findIndex((l) => { const t = l.split(","); return t[0].trim() === "stack" && +t[1] === s.id; });
      if (no < 0) return;
      if (sg.kind === "pick_point") { jf.setField(no, 3, (+sg.x).toFixed(2)); jf.setField(no, 4, (+sg.y).toFixed(2)); }
      else if (sg.kind === "size") { jf.setField(no, s.tray ? 29 : 26, (+sg.L).toFixed(2)); jf.setField(no, s.tray ? 30 : 27, (+sg.W).toFixed(2)); }
      else if (sg.kind === "pick_angle") jf.setField(no, 5, (+sg.angle).toFixed(2));
      btn.disabled = true; btn.textContent = "applied";
      saveBtn.classList.add("primary");
    };
    const one = async (s) => {
      const r = rows[s.id], td = r.tr.children;
      td[2].replaceChildren(h("span", { class: "muted" }, "checking …"));
      try {
        const d = await API.req("POST", `/api/align/check_feeder?client=${API.st.client}&job=${encodeURIComponent(jf.path)}&stack=${s.id}&pick=${pick.checked ? 1 : 0}`);
        td[2].replaceChildren(...(d.pocket_png ? [h("img", { src: "data:image/png;base64," + d.pocket_png, style: "width:110px;border-radius:4px;display:block" })] : []),
          d.pocket ? `${fmt(d.pocket.w, 2)} x ${fmt(d.pocket.h, 2)} mm, ${fmt(d.pocket.off, 2)} mm off` : (s.tray ? "tray" : "no pocket"));
        td[3].replaceChildren(...(d.part_png ? [h("img", { src: "data:image/png;base64," + d.part_png, style: "width:110px;border-radius:4px;display:block" })] : []),
          d.part ? h("span", { style: d.part.size_ok ? "" : "color:var(--bad);font-weight:600" }, `${fmt(d.part.L, 2)} x ${fmt(d.part.W, 2)} mm` + (d.part.expected ? ` (expected ${fmt(d.part.expected[0], 2)} x ${fmt(d.part.expected[1], 2)})` : "")) : (pick.checked ? "—" : "not picked"),
          d.orient && d.orient.checked ? h("div", { class: "muted", style: "font-size:12px" }, d.orient.across.length ? `${d.orient.across.length} of ${d.orient.checked} across their pads` : `all ${d.orient.checked} along their pads`) : null);
        td[4].replaceChildren(...(d.suggest.length ? d.suggest.map((sg) => h("div", { style: "margin:2px 0" }, sg.kind === "info" ? "ℹ " : "⚠ ", sg.text, " ",
          sg.kind !== "info" ? (() => { const b = h("button", { class: "btn small", onclick: () => apply(s, sg, b) }, "Apply"); return b; })() : null))
          : [h("span", { style: "color:var(--ok)" }, "✓ fine")]));
      } catch (e) { td[2].replaceChildren(h("span", { style: "color:var(--bad)" }, e.message)); }
    };
    const all = async () => {
      if (!(await this.need())) return;
      stop = false;
      for (const s of st) { if (stop) break; await one(s); }
      await API.run("raise", {}).catch(() => {});
    };
    const saveBtn = h("button", { class: "btn", onclick: async () => { await jf.save(); saveBtn.classList.remove("primary"); } }, "Save the job");
    root.append(h("div", { class: "card" }, h("div", { class: "row", style: "margin-top:0" },
      h("button", { class: "btn primary", onclick: all }, `Check all ${st.length} feeders`), h("button", { class: "btn", onclick: () => (stop = true) }, "Stop"),
      h("label", {}, pick, " one part of each on the bottom camera (size, which way it lies)"), h("div", { class: "grow" }), saveBtn),
      h("div", { class: "muted", style: "font-size:12px" }, "A single feeder: click its row. Bulk feeders: Job file → Feeder settings → the bulk feeder."),
      table));
    st.forEach((s) => { rows[s.id].tr.style.cursor = "pointer"; rows[s.id].tr.children[0].onclick = async () => { if (await this.need()) one(s); }; });
  },

  // ======================================================================================== board
  async board(root) {
    this.head(root, "Check board", "The marks are read, then each part's pads by the head camera on the chosen panels: where the part should go to sit on its pads (a shift, a turn), whether it would lie across its pads (from Check feeders), and vision tips. Tick what to change and Apply; then Save.");
    const jf = await this.job();
    if (!jf) return;
    const panels = jf.lines.filter((l) => l.split(",")[0].trim() === "mirror").length || 1;
    const comps = this.comps(jf);
    let pads = {};
    try { (await API.req("GET", `/api/align/parts?job=${encodeURIComponent(jf.path)}`)).parts.forEach((p) => (pads[p.ref] = p)); } catch (e) { /* none */ }
    if (!Object.keys(pads).length) {
      root.append(h("div", { class: "card bad" }, "No Gerber pads for this job: Job file → Board → build the pad data from the Gerber and the P&P file first."));
      return;
    }
    const panelBoxes = Array.from({ length: panels }, (_, i) => h("input", { type: "checkbox", checked: i === 0 }));
    const filter = h("input", { type: "text", placeholder: "only these (designators or footprints, comma separated)", style: "width:320px" });
    const out = h("div");
    const res = {};                                         // ref -> [{panel, r}]
    let stop = false;
    const tips = (ref) => {
      const p = pads[ref], c = comps.find((q) => q.ref === ref) || {}, t = [];
      if (p && p.pads >= 8 && /qfp|ssop|tssop|msop|qfn|soic|lqfp/i.test(c.footprint || "")) t.push("fine pitch: Lead vision (the leads on the pads) and pad alignment");
      if (/0201|0402/.test(c.footprint || "")) t.push("small: pad alignment on, Second look");
      return t;
    };
    const scan = async () => {
      if (!(await this.need())) return;
      stop = false;
      out.replaceChildren(h("div", { class: "muted" }, "reading the marks …"));
      try { await API.req("POST", `/api/align/marks?client=${API.st.client}&job=${encodeURIComponent(jf.path)}`); }
      catch (e) { out.replaceChildren(); return toast("marks: " + e.message, "bad"); }
      const want = filter.value.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
      const refs = comps.filter((c) => pads[c.ref] && pads[c.ref].pads >= 2 && (!want.length || want.includes(c.ref.toLowerCase()) || want.includes((c.footprint || "").toLowerCase()))).map((c) => c.ref)
        .filter((r, i, a) => a.indexOf(r) === i);
      const pl = panelBoxes.map((b, i) => (b.checked ? i : -1)).filter((i) => i >= 0);
      const prog = h("div", { class: "muted" });
      out.replaceChildren(prog);
      let n = 0;
      for (const p of pl) for (const ref of refs) {
        if (stop) break;
        prog.textContent = `panel ${p + 1}: ${ref} (${++n} of ${pl.length * refs.length}) …`;
        try { const r = await API.req("POST", `/api/align/part?client=${API.st.client}&job=${encodeURIComponent(jf.path)}&ref=${encodeURIComponent(ref)}&panel=${p}`); (res[ref] = res[ref] || []).push({ panel: p, r }); }
        catch (e) { (res[ref] = res[ref] || []).push({ panel: p, err: e.message }); }
      }
      let orient = {};
      try { (await API.req("GET", `/api/align/orient?job=${encodeURIComponent(jf.path)}`)).parts.forEach((q) => (orient[q.ref] = q)); } catch (e) { /* none */ }
      show(refs, orient);
    };
    const show = (refs, orient) => {
      const rows = refs.map((ref) => {
        const L = res[ref] || [], ok = L.filter((q) => q.r && q.r.aligned);
        const m = (k) => ok.length ? ok.reduce((a, q) => a + q.r[k], 0) / ok.length : 0;
        const dx = m("dx"), dy = m("dy"), dt = m("dtheta");
        const c = comps.find((q) => q.ref === ref) || {};
        const sg = [];
        if (!ok.length) sg.push({ text: `pads not read (${(L[0] && (L[0].err || L[0].r.message)) || "?"}): check the Pad alignment lamp / exposure, or its Gerber pads` });
        if (ok.length && Math.hypot(dx, dy) > 0.08) sg.push({ kind: "move", text: `move it ${fmt(dx, 3)}, ${fmt(dy, 3)} mm onto its pads`, dx, dy, ref });
        if (ok.length && Math.abs(dt) > 1.0) sg.push({ kind: "turn", text: `turn it ${fmt(dt, 2)}°`, dt, ref });
        const o = orient[ref];
        if (o && o.checked && o.across) sg.push({ kind: "rot90", text: `it would sit across its pads (on the nozzle it lies along ${o.part_axis}°, its pads ${o.pads_axis}°): rotation +90°, or the feeder's Pick angle`, ref });
        if (o && !o.checked) sg.push({ text: "along / across its pads: not known (Check feeders with 'one part on the bottom camera' first)" });
        tips(ref).forEach((t) => sg.push({ text: "vision: " + t }));
        return { ref, c, L, ok, dx, dy, dt, sg };
      });
      const ticks = [];
      out.replaceChildren(h("div", { class: "row" }, h("b", {}, `${rows.length} parts`),
        h("span", { class: "muted" }, `${rows.filter((r) => r.sg.some((s) => s.kind)).length} with a change to make`), h("div", { class: "grow" }),
        h("button", { class: "btn primary", onclick: () => applyTicked(ticks) }, "Apply the ticked")),
        h("table", { class: "pick" }, h("tr", {}, ["Part", "Footprint", "Panels read", "Shift (mean)", "Turn", "Suggestions"].map((t) => h("th", {}, t))),
          rows.map((r) => h("tr", {}, h("td", {}, h("b", {}, r.ref)), h("td", {}, r.c.footprint || ""), h("td", {}, `${r.ok.length} / ${r.L.length}`),
            h("td", {}, r.ok.length ? `${fmt(r.dx, 3)}, ${fmt(r.dy, 3)}` : "—"), h("td", {}, r.ok.length ? `${fmt(r.dt, 2)}°` : "—"),
            h("td", {}, ...(r.sg.length ? r.sg.map((s) => h("div", {}, s.kind ? (() => { const b = h("input", { type: "checkbox", checked: s.kind !== "rot90" }); ticks.push([b, s, r]); return h("label", {}, b, " " + s.text); })() : h("span", { class: "muted" }, "ℹ " + s.text)))
              : [h("span", { style: "color:var(--ok)" }, "✓ on its pads")]))))));
    };
    const applyTicked = async (ticks) => {
      let n = 0;
      for (const [b, s, r] of ticks) {
        if (!b.checked) continue;
        const c = r.c; if (c.no === undefined) continue;
        const t = jf.lines[c.no].split(",");
        if (s.kind === "move") {                             // the design point that lands on the pads (panel of the first read)
          const q = r.ok[0];
          try {
            const tgt = q.r.placement_without;
            const d = await API.req("GET", `/api/align/design?job=${encodeURIComponent(jf.path)}&x=${tgt[0] + s.dx}&y=${tgt[1] + s.dy}&x0=${t[6]}&y0=${t[7]}&panel=${q.panel}`);
            jf.setField(c.no, 5, (+d.x).toFixed(4)); jf.setField(c.no, 6, (+d.y).toFixed(4)); n++;
          } catch (e) { toast(`${r.ref}: ${e.message}`, "bad"); }
        } else if (s.kind === "turn") { jf.setField(c.no, 7, (+t[8] + s.dt).toFixed(2)); n++; }
        else if (s.kind === "rot90") { jf.setField(c.no, 7, (((+t[8] + 90) % 360 + 360) % 360).toFixed(2)); n++; }
        b.disabled = true;
      }
      toast(`${n} change(s) made: Save the job to keep them`, n ? "ok" : "warn");
    };
    root.append(h("div", { class: "card" },
      h("div", { class: "row", style: "margin-top:0" }, h("span", {}, "Panels"), ...panelBoxes.map((b, i) => h("label", {}, b, ` ${i + 1}`)),
        h("button", { class: "btn small", onclick: () => panelBoxes.forEach((b) => (b.checked = true)) }, "all")),
      h("div", { class: "row" }, h("span", {}, "Parts"), filter, h("span", { class: "muted", style: "font-size:12px" }, "empty = every part with pads")),
      h("div", { class: "row" }, h("button", { class: "btn primary", onclick: scan }, "Read the marks and scan"), h("button", { class: "btn", onclick: () => (stop = true) }, "Stop"),
        h("div", { class: "grow" }), h("button", { class: "btn", onclick: async () => { await jf.save(); } }, "Save the job")),
      out));
  },
};
