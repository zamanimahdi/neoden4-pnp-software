"""Setup wizard bridge: the web UI calls neosetup.Project methods over JSON (docs/SETUP_WIZARD.md).

    POST /api/setup/call  {"sid": "...", "method": "import_pnp", "args": [...], "kwargs": {...}}
      -> 200 {"ok": true, "result": ..., "messages": [...]}
      -> 422 {"ok": false, "error": "title: text", "title", "text", "messages"}   the tool refused
      -> 400 unknown method / bad argument, 404 unknown component / feeder / part
    POST /api/setup/close?sid=...    drop that session's project

One Project per browser session (sid), as neosetup asks; idle projects are dropped after 12 h.
Tool options are kept in <backup>/neosetup-settings.json, never in the user's own settings.
"""
import math
import os
import threading
import time

# calls the wizard may make (no engine escape hatch, no private helpers)
ALLOWED = {
    # files
    "import_pnp", "import_machine_file", "load_gerber", "clear_gerber", "gerber", "save_project",
    "load_project", "project_snapshot", "is_dirty", "status",
    # options
    "options", "set_options", "learn_slots", "settings", "apply_settings", "load_settings_file",
    # parts
    "bom", "footprints", "heights", "part_heights", "stats", "similar_name_groups", "merge_similar_names",
    "rename_part", "remove_part", "remove_footprint", "set_part_height", "set_footprint_height",
    "set_place_height_settings", "apply_place_heights", "add_fiducial", "fiducials", "components",
    "marks", "add_mark", "set_mark", "delete_mark",
    "edit_component", "delete_component", "turn_component", "set_table_sort", "table_order",
    # board
    "set_board", "board", "check_board_size", "rotate_board_quarter", "rotate_board", "set_board_rotation",
    "flip_x", "flip_y", "board_side", "mirror_board", "apply_offset",
    "shift_board", "board_bounds", "zero_bottom_left", "gerber_shapes",
    # alignment
    "suggest_alignment", "identifiability", "check_point", "plan_alignment", "apply_alignment",
    # feeders
    "prepare_build", "edit_build_part", "build_machine", "adopt_layout", "program", "feeders",
    "set_feeder_fields", "add_feeder", "delete_feeder", "group_edit", "arrange_feeders",
    "enforce_fiducial_rules", "package_library", "apply_package", "board_records", "set_board_record",
    # order
    "placements", "auto_sort", "move", "move_before", "toggle_skip", "set_skip", "assign_feeder",
    "set_placement_fields", "delete_placement",
    # speed
    "speed_slots", "set_slot_widths", "speed_plan", "apply_speed_plan",
    # output
    "parts_kit", "export_kit", "machine_file_text", "write_machine_file", "stages", "export_stages",
    "export_pnp_csv",
}
IDLE_S = 12 * 3600


