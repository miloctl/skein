"""No agent tool reaches a settings or tuning setter (app/tools/__init__.py
says why): an agent that can move them can be talked into moving them."""

import ast
import pathlib

APP = pathlib.Path(__file__).resolve().parent.parent / "app"


def test_no_tool_module_names_a_settings_or_tuning_setter():
    setters = {
        fn.name
        for module in ("settings", "tuning")
        for fn in ast.parse((APP / "services" / f"{module}.py").read_text()).body
        if isinstance(fn, ast.FunctionDef) and fn.name.startswith("set_")
    }
    assert {"set_model_pick", "set_tunable"} <= setters  # the scan found the setters
    reached = []
    for path in sorted((APP / "tools").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            name = (
                getattr(node, "attr", None)  # settings.set_model_pick(...)
                or getattr(node, "name", None)  # from ..services.settings import set_...
                or getattr(node, "id", None)
            )
            if name in setters:
                reached.append(f"{path.name}:{node.lineno}:{name}")
    assert reached == []
