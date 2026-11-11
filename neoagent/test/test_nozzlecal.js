// node neoagent/test/test_nozzlecal.js
// Synthetic up-camera frames of a nozzle tip (bright ring with a dark hole) that runs out
// around a known axis while the nozzle turns; NozzleCal must find the hole each time and fit
// the axis and the runout radius.
const N = require("../www/js/nozzlecal.js");
const assert = (c, m) => { if (!c) { console.error("FAIL:", m); process.exit(1); } };

function frame(hx, hy, { size = 1024, tipR = 70, holeR = 18, bg = 25, tip = 210, hole = 40, noise = 6, seed = 1 } = {}) {
  const data = new Uint8Array(size * size);
  let s = seed;
  const rnd = () => ((s = (s * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff - 0.5) * 2 * noise;
  // anti-aliased discs (4x4 supersampling only near the edges)
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    const d = Math.hypot(x - hx, y - hy);   // pixel centres at integer coordinates (OpenCV convention)
    let v;
    if (d > tipR + 1) v = bg;
    else if (d < holeR - 1) v = hole;
    else if (d > holeR + 1 && d < tipR - 1) v = tip;
    else {
      let acc = 0;
      for (let j = 0; j < 4; j++) for (let i = 0; i < 4; i++) {
        const dd = Math.hypot(x + (i + 0.5) / 4 - 0.5 - hx, y + (j + 0.5) / 4 - 0.5 - hy);
        acc += dd > tipR ? bg : dd < holeR ? hole : tip;
      }
      v = acc / 16;
    }
    data[y * size + x] = Math.max(0, Math.min(255, Math.round(v + rnd())));
  }
  return { w: size, h: size, data };
}

// 1. single frame: hole centre to better than 0.1 px
{
  const r = N.findHole(frame(530.3, 497.6), 512, 512, 200);
  assert(r.ok, "hole found: " + r.why);
  assert(Math.abs(r.x - 530.3) < 0.1 && Math.abs(r.y - 497.6) < 0.1, `hole at ${r.x.toFixed(3)}, ${r.y.toFixed(3)}`);
  assert(Math.abs(r.holeR - 18) < 1.5, "hole radius " + r.holeR);
}
// 2. rotation: hole runs out on a circle (radius 6 px = 0.17 mm) around the axis (540.25, 488.75)
{
  const ax = 540.25, ay = 488.75, run = 6, pts = [];
  for (let a = 0; a < 360; a += 30) {
    const t = a * Math.PI / 180;
    const r = N.findHole(frame(ax + run * Math.cos(t), ay + run * Math.sin(t), { seed: a + 3 }), 512, 512, 220);
    assert(r.ok, "angle " + a + ": " + r.why);
    pts.push([r.x, r.y]);
  }
  const c = N.fitCircle(pts);
  assert(Math.abs(c.x - ax) < 0.1 && Math.abs(c.y - ay) < 0.1, `axis ${c.x.toFixed(3)}, ${c.y.toFixed(3)}`);
  assert(Math.abs(c.r - run) < 0.15, "runout " + c.r);
  const mv = N.axisToHeadMove(c.x, c.y);
  assert(Math.abs(mv[0] + (ax - 512) / 34.5) < 0.005 && Math.abs(mv[1] + (512 - ay) / 34.5) < 0.005, "head move " + mv);
  console.log(`axis ${c.x.toFixed(3)}, ${c.y.toFixed(3)} px  runout ${c.r.toFixed(3)} px (${(c.r / 34.5 * 1000).toFixed(0)} µm)  fit rms ${c.rms.toFixed(3)} px`);
}
// 3. no hole (tip without opening) and no tip at all: clear reasons
{
  const r = N.findHole(frame(512, 512, { holeR: 0.1 }), 512, 512, 200);
  assert(!r.ok && /no hole/.test(r.why), r.why);
  const e = N.findHole(frame(2000, 2000), 512, 512, 200);
  assert(!e.ok && /no bright nozzle tip/.test(e.why), e.why);
}
// 4. px/mm and camera angle from known head moves (up camera mirrors: +X head -> -x image)
{
  const k = 35.2, ang = 0.8 * Math.PI / 180;
  const img = (mx, my) => {                           // head move (mm) -> image shift (px), image y down
    const ix = -k * (mx * Math.cos(ang) - my * Math.sin(ang)), iyUp = -k * (mx * Math.sin(ang) + my * Math.cos(ang));
    return [500 + ix, 520 - iyUp];
  };
  const s = N.scaleFromMoves(img(0, 0), img(3, 0), img(0, 3), 3, 3);
  assert(Math.abs(s.pxmmX - k) < 1e-6 && Math.abs(s.pxmmY - k) < 1e-6, "px/mm " + JSON.stringify(s));
  assert(Math.abs(s.angleX - 0.8) < 1e-6 && Math.abs(s.angleY - 0.8) < 1e-6, "angle " + JSON.stringify(s));
}
console.log("nozzle calibration tests: OK");
