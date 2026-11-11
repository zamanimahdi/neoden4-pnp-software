// Router, header (sections, connection, XY state, control, system menu, STOP), System status, Cameras, Files, Updates.
// Three main sections: Work (run a job), New job (wizard and job file), Machine (manual, cameras,
// calibration, Config.ini). Status / files / updates sit in the System menu. The hash of every page is
// unchanged (#job, #manual, ...), so the page modules keep their own hash checks.
const App = {
  pages: {
    dashboard: (r) => Dashboard.render(r), manual: (r) => Manual.render(r), cameras: (r) => Cameras.render(r),
    setups: (r) => Setups.render(r), settings: (r) => Settings.render(r), files: (r) => Files.render(r), updates: (r) => Updates.render(r),
    wizard: (r) => Wizard.render(r), job: (r) => JobRun.render(r), jobfile: (r) => JobFile.render(r), padalign: (r) => PadAlign.render(r), visionlog: (r) => VisionLog.render(r), network: (r) => Network.render(r), bulk: (r) => Bulk.render(r), stats: (r) => Stats.render(r), feedcheck: (r) => Checks.feeders(r), boardcheck: (r) => Checks.board(r), setupview: (r) => SetupView.render(r), feedsetup: (r) => SetupAuto.feeders(r), camoffset: (r) => SetupAuto.camoffset(r), cammap: (r) => SetupAuto.cammap(r),
  },
  sections: [
    { id: "work", label: "Work", icon: "▶", pages: ["job", "stats"] },
    { id: "newjob", label: "New job", icon: "✚", pages: ["wizard", "jobfile", "feedsetup", "feedcheck", "boardcheck", "setupview"] },
    { id: "machine", label: "Machine settings", icon: "⚙", pages: ["manual", "cameras", "setups", "cammap", "camoffset", "bulk", "padalign", "visionlog", "network", "settings"] },
    { id: "system", label: "System", pages: ["dashboard", "files", "updates"], menu: true },
  ],
  meta: {
    job: ["Work", "Run a job on the fixed table: start, pause, single step, stop and continue where it stopped."],
    wizard: ["New job", "Make a job from the PCB files: parts, board, alignment, feeders, order, speed, output."],
    jobfile: ["Job file", "Edit a job: PCB info and marks, stacks, components and panels (the original Edit screen)."],
    manual: ["Manual control", "Move the head, nozzles, valves, lights and feeders by hand."],
    cameras: ["Cameras", "Live pictures of the up-looking and the head camera; exposure, gain and lamps."],
    setups: ["Calibration", "Teach the nozzle, camera, trash box and feeder positions."],
    padalign: ["Pad alignment", "Try pad alignment and lead vision live on a test board, step by step, and save the values that work."],
    feedcheck: ["Check feeders", "After the setup: each feeder's pick point and one part with the cameras: the pocket, the size (a wrong reel?), along or across its pads."],
    feedsetup: ["Set up feeders", "Feeder by feeder the machine measures the pick point, feed rate, pick height and part size; apply what is right."],
    cammap: ["Camera matrix", "Each camera's pixels against millimetres over the whole picture: scale, skew and the lens, measured and straightened."],
    camoffset: ["Camera offset", "The head camera's offset to the nozzle checked by real placements and corrected."],
    setupview: ["Setup view", "Every part of one board on its Gerber pads, turned as it will be placed: check all the angles at a glance."],
    boardcheck: ["Check board", "Each part's pads read on the board: shifts, turns, parts that would lie across their pads, vision tips; apply what is right."],
    stats: ["Statistics", "What the jobs did per nozzle, feeder and part: picks, failures, rejects, placed, after-place checks; warnings for a nozzle or feeder that fails often."],
    visionlog: ["Vision log", "Part, mark and pad images saved during jobs with what vision found: per footprint found / rejected and size ratios, to calibrate the vision."],
    network: ["Network", "Wi-Fi of the machine: scan, connect to another network, forget a saved one."],
    bulk: ["Bulk feeders", "Loose parts in blocks on the table (up to 30): the layout (a matrix set with the camera), each block's package, pick height and scan. Which part, direction and misses: Job file → Feeder settings."],
    settings: ["Settings", "Every machine setting in Config.ini (the same file NeoDen4.exe uses)."],
    dashboard: ["System status", "Machine PC, programs and the server log."],
    files: ["Files", "Browse, edit, upload and back up files on the machine PC."],
    updates: ["Updates", "Install a signed update package; every replaced file is backed up."],
  },
  sub: { manual: "Manual control", cameras: "Cameras", setups: "Calibration", padalign: "Pad alignment", visionlog: "Vision log", network: "Network", bulk: "Bulk feeders", stats: "Statistics", feedcheck: "Check feeders", boardcheck: "Check board", setupview: "Setup view", feedsetup: "Set up feeders", camoffset: "Camera offset", cammap: "Camera matrix", settings: "Settings (Config.ini)",
    dashboard: "System status", files: "Files", updates: "Updates", job: "Work", wizard: "Setup wizard", jobfile: "Job file settings" },
  sectionOf(name) { return this.sections.find((s) => s.pages.includes(name)) || this.sections[0]; },
  nav(name) {
    const sec = this.sectionOf(name);
    const nav = document.getElementById("nav");
    nav.replaceChildren(...this.sections.filter((s) => !s.menu).map((s) => h("a", { href: "#" + (s.last || s.pages[0]), class: s === sec ? "on" : "" },
      h("span", { class: "ic" }, s.icon), s.label)));
    const sub = document.getElementById("subnav");
    sub.replaceChildren(...(sec.pages.length > 1 ? sec.pages.map((p) => h("a", { href: "#" + p, class: p === name ? "on" : "" }, this.sub[p])) : []));
    if (JobRun.mounted && name !== "job") sub.append(h("div", { class: "grow" }),
      h("a", { href: "#job", class: "backmount" }, `◀ Back to mount: ${JobRun.path.split(/[\\/]/).pop().replace(/\.csv$/i, "")}`));
    const menu = document.getElementById("sysmenu"), sys = this.sections.find((s) => s.menu);
    menu.replaceChildren(...sys.pages.map((p) => h("a", { href: "#" + p, class: p === name ? "on" : "" }, this.sub[p])));
    document.getElementById("sysbtn").classList.toggle("on", sec === sys);
  },
  // every page can rely on Config.ini (camera offset, px/mm, ...): the Config path comes from the server
  // first, then the file is loaded once, before the first page is drawn (also on a reload or a bookmark)
  async show() {
    await App.ready;
    if (!Cfg.loaded) await Cfg.load().catch(() => {});
    let name = (location.hash || "#job").slice(1);
    if (!this.pages[name]) name = "job";
    this.sectionOf(name).last = name;
    this.nav(name);
    // each redraw into its own box: a page drawn with waits (server calls) that a newer redraw overtook
    // then fills a box no longer on the page, not the page a second time
    const root = h("div", { class: "pagebox" });
    document.getElementById("page").replaceChildren(root);
    const [title, desc] = this.meta[name];
    if (!(name === "setups" && Setups.current)) root.append(h("div", { class: "pagehead" }, h("h2", {}, title), h("span", { class: "muted" }, desc)));
    // while the page waits for the server it says so; a page that fails shows why (not a blank screen)
    const wait = h("div", { class: "pageload", "aria-busy": "true" }, "Loading …");
    root.append(wait);
    Promise.resolve().then(() => this.pages[name](root))
      .catch((e) => { console.error(e); root.append(h("div", { class: "card bad", role: "alert" }, `This page could not be drawn: ${e && e.message || e}. Reload the page; if it stays, tell the service.`)); })
      .finally(() => wait.remove());
  },
  // the last errors, in the top bar (the ⚠ button): this screen's and everyone else's
  events: [],
  noteEvent(text, kind, remote) {
    if (kind !== "bad") return;
    this.events.unshift({ t: new Date().toTimeString().slice(0, 8), text: String(text), remote: !!remote });
    this.events = this.events.slice(0, 30);
    const b = document.getElementById("evbtn");
    if (b) { b.hidden = false; b.textContent = `⚠ ${this.events.length}`; }
  },
  showEvents() {
    const box = document.getElementById("evlist");
    if (!box) return;
    if (!box.hidden) { box.hidden = true; return; }
    box.replaceChildren(h("div", { class: "row", style: "margin:0 0 4px" }, h("b", {}, "Last errors (all screens)"), h("div", { class: "grow" }),
      h("button", { class: "btn small", onclick: () => { this.events = []; box.hidden = true; const b = document.getElementById("evbtn"); if (b) b.hidden = true; } }, "Clear")),
      ...this.events.map((e) => h("div", { class: "evitem" }, h("span", { class: "muted" }, e.t + " "), e.text)));
    box.hidden = false;
  },
  async poll() {
    try {
      API.st.status = await API.req("GET", `/api/mc/status?ev=${App.evSeq === undefined ? -1 : App.evSeq}`).catch((e) => ({ ok: false, error: e.message }));
      const ss = API.st.status;
      if (ss && ss.ev_next !== undefined) {               // the errors of the other screens and of the machine
        if (App.evSeq !== undefined) (ss.events || []).filter((e) => e.from !== API.st.client).forEach((e) =>
          toast(`${e.from && !/^(machine|job|calib|align|server)$/.test(e.from) ? "(another screen) " : ""}${e.text}`, e.level || "bad", true));
        App.evSeq = ss.ev_next;
      }
      API.st.statusAt = Date.now();
    } catch (e) { API.st.status = { ok: false, error: e.message }; }
    const s = API.st.status;
    const conn = document.getElementById("conn"), xy = document.getElementById("xyst");
    conn.textContent = s.ok ? "machine connected" : (s.error || "no machine");
    conn.className = "pill " + (s.ok ? "ok" : "bad");
    xy.textContent = "XY: " + (s.xy_state_name || "–") + (s.pos ? `  ${fmt(s.pos[0])}, ${fmt(s.pos[1])}` : "");
    xy.className = "pill " + (s.xy_state === 0 ? "ok" : s.xy_state >= 3 ? "bad" : "warn");
    // Home button: highlighted while XY is not homed; "Homing…" while the server homes
    const hb = document.getElementById("homebtn");
    const homing = s.homing || (App.homeReq && s.xy_state !== 0 && Date.now() - App.homeReq < 90000);
    if (s.xy_state === 0 && App.homeReq && !s.homing) { App.homeReq = 0; toast("XY homed", "ok"); }
    hb.textContent = homing ? "Homing…" : "⌂ Home";
    hb.disabled = !!homing || !s.ok;
    hb.classList.toggle("primary", !homing && s.ok && s.xy_state !== 0);
    if (s.owner && s.owner !== API.st.client && API.st.haveControl) { API.st.haveControl = false; App.hdr(); }
    if (Manual._showStatus && location.hash === "#manual") { Manual._showStatus(); Points.show(); }
    (Manual.coordCams || []).forEach((c) => { if (c.el.isConnected && !c.live) c._draw(); });
    (Manual.maps || []).forEach((m) => m.draw());
  },
  hdr() {
    const b = document.getElementById("ctl");
    b.textContent = API.st.haveControl ? "In control ✓ (release)" : "Take control";
    b.classList.toggle("primary", API.st.haveControl);
  },
};

