// Live camera view: frames from /api/cam/grab (BMP, already turned like NeoDen4.exe),
// zoom 1x..16x around the centre, crosshair with mm ticks, clicks reported in full-frame
// pixels relative to the centre (dx right, dy UP), so callers can turn them into mm.
// cam 0 = up-looking camera (34.5 px/mm), cam 1 = head camera (112 px/mm).
document.addEventListener("pointerdown", (e) => { if (!e.target.closest || !e.target.closest(".cam")) CamView.arm(null); }, true);
class CamView {
  constructor(cam, opts = {}) {
    this.cam = cam;
    this.pxmm = Cfg.pxmm(cam);
    this.zoom = 1;
    this.size = 1024;                       // full frame (window) size in px
    this.live = false;
    this.points = opts.points || null;      // [[x,y] x4] marks in full-frame px (4-up nozzle points)
    this.onClick = opts.onClick || null;    // (dxPx, dyUpPx, ev) in full-frame px
    this.frame = null;
    this.el = h("div", { class: "cam" });
    this.canvas = h("canvas", { width: 640, height: 640 });
    this.info = h("div", { class: "info" }, "");
    this.el.append(this.canvas, this.info);
    this.canvas.addEventListener("click", (ev) => this._click(ev));
    this.ctx = this.canvas.getContext("2d");
    this.canvas.addEventListener("pointerdown", () => CamView.arm(this));
    this._draw();
  }

  // the arrow keys jog the head only after a click on a camera picture (it shows "arrows jog"); a click
  // anywhere else turns that off: an arrow pressed to scroll a page must not move the machine
  static arm(v) {
    if (CamView.armed && CamView.armed !== v) CamView.armed.el.classList.remove("keys");
    CamView.armed = v || null;
    if (v) v.el.classList.add("keys");
  }
  static keysOn() { return !!(CamView.armed && CamView.armed.el.isConnected); }

  controls() {
    const zoom = h("select", { onchange: (e) => { this.zoom = +e.target.value; this._draw(); } },
      [1, 2, 4, 8, 16].map((z) => h("option", { value: z }, `${z}x`)));
    const live = h("input", { type: "checkbox", onchange: (e) => (e.target.checked ? this.start() : this.stop()) });
    this.liveBox = live;
    return h("div", { class: "row" },
      h("button", { class: "btn", onclick: () => this.grab() }, "Grab"),
      h("label", {}, live, " Live"), h("label", {}, "Zoom "), zoom);
  }

  async grab() {
    // full resolution when zoomed, half resolution for the live overview
    const scale = this.zoom >= 2 ? 1 : 2;
    const st = API.st.status || {};
    if (st.led > 0) this._lit = false;
    if (this.cam === 1 && API.st.haveControl && st.led === 0 && !st.led_user_off && !this._lit) {
      this._lit = true;                       // the head lamp is off (after a restart, a vision task): on
      await API.run("led", { v: 3 });
      await new Promise((r) => setTimeout(r, 200));
    }
    try {
      const r = await API.req("GET", `/api/cam/grab?cam=${this.cam}&scale=${scale}&t=${Date.now()}`, null, true);
      const bmp = await createImageBitmap(await r.blob());
      this.frame = { bmp, scale };
      this.size = bmp.width * scale;
      this._draw();
      return true;
    } catch (e) {
      this.info.textContent = "camera: " + e.message;
      this.stop();
      return false;
    }
  }

  // show a picture from elsewhere (the job's last vision picture), `scale` = full frame / picture
  async showUrl(url, scale = 1) {
    try {
      const r = await API.req("GET", url, null, true);
      const bmp = await createImageBitmap(await r.blob());
      this.frame = { bmp, scale }; this.size = bmp.width * scale; this._draw();
    } catch (e) { /* none yet */ }
  }

  start() {
    if (this.live) return;
    this.live = true;
    if (this.liveBox) this.liveBox.checked = true;
    // started before its card is on the page (built, then appended): wait for it up to 3 s
    const t0 = Date.now();
    const loop = async () => {
      if (!this.live) return;
      if (!this.el.isConnected) {
        if (Date.now() - t0 < 3000) { setTimeout(loop, 100); return; }
        this.live = false; return;
      }
      // paced: the machine's own small PC grabs, sends and draws every frame (and the kiosk browser runs
      // on it too); at most ~6 frames a second, slower when a frame takes longer, none while hidden
      if (document.hidden) { setTimeout(loop, 500); return; }
      const t = performance.now();
      await this.grab();
      const took = performance.now() - t;
      setTimeout(loop, Math.max(150, 1.5 * took) - Math.min(took, 100));
    };
    loop();
  }
  stop() { this.live = false; if (this.liveBox) this.liveBox.checked = false; }