def plain(v):
    """JSON-safe copy (tuples -> lists, dict keys -> str, sets -> sorted lists)"""
    if isinstance(v, dict):
        return {str(k): plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [plain(x) for x in v]
    if isinstance(v, (set, frozenset)):
        return sorted(plain(x) for x in v)
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    return str(v)


class Setup:
    def __init__(self, backup, log):
        self.settings_path = os.path.join(backup, "neosetup-settings.json")
        self.log, self.lock, self.projects = log, threading.Lock(), {}

    def project(self, sid):
        with self.lock:
            now = time.monotonic()
            for k in [k for k, (_, t) in self.projects.items() if now - t > IDLE_S]:
                del self.projects[k]
            if sid not in self.projects:
                from neosetup import Project
                self.projects[sid] = [Project(settings_path=self.settings_path, machine_fixes=True), now]
                self.log(f"setup: new project for session {sid}")
            self.projects[sid][1] = now
            return self.projects[sid][0]

    def close(self, sid):
        with self.lock:
            self.projects.pop(sid, None)
            getattr(self, "boards", {}).pop(sid, None)

    # ---- the board as one picture: the Gerber drawn here, not as 18 000 polygons in the browser ----
    # (BOARD-A: 4.4 MB of shapes for every redraw of the Board step; on the machine's own screen the
    # picture never came.) The page asks board_info (cheap: has anything changed?) and then the PNG.
    BOARD_COLORS = {"copper": (64, 96, 212), "silk": (40, 200, 250), "outline": (128, 222, 74)}     # BGR
    BOARD_BG = (23, 17, 13)

    def _board_rev(self, p):
        g = p.engine
        gs = g["gerber_state"]
        return repr((tuple(round(float(v), 6) for v in g["board_xform"]), tuple(round(float(v), 4) for v in gs["offset"]),
                     sorted((k, bool(v)) for k, v in gs["visible"].items()), [(L["key"], len(L["shapes"])) for L in gs["layers"]]))

    def board_info(self, sid):
        """{rev, loaded, extent [x0, y0, x1, y1] mm, k px/mm, w, h}: the picture's place in the P&P frame"""
        p = self.project(sid)
        rev = self._board_rev(p)
        c = self.__dict__.setdefault("boards", {}).get(sid)
        if c is None or c["key"] != rev:
            c = self._board_draw(p, rev)
            c["key"] = rev
            self.boards[sid] = c
        return {k: v for k, v in c.items() if k not in ("png", "key")}

    def board_png(self, sid):
        self.board_info(sid)
        return self.boards[sid]["png"]

    def _board_draw(self, p, rev, max_px=2800, margin=3.0):
        import cv2
        import numpy as np
        import zlib
        layers = p.gerber_shapes()["layers"]
        xs, ys = [], []
        for L in layers:
            for s in L["shapes"]:
                if s[0] == "C":
                    xs += [s[1] - s[3], s[1] + s[3]]
                    ys += [s[2] - s[3], s[2] + s[3]]
                else:
                    xs += [q[0] for q in s[1]]
                    ys += [q[1] for q in s[1]]
        tag = format(zlib.crc32(rev.encode()) & 0xFFFFFFFF, "08x")
        if not xs:
            return {"rev": tag, "loaded": False, "png": b""}
        x0, y0, x1, y1 = min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin
        k = min(16.0, max_px / max(x1 - x0, y1 - y0))
        W, H = int(math.ceil((x1 - x0) * k)), int(math.ceil((y1 - y0) * k))
        img = np.empty((H, W, 3), np.uint8)
        img[:] = self.BOARD_BG
        SH = 4                                                    # 1/16 px
        f = k * (1 << SH)
        for kind in ("copper", "silk", "outline"):
            col = self.BOARD_COLORS[kind]
            for L in layers:
                if L["kind"] != kind:
                    continue
                for s in L["shapes"]:
                    c = col if s[-1] is not False else self.BOARD_BG
                    if s[0] == "C":
                        cv2.circle(img, (int(round((s[1] - x0) * f)), int(round((y1 - s[2]) * f))), max(1 << SH, int(round(s[3] * f))), c, -1, cv2.LINE_AA, SH)
                    elif len(s[1]) >= 3:
                        pts = np.array([[(q[0] - x0) * f, (y1 - q[1]) * f] for q in s[1]]).round().astype(np.int32)
                        cv2.fillPoly(img, [pts], c, cv2.LINE_AA, SH)
        ok, png = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 3])
        return {"rev": tag, "loaded": True, "extent": [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)], "k": round(k, 4),
                "w": W, "h": H, "png": png.tobytes()}

    def call(self, req):
        """-> (http code, reply dict)"""
        from neosetup import ToolError
        sid, method = str(req.get("sid") or ""), str(req.get("method") or "")
        if not sid:
            return 400, {"ok": False, "error": "sid needed"}
        if method not in ALLOWED:
            return 400, {"ok": False, "error": f"unknown setup call {method!r}"}
        p = self.project(sid)
        try:
            r = getattr(p, method)(*(req.get("args") or []), **(req.get("kwargs") or {}))
        except ToolError as e:
            return 422, {"ok": False, "error": str(e), "title": e.title, "text": e.text, "messages": plain(e.messages)}
        except KeyError as e:
            return 404, {"ok": False, "error": f"not found: {e}", "messages": plain(p.last_messages)}
        except (ValueError, TypeError) as e:
            return 400, {"ok": False, "error": f"{type(e).__name__}: {e}", "messages": plain(p.last_messages)}
        return 200, {"ok": True, "result": plain(r), "messages": plain(p.last_messages), "dirty": p.is_dirty()}
