// Vision log: every part / mark / pad image the job took, with what vision found (PP's neoserver
// /api/visionlog/*, docs/PADALIGN.md "Vision log"). Temporary: to calibrate the vision, then switch off
// ([Job] bVisionLog) and delete. Offline re-runs: tools/visionlog.py (summary, rerun, scale).
const VisionLog = {
  async render(root) {
    if (!Cfg.loaded) await Cfg.load().catch(() => {});
    const on = !!Cfg.get("Job/bVisionLog", false);
    const body = h("div", {}, h("div", { class: "muted" }, "loading…"));
    root.append(h("div", { class: "card" }, h("div", { class: "row", style: "margin:0" },
      h("span", { class: on ? "pill ok" : "pill" }, on ? "logging is ON" : "logging is off"),
      h("span", { class: "muted" }, `limit ${Cfg.get("Job/nVisionLogMaxMB", 2000)} MB · switch it in Settings → Job`),
      h("div", { class: "grow" }),
      h("a", { class: "btn", href: "#settings", onclick: () => (Settings.tab = "job") }, "Settings…"),
      h("button", { class: "btn", onclick: () => App.show() }, "Refresh"),
      h("button", { class: "btn danger", onclick: () => this.clear() }, "Delete all"))), body);
    const r = await API.req("GET", "/api/visionlog/summary").catch((e) => ({ error: e.message }));
    if (r.error) { body.replaceChildren(h("div", { class: "card bad" }, r.error)); return; }
    const days = r.days || [];
    if (!days.length) { body.replaceChildren(h("div", { class: "card muted" }, "No images logged yet. Switch logging on and run a job.")); return; }
    const pct = (a, b) => (b ? `${Math.round(a / b * 100)} %` : "–");
    const rng = (q) => (q && q.n ? `${fmt(q.min, 2)} · ${fmt(q.median, 2)} · ${fmt(q.max, 2)}` : "–");
    body.replaceChildren(...days.map((d) => h("div", { class: "card", style: "margin-top:12px" },
      h("h3", {}, `${d.job} · ${d.day}`),
      h("div", { class: "muted" }, `${d.records} images (${Object.entries(d.kinds || {}).map(([k, n]) => `${n} ${k}`).join(", ")}), ${fmt(d.mb, 1)} MB · ${d.dir}`),
      h("div", { class: "scroll", style: "max-height:50vh;margin-top:8px" }, h("table", {},
        h("tr", {}, ["Footprint", "Images", "Found", "Placed", "Rejected (reason: n)", "Size ratio W min · median · max", "Size ratio H"].map((t) => h("th", {}, t))),
        Object.entries(d.footprints || {}).sort((a, b) => b[1].images - a[1].images).map(([fp, f]) => {
          const rej = Object.entries(f.rejected || {});
          return h("tr", {}, h("td", {}, fp || "–"), h("td", {}, f.images), h("td", {}, `${f.found} (${pct(f.found, f.images)})`), h("td", {}, f.placed),
            h("td", { class: rej.length ? "bad" : "muted", style: "white-space:normal" }, rej.length ? rej.map(([k, n]) => `${k}: ${n}`).join(", ") : "none"),
            h("td", { class: "mono" }, rng(f.size_ratio_w)), h("td", { class: "mono" }, rng(f.size_ratio_h)));
        }))))),
      h("div", { class: "muted", style: "margin-top:10px" }, "Size ratio = measured / package size; NeoDen4 accepted 0.8 – 1.25 (Settings → Vision). " +
        "To try other vision parameters on these images: tools/visionlog.py rerun (see docs/PADALIGN.md, Vision log)."));
  },

  async clear() {
    if (!confirm("Delete the whole vision log (all logged images and results)? This cannot be undone.")) return;
    try { await API.req("POST", "/api/visionlog/clear"); toast("vision log deleted", "ok"); App.show(); } catch (e) { toast(e.message, "bad"); }
  },
};
