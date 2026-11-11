"""Setup wizard bridge: the web UI calls neosetup.Project methods over JSON (docs/SETUP_WIZARD.md).

    POST /api/setup/call  {"sid": "...", "method": "import_pnp", "args": [...], "kwargs": {...}}
      -> 200 {"ok": true, "result": ..., "messages": [...]}
      -> 422 {"ok": false, "error": "title: text", "title", "text", "messages"}   the tool refused
      -> 400 unknown method / bad argument, 404 unknown component / feeder / part
    POST /api/setup/close?sid=...    drop that session's project

One Project per browser session (sid), as neosetup asks; idle projects are dropped after 12 h.
Tool options are kept in <backup>/neosetup-settings.json, never in the user's own settings.
"""
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