const Dashboard = {
  render(root) {
    const progs = h("div"), info = h("div", { class: "kv" }), logEl = h("div", { class: "log" });
    root.append(h("div", { class: "grid" },
      h("div", { class: "card" }, h("h3", {}, "Machine PC"), info),
      h("div", { class: "card" }, h("h3", {}, "Programs"), progs),
      h("div", { class: "card", style: "grid-column: 1 / -1" }, h("h3", {}, "Log"), logEl)));
    const load = async () => {
      const s = await API.req("GET", "/api/status").catch((e) => ({ error: e.message }));
      info.replaceChildren(h("div", {}, "Agent"), h("div", {}, s.agent ? `neoagent ${s.agent}` : s.error),
        h("div", {}, "Host"), h("div", {}, s.host || "–"),
        h("div", {}, "Uptime"), h("div", {}, s.uptime_s !== undefined ? `${Math.floor(s.uptime_s / 3600)} h ${Math.floor(s.uptime_s / 60) % 60} min` : "–"),
        h("div", {}, "Camera"), h("div", {}, String(s.camera || "–")),
        h("div", {}, "Serial bridge"), h("div", {}, (API.st.status && (API.st.status.bridge || API.st.status.error)) || "–"));
      progs.replaceChildren(h("table", {}, (s.procs || []).map((p) => h("tr", {},
        h("td", {}, h("b", {}, p.name)), h("td", { class: p.running ? "" : "muted" }, p.running ? `running ${Math.floor(p.uptime_s / 60)} min` : "stopped"),
        h("td", { class: "muted" }, p.restarts ? `${p.restarts} restart(s)` : ""),
        h("td", {}, ["start", "stop", "restart"].map((a) => h("button", { class: "btn", onclick: async () => {
          try { await API.req("POST", `/api/proc?name=${p.name}&action=${a}`); toast(`${p.name}: ${a}`, "ok"); load(); } catch (e) { toast(e.message, "bad"); }
        } }, a)))))));
    };
    load();
    let since = 0;
    const tail = async () => {
      if (!logEl.isConnected) return;
      const r = await API.req("GET", `/api/log?since=${since}`).catch(() => null);
      if (r) { since = r.next; if (r.lines.length) { logEl.textContent += r.lines.join("\n") + "\n"; logEl.scrollTop = logEl.scrollHeight; } }
      setTimeout(tail, 1500);
    };
    tail();
  },
};

