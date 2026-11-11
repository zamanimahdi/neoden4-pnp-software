// Package library for the size check: the part's length x width (mm) as the up camera sees it (body
// with its leads / terminals, lit from below). Built-in typical sizes of standard packages, merged
// with the machine's own library Config/pkg.csv (name,L,W,H; it wins for the same name, and
// "Save to library" adds to it). Search by name or alias; suggestions from a feeder's footprint /
// value ("0603/10K", "SOT-23", "LQFP64" ...).
const PkgLib = {
  // [name, L, W, H, aliases]
  builtin: [
    ["01005 (0402M)", 0.4, 0.2, 0.13, "01005 r01005 c01005"],
    ["0201 (0603M)", 0.6, 0.3, 0.25, "0201 r0201 c0201"],
    ["0402 (1005M)", 1.0, 0.5, 0.35, "0402 r0402 c0402 l0402 led0402"],
    ["0603 (1608M)", 1.6, 0.8, 0.45, "0603 r0603 c0603 l0603 led0603"],
    ["0805 (2012M)", 2.0, 1.25, 0.5, "0805 r0805 c0805 l0805 led0805"],
    ["1206 (3216M)", 3.2, 1.6, 0.55, "1206 r1206 c1206 l1206 led1206"],
    ["1210 (3225M)", 3.2, 2.5, 0.55, "1210 r1210 c1210"],
    ["1812 (4532M)", 4.5, 3.2, 0.6, "1812"],
    ["2010 (5025M)", 5.0, 2.5, 0.6, "2010"],
    ["2512 (6332M)", 6.3, 3.2, 0.6, "2512"],
    ["Tantalum A (3216)", 3.2, 1.6, 1.6, "tantalum casea 3216-18"],
    ["Tantalum B (3528)", 3.5, 2.8, 1.9, "tantalum caseb 3528-21"],
    ["Tantalum C (6032)", 6.0, 3.2, 2.5, "tantalum casec 6032-28"],
    ["Tantalum D (7343)", 7.3, 4.3, 2.8, "tantalum cased 7343-31"],
    ["LED PLCC2 3528", 3.5, 2.8, 1.9, "3528 plcc2 led"],
    ["LED 5050", 5.0, 5.0, 1.6, "5050 plcc6 led ws2812"],
    ["SOD-523", 1.6, 0.8, 0.6, "sod523"],
    ["SOD-323", 2.5, 1.25, 0.9, "sod323"],
    ["SOD-123", 3.7, 1.6, 1.1, "sod123"],
    ["MiniMELF (SOD-80)", 3.5, 1.5, 1.5, "minimelf sod80 ll34"],
    ["SMA (DO-214AC)", 5.0, 2.6, 2.1, "sma do214ac"],
    ["SMB (DO-214AA)", 5.3, 3.6, 2.2, "smb do214aa"],
    ["SMC (DO-214AB)", 7.9, 5.9, 2.2, "smc do214ab"],
    ["SOT-523 (SC-89)", 1.6, 1.6, 0.8, "sot523 sc89"],
    ["SOT-323 (SC-70)", 2.1, 2.1, 1.0, "sot323 sc70"],
    ["SOT-363 (SC-70-6)", 2.1, 2.1, 1.0, "sot363 sc706 sc88"],
    ["SOT-23", 2.9, 2.4, 1.1, "sot23 sot23-3 to236"],
    ["SOT-23-5", 2.9, 2.8, 1.1, "sot235 sot23-5 sot25"],
    ["SOT-23-6", 2.9, 2.8, 1.1, "sot236 sot23-6 sot26"],
    ["SOT-89", 4.5, 4.1, 1.5, "sot89"],
    ["SOT-223", 6.5, 7.0, 1.7, "sot223"],
    ["TO-252 (DPAK)", 6.6, 10.1, 2.3, "to252 dpak"],
    ["TO-263 (D2PAK)", 10.2, 15.3, 4.5, "to263 d2pak"],
    ["MSOP-8", 3.0, 4.9, 1.1, "msop8 vssop8"],
    ["MSOP-10", 3.0, 4.9, 1.1, "msop10"],
    ["SOIC-8", 4.9, 6.0, 1.5, "soic8 so8 sop8"],
    ["SOIC-14", 8.65, 6.0, 1.5, "soic14 so14 sop14"],
    ["SOIC-16", 9.9, 6.0, 1.5, "soic16 so16 sop16"],
    ["SOIC-16W", 10.3, 10.3, 2.4, "soic16w so16w"],
    ["SOIC-20W", 12.8, 10.3, 2.4, "soic20 so20"],
    ["TSSOP-8", 3.0, 6.4, 1.1, "tssop8"],
    ["TSSOP-14", 5.0, 6.4, 1.1, "tssop14"],
    ["TSSOP-16", 5.0, 6.4, 1.1, "tssop16"],
    ["TSSOP-20", 6.5, 6.4, 1.1, "tssop20"],
    ["TSSOP-28", 9.7, 6.4, 1.1, "tssop28"],
    ["SSOP-20", 7.2, 7.8, 1.8, "ssop20"],
    ["SSOP-28", 10.2, 7.8, 1.8, "ssop28"],
    ["DFN-8 2x2", 2.0, 2.0, 0.8, "dfn8 2x2"],
    ["DFN-8 3x3", 3.0, 3.0, 0.9, "dfn8 3x3 son8"],
    ["QFN-16 3x3", 3.0, 3.0, 0.9, "qfn16"],
    ["QFN-20 4x4", 4.0, 4.0, 0.9, "qfn20"],
    ["QFN-24 4x4", 4.0, 4.0, 0.9, "qfn24"],
    ["QFN-32 5x5", 5.0, 5.0, 0.9, "qfn32"],
    ["QFN-48 7x7", 7.0, 7.0, 0.9, "qfn48"],
    ["QFN-64 9x9", 9.0, 9.0, 0.9, "qfn64"],
    ["LQFP-32 7x7", 9.0, 9.0, 1.6, "lqfp32 tqfp32"],
    ["LQFP-44 10x10", 12.0, 12.0, 1.6, "lqfp44 tqfp44"],
    ["LQFP-48 7x7", 9.0, 9.0, 1.6, "lqfp48 tqfp48"],
    ["LQFP-64 10x10", 12.0, 12.0, 1.6, "lqfp64 tqfp64"],
    ["LQFP-100 14x14", 16.0, 16.0, 1.6, "lqfp100 tqfp100"],
    ["LQFP-144 20x20", 22.0, 22.0, 1.6, "lqfp144"],
    ["Crystal 3225", 3.2, 2.5, 0.8, "xtal 3225 crystal"],
    ["Crystal 5032", 5.0, 3.2, 1.0, "xtal 5032 crystal"],
    ["Inductor 4x4 (CD43)", 4.5, 4.0, 3.2, "cd43 inductor"],
  ],
  machine: null,              // [[name, L, W, H]] from Config/pkg.csv
  path: "",

  async load() {
    if (this.machine) return;
    try {
      const st = await API.req("GET", "/api/status");
      this.path = PathU.join(PathU.join(st.neoden_dir, "Config"), "pkg.csv");
      const r = await API.req("GET", `/api/file?path=${encodeURIComponent(this.path)}`, null, true);
      this.machine = (await r.text()).split(/\r?\n/).map((l) => l.split(",")).filter((t) => t.length >= 3 && t[0].trim() && !isNaN(+t[1]) && !isNaN(+t[2]))
        .map((t) => [t[0].trim(), +t[1], +t[2], +t[3] || 0]);
    } catch (e) { this.machine = []; }
  },

  all() {
    const mine = (this.machine || []).map(([n, l, w, hh]) => ({ name: n, l, w, h: hh, alias: n.toLowerCase(), mine: true }));
    const names = new Set(mine.map((p) => p.name.toLowerCase()));
    return mine.concat(this.builtin.filter(([n]) => !names.has(n.toLowerCase())).map(([n, l, w, hh, a]) => ({ name: n, l, w, h: hh, alias: (n + " " + a).toLowerCase() })));
  },

  // the packages whose name / alias best match a text (a footprint, a value, or what is typed)
  suggest(text, n = 6, min = 1) {
    const norm = (s) => String(s || "").toLowerCase().replace(/[\s_\-.]/g, "");
    const words = String(text || "").toLowerCase().split(/[\/,;\s]+/).map(norm).filter((w) => w.length >= 2);
    if (!words.length) return [];
    const scored = this.all().map((p) => {
      const a = norm(p.alias), nm = norm(p.name);
      let s = 0;
      for (const w of words) {
        if (nm === w) s += 100;
        else if (p.alias.split(" ").map(norm).includes(w)) s += 60;
        else if (nm.startsWith(w)) s += 30;
        else if (a.includes(w)) s += 10 + Math.min(w.length, 8);
      }
      return [s + (p.mine ? 1 : 0), p];
    }).filter(([s]) => s >= min).sort((x, y) => y[0] - x[0]);
    return scored.slice(0, n).map(([, p]) => p);
  },

  // add / replace a package in Config/pkg.csv
  async save(name, l, w, hh) {
    await this.load();
    name = String(name || "").trim().replace(/,/g, " ");
    if (!name || !(l > 0) || !(w > 0)) throw new Error("a name and L, W > 0");
    this.machine = this.machine.filter(([n]) => n.toLowerCase() !== name.toLowerCase()).concat([[name, +l, +w, +hh || 0]]);
    const text = this.machine.map(([n, a, b, c]) => `${n},${(+a).toFixed(2)},${(+b).toFixed(2)},${(+c).toFixed(2)},`).join("\r\n") + "\r\n";
    await API.req("PUT", `/api/file?path=${encodeURIComponent(this.path)}`, INI.toBytes(text));
  },
};
