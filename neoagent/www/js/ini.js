// NeoDen4 Config.ini (Qt QSettings INI) read/write; same rules as neocam/config.py:
// floats as @Variant(\0\0\0\x87 + big-endian float32), Qt escapes, quoting on ; , =
// Values are kept as {type, value}: "f" float, "i" int, "b" bool, "s" string, "raw" other.
const INI = (() => {
  function unescape(s) {                       // -> byte array
    const out = [];
    const simple = { "0": 0, a: 7, b: 8, t: 9, n: 10, v: 11, f: 12, r: 13, '"': 34, "\\": 92, "'": 39, "?": 63 };
    for (let i = 0; i < s.length; i++) {
      const c = s[i];
      if (c === "\\" && i + 1 < s.length) {
        const n = s[i + 1];
        if (n === "x") {
          let j = i + 2;
          while (j < s.length && /[0-9a-fA-F]/.test(s[j])) j++;
          out.push(parseInt(s.slice(i + 2, j) || "0", 16) & 255);
          i = j - 1;
          continue;
        }
        out.push(n in simple ? simple[n] : n.charCodeAt(0) & 255);
        i++;
        continue;
      }
      out.push(c.charCodeAt(0) & 255);
    }
    return out;
  }
  function escape(bytes) {
    let out = "", afterHex = false;
    for (const c of bytes) {
      const ch = String.fromCharCode(c);
      if (c === 0) { out += "\\0"; afterHex = false; }
      else if (c === 92) { out += "\\\\"; afterHex = false; }
      else if (c === 34) { out += '\\"'; afterHex = false; }
      else if (c >= 7 && c <= 13) { out += "\\" + "abtnvfr"[c - 7]; afterHex = false; }
      else if (c < 32 || c >= 127 || (afterHex && /[0-9a-fA-F]/.test(ch))) { out += "\\x" + c.toString(16); afterHex = true; }
      else { out += ch; afterHex = false; }
    }
    return out;
  }
  const quote = (s) => (/[;,=]/.test(s) || s.startsWith(" ") || s.endsWith(" ")) ? `"${s}"` : s;

  function parseValue(v) {
    v = v.trim();
    if (v.length > 1 && v[0] === '"' && v[v.length - 1] === '"') v = v.slice(1, -1);
    const m = /^@Variant\((.*)\)$/s.exec(v);
    if (m) {
      const b = unescape(m[1]);
      if (b.length >= 8 && b[0] === 0 && b[1] === 0 && b[2] === 0 && b[3] === 0x87) {
        const dv = new DataView(new Uint8Array(b.slice(4, 8)).buffer);
        return { type: "f", value: dv.getFloat32(0, false) };
      }
      return { type: "raw", value: v };
    }
    if (v === "true" || v === "false") return { type: "b", value: v === "true" };
    if (/^-?\d+$/.test(v)) return { type: "i", value: parseInt(v, 10) };
    return { type: "s", value: v };
  }
  function formatValue(t) {
    if (t.type === "b") return t.value ? "true" : "false";
    if (t.type === "f") {
      const dv = new DataView(new ArrayBuffer(4));
      dv.setFloat32(0, t.value, false);
      return quote("@Variant(" + escape([0, 0, 0, 0x87, ...new Uint8Array(dv.buffer)]) + ")");
    }
    if (t.type === "raw") return t.value;
    return quote(String(t.value));
  }

  // text is latin-1 ("binary") as stored; returns {lines, data:{"Group/key": {type,value}}}
  function parse(text) {
    const lines = text.split("\n"), data = {};
    let sec = "General";
    for (const line0 of lines) {
      const line = line0.replace(/\r$/, "");
      if (line.startsWith("[") && line.endsWith("]")) { sec = line.slice(1, -1); continue; }
      const i = line.indexOf("=");
      if (i > 0 && !line.startsWith(";")) data[`${sec}/${line.slice(0, i).trim()}`] = parseValue(line.slice(i + 1));
    }
    return { lines, data };
  }

  // write changed keys back into the original lines (unchanged lines stay byte-identical)
  function serialize(doc, changes) {
    const lines = doc.lines.slice(), todo = Object.assign({}, changes), last = {};
    let sec = "General";
    lines.forEach((line0, i) => {
      const cr = line0.endsWith("\r"), line = cr ? line0.slice(0, -1) : line0;
      if (line.startsWith("[") && line.endsWith("]")) { sec = line.slice(1, -1); if (!(sec in last)) last[sec] = i; return; }
      const k = line.indexOf("=");
      if (k > 0) {
        last[sec] = i;
        const key = `${sec}/${line.slice(0, k).trim()}`;
        if (key in todo) { lines[i] = line.slice(0, k) + "=" + formatValue(todo[key]) + (cr ? "\r" : ""); delete todo[key]; }
      }
    });
    for (const [key, t] of Object.entries(todo)) {
      const slash = key.indexOf("/"), g = key.slice(0, slash), k = key.slice(slash + 1);
      if (g in last) {
        lines.splice(last[g] + 1, 0, `${k}=${formatValue(t)}`);
        for (const gg in last) if (last[gg] > last[g]) last[gg]++;
        last[g]++;
      } else {
        if (lines.length && lines[lines.length - 1].trim()) lines.push("");
        lines.push(`[${g}]`, `${k}=${formatValue(t)}`);
        last[g] = lines.length - 1;
      }
    }
    return lines.join("\n");
  }

  // latin-1 <-> bytes for the HTTP file API
  const fromBytes = (buf) => { let s = ""; const u = new Uint8Array(buf); for (let i = 0; i < u.length; i++) s += String.fromCharCode(u[i]); return s; };
  const toBytes = (s) => { const u = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i) & 255; return u; };

  return { parse, serialize, parseValue, formatValue, fromBytes, toBytes };
})();

