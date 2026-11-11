// Board view for the wizard: Gerber outline (green), copper pads (red), silkscreen (yellow), parts
// (dots; fiducials cyan rings) and 0, 0. Mouse wheel zooms at the pointer, drag pans, double-click
// fits the board again. Designators show when zoomed in far enough. Y up, in the P&P frame (mm).
const BoardMap = {
  make(comps, layers, bd) {
    const cv = h("canvas", { class: "boardmap" });
    const tip = h("div", { class: "maptip" }, "");
    const lay = {};
    const box = h("div", { class: "mapbox" }, cv, tip);
    const toggles = h("div", { class: "row" }, h("span", { class: "muted" }, "Show:"),
      ...[["outline", "outline", true], ["copper", "pads", true], ["silk", "silkscreen", true], ["parts", "parts", true], ["names", "names", true]].map(([k, t, on]) => {
        lay[k] = on;
        return h("label", {}, h("input", { type: "checkbox", checked: on, onchange: (e) => { lay[k] = e.target.checked; draw(); } }), " " + t);
      }),
      h("span", { class: "muted" }, "· wheel = zoom, drag = move, double-click = whole board"));
    const g = cv.getContext("2d");
    // world extent: outline, else all shapes and parts
    const pts = comps.map((p) => [p.x, p.y]);
    const outl = layers.filter((L) => L.kind === "outline");
    const ext = (Ls) => { const xs = [], ys = []; for (const L of Ls) for (const s of L.shapes) { if (s[0] === "C") { xs.push(s[1] - s[3], s[1] + s[3]); ys.push(s[2] - s[3], s[2] + s[3]); } else for (const p of s[1]) { xs.push(p[0]); ys.push(p[1]); } } return xs.length ? [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)] : null; };
    let W0 = ext(outl.length ? outl : layers);
    if (pts.length) {
      const e = [Math.min(...pts.map((p) => p[0])), Math.min(...pts.map((p) => p[1])), Math.max(...pts.map((p) => p[0])), Math.max(...pts.map((p) => p[1]))];
      W0 = W0 ? [Math.min(W0[0], e[0]), Math.min(W0[1], e[1]), Math.max(W0[2], e[2]), Math.max(W0[3], e[3])] : e;
    }
    if (!W0) return h("div", { class: "muted" }, "Nothing to draw yet: load a Pick & Place file (and the Gerber).");
    let k = 1, ox = 0, oy = 0;                                   // screen = (x * k + ox, -y * k + oy)
    const fit = () => {
      const w = cv.width, ht = cv.height, m = 20;
      k = Math.min((w - 2 * m) / Math.max(1, W0[2] - W0[0]), (ht - 2 * m) / Math.max(1, W0[3] - W0[1]));
      ox = m - W0[0] * k + ((w - 2 * m) - (W0[2] - W0[0]) * k) / 2;
      oy = ht - m + W0[1] * k - ((ht - 2 * m) - (W0[3] - W0[1]) * k) / 2;
    };
    const S = (x, y) => [x * k + ox, -y * k + oy];
    const dark = matchMedia("(prefers-color-scheme: dark)").matches;
    const colors = { outline: "#4ade80", copper: dark ? "#d9534f" : "#c0392b", silk: "#facc15" };
    const shape = (s, col) => {
      if (s[0] === "C") { const [x, y] = S(s[1], s[2]); g.fillStyle = col; g.beginPath(); g.arc(x, y, Math.max(0.6, s[3] * k), 0, 7); g.fill(); return; }
      const p = s[1]; if (!p.length) return;
      g.beginPath(); let [x, y] = S(p[0][0], p[0][1]); g.moveTo(x, y);
      for (let i = 1; i < p.length; i++) { [x, y] = S(p[i][0], p[i][1]); g.lineTo(x, y); }
      g.closePath(); g.fillStyle = col; g.fill();
    };
    const draw = () => {
      const w = cv.width, ht = cv.height;
      g.fillStyle = dark ? "#0d1117" : "#f6f7f9"; g.fillRect(0, 0, w, ht);
      for (const kind of ["copper", "silk", "outline"]) {
        if (!lay[kind]) continue;
        for (const L of layers.filter((L) => L.kind === kind)) for (const s of L.shapes) if (s[s.length - 1] !== false) shape(s, colors[kind]);
      }
      if (!outl.length && bd && bd.width && bd.height) { const [x, y] = S(0, +bd.height); g.strokeStyle = colors.outline; g.strokeRect(x, y, +bd.width * k, +bd.height * k); }
      // 0, 0
      const [zx, zy] = S(0, 0);
      g.strokeStyle = "#60a5fa"; g.lineWidth = 1.5; g.beginPath(); g.moveTo(zx, zy); g.lineTo(zx + 30, zy); g.moveTo(zx, zy); g.lineTo(zx, zy - 30); g.stroke(); g.lineWidth = 1;
      g.fillStyle = "#60a5fa"; g.font = "11px system-ui"; g.fillText("0,0", zx + 3, zy + 12);
      if (lay.parts) for (const p of comps) {
        const [x, y] = S(p.x, p.y);
        if (p.fiducial) { g.strokeStyle = "#22d3ee"; g.lineWidth = 2; g.beginPath(); g.arc(x, y, 6, 0, 7); g.stroke(); g.lineWidth = 1; }
        else { g.fillStyle = dark ? "#ffffff" : "#1d2330"; g.fillRect(x - 1.5, y - 1.5, 3, 3); }
        if (lay.names && k > 6) { g.fillStyle = dark ? "#cbd5e1" : "#334155"; g.font = `${Math.min(13, 4 + k / 3)}px system-ui`; g.fillText(p.designator, x + 4, y - 4); }
      }
    };
    const size = () => { const r = cv.getBoundingClientRect(); const w = Math.max(300, Math.round(r.width)), ht = Math.round(w * 0.62); if (cv.width !== w) { cv.width = w; cv.height = ht; fit(); } draw(); };
    const at = (ev) => { const r = cv.getBoundingClientRect(); return [(ev.clientX - r.left) * cv.width / r.width, (ev.clientY - r.top) * cv.height / r.height]; };
    cv.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      const [mx, my] = at(ev), f = ev.deltaY < 0 ? 1.25 : 0.8;
      ox = mx - (mx - ox) * f; oy = my - (my - oy) * f; k *= f; draw();
    }, { passive: false });
    let drag = null;
    cv.addEventListener("mousedown", (ev) => { drag = at(ev); });
    window.addEventListener("mouseup", () => (drag = null));
    cv.addEventListener("mousemove", (ev) => {
      const [mx, my] = at(ev);
      if (drag) { ox += mx - drag[0]; oy += my - drag[1]; drag = [mx, my]; draw(); }
      const x = (mx - ox) / k, y = (oy - my) / k;
      let near = null, best = 1e9;
      for (const p of comps) { const d = Math.hypot(p.x - x, p.y - y); if (d < best) { best = d; near = p; } }
      tip.textContent = `X ${x.toFixed(2)}  Y ${y.toFixed(2)} mm` + (near && best * k < 12 ? `   ${near.designator} ${near.part || ""} (${near.footprint || ""})` : "");
    });
    cv.addEventListener("dblclick", () => { fit(); draw(); });
    requestAnimationFrame(size);
    window.addEventListener("resize", () => { if (cv.isConnected) size(); });
    return h("div", {}, toggles, box);
  },
};
