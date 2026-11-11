"""neosetup -- the KFG Pick & Place Tool as a UI-free library, for the machine's setup wizard.

    from neosetup import Project
    p = Project()                       # one headless instance of the tool
    p.import_pnp("board.csv")
    p.build_machine()
    p.write_machine_file("board-machine.csv")

See docs/SETUP_WIZARD.md. The tool itself is vendored unchanged in engine/ (sync_engine.py).
"""
from .project import Project, ToolError, SORT_MODELS, FEEDER_FIELDS, COMP_FIELDS

__all__ = ["Project", "ToolError", "SORT_MODELS", "FEEDER_FIELDS", "COMP_FIELDS"]
