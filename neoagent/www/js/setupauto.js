// Set-up done by the machine's own measurements:
//  - Set up feeders: feeder by feeder, the pocket at the pick point (the pick point on it), the pitch from
//    the pocket spacing (feed rate), the pick height by the vacuum, one part measured on the bottom camera
//    (size, a package from the library), optionally a Feed test; a table of what was found, Apply per row.
//  - Camera offset: one kind of part placed for real a few times (Placement test, the after-place photo
//    on): the mean of where the parts landed against their pads is the head camera's offset error.
const SetupAuto = {
  // ======================================================================== Set up feeders
  async feeders(root) {
    Checks.head(root, "Set up feeders", "Feeder by feeder the machine measures what you would set by hand: the pocket at the pick point (the pick point is put on it), the pitch from the pocket spacing (feed rate), the pick height by the vacuum (the nozzle goes down until it touches the part), one part picked and measured on the bottom camera (its size, a package from the library). Nothing is changed until you Apply; then Save the job.");
    const jf = await Checks.job();
    if (!jf) return;
    if (!Cfg.loaded) await Cfg.load().catch(() => {});
    const comps = Checks.comps(jf);
    const st = Checks.stacks(jf).filter((s) => !s.tray && !s.skip && comps.some((c) => c.stack === s.id));
    const steps = { pocket: ["the pocket → the pick point", true], pitch: ["the pitch → the feed rate", true], height: ["the pick height by the vacuum", true],
      part: ["one part on the bottom camera → its size", true], feed: ["Feed test at the end (one step)", false] };
    const boxes = {};
    const tick = {};                                        // feeder -> its checkbox in the list
    const out = h("div"), prog = h("span", { class: "muted" });
    let stop = false;
    const res = {};                                         // feeder -> what was measured
    const lineOf = (sid) => jf.lines.findIndex((l) => { const t = l.split(","); return t[0].trim() === "stack" && +t[1] === sid; });
    const field = (sid, i) => (jf.lines[lineOf(sid)].split(",")[i + 1] || "").trim();
    const pkgFor = (L, W) => {                             // the library's packages of that size (+-12 %)
      if (typeof PkgLib === "undefined" || !PkgLib.all) return [];
      return PkgLib.all().filter((p) => (Math.abs(p.l - L) <= 0.12 * L + 0.05 && Math.abs(p.w - W) <= 0.12 * W + 0.05))
        .sort((a, b) => Math.abs(a.l - L) + Math.abs(a.w - W) - Math.abs(b.l - L) - Math.abs(b.w - W)).slice(0, 3);
    };
    const feed = async (sid) => {                          // as Feed test on the feeder page
      const fid = +Cfg.get(`Stack/lstStack${sid - 1}.nFeedId`, sid) || sid, pid = +Cfg.get(`Stack/lstStack${sid - 1}.nPeelId`, sid) || sid;
      const pitch = +field(sid, 16) || 4;
      const peel = Math.min(64, Math.max(4, Math.max(+Cfg.get(`Peel/lstPeel${pid - 1}.nLoad`, 0) || 0, Math.round(3 * pitch))));
      await API.run("peel", { n: pid, a: Math.round(+field(sid, 18) || 50), b: peel });
      return API.run("feed", { n: fid, p0: Math.round(+field(sid, 17) || 60), p1: Math.round(pitch), mode: 0 });
    };
    const run = async () => {
      const ids = st.filter((s) => tick[s.id].checked).map((s) => s.id);
      if (!ids.length) return toast("tick the feeders to set up", "warn");
      const want = Object.keys(steps).filter((k) => boxes[k].checked);
      if (!confirm(`The machine goes over ${ids.length} feeder(s): ${want.join(", ")}. With "pick height" and "part" a nozzle goes down into each feeder's pocket and one part is picked and put back. Go?`)) return;
      if (jf.dirty) {                                      // the machine works from the saved feeders
        if (!confirm("The job has unsaved changes: save them first? (the machine reads the saved feeders)")) return;
        await jf.save();
      }
      if (!(await Checks.need())) return;
      stop = false;
      for (let i = 0; i < ids.length && !stop; i++) {
        const sid = ids[i];
        prog.textContent = `feeder ${sid} (${i + 1} of ${ids.length}) …`;
        try {
          const srv = want.filter((k) => k !== "feed");
          res[sid] = srv.length ? await API.req("POST", `/api/feedsetup/feeder?client=${API.st.client}&job=${encodeURIComponent(jf.path)}&stack=${sid}&steps=${srv.join(",")}`) : { stack: sid };
          if (want.includes("feed")) { res[sid].fed = !!(await feed(sid)); }
        } catch (e) { res[sid] = { stack: sid, err: e.message }; }
        show();
      }
      prog.textContent = stop ? "stopped" : `done: ${ids.length} feeder(s)`;
      await API.run("raise", {});
    };
    const apply = (sid, what) => {
      const r = res[sid], no = lineOf(sid);
      if (!r || no < 0) return;
      if (what.includes("pocket") && r.pocket) { jf.setField(no, 3, r.pocket.x.toFixed(2)); jf.setField(no, 4, r.pocket.y.toFixed(2)); }
      if (what.includes("pitch") && r.pitch && r.pitch.feed) jf.setField(no, 16, String(r.pitch.feed));
      if (what.includes("height") && r.height && r.height.ok) jf.setField(no, 8, r.height.pick_height.toFixed(2));
      if (what.includes("delay") && r.pick_delay) jf.setField(no, 9, String(r.pick_delay));
      if (what.includes("part") && r.part) {
        const tray = field(sid, 1) === "1", iL = tray ? 29 : 26, iW = tray ? 30 : 27;   // as the feeder page
        jf.setField(no, iL, r.part.L.toFixed(2)); jf.setField(no, iW, r.part.W.toFixed(2));
      }
      (r.applied = r.applied || new Set()); what.forEach((w) => r.applied.add(w));
      saveBtn.classList.add("primary");
      show();
    };
    const cell = (sid, kind, text, ok) => {
      const r = res[sid] || {};
      const done = r.applied && r.applied.has(kind);
      return h("td", {}, text, ok ? " " : null, ok ? h("button", { class: "btn small" + (done ? "" : " primary"), disabled: done, onclick: () => apply(sid, [kind]) }, done ? "applied" : "Apply") : null);
    };
    const show = () => {
      const rows = st.map((s) => {
        const r = res[s.id];
        tick[s.id] = tick[s.id] || h("input", { type: "checkbox", checked: true });
        if (!r) return h("tr", {}, h("td", {}, tick[s.id]), h("td", {}, h("b", {}, s.id)), h("td", {}, s.value), h("td", { colspan: 6, class: "muted" }, "—"));
        if (r.err) return h("tr", {}, h("td", {}, tick[s.id]), h("td", {}, h("b", {}, s.id)), h("td", {}, s.value), h("td", { colspan: 6, class: "bad" }, r.err));
        const was = r.was || {};
        const pk = r.part ? pkgFor(r.part.L, r.part.W) : [];
        return h("tr", {}, h("td", {}, tick[s.id]), h("td", {}, h("b", {}, s.id)), h("td", {}, s.value),
          r.pocket ? cell(s.id, "pocket", `${fmt(r.pocket.x, 2)}, ${fmt(r.pocket.y, 2)} (${fmt(r.pocket.off, 2)} mm off)`, r.pocket.off > 0.03) : h("td", { class: r.pocket_err ? "bad" : "muted" }, r.pocket_err || "no pocket found"),
          r.pitch ? cell(s.id, "pitch", r.pitch.feed ? `${r.pitch.feed} mm (was ${was.feed})` : `${fmt(r.pitch.mm, 2)} mm: not a standard pitch`, r.pitch.feed && r.pitch.feed !== was.feed) : h("td", { class: "muted" }, "—"),
          r.height ? cell(s.id, "height", r.height.ok ? `${fmt(r.height.pick_height, 2)} mm (was ${fmt(was.pick_height, 2)})` : r.height.text, r.height.ok) : h("td", { class: "muted" }, "—"),
          r.pick_delay ? cell(s.id, "delay", `${r.pick_delay} ms (sealed in ${fmt(r.seal_s, 2)} s; was ${field(s.id, 9) || 0})`, true) : h("td", { class: "muted" }, "—"),
          r.part ? cell(s.id, "part", `${fmt(r.part.L, 2)} x ${fmt(r.part.W, 2)} mm` + (pk.length ? ` = ${pk.map((p) => p.name).join(" / ")}` : " (not in the library)"), true)
            : h("td", { class: r.part === null ? "bad" : "muted" }, r.part === null ? "nothing picked" : "—"),
          h("td", {}, r.fed === undefined ? "" : r.fed ? "fed" : "feed failed",
            " ", h("button", { class: "btn small", title: "apply everything found for this feeder", onclick: () => apply(s.id, ["pocket", "pitch", "height", "delay", "part"]) }, "Apply all")));
      });
      out.replaceChildren(h("div", { class: "scroll" }, h("table", { class: "pick" },
        h("tr", {}, ["", "Feeder", "Part", "Pick point", "Feed rate", "Pick height", "Pick delay", "Part size", ""].map((t) => h("th", {}, t))), rows)));
    };
    const saveBtn = h("button", { class: "btn", onclick: async () => { await jf.save(); saveBtn.classList.remove("primary"); } }, "Save the job");
    root.append(h("div", { class: "card" },
      h("div", { class: "row", style: "margin-top:0" }, ...Object.entries(steps).map(([k, [t, on]]) => h("label", {}, (boxes[k] = h("input", { type: "checkbox", checked: on })), " " + t))),
      h("div", { class: "row" }, h("button", { class: "btn primary", onclick: run }, "Start"), h("button", { class: "btn", onclick: () => { stop = true; } }, "Stop after this feeder"),
        h("button", { class: "btn small", onclick: () => { st.forEach((s) => (tick[s.id].checked = true)); } }, "all"),
        h("button", { class: "btn small", onclick: () => { st.forEach((s) => (tick[s.id].checked = false)); } }, "none"), prog, h("div", { class: "grow" }), saveBtn)), out);
    show();
  },

  // ======================================================================== camera pixel maps
  async cammap(root) {
    Checks.head(root, "Camera matrix", "Each camera's pixels against machine millimetres over the whole picture (scale X / Y, skew, the lens), measured with a grid of head moves; with the map on, every picture is straightened so a pixel is exactly 1 / px-per-mm everywhere. Off after a calibration: switch it on, then measure again what was measured in raw pixels (the 4-up points, the up camera's angle, the head camera offset).");
    if (!Cfg.loaded) await Cfg.load().catch(() => {});
    const out = h("div");
    const draw = async () => {
      let st = {};
      try { st = (await API.req("GET", "/api/cammap/status")).maps || {}; } catch (e) { out.replaceChildren(h("div", { class: "card bad" }, e.message)); return; }
      const card = (cam, title, how, params) => {
        const m = st[String(cam)] || {};
        const prog = h("span", { class: "muted" });
        const run = async () => {
          if (!confirm(`${title}: ${how}

The head moves over a grid of points (about ${cam ? "1" : "2"} minute). Go?`)) return;
          if (!(await Checks.need())) return;
          prog.textContent = " measuring …";
          try {
            const r = await API.req("POST", `/api/cammap/calibrate?client=${API.st.client}&cam=${cam}${params}`);
            toast(`${title}: ${r.n} points, rms ${fmt(r.rms_px, 2)} px (one matrix: ${fmt(r.rms_linear_px, 2)} px)`, "ok");
          } catch (e) { toast(e.message, "bad"); }
          prog.textContent = ""; draw();
        };
        const toggle = async (on) => {
          if (on && !confirm("With the map on, measure again what was measured in raw pixels: the 4-up points and the up camera's angle (Calibration), and the head camera offset (Camera offset). Switch it on?")) return;
          if (!(await Checks.need())) return;
          try { await API.req("POST", `/api/cammap/on?client=${API.st.client}&cam=${cam}&on=${on ? 1 : 0}`); } catch (e) { toast(e.message, "bad"); }
          draw();
        };
        const k = Cfg.pxmm(cam);
        return h("div", { class: "card" }, h("h3", { style: "margin-top:0" }, title, m.on ? h("span", { class: "ok" }, "  · map on") : m.n ? h("span", { class: "muted" }, "  · map off") : null),
          h("div", { class: "muted", style: "font-size:12px" }, how),
          m.n ? h("div", { class: "kv", style: "margin:8px 0" },
            h("div", {}, "measured"), h("div", {}, `${m.time}: ${m.n} points over ${m.range_mm[0]} … ${m.range_mm[1]} mm (${m.missed || 0} not found)`),
            h("div", {}, "scale (px/mm X, Y)"), h("div", {}, `centre ${m.scale_centre.join(", ")} · corner ${m.scale_corner.join(", ")} · set ${fmt(k, 2)}`),
            h("div", {}, "skew, angle"), h("div", {}, `${fmt(m.skew_deg, 3)}°, ${fmt(m.angle_deg, 3)}° (kept)`),
            h("div", {}, "left over"), h("div", {}, `the map: rms ${fmt(m.rms_px, 2)} px, max ${fmt(m.max_px, 2)} px (${fmt(m.max_px / k, 3)} mm) · one matrix would leave max ${fmt(m.max_linear_px, 2)} px (${fmt(m.max_linear_px / k, 3)} mm)`))
            : h("div", { class: "muted", style: "margin:8px 0" }, "not measured yet"),
          h("div", { class: "row" }, h("button", { class: "btn primary", onclick: run }, m.n ? "Measure again" : "Measure"), prog,
            m.n ? h("button", { class: "btn" + (m.on ? "" : " primary"), onclick: () => toggle(!m.on) }, m.on ? "Switch the map off" : "Switch the map on") : null));
      };
      out.replaceChildren(
        card(1, "Head camera", "First bring the head camera near a round mark on the board (a fiducial; Manual → Centre on the circle): the mark is followed over a 7 x 7 grid of ±3.2 mm.", "&n=7&span=3.2"),
        card(0, "Up camera", "Nozzle 1 empty (no part), raised (its tip plane, Z 0): its tip is followed over a 9 x 9 grid of ±12 mm above the camera, with the up light on.", "&n=9&span=12&nozzle=1"));
    };
    root.append(out);
    draw();
  },

  // ======================================================================== camera offset
  async camoffset(root) {
    Checks.head(root, "Camera offset", "The head camera's offset to the nozzle checked by real placements: parts of one kind are placed (Placement test, the after-place photo on), and where they landed against their pads is measured by the head camera. The marks and the photo both go through that offset, so the mean of the misses is its error: proposed = now + mean. Use a test board; the parts are placed for real. Apply, then run it again: the misses should come out near zero.");
    const jf = await Checks.job();
    if (!jf) return;
    if (!Cfg.loaded) await Cfg.load().catch(() => {});
    const comps = Checks.comps(jf);
    const seen = {};
    comps.forEach((c) => { seen[c.ref] = (seen[c.ref] || 0) + 1; c.key = seen[c.ref] > 1 ? `${c.ref}#${seen[c.ref]}` : c.ref; });
    const kinds = {};
    comps.filter((c) => c.stack > 0).forEach((c) => (kinds[c.value + " / " + c.footprint] = kinds[c.value + " / " + c.footprint] || []).push(c));
    const names = Object.keys(kinds).sort((a, b) => kinds[b].length - kinds[a].length);
    const kindSel = h("select", {}, names.map((k) => h("option", { value: k }, `${k} (${kinds[k].length})`)));
    const nIn = h("input", { type: "number", min: 1, max: 20, value: 4, style: "width:60px" });
    const out = h("div"), prog = h("span", { class: "muted" });
    const now = Cfg.pt("Position/dPosCamLookDownMouse1");
    const got = [];
    let stop = false;
    const show = () => {
      const ok = got.filter((g) => g.dx !== undefined);
      const m = (k) => ok.reduce((a, g) => a + g[k], 0) / Math.max(1, ok.length);
      const sd = (k) => Math.sqrt(ok.reduce((a, g) => a + (g[k] - m(k)) ** 2, 0) / Math.max(1, ok.length - 1));
      const mx = m("dx"), my = m("dy"), prop = [now[0] + mx, now[1] + my];
      out.replaceChildren(h("table", { class: "pick" }, h("tr", {}, ["Part", "Landed off its pads (X, Y mm)", "Turn"].map((t) => h("th", {}, t))),
        got.map((g) => h("tr", {}, h("td", {}, g.key), h("td", { class: g.err ? "bad" : "" }, g.err || `${fmt(g.dx, 3)}, ${fmt(g.dy, 3)}`), h("td", {}, g.da === undefined ? "" : `${fmt(g.da, 2)}°`)))),
        ok.length ? h("div", { class: "card", style: "margin-top:8px" },
          h("div", {}, `mean miss ${fmt(mx, 3)}, ${fmt(my, 3)} mm` + (ok.length > 1 ? `, spread ±${fmt(sd("dx"), 3)}, ±${fmt(sd("dy"), 3)}` : "") + ` over ${ok.length} part(s)`),
          h("div", {}, `camera offset now ${fmt(now[0], 3)}, ${fmt(now[1], 3)} → proposed ${fmt(prop[0], 3)}, ${fmt(prop[1], 3)}`),
          h("div", { class: "row" }, h("button", { class: "btn primary", onclick: async () => {
            if (!confirm(`Position/dPosCamLookDownMouse1 = ${fmt(prop[0], 3)}, ${fmt(prop[1], 3)} (was ${fmt(now[0], 3)}, ${fmt(now[1], 3)}). Then read the marks again (or run this again) before placing.`)) return;
            Cfg.setPt("Position/dPosCamLookDownMouse1", prop);
            try { await Cfg.save(); toast("camera offset saved in Config.ini", "ok"); } catch (e) { toast(e.message, "bad"); }
          } }, "Apply the proposed offset"), h("span", { class: "muted", style: "font-size:12px" }, "the sign is checked by the second run: the misses must shrink"))) : null);
    };
    const runOne = async (path, c) => {
      await API.req("POST", `/api/job/start?path=${encodeURIComponent(path)}&resume=0&only=${encodeURIComponent(c.key)}&client=${API.st.client}`);
      let since = 0, lines = [], s;
      do {
        await new Promise((r) => setTimeout(r, 700));
        s = await API.req("GET", `/api/job/status?since=${since}`);
        lines = lines.concat(s.log || []); since = s.log_n || since;
        if (s.question) { await API.req("POST", `/api/job/answer?a=no`).catch(() => {}); return { key: c.key, err: "stopped: " + s.question.text }; }
      } while (s.running);
      const re = new RegExp(`^${c.ref.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}: placed ([-+]?\\d+\\.\\d+), ([-+]?\\d+\\.\\d+) mm off, turned ([-+]?\\d+\\.\\d+)`);
      const hit = lines.map((l) => String(l).replace(/^\S+\s+job:\s*/, "")).map((l) => l.match(re)).filter(Boolean).pop();
      return hit ? { key: c.key, dx: +hit[1], dy: +hit[2], da: +hit[3] } : { key: c.key, err: "no after-place photo result (Settings → Job: after-place checks on?)" };
    };
    const run = async () => {
      const list = (kinds[kindSel.value] || []).slice(0, Math.max(1, +nIn.value || 1));
      if (!list.length) return;
      if (Cfg.get("Job/bPlaceCheck", true) === false || String(Cfg.get("Job/bPlaceCheck", "true")) === "false") return toast("Settings → Job: the after-place checks are off", "warn");
      if (!confirm(`${list.length} part(s) ${kindSel.value} are placed for real on the board (${list.map((c) => c.key).join(", ")}), each with the after-place photo. Use a test board. Go?`)) return;
      if (!(await Checks.need())) return;
      let copy;
      try { copy = (await API.req("POST", `/api/feedsetup/camoffset_job?client=${API.st.client}&job=${encodeURIComponent(jf.path)}`)).path; }
      catch (e) { return toast(e.message, "bad"); }
      got.length = 0; stop = false;
      for (let i = 0; i < list.length && !stop; i++) {
        prog.textContent = `${list[i].key} (${i + 1} of ${list.length}) …`;
        try { got.push(await runOne(copy, list[i])); } catch (e) { got.push({ key: list[i].key, err: e.message }); }
        show();
      }
      prog.textContent = stop ? "stopped" : "done";
    };
    root.append(h("div", { class: "card" },
      h("div", { class: "row", style: "margin-top:0" }, h("label", {}, "Part "), kindSel, h("label", {}, "how many "), nIn,
        h("button", { class: "btn primary", onclick: run }, "Place and measure"), h("button", { class: "btn", onclick: () => { stop = true; } }, "Stop after this part"), prog),
      h("div", { class: "muted", style: "font-size:12px" }, `camera offset now (Position/dPosCamLookDownMouse1): ${fmt(now[0], 3)}, ${fmt(now[1], 3)} mm`)), out);
  },
};