  _draw() {
    const c = this.ctx, W = this.canvas.width, Hh = this.canvas.height;
    c.fillStyle = "#000"; c.fillRect(0, 0, W, Hh);
    if (this.frame) {
      const { bmp, scale } = this.frame;
      const src = bmp.width / this.zoom, sx = (bmp.width - src) / 2;
      c.imageSmoothingEnabled = this.zoom < 4;
      c.drawImage(bmp, sx, sx, src, src, 0, 0, W, Hh);
    }
    // crosshair with 1 mm ticks
    const k = (W / this.size) * this.zoom;            // canvas px per full-frame px
    c.strokeStyle = "rgba(0,255,120,.8)"; c.lineWidth = 1;
    c.beginPath(); c.moveTo(W / 2, 0); c.lineTo(W / 2, Hh); c.moveTo(0, Hh / 2); c.lineTo(W, Hh / 2); c.stroke();
    const tick = this.pxmm * k;
    if (tick > 6) {
      c.beginPath();
      for (let i = -20; i <= 20; i++) {
        if (!i) continue;
        const p = W / 2 + i * tick, q = Hh / 2 + i * tick, l = i % 5 ? 4 : 8;
        c.moveTo(p, Hh / 2 - l); c.lineTo(p, Hh / 2 + l); c.moveTo(W / 2 - l, q); c.lineTo(W / 2 + l, q);
      }
      c.stroke();
    }
    // canvas px per screen px: text and lines are drawn this much bigger so they stay readable when the
    // 640 px canvas is shown small
    // (its shown width is read once and on a resize: reading it on every frame forces a layout of the page)
    if (this._cw === undefined) {
      this._cw = this.canvas.clientWidth || 0;
      if (window.ResizeObserver) new ResizeObserver(() => { this._cw = this.canvas.clientWidth || 0; }).observe(this.canvas);
    }
    if (!this._cw && this.canvas.isConnected) this._cw = this.canvas.clientWidth || 0;
    const sc = Math.max(1, W / Math.max(1, this._cw || W));
    if (this.overlay) this.overlay(c, (x, y) => [W / 2 + (x - this.size / 2) * k, Hh / 2 + (y - this.size / 2) * k], sc);
    if (this.points) {
      c.strokeStyle = "rgba(255,80,80,.9)";
      this.points.forEach(([x, y], i) => {
        const u = W / 2 + (x - this.size / 2) * k, v = Hh / 2 + (y - this.size / 2) * k;
        c.beginPath(); c.moveTo(u - 12, v); c.lineTo(u + 12, v); c.moveTo(u, v - 12); c.lineTo(u, v + 12); c.stroke();
        c.fillStyle = "rgba(255,80,80,.9)"; c.fillText(String(i + 1), u + 6, v - 6);
      });
    }
    if (this.labelBelow) {                  // the caller's text and the camera line under the picture, not on it
      if (!this.cap) { this.cap = h("div", { class: "camcap" }); this.el.classList.add("below"); this.el.append(this.cap); this.cap.append(this.info); }
      const lines = this.label ? (this.label() || []).filter(Boolean) : [];
      if (!this.capText) { this.capText = h("div", {}); this.cap.prepend(this.capText); }
      const txt = lines.join("\n");
      if (txt !== this._capTxt) { this._capTxt = txt; this.capText.replaceChildren(...lines.map((l) => h("div", {}, l))); }
    } else if (this.label) {                // caller's text in the top-left corner (e.g. coordinates)
      const lines = this.label() || [];
      const fs = Math.round(13 * sc), lh = Math.round(fs * 1.3);
      c.font = `bold ${fs}px ui-monospace, Consolas, monospace`;
      const w = Math.max(0, ...lines.map((l) => c.measureText(l).width));
      c.fillStyle = "rgba(0,0,0,.7)"; c.fillRect(4, 4, w + 12, lines.length * lh + 8);
      c.fillStyle = "#bff"; lines.forEach((l, i) => c.fillText(l, 10, 4 + lh * (i + 1)));
    }
    const inf = `${this.cam === 0 ? "up camera" : "head camera"}  ${this.size}px  ${this.zoom}x  ${(+this.pxmm).toFixed(2)} px/mm`;
    if (this.info.textContent !== inf) this.info.textContent = inf;
  }

  // canvas click -> full-frame px from the centre (dy positive = up)
  _click(ev) {
    if (!this.onClick) return;
    const r = this.canvas.getBoundingClientRect();
    const k = (r.width / this.size) * this.zoom;
    const dx = (ev.clientX - r.left - r.width / 2) / k, dyUp = (r.height / 2 - (ev.clientY - r.top)) / k;
    this.onClick(dx, dyUp, ev);
  }
}
