// Statistics (neojob/learn.py, GET /api/stats?days=): what the jobs did, per nozzle, feeder and part:
// picks, failed picks (vacuum), vision rejects, parts thrown away, placed, after-place checks; the
// warnings for a nozzle / feeder whose share of failures is high (Settings -> Job: counting on / off).
const Stats = {
  days: 7,
  async render(root) {
    const days = h("select", { onchange: (e) => { this.days = +e.target.value; App.show(); } },
      [[1, "today"], [7, "the last 7 days"], [30, "the last 30 days"], [365, "the last year"]].map(([v, t]) => h("option", { value: v, selected: v === this.days }, t)));
    root.append(h("div", { class: "row" }, h("span", { class: "muted" }, "Period"), days));
    let d;
    try { d = await API.req("GET", `/api/stats?days=${this.days}`); } catch (e) { root.append(h("div", { class: "card bad" }, e.message)); return; }
    const hm = (s) => s >= 3600 ? `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min` : `${Math.round(s / 60)} min`;
    const tile = (label, value) => h("div", { class: "card", style: "padding:10px 14px;min-width:130px" }, h("div", { class: "muted", style: "font-size:12px" }, label), h("div", { style: "font-size:22px;font-weight:600" }, value));
    root.append(h("div", { class: "row", style: "gap:10px;flex-wrap:wrap" }, tile("Parts placed", String(d.placed)), tile("Jobs", String(d.jobs)),
      tile("Working time", hm(d.run_s || 0)), tile("Parts per hour", d.per_hour ? String(d.per_hour) : "—")));
    if (d.warnings && d.warnings.length)
      root.append(h("div", { class: "card", style: "border-color:var(--bad)" }, h("h3", { style: "margin-top:0;color:var(--bad)" }, "Warnings"),
        d.warnings.map((w) => h("div", {}, "⚠ " + w))));
    const warn = +(Cfg.get("Job/fStatsWarnPct", 15) || 15);
    const table = (title, rows, keyName) => h("div", { class: "card" }, h("h3", { style: "margin-top:0" }, title),
      rows.length ? h("table", { class: "pick" },
        h("tr", {}, [keyName, "Picks", "Pick failed", "Vision rejected", "Thrown away", "Placed", "Checked after placing", "Check failed", "Failed %"].map((t) => h("th", {}, t))),
        rows.map((r) => h("tr", {}, h("td", {}, h("b", {}, r.key)), h("td", {}, String(r.picks)), h("td", {}, String(r.pick_fail)), h("td", {}, String(r.vision_fail)),
          h("td", {}, String(r.discarded)), h("td", {}, String(r.placed)), h("td", {}, String(r.checked)), h("td", {}, String(r.check_fail)),
          h("td", { style: r.picks >= 20 && r.fail_pct >= warn ? "color:var(--bad);font-weight:600" : "" }, r.picks ? `${fmt(r.fail_pct, 1)} %` : "—")))) :
        h("div", { class: "muted" }, "nothing counted in this period"));
    root.append(table("Nozzles", d.nozzle || [], "Nozzle"), table("Feeders", d.feeder || [], "Feeder"), table("Parts", d.part || [], "Part"),
      h("div", { class: "muted", style: "font-size:12px" }, `Failed % = (failed picks + vision rejects) / picks; a nozzle or feeder with ${warn} % or more over 20 picks or more gets a warning (Settings → Job). ` +
        "The counts are kept in <data>/stats, one file per day."));
  },
};