// Cameras: live picture per camera; exposure / gain with − + buttons (applied at once), window size,
// lamp and ring light as buttons. Values are kept per camera for the session.
const Cameras = {
  state: {},
  render(root) {
    const mk = (cam) => {
      const v = new CamView(cam);
      const st = this.state[cam] = this.state[cam] || { exp: cam === 0 ? Math.trunc(50 * (Cfg.get("Default/fBrightness", 3) || 3)) : 25, gain: 8, win: 1024, led: 0, ring: false };
      let timer = null;
      const send = () => {                               // debounced: several clicks give one camera update
        clearTimeout(timer);
        timer = setTimeout(async () => {
          try { await API.req("POST", `/api/cam/set?cam=${cam}&w=${st.win}&h=${st.win}&exposure=${st.exp}&gain=${st.gain}&save=1`); v.grab(); }
          catch (e) { toast(e.message, "bad"); }
        }, 250);
      };
      const stepper = (label, key, min, max, steps) => {
        const val = h("input", { type: "number", value: st[key], min, max, class: "stepval",
          onchange: (e) => { st[key] = Math.min(max, Math.max(min, Math.round(+e.target.value || min))); val.value = st[key]; send(); } });
        const bump = (f) => () => { st[key] = Math.min(max, Math.max(min, Math.round(f(st[key])))); val.value = st[key]; send(); };
        return h("div", { class: "stepper" }, h("span", { class: "steplbl" }, label),
          ...steps.map(([t, f]) => (t.startsWith("−") ? h("button", { class: "btn", onclick: bump(f) }, t) : null)), val,
          ...steps.map(([t, f]) => (t.startsWith("+") ? h("button", { class: "btn", onclick: bump(f) }, t) : null)));
      };
      const seg = (opts, cur, onpick) => {
        const box = h("div", { class: "seg" });
        const draw = (c) => box.replaceChildren(...opts.map(([val, t]) => h("button", { class: "btn" + (val === c ? " primary" : ""), onclick: () => { draw(val); onpick(val); } }, t)));
        draw(cur);
        return box;
      };
      const expSteps = cam === 0
        ? [["−50", (x) => x - 50], ["−10", (x) => x - 10], ["+10", (x) => x + 10], ["+50", (x) => x + 50]]
        : [["−5", (x) => x - 5], ["−1", (x) => x - 1], ["+1", (x) => x + 1], ["+5", (x) => x + 5]];
      const light = cam === 0
        ? seg([[false, "Ring light off"], [true, "Ring light on"]], st.ring, (on) => { st.ring = on; API.run("light", { on: on ? 1 : 0 }).then(() => v.grab()); })
        : seg([[0, "Lamp off"], [1, "Inner"], [2, "Outer"], [3, "Both"]], st.led, (n) => { st.led = n; API.run("led", { v: n }).then(() => v.grab()); });
      const save = h("button", { class: "btn", onclick: () => { if (v.frame) { const c = document.createElement("canvas"); c.width = v.frame.bmp.width; c.height = v.frame.bmp.height;
        c.getContext("2d").drawImage(v.frame.bmp, 0, 0); const a = h("a", { href: c.toDataURL("image/png"), download: `${cam ? "head" : "up"}_${Date.now()}.png` }); a.click(); } } }, "Save PNG");
      return h("div", { class: "card" }, h("h3", {}, cam === 0 ? "Up-looking camera (parts on the nozzles)" : "Head camera (board, fiducials)"),
        v.el, v.controls(),
        h("div", { class: "camctl" },
          stepper("Exposure", "exp", 1, 65535, expSteps),
          stepper("Gain", "gain", 0, 255, [["−4", (x) => x - 4], ["−1", (x) => x - 1], ["+1", (x) => x + 1], ["+4", (x) => x + 4]]),
          h("div", { class: "stepper" }, h("span", { class: "steplbl" }, "Window"),
            seg((cam === 0 ? [1024] : [1024, 800, 32]).map((n) => [n, `${n}×${n}`]), st.win, (n) => { st.win = n; send(); })),
          h("div", { class: "stepper" }, h("span", { class: "steplbl" }, cam === 0 ? "Light" : "Lamp"), light)),
        h("div", { class: "row" }, save));
    };
    root.append(h("div", { class: "grid", style: "grid-template-columns: repeat(auto-fill, minmax(380px, 1fr))" }, mk(0), mk(1)));
  },
};

