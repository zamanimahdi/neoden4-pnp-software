// Automatic nozzle calibration over the up camera: find the nozzle tip's hole in each frame,
// fit a circle through the hole centres seen at several nozzle angles = the ROTATION AXIS
// (what a placed part turns about) and the runout radius (bent / worn nozzle).
// Pure functions on gray images {w, h, data: Uint8Array}; no DOM, so node can test them.
const NozzleCal = (() => {
  // Otsu threshold of a gray image (or of the pixels in [x0,x1) x [y0,y1))
  function otsu(img, x0 = 0, y0 = 0, x1 = img.w, y1 = img.h) {
    const hist = new Float64Array(256);
    for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) hist[img.data[y * img.w + x]]++;
    let total = 0, sum = 0;
    for (let i = 0; i < 256; i++) { total += hist[i]; sum += i * hist[i]; }
    let wB = 0, sB = 0, best = 0, t = 128;
    for (let i = 0; i < 256; i++) {
      wB += hist[i]; if (!wB) continue;
      const wF = total - wB; if (!wF) break;
      sB += i * hist[i];
      const mB = sB / wB, mF = (sum - sB) / wF, v = wB * wF * (mB - mF) * (mB - mF);
      if (v > best) { best = v; t = i; }
    }
    return t;
  }

  // label 4-connected components of mask (Uint8Array 0/1); returns {labels, comps:[{n,sx,sy,x0,y0,x1,y1,border}]}
  function components(mask, w, h) {
    const labels = new Int32Array(w * h), comps = [], stack = new Int32Array(w * h);
    for (let i = 0; i < w * h; i++) {
      if (!mask[i] || labels[i]) continue;
      const c = { n: 0, sx: 0, sy: 0, x0: w, y0: h, x1: 0, y1: 0, border: false };
      const id = comps.length + 1;
      let sp = 0;
      stack[sp++] = i; labels[i] = id;
      while (sp) {
        const p = stack[--sp], x = p % w, y = (p - x) / w;
        c.n++; c.sx += x; c.sy += y;
        if (x < c.x0) c.x0 = x; if (x > c.x1) c.x1 = x; if (y < c.y0) c.y0 = y; if (y > c.y1) c.y1 = y;
        if (!x || !y || x === w - 1 || y === h - 1) c.border = true;
        if (x > 0 && mask[p - 1] && !labels[p - 1]) { labels[p - 1] = id; stack[sp++] = p - 1; }
        if (x < w - 1 && mask[p + 1] && !labels[p + 1]) { labels[p + 1] = id; stack[sp++] = p + 1; }
        if (y > 0 && mask[p - w] && !labels[p - w]) { labels[p - w] = id; stack[sp++] = p - w; }
        if (y < h - 1 && mask[p + w] && !labels[p + w]) { labels[p + w] = id; stack[sp++] = p + w; }
      }
      comps.push(c);
    }
    return { labels, comps };
  }

  // Find the nozzle tip and its hole near (cx, cy) inside a search box of half size r (px).
  // Tip = bright blob (lit by the ring light) touching the box centre region; hole = dark area
  // enclosed by the tip (not connected to the box border). Returns {ok, x, y, holeR, tipX, tipY, tipR}
  // in full-image px (sub-pixel: intensity-weighted centroid of the hole), or ok=false + why.
  function findHole(img, cx = img.w / 2, cy = img.h / 2, r = 160, minHole = 6) {
    const x0 = Math.max(0, Math.round(cx - r)), y0 = Math.max(0, Math.round(cy - r));
    const x1 = Math.min(img.w, Math.round(cx + r)), y1 = Math.min(img.h, Math.round(cy + r));
    const w = x1 - x0, h = y1 - y0;
    const t = otsu(img, x0, y0, x1, y1);
    {   // a real lit tip separates clearly from the background; a uniform image only splits noise
      let nb = 0, sb = 0, nf = 0, sf = 0;
      for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) { const v = img.data[y * img.w + x]; if (v > t) { nf++; sf += v; } else { nb++; sb += v; } }
      if (!nf || !nb || sf / nf - sb / nb < 40) return { ok: false, why: "no bright nozzle tip in the search box (light, exposure, position?)" };
    }
    const bright = new Uint8Array(w * h);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) bright[y * w + x] = img.data[(y + y0) * img.w + x + x0] > t ? 1 : 0;
    const B = components(bright, w, h);
    // tip: largest bright component not touching the border (the background may be bright too)
    let tip = null;
    for (const c of B.comps) if (!c.border && c.n > 50 && (!tip || c.n > tip.n)) tip = c;
    if (!tip) return { ok: false, why: "no bright nozzle tip in the search box (light, exposure, position?)" };
    const tipId = B.comps.indexOf(tip) + 1;
    // dark pixels inside the tip's bounding box that are enclosed (not reachable from outside the tip)
    const dark = new Uint8Array(w * h);
    for (let y = tip.y0; y <= tip.y1; y++) for (let x = tip.x0; x <= tip.x1; x++) {
      const i = y * w + x;
      if (B.labels[i] !== tipId) dark[i] = 1;
    }
    const D = components(dark, w, h);
    let hole = null;
    const tcx = tip.sx / tip.n, tcy = tip.sy / tip.n;
    for (const c of D.comps) {
      if (c.n < minHole) continue;
      // enclosed: completely inside the tip's box, not on the box edge
      if (c.x0 <= tip.x0 || c.y0 <= tip.y0 || c.x1 >= tip.x1 || c.y1 >= tip.y1) continue;
      const d = Math.hypot(c.sx / c.n - tcx, c.sy / c.n - tcy);
      if (!hole || d < hole.d) hole = Object.assign(c, { d, id: D.comps.indexOf(c) + 1 });
    }
    if (!hole) return { ok: false, why: "nozzle tip found but no hole inside it (focus, exposure too high?)",
                        tipX: tcx + x0, tipY: tcy + y0, tipR: Math.sqrt(tip.n / Math.PI) };
    // sub-pixel: weight each hole pixel (and a 1 px ring around it) by its darkness below the threshold
    let sw = 0, swx = 0, swy = 0;
    for (let y = Math.max(0, hole.y0 - 1); y <= Math.min(h - 1, hole.y1 + 1); y++)
      for (let x = Math.max(0, hole.x0 - 1); x <= Math.min(w - 1, hole.x1 + 1); x++) {
        const i = y * w + x;
        if (B.labels[i] === tipId && D.labels[i] !== hole.id) {
          // ring pixel: only if it touches the hole
          const nb = (x > 0 && D.labels[i - 1] === hole.id) || (x < w - 1 && D.labels[i + 1] === hole.id) ||
                     (y > 0 && D.labels[i - w] === hole.id) || (y < h - 1 && D.labels[i + w] === hole.id);
          if (!nb) continue;
        } else if (D.labels[i] !== hole.id) continue;
        const v = Math.max(0, t + 1 - img.data[(y + y0) * img.w + x + x0]);
        sw += v; swx += v * x; swy += v * y;
      }
    if (!sw) { sw = hole.n; swx = hole.sx; swy = hole.sy; }
    return { ok: true, x: swx / sw + x0, y: swy / sw + y0, holeR: Math.sqrt(hole.n / Math.PI),
             tipX: tcx + x0, tipY: tcy + y0, tipR: Math.sqrt(tip.n / Math.PI), threshold: t };
  }

  // least-squares circle (Kasa) through points [[x,y],...]: {x, y, r, rms}
  function fitCircle(pts) {
    const n = pts.length;
    if (n < 3) return null;
    let mx = 0, my = 0;
    for (const [x, y] of pts) { mx += x; my += y; }
    mx /= n; my /= n;
    let suu = 0, svv = 0, suv = 0, suuu = 0, svvv = 0, suvv = 0, svuu = 0;
    for (const [x, y] of pts) {
      const u = x - mx, v = y - my;
      suu += u * u; svv += v * v; suv += u * v; suuu += u * u * u; svvv += v * v * v; suvv += u * v * v; svuu += v * u * u;
    }
    const det = suu * svv - suv * suv;
    if (Math.abs(det) < 1e-12) {                       // all at one point: no runout measurable
      return { x: mx, y: my, r: 0, rms: 0 };
    }
    const a = 0.5 * (suuu + suvv), b = 0.5 * (svvv + svuu);
    const uc = (a * svv - b * suv) / det, vc = (b * suu - a * suv) / det;
    const r = Math.sqrt(uc * uc + vc * vc + (suu + svv) / n);
    let ss = 0;
    for (const [x, y] of pts) { const d = Math.hypot(x - mx - uc, y - my - vc) - r; ss += d * d; }
    return { x: mx + uc, y: my + vc, r, rms: Math.sqrt(ss / n) };
  }

  // axis pixel -> head correction in mm (SETUP_SPEC 6: up camera, clicked point to the centre = move -(dx, dy_up)/k)
  function axisToHeadMove(ax, ay, size = 1024, pxmm = 34.5) {
    return [-(ax - size / 2) / pxmm, -(size / 2 - ay) / pxmm];
  }

  // px/mm and camera angle from hole positions before / after known head moves (dx mm in X, dy mm in Y)
  // up camera: a head move of +X shows the nozzle at image -x (it looks up), see axisToHeadMove
  function scaleFromMoves(p0, pX, pY, dx, dy) {
    const vx = [pX[0] - p0[0], -(pX[1] - p0[1])], vy = [pY[0] - p0[0], -(pY[1] - p0[1])];  // image up = +
    const kx = Math.hypot(vx[0], vx[1]) / Math.abs(dx), ky = Math.hypot(vy[0], vy[1]) / Math.abs(dy);
    // expected: +X head -> -x image; angle of the actual direction vs the expected one
    const angX = Math.atan2(-vx[1] * Math.sign(dx), -vx[0] * Math.sign(dx)) * 180 / Math.PI;
    const angY = Math.atan2(vy[0] * Math.sign(dy), -vy[1] * Math.sign(dy)) * 180 / Math.PI;
    return { pxmmX: kx, pxmmY: ky, angleX: angX, angleY: angY };
  }

  return { otsu, components, findHole, fitCircle, axisToHeadMove, scaleFromMoves };
})();
if (typeof module !== "undefined") module.exports = NozzleCal;
