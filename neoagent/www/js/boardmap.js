// Board view for the wizard: the Gerber as ONE picture drawn by the server (GET /api/setup/board.png:
// copper red, silkscreen yellow, outline green), with the parts, the marks (fiducials) and the head
// camera drawn over it. Mouse wheel zooms at the pointer, drag pans, double-click fits the board again.
// Y up, in the P&P frame (mm). The picture is fetched once per change of the board (its `rev`).
//   const m = BoardMap.make({ sid, comps, marks, cam, onPick })
//   m.el                     the element
//   m.set({ marks, sel })    new marks / a selected point, redrawn
//   cam: () => ({ x, y, size, bmp }) | null    the head camera: where it looks (mm), how much it sees (mm),
//                                              its last frame (drawn on the board there)
//   onPick(x, y, part)       a click (not a drag) on the board: the point in mm and the nearest part
const BoardMap = {
  cache: {},                                         // sid -> { rev, info, img }

  async picture(sid) {
    const info = await API.req("GET", `/api/setup/board?sid=${encodeURIComponent(sid)}`);
    const c = this.cache[sid];
    if (c && c.rev === info.rev) return c;
    const n = { rev: info.rev, info, img: null };
    if (info.loaded) {
      const r = await API.req("GET", `/api/setup/board.png?sid=${encodeURIComponent(sid)}&rev=${info.rev}`, null, true);
      n.img = await createImageBitmap(await r.blob());
    }
    return (this.cache[sid] = n);
  },

  make(o) {
    const comps = o.comps || [];
    const st = { marks: o.marks || [], sel: null, pic: null };
    const cv = h("canvas", { class: "boardmap" });
    const tip = h("div", { class: "maptip" }, "");
    const note = h("div", { class: "mapnote" }, "loading the board picture …");
    const lay = { board: true, parts: true, names: true };
    const toggles = h("div", { class: "maptools" },
      ...[["board", "Gerber"], ["parts", "Parts"], ["names", "Names"]].map(([k, t]) =>
        h("label", { class: "chip" }, h("input", { type: "checkbox", checked: true, onchange: (e) => { lay[k] = e.target.checked; draw(); } }), t)),
      h("button", { class: "btn small", onclick: () => { fit(); draw(); } }, "Whole board"),
      h("span", { class: "muted" }, "wheel: zoom · drag: move" + (o.onPick ? " · click: pick a point" : "")));
    const box = h("div", { class: "mapbox" }, cv, tip, note);
    const g = cv.getContext("2d");
    let W0 = null, k = 1, ox = 0, oy = 0;                         // screen = (x * k + ox, -y * k + oy)
    const extent = () => {
      const e = st.pic && st.pic.info.loaded ? st.pic.info.extent.slice() : null;
      if (!comps.length) return e;
      const xs = comps.map((p) => p.x), ys = comps.map((p) => p.y);
      const c = [Math.min(...xs) - 3, Math.min(...ys) - 3, Math.max(...xs) + 3, Math.max(...ys) + 3];
      return e ? [Math.min(e[0], c[0]), Math.min(e[1], c[1]), Math.max(e[2], c[2]), Math.max(e[3], c[3])] : c;
    };
    const fit = () => {
      W0 = extent();
      if (!W0) return;
      const w = cv.width, ht = cv.height, m = 14;
      k = Math.min((w - 2 * m) / Math.max(1, W0[2] - W0[0]), (ht - 2 * m) / Math.max(1, W0[3] - W0[1]));
      ox = m - W0[0] * k + ((w - 2 * m) - (W0[2] - W0[0]) * k) / 2;
      oy = ht - m + W0[1] * k - ((ht - 2 * m) - (W0[3] - W0[1]) * k) / 2;
    };
    const S = (x, y) => [x * k + ox, -y * k + oy];
    const css = (v, d) => (getComputedStyle(document.documentElement).getPropertyValue(v) || d).trim() || d;
    const draw = () => {
      const w = cv.width, ht = cv.height;
      g.setTransform(1, 0, 0, 1, 0, 0);
      g.fillStyle = "#0d1117"; g.fillRect(0, 0, w, ht);
      if (!W0) return;
      const pic = st.pic;
      if (lay.board && pic && pic.img) {
        const e = pic.info.extent, [x, y] = S(e[0], e[3]);
        g.imageSmoothingEnabled = k < pic.info.k * 1.5;           // zoomed in past the picture: crisp pixels
        g.drawImage(pic.img, x, y, (e[2] - e[0]) * k, (e[3] - e[1]) * k);
      }
      const [zx, zy] = S(0, 0);                                    // 0, 0
      g.strokeStyle = "#60a5fa"; g.lineWidth = 1.5; g.beginPath(); g.moveTo(zx, zy); g.lineTo(zx + 30, zy); g.moveTo(zx, zy); g.lineTo(zx, zy - 30); g.stroke();
      g.fillStyle = "#60a5fa"; g.font = "11px system-ui"; g.fillText("0,0", zx + 3, zy + 12);
      if (lay.parts) for (const p of comps) {
        const [x, y] = S(p.x, p.y);
        if (x < -20 || y < -20 || x > w + 20 || y > ht + 20) continue;
        if (p.fiducial) { g.strokeStyle = "#22d3ee"; g.lineWidth = 2; g.beginPath(); g.arc(x, y, 6, 0, 7); g.stroke(); }
        else { g.fillStyle = "#ffffff"; g.fillRect(x - 1.5, y - 1.5, 3, 3); }
        if (lay.names && k > 7) { g.fillStyle = "#e2e8f0"; g.font = `${Math.min(13, 4 + k / 3)}px system-ui`; g.fillText(p.designator, x + 4, y - 4); }
      }
      const c = o.cam && o.cam();                                 // the head camera: its frame where it looks
      if (c) {
        const [x, y] = S(c.x - c.size / 2, c.y + c.size / 2), s = c.size * k;
        if (c.bmp && s > 24) { g.imageSmoothingEnabled = true; g.drawImage(c.bmp, x, y, s, s); }
        g.strokeStyle = "#f472b6"; g.lineWidth = 2; g.strokeRect(x, y, s, s);
        const [mx, my] = S(c.x, c.y);
        g.beginPath(); g.moveTo(mx - 9, my); g.lineTo(mx + 9, my); g.moveTo(mx, my - 9); g.lineTo(mx, my + 9); g.stroke();
        g.fillStyle = "#f472b6"; g.font = "11px system-ui"; g.fillText("camera", x + 3, y - 4);
      }
      st.marks.forEach((m, i) => {                                 // the marks (fiducials)
        const [x, y] = S(m[0], m[1]);
        g.strokeStyle = "#22d3ee"; g.lineWidth = 2.5; g.beginPath(); g.arc(x, y, 9, 0, 7); g.stroke();
        g.beginPath(); g.moveTo(x - 13, y); g.lineTo(x + 13, y); g.moveTo(x, y - 13); g.lineTo(x, y + 13); g.stroke();
        g.fillStyle = "#22d3ee"; g.font = "bold 12px system-ui"; g.fillText(`M${i + 1}`, x + 11, y - 9);
      });
      if (st.sel) {
        const [x, y] = S(st.sel[0], st.sel[1]);
        g.strokeStyle = "#fbbf24"; g.lineWidth = 2; g.beginPath(); g.arc(x, y, 7, 0, 7); g.stroke();
      }
      g.lineWidth = 1;
    };
    const size = () => {
      const r = box.getBoundingClientRect();
      const room = innerHeight - Math.max(0, r.top) - 76;                // down to the wizard's Back / Next bar
      const w = Math.max(300, Math.round(r.width)), ht = Math.max(280, Math.round(o.height || Math.min(room > 280 ? room : innerHeight - 250, w)));
      if (cv.width !== w || cv.height !== ht) { cv.width = w; cv.height = ht; cv.style.height = ht + "px"; fit(); }
      draw();
    };
    const at = (ev) => { const r = cv.getBoundingClientRect(); return [(ev.clientX - r.left) * cv.width / r.width, (ev.clientY - r.top) * cv.height / r.height]; };
    const world = ([mx, my]) => [(mx - ox) / k, (oy - my) / k];
    const nearest = (x, y) => { let near = null, best = 1e9; for (const p of comps) { const d = Math.hypot(p.x - x, p.y - y); if (d < best) { best = d; near = p; } } return [near, best]; };
    cv.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      const [mx, my] = at(ev), f = ev.deltaY < 0 ? 1.25 : 0.8;
      ox = mx - (mx - ox) * f; oy = my - (my - oy) * f; k *= f; draw();
    }, { passive: false });
    let drag = null, moved = 0;
    cv.addEventListener("pointerdown", (ev) => { drag = at(ev); moved = 0; cv.setPointerCapture(ev.pointerId); });
    cv.addEventListener("pointerup", (ev) => {
      const was = drag; drag = null;
      if (was && moved < 5 && o.onPick) { const [x, y] = world(at(ev)); const [near, d] = nearest(x, y); st.sel = [x, y]; draw(); o.onPick(x, y, near && d * k < 14 ? near : null); }
    });
    cv.addEventListener("pointermove", (ev) => {
      const m = at(ev);
      if (drag) { ox += m[0] - drag[0]; oy += m[1] - drag[1]; moved += Math.abs(m[0] - drag[0]) + Math.abs(m[1] - drag[1]); drag = m; draw(); }
      const [x, y] = world(m), [near, d] = nearest(x, y);
      tip.textContent = `X ${x.toFixed(2)}  Y ${y.toFixed(2)} mm` + (near && d * k < 14 ? `   ${near.designator} ${near.part || ""} (${near.footprint || ""})` : "");
    });
    cv.addEventListener("dblclick", () => { fit(); draw(); });
    const onResize = () => { if (cv.isConnected) size(); else removeEventListener("resize", onResize); };
    addEventListener("resize", onResize);
    requestAnimationFrame(size);
    this.picture(o.sid).then((p) => {
      st.pic = p;
      note.textContent = p.info.loaded ? "" : (comps.length ? "No Gerber loaded: the parts alone (step 1: load the Gerber .zip)." : "Nothing to draw yet: load a Pick & Place file and the Gerber (step 1).");
      note.style.display = note.textContent ? "" : "none";
      fit(); draw();
    }).catch((e) => { note.textContent = "board picture: " + e.message; fit(); draw(); });
    return { el: h("div", { class: "boardview" }, toggles, box), draw, fit: () => { fit(); draw(); },
             set: (v) => { Object.assign(st, v); draw(); }, live: () => cv.isConnected };
  },
};