const Files = {
  path: localStorage.getItem("filesPath") || "D:\\NeoDen4",
  textExt: /\.(ini|csv|txt|log|md|json|xml|bat|cfg|conf|html|js|css)$/i,
  async render(root) {
    const pathIn = h("input", { value: this.path, style: "width:420px" });
    const list = h("div", { class: "scroll" }), editor = h("div");
    const go = async (p) => {
      this.path = p; pathIn.value = p; localStorage.setItem("filesPath", p);
      try {
        const r = await API.req("GET", `/api/files?path=${encodeURIComponent(p)}`);
        const up = PathU.parent(p);
        const ents = r.entries.sort((a, b) => (b.dir - a.dir) || a.name.localeCompare(b.name));
        list.replaceChildren(h("table", {},
          h("tr", {}, ["Name", "Size", "Modified", ""].map((t) => h("th", {}, t))),
          p ? h("tr", {}, h("td", {}, h("a", { href: "javascript:void 0", onclick: () => go(up) }, "⬑ ..")), h("td"), h("td"), h("td")) : null,
          ents.map((e) => {
            const full = p ? PathU.join(p, e.name) : (/^[A-Z]:$/.test(e.name) ? e.name + "\\" : e.name);
            return h("tr", {},
              h("td", {}, e.dir ? h("a", { href: "javascript:void 0", onclick: () => go(full) }, "📁 " + e.name)
                : h("a", { href: "javascript:void 0", onclick: () => this.open(full, editor) }, e.name)),
              h("td", { class: "muted" }, e.dir ? "" : e.size.toLocaleString()), h("td", { class: "muted" }, e.mtime),
              h("td", {}, p ? [
                e.dir ? null : h("a", { class: "btn", href: `/api/file?path=${encodeURIComponent(full)}`, download: e.name }, "Download"),
                h("button", { class: "btn", onclick: async () => { const n = prompt("Rename to", e.name); if (!n) return;
                  try { await API.req("POST", `/api/rename?from=${encodeURIComponent(full)}&to=${encodeURIComponent(PathU.join(p, n))}`); go(p); } catch (x) { toast(x.message, "bad"); } } }, "Rename"),
                h("button", { class: "btn", onclick: async () => { if (!confirm(`Delete ${full}? (files are backed up first)`)) return;
                  try { await API.req("POST", `/api/delete?path=${encodeURIComponent(full)}`); go(p); } catch (x) { toast(x.message, "bad"); } } }, "Delete")] : null));
          })));
      } catch (e) { list.replaceChildren(h("div", { class: "muted" }, e.message)); }
    };
    const upl = h("input", { type: "file", multiple: true, onchange: async (e) => {
      for (const f of e.target.files) {
        try { await API.req("PUT", `/api/file?path=${encodeURIComponent(PathU.join(this.path, f.name))}`, await f.arrayBuffer()); toast(`uploaded ${f.name}`, "ok"); }
        catch (x) { toast(`${f.name}: ${x.message}`, "bad"); }
      }
      go(this.path);
    } });
    root.append(h("div", { class: "card" },
      h("div", { class: "row" }, h("button", { class: "btn", onclick: () => go("") }, "Drives"), pathIn, h("button", { class: "btn", onclick: () => go(pathIn.value) }, "Open"),
        h("button", { class: "btn", onclick: async () => { const n = prompt("New folder name"); if (n) { await API.req("POST", `/api/mkdir?path=${encodeURIComponent(PathU.join(this.path, n))}`).catch((x) => toast(x.message, "bad")); go(this.path); } } }, "New folder"),
        h("label", { class: "btn" }, "Upload…", h("span", { style: "display:none" }, upl)), h("span", { class: "muted" }, "FTP: same login, port 21")),
      list), editor, this.backups());
    upl.parentNode.parentNode.addEventListener("click", () => upl.click());
    go(this.path);
  },
  async open(path, box) {
    if (!this.textExt.test(path)) { location.href = `/api/file?path=${encodeURIComponent(path)}`; return; }
    const r = await API.req("GET", `/api/file?path=${encodeURIComponent(path)}`, null, true).catch((e) => toast(e.message, "bad"));
    if (!r) return;
    const text = INI.fromBytes(await r.arrayBuffer());      // bytes kept 1:1 (latin-1), saved back unchanged
    const ta = h("textarea", { rows: 26 }, text);
    box.replaceChildren(h("div", { class: "card" }, h("h3", {}, path), ta,
      h("div", { class: "row" }, h("button", { class: "btn primary", onclick: async () => {
        try { const s = await API.req("PUT", `/api/file?path=${encodeURIComponent(path)}`, INI.toBytes(ta.value)); toast(`saved${s.backup ? " (backup " + s.backup + ")" : ""}`, "ok"); }
        catch (e) { toast(e.message, "bad"); }
      } }, "Save"), h("button", { class: "btn", onclick: () => box.replaceChildren() }, "Close"),
        h("span", { class: "muted" }, "Text is shown byte for byte (Chinese GBK text shows as symbols but is saved unchanged)."))));
    box.scrollIntoView({ behavior: "smooth" });
  },
  backups() {
    const box = h("div", { class: "card" }, h("h3", {}, "Backups (every overwrite, delete, FTP upload and update)"));
    const tbl = h("div", { class: "scroll" });
    box.append(tbl);
    API.req("GET", "/api/backups").then((list) => {
      tbl.replaceChildren(h("table", {}, h("tr", {}, ["Backup", "Why", "Files", ""].map((t) => h("th", {}, t))),
        list.slice(0, 200).map((b) => h("tr", {}, h("td", { class: "mono" }, b.id), h("td", {}, b.why),
          h("td", { class: "mono", style: "white-space:normal" }, b.files.join("\n")),
          h("td", {}, h("button", { class: "btn", onclick: async () => {
            if (!confirm(`Put back the files of ${b.id}? The current versions are backed up first.`)) return;
            try { const r = await API.req("POST", `/api/restore?id=${b.id}`); toast(r.report || "restored", "ok"); } catch (e) { toast(e.message, "bad"); }
          } }, "Restore"))))));
    }).catch((e) => tbl.replaceChildren(e.message));
    return box;
  },
};

