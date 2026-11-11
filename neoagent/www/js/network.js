// Network page: Wi-Fi of the machine PC (USB dongle) through /api/wifi/* (neoserver/wifi.py):
// what it is connected to, scan, connect to a network (password asked here, never shown back),
// forget a known one. Wired Ethernet is not changed from here.
const Network = {
  scanRes: null,

  async render(root) {
    const st = h("div", { class: "kv" }, h("div", {}, "Wi-Fi"), h("div", { class: "muted" }, "…"));
    const list = h("div", {}, this.scanRes ? this.table(this.scanRes) : h("div", { class: "muted" }, "Press Scan to list the networks in range."));
    const scanBtn = h("button", { class: "btn primary", onclick: async () => {
      scanBtn.disabled = true; scanBtn.textContent = "Scanning…";
      try { this.scanRes = await API.req("POST", "/api/wifi/scan"); list.replaceChildren(this.table(this.scanRes)); }
      catch (e) { list.replaceChildren(h("div", { class: "bad" }, e.message)); }
      scanBtn.disabled = false; scanBtn.textContent = "Scan";
    } }, "Scan");
    const hs = h("input", { placeholder: "network name (SSID)", style: "width:220px" });
    const hp = h("input", { type: "password", placeholder: "password", style: "width:180px" });
    root.append(
      h("div", { class: "card" }, h("h3", {}, "Wi-Fi status"), st),
      h("div", { class: "card", style: "margin-top:12px" }, h("div", { class: "row", style: "margin-top:0" }, h("h3", { style: "margin:0" }, "Networks in range"), h("div", { class: "grow" }), scanBtn), list),
      h("details", { class: "card", style: "margin-top:12px" }, h("summary", {}, "Hidden network"),
        h("div", { class: "row" }, hs, hp, h("button", { class: "btn", onclick: () => this.connect(hs.value, hp.value, true) }, "Connect"))),
      h("div", { class: "muted", style: "margin-top:10px" }, "The machine PC has no Wi-Fi of its own: plug in a USB Wi-Fi dongle that Linux supports. " +
        "Changing Wi-Fi does not touch the wired network. If the browser is connected over Wi-Fi, it loses the page while the machine moves to the new network: " +
        "open http://neoden.local again from that network."));
    this.showStatus(st);
  },

  async showStatus(box) {
    const s = await API.req("GET", "/api/wifi/status").catch((e) => ({ error: e.message }));
    if (s.error) { box.replaceChildren(h("div", {}, "Wi-Fi"), h("div", { class: "bad" }, s.error)); return; }
    box.replaceChildren(
      h("div", {}, "Adapter"), h("div", {}, s.iface || h("span", { class: "warn" }, s.note || "none")),
      h("div", {}, "Connected to"), h("div", {}, s.connected ? h("b", {}, s.ssid) : h("span", { class: "muted" }, "not connected")),
      h("div", {}, "Signal"), h("div", {}, s.signal_dbm !== null && s.signal_dbm !== undefined ? `${s.signal_dbm} dBm ${this.bars(s.signal_dbm)}` : "–"),
      h("div", {}, "Address"), h("div", { class: "mono" }, s.ip || "–"),
      h("div", {}, "Known networks"), h("div", {}, (s.known || []).length ? (s.known || []).map((n, i) => h("span", { class: "pill", style: "margin:0 6px 4px 0;display:inline-flex;gap:6px;align-items:center" },
        (i === 0 ? "★ " : "") + n, h("a", { href: "javascript:void 0", title: "forget", onclick: () => this.forget(n) }, "✕"))) : h("span", { class: "muted" }, "none")));
  },

  bars(dbm) { const n = dbm >= -55 ? 4 : dbm >= -65 ? 3 : dbm >= -75 ? 2 : 1; return "▂▄▆█".slice(0, n); },

  table(r) {
    if (!r.networks.length) return h("div", { class: "muted" }, "No networks found.");
    return h("table", {}, h("tr", {}, ["Network", "Signal", "Band", "Security", ""].map((t) => h("th", {}, t))),
      r.networks.map((n) => {
        const pw = h("input", { type: "password", placeholder: n.known ? "saved (leave empty)" : "password", style: "width:160px;display:" + (n.secure ? "" : "none") });
        return h("tr", {}, h("td", {}, h("b", {}, n.ssid), n.connected ? h("span", { class: "pill ok", style: "margin-left:6px" }, "connected") : n.known ? h("span", { class: "pill", style: "margin-left:6px" }, "known") : null),
          h("td", { class: "mono" }, `${this.bars(n.signal_dbm)} ${n.signal_dbm} dBm`), h("td", {}, n.freq ? (n.freq > 4000 ? "5 GHz" : "2.4 GHz") : "–"),
          h("td", {}, n.secure ? "🔒 WPA" : "open"),
          h("td", {}, h("div", { class: "row", style: "margin:0;flex-wrap:nowrap" }, pw,
            h("button", { class: "btn" + (n.connected ? "" : " primary"), disabled: n.connected, onclick: () => this.connect(n.ssid, pw.value, false, n.secure && !n.known) }, "Connect"))));
      }));
  },

  async connect(ssid, password, hidden, needPw) {
    if (!ssid) return toast("network name?", "warn");
    if (needPw && !password) return toast("enter the password", "warn");
    if (!confirm(`Connect the machine to "${ssid}"? Wired Ethernet stays as it is.`)) return;
    try {
      const r = await API.req("POST", "/api/wifi/connect", JSON.stringify({ ssid, password: password || "", hidden: !!hidden }));
      toast(r.note || "connecting…", "ok");
      setTimeout(() => { if (location.hash === "#network") App.show(); }, 8000);
    } catch (e) { toast(e.message, "bad"); }
  },

  async forget(ssid) {
    if (!confirm(`Forget "${ssid}"? Its saved password is deleted.`)) return;
    try { await API.req("POST", `/api/wifi/forget?ssid=${encodeURIComponent(ssid)}`); toast("forgotten", "ok"); App.show(); }
    catch (e) { toast(e.message, "bad"); }
  },
};