// the machine's Config.ini, loaded once and shared by Settings, Setups and Manual
const Cfg = {
  path: localStorage.getItem("cfgPath") || "D:\\NeoDen4\\Config.ini",
  doc: null, changes: {}, loaded: false,
  async load() {
    const r = await API.req("GET", `/api/file?path=${encodeURIComponent(this.path)}`, null, true);
    this.doc = INI.parse(INI.fromBytes(await r.arrayBuffer()));
    this.changes = {}; this.loaded = true;
    return this;
  },
  get(key, def) {
    if (key in this.changes) return this.changes[key].value;
    const t = this.doc && this.doc.data[key];
    return t ? t.value : def;
  },
  set(key, value, type) {
    const old = this.doc && this.doc.data[key];
    this.changes[key] = { type: type || (old ? old.type : (Number.isInteger(value) ? "i" : "f")), value };
  },
  pt(key) { return [Number(this.get(key + ".x", 0)), Number(this.get(key + ".y", 0))]; },
  setPt(key, xy) { this.set(key + ".x", xy[0], "f"); this.set(key + ".y", xy[1], "f"); },
  dirty() { return Object.keys(this.changes).length; },
  async save() {
    const text = INI.serialize(this.doc, this.changes);
    const r = await API.req("PUT", `/api/file?path=${encodeURIComponent(this.path)}`, INI.toBytes(text));
    await this.load();
    return r;
  },
  // camera scale (px/mm), from Settings -> Vision; NeoDen4's constants when not set
  pxmm(cam) { return Number(cam === 0 ? this.get("Vision/fUpPxPerMm", 34.5) : this.get("Vision/fHeadPxPerMm", 112)) || (cam === 0 ? 34.5 : 112); },
  // tool offsets (SETUP_SPEC section 6): 0 head camera, 1 nozzle 1 = (0,0), 2..4 nozzle n
  offset(tool) {
    if (tool === 0) return this.pt("Position/dPosCamLookDownMouse1");
    if (tool >= 2 && tool <= 4) {
      const a = this.pt(`Position/posCamLookUpMouse${tool - 1}`), b = this.pt("Position/posCamLookUpMouse0");
      return [a[0] - b[0], a[1] - b[1]];
    }
    return [0, 0];
  },
};