const Updates = {
  render(root) {
    const out = h("pre", { class: "log", style: "height:auto;min-height:120px" }, "");
    const file = h("input", { type: "file", accept: ".neopkg" });
    root.append(h("div", { class: "card" }, h("h3", {}, "Install an update package"),
      h("div", { class: "muted" }, "Packages are built with tools/mkpkg.py and signed with the update key from neoagent.ini. They can replace anything: programs (camserver, serbridge, neoagent itself), the web UI, Config.ini, job files. The agent checks the signature and every file's SHA-256, backs up what it replaces, stops and restarts the affected programs, and rolls everything back on any error."),
      h("div", { class: "row" }, file, h("button", { class: "btn primary", onclick: async () => {
        if (!file.files[0]) return;
        out.textContent = "installing…";
        try {
          const r = await fetch(location.origin + "/api/update", { method: "POST", body: await file.files[0].arrayBuffer() });
          const j = await r.json();
          out.textContent = (j.ok ? "OK\n" : "FAILED\n") + (j.report || j.error || "");
          toast(j.ok ? "update installed" : "update failed", j.ok ? "ok" : "bad");
        } catch (e) { out.textContent = "error: " + e.message; }
      } }, "Install")), out),
      Files.backups());
  },
};

// header buttons, polling, routing
document.getElementById("ctl").onclick = () => (API.st.haveControl ? API.release() : API.take());
document.getElementById("homebtn").onclick = async () => {
  const s = API.st.status || {};
  if (s.xy_state === 0 && !confirm("XY is already homed. Home again?")) return;
  App.homeReq = Date.now();
  document.getElementById("homebtn").disabled = true;
  if (!(await API.run("home", { wait: 0 }))) App.homeReq = 0;
};
(() => {                                    // System menu: open on click, close on any outside click / Escape
  const btn = document.getElementById("sysbtn"), menu = document.getElementById("sysmenu");
  const set = (open) => { menu.hidden = !open; btn.setAttribute("aria-expanded", open); };
  btn.onclick = (e) => { e.stopPropagation(); set(menu.hidden); };
  document.addEventListener("click", () => set(false));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") set(false); });
})();
// the red STOP: whatever runs (job, auto calibration, a move) stops; valves off, nozzles up, head home
document.getElementById("stop").onclick = async () => {
  await API.req("POST", "/api/mc/stop").catch(() => API.req("POST", "/api/mc/cancel").catch(() => {}));
  toast("stopped: the head goes home", "bad");
};
API.onChange(() => App.hdr());
// default paths from the agent's neoden_dir (unless the user picked others)
App.ready = API.req("GET", "/api/status").then((s) => {
  if (!s.neoden_dir) return;
  if (!localStorage.getItem("cfgPath")) Cfg.path = PathU.join(s.neoden_dir, "Config.ini");
  if (!localStorage.getItem("filesPath")) Files.path = s.neoden_dir;
}).catch(() => {});
window.addEventListener("hashchange", async (e) => {
  // leaving the job editor with unsaved changes (feeders, parts …): the Mount screen and the run read the saved file
  const from = (() => { try { return new URL(e.oldURL).hash; } catch (x) { return ""; } })();
  if (from === "#jobfile" && location.hash !== "#jobfile" && JobFile.dirty && JobFile.path) {
    const name = JobFile.path.split(/[\\/]/).pop();
    if (confirm(`${name} has unsaved changes (feeders, parts …). Save them now?\n\nCancel: they stay unsaved here; the Mount screen and the job use the saved file.`)) {
      try { await JobFile.save(); } catch (x) { toast("save: " + x.message, "bad"); }
    }
  }
  if (location.hash !== "#setups" && Setups.current) Setups.close();
  App.show();
});
setInterval(() => { if (!document.hidden) App.poll(); }, 700);
App.poll(); App.hdr(); App.show();
