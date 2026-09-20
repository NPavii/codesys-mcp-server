# -*- coding: utf-8 -*-
# Generic bridge executor: reads task.json, executes one operation via the
# CODESYS ScriptEngine API, writes result.json. Run with:
#   CODESYS.exe --noUI --profile="..." --runscript=bridge.py
import json
import os
import re
import traceback
import time

try:
    _BRIDGE_HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _BRIDGE_HERE = os.getcwd()
BRIDGE_DIR = os.environ.get("CB_BRIDGE_DIR", _BRIDGE_HERE)

# Cached ScriptOnlineApplication sessions (keyed by application path);
# persists across tasks within the warm daemon session.
ONLINE = {}
# Allow parallel sessions: each session uses its own task/result pair via env
# vars CB_TASK / CB_RESULT (bridge.cmd inherits the caller's environment).
TASK_FILE = os.environ.get("CB_TASK", BRIDGE_DIR + r"\task.json")
RESULT_FILE = os.environ.get("CB_RESULT", BRIDGE_DIR + r"\result.json")

# Optional stage-progress log (opened by ops with long phases, e.g.
# validate_library); lets a client see where a long-running task currently is.
_VPROG = None

def vlog(msg):
    try:
        if _VPROG is not None:
            _VPROG.write("%.2f %s\n" % (time.time(), msg))
            _VPROG.flush()
    except Exception:
        pass

def _safe_name(obj):
    try:
        return obj.get_name()
    except Exception:
        return "?"

# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------

def obj_info(obj, slim=False):
    try:
        name = obj.get_name()
    except Exception:
        name = "<project root>"
    if slim:
        # minimal payload: name + type only (token diet for big trees)
        try:
            return {"name": name, "type": str(obj.type)}
        except Exception:
            return {"name": name, "type": "<no type>"}
    info = {
        "name": name,
        "type": str(obj.type),
        "is_folder": bool(getattr(obj, "is_folder", False)),
        "guid": str(obj.guid),
    }
    try:
        info["parent"] = obj.parent.get_name()
    except Exception:
        info["parent"] = None
    return info

def tree(obj, recursive=True, max_depth=None, depth=0, slim=False):
    nodes = []
    try:
        children = obj.get_children(recursive=False)
    except Exception:
        return nodes
    for ch in children:
        nodes.append(obj_info(ch, slim))
        if recursive and (max_depth is None or depth < max_depth):
            nodes.extend(tree(ch, True, max_depth, depth + 1, slim))
    return nodes

def mark_touched(state):
    """Record that the session made changes; run_task saves only when touched
    (or the project reports itself dirty)."""
    try:
        state["touched"] = True
    except Exception:
        pass

def find_obj(proj, path):
    """Find object by path like 'Folder1/Folder2/PouName'."""
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    node = proj
    for i, part in enumerate(parts):
        found = node.find(part, recursive=False)
        if found is not None and not hasattr(found, "get_name"):
            # find() returns a list of matches in this ScriptEngine version
            matches = list(found)
            found = matches[0] if matches else None
        if found is None:
            raise Exception("Object not found: " + "/".join(parts[:i + 1]))
        node = found
    return node

def get_project(proj_path):
    """Return already-open project with this path, or None."""
    try:
        p = projects.get_by_path(proj_path)
        if p is not None:
            return p
    except Exception:
        pass
    # fall back to manual scan over open projects
    try:
        norm = proj_path.replace("/", "\\").lower()
        for p in list(projects.all):
            try:
                if str(p.path).replace("/", "\\").lower() == norm:
                    return p
            except Exception:
                pass
    except Exception:
        pass
    return None

def require_container(obj, what="object"):
    for m in ("create_pou", "create_gvl", "create_dut"):
        if not hasattr(obj, m):
            raise Exception("Container API not available on this " + what)

# ---------------------------------------------------------------
# Operations
# ---------------------------------------------------------------

def op_list_open_projects(args):
    return {
        "projects": [str(p.path) for p in list(projects.all)],
        "primary": None if projects.primary is None else str(projects.primary.path),
    }

def op_open_project(args):
    path = args["path"]
    proj = get_project(path)
    if proj is None:
        if args.get("primary") is False:
            # warm/daemon mode: never occupy the primary slot, so tasks that
            # structurally edit another project (validate_library) can open it
            # as primary and avoid the headless create_property hang
            proj = projects.open(path, primary=False)
        else:
            proj = _open_project_auto(path)
    return {"path": str(proj.path), "dirty": bool(getattr(proj, "dirty", False))}

def op_create_project(args, state=None):
    """Create a new empty project, save it immediately and return its path."""
    proj = projects.create(args["name"], args["path"])
    proj.save()
    if state is not None:
        state["proj"] = proj
        mark_touched(state)
    return {"path": str(proj.path), "dirty": bool(getattr(proj, "dirty", False))}

def op_reflect(args, state=None):
    """Introspection helper: list methods of an object (default: projects / project)."""
    target = args.get("target", "projects")
    if target == "projects":
        return {"members": sorted(m for m in dir(projects) if not m.startswith("_"))}
    proj = None if state is None else state.get("proj")
    if proj is None:
        proj = _open_project_auto(args["project"])
        state["proj"] = proj
    return {"members": sorted(m for m in dir(proj) if not m.startswith("_"))}

def op_eval(args, state=None):
    """Evaluate a Python expression in the ScriptEngine context (diagnostics)."""
    ctx = {"projects": projects, "PouType": PouType}
    ctx.update({k: v for k, v in globals().items() if not k.startswith("__")})
    if state is not None and state.get("proj") is not None:
        ctx["proj"] = state["proj"]
    if args.get("project") and ctx.get("proj") is None:
        ctx["proj"] = _open_project_auto(args["project"])
        if state is not None:
            state["proj"] = ctx["proj"]
    result = eval(args["expr"], ctx)
    return {"result": repr(result)}

def op_batch(args, state):
    """Execute several operations in one ScriptEngine session.
    steps: [{"op": ..., "args": {...}}, ...]  — "project" is auto-filled."""
    proj_path = args["project"]
    proj = get_project(proj_path)
    if proj is None:
        proj = _open_project_auto(proj_path)
    state["proj"] = proj
    if args.get("dry_run"):
        return {"dry_run": True,
                "planned": [{"step": i, "op": s.get("op"),
                             "args_keys": sorted(s.get("args", {}).keys())}
                            for i, s in enumerate(args["steps"])]}
    results = []
    for i, step in enumerate(args["steps"]):
        op = step["op"]
        sa = dict(step.get("args", {}))
        sa.setdefault("project", proj_path)
        try:
            fn, needs_project = OPS[op]
            data = fn(sa, state) if needs_project else fn(sa)
            results.append({"step": i, "op": op, "ok": True, "data": data})
        except Exception as e:
            results.append({"step": i, "op": op, "ok": False, "error": str(e),
                            "traceback": traceback.format_exc()})
            if not args.get("continue_on_error", True):
                raise
    if state.get("touched") or bool(getattr(proj, "dirty", False)):
        proj.save()
    return {"results": results}

def op_add_device(args, state):
    """Add a PLC device to the project by name (optionally filter by version)."""
    proj = state["proj"]
    mark_touched(state)
    devs = list(device_repository.get_all_devices())
    matches = [d for d in devs if d.device_info.name == args["device_name"]]
    if args.get("version"):
        matches = [d for d in matches if args["version"] in str(d.device_id)]
    if not matches:
        raise Exception("Device not found: " + args["device_name"])
    dev = matches[-1]
    proj.add(args.get("name", args["device_name"]), dev.device_id, args.get("module", ""))
    return {"added": args["device_name"], "id": str(dev.device_id)}

def op_close_project(args, state):
    proj = state["proj"]
    if args.get("save", False):
        proj.save()
    proj.close()
    state["proj"] = None
    return {"closed": True}

def op_project_info(args, state):
    proj = state["proj"]
    info = {"path": str(proj.path), "dirty": bool(getattr(proj, "dirty", False))}
    try:
        apps = []
        app = proj.active_application
        if app is not None:
            info["active_application"] = app.get_name()
    except Exception:
        pass
    return info

def op_list_objects(args, state):
    proj = state["proj"]
    root = proj
    if args.get("path"):
        root = find_obj(proj, args["path"])
    recursive = bool(args.get("recursive", True))
    max_depth = args.get("max_depth")
    slim = bool(args.get("slim", False))
    return {"objects": tree(root, recursive, max_depth, 0, slim)}

def op_get_object(args, state):
    proj = state["proj"]
    obj = find_obj(proj, args["path"])
    out = obj_info(obj)
    try:
        if hasattr(obj, "has_textual_declaration") and obj.has_textual_declaration:
            out["declaration"] = obj.textual_declaration.text
    except Exception as e:
        out["declaration_error"] = str(e)
    try:
        if hasattr(obj, "has_textual_implementation") and obj.has_textual_implementation:
            out["implementation"] = obj.textual_implementation.text
    except Exception as e:
        out["implementation_error"] = str(e)
    try:
        if bool(args.get("slim", False)):
            out["children"] = [c.get_name() for c in obj.get_children(recursive=False)]
        else:
            out["children"] = [obj_info(c) for c in obj.get_children(recursive=False)]
    except Exception:
        pass
    return out

def op_dump_tree(args, state):
    """Recursively dump declaration+implementation of an object and all children."""
    proj = state["proj"]
    obj = find_obj(proj, args["path"])

    def dump(o):
        out = obj_info(o)
        try:
            if hasattr(o, "has_textual_declaration") and o.has_textual_declaration:
                out["declaration"] = o.textual_declaration.text
        except Exception as e:
            out["declaration_error"] = str(e)
        try:
            if hasattr(o, "has_textual_implementation") and o.has_textual_implementation:
                out["implementation"] = o.textual_implementation.text
        except Exception as e:
            out["implementation_error"] = str(e)
        try:
            kids = o.get_children(recursive=False)
        except Exception:
            kids = []
        out["children"] = [dump(c) for c in kids]
        return out

    return {"root": dump(obj)}

def op_create_interface(args, state):
    """Create an interface with optional full declaration text (incl. method signatures)."""
    proj = state["proj"]
    mark_touched(state)
    parent = proj if not args.get("folder") else find_obj(proj, args["folder"])
    obj = parent.create_interface(args["name"])
    if args.get("declaration"):
        set_text(obj.textual_declaration, args["declaration"])
    return {"created": obj_info(obj)}

def op_create_pou(args, state):
    proj = state["proj"]
    mark_touched(state)
    parent = proj if not args.get("folder") else find_obj(proj, args["folder"])
    require_container(parent, "parent folder")
    pt = args.get("pou_type", "function_block").lower()
    pou_type = {"program": PouType.Program, "function_block": PouType.FunctionBlock,
                "function": PouType.Function}.get(pt)
    if pou_type is None:
        raise Exception("Unknown pou_type: " + args.get("pou_type", ""))
    kwargs = {"name": args["name"], "type": pou_type}
    if args.get("return_type"):
        kwargs["return_type"] = args["return_type"]
    if args.get("base_type"):
        kwargs["base_type"] = args["base_type"]
    if args.get("interfaces"):
        kwargs["interfaces"] = args["interfaces"]
    obj = parent.create_pou(**kwargs)
    if args.get("declaration"):
        set_text(obj.textual_declaration, args["declaration"])
    if args.get("implementation"):
        set_text(obj.textual_implementation, args["implementation"])
    return {"created": obj_info(obj)}

def op_create_member(args, state):
    """Create action / method / property on a POU."""
    proj = state["proj"]
    mark_touched(state)
    obj = find_obj(proj, args["path"])
    kind = args["kind"].lower()
    if kind == "action":
        member = obj.create_action(args["name"])
    elif kind == "method":
        member = obj.create_method(args["name"], return_type=args.get("return_type"))
    elif kind == "property":
        member = obj.create_property(args["name"], return_type=args.get("return_type", "INT"))
    else:
        raise Exception("Unknown member kind: " + args["kind"])
    if args.get("implementation"):
        set_text(member.textual_implementation, args["implementation"])
    return {"created": obj_info(member)}

def op_create_dut(args, state):
    proj = state["proj"]
    mark_touched(state)
    parent = proj if not args.get("folder") else find_obj(proj, args["folder"])
    dt = {"structure": DutType.Structure, "enumeration": DutType.Enumeration,
          "alias": DutType.Alias, "union": DutType.Union}.get(args.get("dut_type", "structure").lower())
    obj = parent.create_dut(args["name"], type=dt, baseType=args.get("base_type"))
    if args.get("declaration"):
        set_text(obj.textual_declaration, args["declaration"])
    return {"created": obj_info(obj)}

def op_create_gvl(args, state):
    proj = state["proj"]
    mark_touched(state)
    parent = proj if not args.get("folder") else find_obj(proj, args["folder"])
    obj = parent.create_gvl(args["name"])
    if args.get("declaration"):
        set_text(obj.textual_declaration, args["declaration"])
    return {"created": obj_info(obj)}

def op_set_code(args, state):
    proj = state["proj"]
    mark_touched(state)
    obj = find_obj(proj, args["path"])
    part = args.get("part", "implementation")
    text = args["text"]
    if part == "declaration":
        set_text(obj.textual_declaration, text)
    else:
        set_text(obj.textual_implementation, text)
    return {"path": args["path"], "part": part, "length": len(text)}

def op_save_project(args, state):
    proj = state["proj"]
    proj.save()
    return {"saved": True, "path": str(proj.path)}

def op_ensure_task(args, state):
    """Create task configuration / cyclic task and attach a POU call."""
    proj = state["proj"]
    mark_touched(state)
    app = find_obj(proj, args["application"])
    task_name = args.get("task_name", "MainTask")
    pou_name = args.get("pou")
    tc = None
    for ch in app.get_children(recursive=False):
        try:
            if getattr(ch, "is_task_configuration", False):
                tc = ch
                break
        except Exception:
            pass
    created_tc = False
    if tc is None:
        tc = app.create_task_configuration()
        created_tc = True
    task = None
    for ch in tc.get_children(recursive=False):
        try:
            if getattr(ch, "is_task", False) and ch.get_name() == task_name:
                task = ch
                break
        except Exception:
            pass
    created_task = False
    if task is None:
        task = tc.create_task(task_name)
        created_task = True
    task.kind_of_task = KindOfTask.Cyclic
    if args.get("interval"):
        task.interval = args["interval"]
    if args.get("priority"):
        task.priority = args["priority"]
    added_pou = False
    if pou_name:
        names = []
        try:
            for p in task.pous:
                try:
                    names.append(p.name)
                except Exception:
                    try:
                        names.append(str(p))
                    except Exception:
                        pass
        except Exception:
            pass
        if pou_name not in names:
            task.pous.add(pou_name)
            added_pou = True
    return {
        "task_configuration_created": created_tc,
        "task_created": created_task,
        "task": task_name,
        "pou": pou_name,
        "pou_added": added_pou,
        "existing_pou_calls": names,
    }

def op_delete_object(args, state):
    """Delete an object from the project tree (only on explicit user request)."""
    if not args.get("confirm"):
        return {"error": "destructive op: pass confirm=true (requires explicit user approval)"}
    proj = state["proj"]
    mark_touched(state)
    obj = find_obj(proj, args["path"])
    parent = getattr(obj, "parent", None)
    errors = []
    for name, call in (
        ("parent.remove_child", lambda: parent.remove_child(obj)),
        ("parent.remove", lambda: parent.remove(obj)),
        ("obj.remove", lambda: obj.remove()),
        ("proj.remove_child", lambda: proj.remove_child(obj)),
    ):
        if call is None or (name.startswith("parent") and parent is None):
            continue
        try:
            call()
            return {"deleted": args["path"], "via": name}
        except Exception as e:
            errors.append(name + ": " + str(e))
    raise Exception("no delete API worked | " + " ; ".join(errors))

def _delete_obj(obj):
    parent = getattr(obj, "parent", None)
    last = None
    for name, call in (
        ("parent.remove_child", lambda: parent.remove_child(obj)),
        ("parent.remove", lambda: parent.remove(obj)),
        ("obj.remove", lambda: obj.remove()),
    ):
        if name.startswith("parent") and parent is None:
            continue
        try:
            call()
            return name
        except Exception as e:
            last = e
    raise Exception("delete failed: " + str(last))

def _decl_of(obj):
    try:
        if getattr(obj, "has_textual_declaration", False):
            return obj.textual_declaration.text
    except Exception:
        pass
    return ""

def _impl_of(obj):
    try:
        if getattr(obj, "has_textual_implementation", False):
            return obj.textual_implementation.text
    except Exception:
        pass
    return ""

T_DUT = "2db5746d-d284-4425-9f7f-2663a34b0ebc"
T_POU = "6f9dac99-8de1-4efc-8465-68ac443b7d08"
T_GVL = "ffbfa93a-b94d-45fc-a329-229860183b1d"
T_IFACE = "6654496c-404d-479a-aad2-8551054e5f1e"
T_PROP = "5a3b8626-d3e9-4f37-98b5-66420063d91e"
SKIP_NAMES = {"Project Settings", "Project Information", "__VisualizationStyle",
              "GlobalTextList", "Library Manager"}

def _strip_iface_wrapper(text):
    """Remove the INTERFACE header / END_INTERFACE footer from a full
    interface declaration, leaving only the member list (create_interface
    already adds its own wrapper)."""
    s = text.strip()
    s = re.sub(r"^\s*INTERFACE\b[^\n]*\n", "", s, flags=re.I)
    s = re.sub(r"\bEND_INTERFACE\s*;?\s*$", "", s, flags=re.I)
    return s

def _create_mirror(src, dst_parent, log):
    """Recreate the object `src` inside `dst_parent` (flattening folders)."""
    name = src.get_name()
    vlog("cm: " + name + " (parent " + _safe_name(dst_parent) + ")")
    if bool(getattr(src, "is_folder", False)):
        for ch in src.get_children(recursive=False):
            _create_mirror(ch, dst_parent, log)
        return
    t = str(src.type)
    decl = _decl_of(src)
    impl = _impl_of(src)
    if t == T_GVL:
        obj = dst_parent.create_gvl(name)
    elif t == T_IFACE:
        vlog("cm create_interface: " + name)
        obj = dst_parent.create_interface(name)
        vlog("cm create_interface ok: " + name)
    elif t == T_DUT:
        low = decl.lower()
        dt = DutType.Enumeration if ": (" in low else (DutType.Alias if " alias " in low.replace("\t", " ") else DutType.Structure)
        obj = dst_parent.create_dut(name, type=dt)
    elif t == T_POU:
        mkind = re.search(r"^\s*(function_block|program|function)\b", decl, re.I | re.M)
        kind = mkind.group(1).lower() if mkind else "function_block"
        pt = {"function_block": PouType.FunctionBlock,
              "program": PouType.Program,
              "function": PouType.Function}[kind]
        m = re.search(r"implements\s+([\w\.]+)", decl, re.I)
        ifaces = m.group(1) if m else None
        obj = None
        errs = []
        for call in (lambda: dst_parent.create_pou(name, pt, None, None, None, ifaces),
                     lambda: dst_parent.create_pou(name=name, type=pt),
                     lambda: dst_parent.create_pou(name, pt)):
            try:
                obj = call()
                break
            except Exception as e:
                errs.append(str(e))
                continue
        if obj is None:
            raise Exception("create_pou('%s') failed: %s" % (name, " | ".join(errs)))
    elif t == T_PROP:
        m = re.search(r":\s*([\w\.]+)", decl)
        rt = m.group(1) if m else "BOOL"
        existing = None
        try:
            f = dst_parent.find(name, recursive=False)
            if f is not None and not hasattr(f, "get_name"):
                fl = list(f)
                existing = fl[0] if fl else None
            else:
                existing = f
        except Exception:
            existing = None
        if existing is not None:
            obj = existing
        else:
            obj = None
            vlog("cm create_property: " + name + " rt=" + str(rt))
            for call in (lambda: dst_parent.create_property(name, return_type=rt),
                         lambda: dst_parent.create_property(name, rt, None),
                         lambda: dst_parent.create_property(name, rt)):
                try:
                    obj = call()
                    break
                except Exception:
                    continue
            vlog("cm create_property done: " + name + " obj=" + str(obj is not None))
            if obj is None:
                raise Exception("create_property failed for '%s' under '%s'" % (name, dst_parent.get_name()))
        if decl.strip():
            try:
                set_text(obj.textual_declaration, decl)
            except Exception:
                pass
    else:
        # method / property accessor: reuse an existing child (e.g. Get/Set
        # accessors are auto-created by create_property) or create a method
        existing = None
        try:
            f = dst_parent.find(name, recursive=False)
            if f is not None and not hasattr(f, "get_name"):
                fl = list(f)
                existing = fl[0] if fl else None
            else:
                existing = f
        except Exception:
            existing = None
        if existing is not None:
            obj = existing
        else:
            m = re.search(r":\s*([\w\.]+)", decl)
            try:
                obj = dst_parent.create_method(name, return_type=m.group(1) if m else None)
            except Exception:
                try:
                    obj = dst_parent.create_method(name)
                except Exception as e2:
                    raise Exception("create_method failed for member '%s' under '%s': %s"
                                    % (name, dst_parent.get_name(), e2))
        if decl.strip():
            set_text(obj.textual_declaration, decl)
    # Interfaces: NEVER set textual_declaration -- writing any text to an
    # interface's declaration corrupts the object (compiler then reports
    # "interface definition not found"). Members are child objects.
    if decl.strip() and t != T_IFACE:
        try:
            set_text(obj.textual_declaration, decl)
        except Exception:
            pass
    if impl.strip():
        set_text(obj.textual_implementation, impl)
    try:
        kids = src.get_children(recursive=False)
    except Exception:
        kids = []
    for ch in kids:
        _create_mirror(ch, obj, log)
    log.append("copied " + name)

def _fingerprint(obj):
    """Recursive content signature of an object tree (names, types, texts).
    Children are compared as an order-insensitive multiset: CODESYS reorders
    members (e.g. property accessors) on load/save, and order differences
    must not force a needless (and risky) mirror recreation."""
    try:
        nm = obj.get_name()
    except Exception:
        nm = "?"
    try:
        t = str(obj.type)
    except Exception:
        t = ""
    parts = [nm, t, _decl_of(obj), _impl_of(obj)]
    kid_sigs = []
    try:
        kids = obj.get_children(recursive=False)
    except Exception:
        kids = []
    for ch in kids:
        kid_sigs.append(_fingerprint(ch))
    kid_sigs.sort()
    parts.extend(kid_sigs)
    return "\x00".join(parts)

def _manifest_path(tgt_path):
    import hashlib
    h = hashlib.md5(tgt_path.replace("/", "\\").lower().encode("utf-8")).hexdigest()[:12]
    return os.path.join(BRIDGE_DIR, "mirror_manifest_" + h + ".json")

def _load_manifest(tgt_path):
    try:
        with open(_manifest_path(tgt_path), "r") as f:
            return set(json.load(f).get("names", []))
    except Exception:
        return set()

def _save_manifest(tgt_path, names):
    try:
        with open(_manifest_path(tgt_path), "w") as f:
            json.dump({"names": sorted(names)}, f)
    except Exception:
        pass

def _validate_library_standalone(lib_path):
    """Pool-check a .library on its own, no mirror project involved.
    Used when args.project is absent or the mirror file no longer exists."""
    lib = get_project(lib_path)
    was_open = lib is not None
    if lib is not None:
        try:
            prim = projects.primary
            is_prim = prim is not None and str(prim.path) == str(lib.path)
        except Exception:
            is_prim = False
        if not is_prim:
            try:
                lib.close()
            except Exception:
                pass
            lib = None
    opened_here = False
    if lib is None:
        lib = projects.open(lib_path, primary=True)
        opened_here = True
    try:
        entries = _pool_check(lib)
    finally:
        _restore_library(lib, opened_here, was_open, lib_path)
    errors = [e for e in entries if e["severity"] in ("fatal", "error")]
    warns = [e for e in entries if e["severity"] == "warning"]
    return {"build_result": len(errors) == 0, "warnings": len(warns),
            "unchanged": False, "build_skipped": False, "kept": 0,
            "changed": [], "removed": [], "removal_errors": [],
            "messages": (errors + warns)[:40], "copied": 0,
            "error_count": len(errors), "check": "pool_objects",
            "mirror": None}


def op_validate_library(args, state=None):
    """Sync objects from a .library into a device project and compile it.
    Incremental: unchanged mirrors are kept, only changed/new objects are
    recreated, stale ones (per sidecar manifest) are removed; if nothing
    changed the build is skipped (build_skipped=true).
    args: library (path), application (path in target); the target project
    comes from args.project."""
    # Never re-open a project that is already open in this session (a second
    # open of the same file deadlocks on the file lock); close only what we
    # opened ourselves.
    lib_path = args["library"]
    tgt_path = (args.get("project") or "").strip()
    if state is None:
        state = {"proj": None}
    if tgt_path and not os.path.exists(tgt_path):
        # mirror project is gone: validate the library standalone instead
        tgt_path = ""
    if not tgt_path:
        return _validate_library_standalone(lib_path)
    # Never re-open a project that is already open in this session (a second
    # open of the same file deadlocks on the file lock); close only what we
    # opened ourselves.
    lib = get_project(lib_path)
    lib_was_open = lib is not None
    lib_was_primary = False
    if lib is not None:
        try:
            prim = projects.primary
            lib_was_primary = prim is not None and str(prim.path) == str(lib.path)
        except Exception:
            lib_was_primary = False
    if lib is not None and not lib_was_primary:
        try:
            lib.close()
        except Exception:
            pass
        lib = None
    tgt = state.get("proj")
    if tgt is None:
        tgt = get_project(tgt_path)
        if tgt is None:
            tgt = projects.open(tgt_path, primary=False)
        state["proj"] = tgt
    # demote the mirror if it currently holds the primary slot
    try:
        _prim = projects.primary
        _tgt_path = str(getattr(tgt, "path", ""))
        if _prim is not None and str(getattr(_prim, "path", "")) == _tgt_path:
            try:
                tgt.close()
            except Exception:
                pass
            tgt = projects.open(_tgt_path, primary=False)
            state["proj"] = tgt
    except Exception:
        pass
    lib_opened_here = False
    if lib is None:
        lib = projects.open(lib_path, primary=True)
        lib_opened_here = True
    log = []
    # stage progress (visible while a long validation runs; py2-safe)
    global _VPROG
    try:
        _VPROG = open(os.path.join(BRIDGE_DIR, "vprogress.log"), "w")
    except Exception:
        _VPROG = None
    _v = vlog
    _v("start validate")
    try:
        tgt_path = str(tgt.path)
    except Exception:
        tgt_path = ""
    _v("lib resolved")
    # collect source objects (skip infrastructure)
    src_objs = []
    for ch in lib.get_children(recursive=False):
        try:
            if ch.get_name() in SKIP_NAMES:
                continue
            src_objs.append(ch)
        except Exception:
            pass
    flat = []
    def flatten(o):
        if bool(getattr(o, "is_folder", False)):
            for c in o.get_children(recursive=False):
                flatten(c)
        else:
            flat.append(o)
    for ch in src_objs:
        flatten(ch)
    lib_names = set()
    for ch in flat:
        try:
            lib_names.add(ch.get_name())
        except Exception:
            pass
    _v("src flat: %d" % len(flat))

    # existing mirror objects in target by name
    existing = {}
    for ch in tgt.get_children(recursive=False):
        try:
            existing[ch.get_name()] = ch
        except Exception:
            pass
    _v("tgt children: %d" % len(existing))

    changed = []
    kept = []
    removed = []
    removal_errors = []
    to_replace = []
    src_by_name = {}
    for src in flat:
        nm = src.get_name()
        src_by_name[nm] = src
        fp = _fingerprint(src)
        ex = existing.get(nm)
        if ex is not None:
            try:
                if _fingerprint(ex) == fp:
                    kept.append(nm)
                    continue
            except Exception:
                pass
            to_replace.append(nm)
        changed.append(nm)

    # Headless CODESYS hangs when members are added to an interface while
    # implementor FBs exist (member propagation pops a modal). Mirror the old
    # full-wipe invariant: recreate an interface's implementors together with
    # the interface itself.
    iface_changed = set()
    for nm in to_replace:
        src = src_by_name.get(nm)
        if src is not None and str(src.type) == T_IFACE:
            iface_changed.add(nm)
    if iface_changed:
        for src in flat:
            if str(src.type) != T_POU:
                continue
            decl = _decl_of(src)
            m = re.search(r"implements\s+([\w\.]+)", decl, re.I)
            refs_iface = any(re.search(r"\b%s\b" % re.escape(inm), decl)
                             for inm in iface_changed)
            if (m and m.group(1) in iface_changed) or refs_iface:
                nm = src.get_name()
                if nm in kept:
                    kept.remove(nm)
                if nm not in changed:
                    changed.append(nm)
                    to_replace.append(nm)
                    _v("implementor/ref of changed interface also replaced: " + nm)

    # Clear PLC_PRG before deleting anything: it references every mirror FB,
    # and recreating members while dangling references exist can make headless
    # CODESYS pop a modal dialog and block forever.
    if changed or removal_errors:
        try:
            plc = tgt.find("PLC_PRG", recursive=True)
            plc = list(plc)[0] if plc is not None and not hasattr(plc, "get_name") else plc
        except Exception:
            plc = None
        if plc is not None:
            try:
                set_text(plc.textual_declaration, "PROGRAM PLC_PRG\nVAR\nEND_VAR\n")
                set_text(plc.textual_implementation, "")
                _v("plc cleared")
            except Exception as e:
                _v("plc clear failed: " + str(e))

    for nm in to_replace:
        ex = existing.get(nm)
        try:
            _delete_obj(ex)
            removed.append(nm + " (replaced)")
        except Exception as e:
            removal_errors.append(nm + ": " + str(e))
    _v("fp done: kept=%d changed=%d" % (len(kept), len(changed)))
    _v("removed: " + "; ".join(removed))
    _v("removal_errors: " + "; ".join(removal_errors))

    # stale mirrors: names synced last time but gone from the library now
    prev_names = _load_manifest(tgt_path)
    for nm in prev_names:
        if nm not in lib_names and existing.get(nm) is not None:
            try:
                _delete_obj(existing[nm])
                removed.append(nm + " (stale)")
            except Exception as e:
                removal_errors.append(nm + ": " + str(e))
    _v("stale done")

    # recreate changed objects: pass 1 = non-POU, pass 2 = POU (dependencies first)
    changed_set = set(changed)
    _v("changed: " + ", ".join(sorted(changed_set)))
    for ch in [c for c in flat if c.get_name() in changed_set and str(c.type) != T_POU] + \
              [c for c in flat if c.get_name() in changed_set and str(c.type) == T_POU]:
        _v("mirror -> " + ch.get_name())
        _create_mirror(ch, tgt, log)
        _v("mirror ok " + ch.get_name())
    _v("mirrors done: %d" % len(log))
    mark_touched(state)

    if not changed and not removal_errors:
        # nothing changed since the last validated sync: keep everything as is
        _save_manifest(tgt_path, lib_names)
        _restore_library(lib, lib_opened_here, lib_was_open, lib_path)
        return {"build_result": True, "unchanged": True, "build_skipped": True,
                "kept": len(kept), "changed": [], "removed": removed,
                "removal_errors": [], "messages": [], "copied": 0,
                "check": "pool_objects_skipped"}
    tgt.save()
    # Compile check on the LIBRARY ITSELF: check_all_pool_objects() is the
    # script API of the GUI "Build > Check all Pool Objects" (F11 for
    # .library) — the same library context the IDE reports 0/0 on. The
    # mirror application build is deliberately NOT used here: it pulls in
    # visualization/device noise (hundreds of "Относительная позиция") that
    # does not exist in the library context.
    _v("pool-checking library")
    entries = _pool_check(lib)
    errors = [e for e in entries if e["severity"] in ("fatal", "error")]
    warns = [e for e in entries if e["severity"] == "warning"]
    build_ok = len(errors) == 0
    _v("pool-check done: errors=%d warnings=%d" % (len(errors), len(warns)))
    _restore_library(lib, lib_opened_here, lib_was_open, lib_path)
    _save_manifest(tgt_path, lib_names)
    return {"build_result": build_ok, "warnings": len(warns),
            "unchanged": False, "build_skipped": False,
            "kept": len(kept), "changed": changed, "removed": removed,
            "removal_errors": removal_errors,
            "messages": (errors + warns)[:40], "copied": len(log),
            "error_count": len(errors), "check": "pool_objects"}

_BUILD_SUMMARY_RE = re.compile(r"--\s*(\d+)\s*[^,;]*[,;]\s*(\d+)")

def _pool_check(lib):
    """Run check_all_pool_objects() on a library and return error/warning
    entries from every message category. Severity is compared as a decoded
    string (the Severity enum is not [Flags]-safe across versions)."""
    cats = []
    try:
        cats = list(system.get_message_categories())
    except Exception:
        pass
    for c in cats:
        try:
            system.clear_messages(c)
        except Exception:
            pass
    lib.check_all_pool_objects()
    out = []
    for c in cats:
        try:
            objs = system.get_message_objects(c)
        except Exception:
            continue
        if objs is None:
            continue
        for m in objs:
            sev = str(getattr(m, "severity", "")).lower()
            if sev not in ("fatalerror", "error", "warning"):
                continue
            entry = {"severity": "fatal" if sev == "fatalerror" else sev,
                     "text": str(getattr(m, "text", str(m)))[:300]}
            code = (str(getattr(m, "prefix", "")) + str(getattr(m, "number", ""))).strip()
            if code:
                entry["code"] = code
            try:
                o = getattr(m, "object", None)
                if o is not None:
                    entry["object"] = str(o.get_name())
            except Exception:
                pass
            try:
                entry["position"] = str(m.position_text)[:120]
            except Exception:
                pass
            out.append(entry)
    return out

def _restore_library(lib, opened_here, was_open, lib_path):
    """After validation: never leave the library as a foreign PRIMARY project
    in a warm session (a warm primary hangs structural edits elsewhere).
    If it was open (warm) before, close and reopen it as non-primary."""
    if not opened_here:
        return
    try:
        lib.close()
    except Exception:
        pass
    if was_open:
        try:
            projects.open(lib_path, primary=False)
        except Exception:
            pass

def _parse_build_summary(msgs):
    """Extract (ok, warning_count) from the compiler summary message.
    CODESYS build summary is localized ("... -- 501 errors, 0 warnings" /
    "... -- 0 ошибок, 30 предупреждений"), so parse only the digits after '--'.
    Falls back to (False, None) when no summary line is found."""
    for m in reversed(msgs):
        m = str(m)
        if "--" in m:
            mm = _BUILD_SUMMARY_RE.search(m)
            if mm:
                return (int(mm.group(1)) == 0), int(mm.group(2))
    return False, None

def _find_lib_manager(proj, args):
    """Locate the Library Manager object of the active application."""
    app = None
    if args.get("application"):
        app = find_obj(proj, args["application"])
    else:
        try:
            app = proj.active_application
        except Exception:
            pass
    if app is None:
        raise Exception("No active application; pass args.application")
    lm = app.find("Library Manager", recursive=False)
    if lm is not None and not hasattr(lm, "get_name"):
        matches = list(lm)
        lm = matches[0] if matches else None
    if lm is None:
        for ch in app.get_children(recursive=False):
            try:
                if "library" in ch.get_name().lower() and "manager" in ch.get_name().lower():
                    lm = ch
                    break
            except Exception:
                pass
    if lm is None:
        raise Exception("Library Manager not found under application")
    return lm


def op_lib_install(args, state=None):
    """Install a .library file into a library repository.
    args: path (str) — path to .library; repository (str, optional) — repo
    name (default: first repo); overwrite (bool, optional)."""
    path = args["path"]
    repo = None
    if args.get("repository"):
        for r in librarymanager.repositories:
            try:
                if r.name == args["repository"]:
                    repo = r
                    break
            except Exception:
                pass
        if repo is None:
            raise Exception("Repository not found: " + args["repository"])
    if repo is None:
        repos = librarymanager.repositories
        if not repos:
            raise Exception("No library repositories configured")
        repo = repos[0]
    lib = librarymanager.install_library(path, repo, bool(args.get("overwrite", False)))
    return {"name": str(getattr(lib, "name", lib)),
            "version": str(getattr(lib, "version", "?")),
            "company": str(getattr(lib, "company", "?"))}


def op_lib_add(args, state):
    """Add a library reference to the project's Library Manager.
    args: library (str) — display name, e.g. 'Kimi_Lib, 0.3.1 (Kimi)' or bare
    'Kimi_Lib'; application (str, optional)."""
    proj = state["proj"]
    name = args["library"]
    found = None
    try:
        found = librarymanager.find_library(name)
    except Exception:
        found = None
    if found is None:
        # fall back: scan all libraries for a matching display name prefix
        for cand in librarymanager.get_all_libraries(False):
            dn = str(cand)
            if dn.startswith(name + ",") or dn == name:
                found = (cand, None)
                break
    if found is None:
        raise Exception("Library not found in any repository: " + name)
    managed = found[0]
    lm = _find_lib_manager(proj, args)
    before = set(lm.get_libraries())
    if name in before or str(getattr(managed, "name", "")) in before:
        return {"added": False, "already": True,
                "libraries": sorted(before)}
    lm.add_library(managed)
    mark_touched(state)
    after = set(lm.get_libraries())
    return {"added": True, "libraries": sorted(after)}


def op_lib_remove(args, state):
    """Remove a library reference from the project's Library Manager."""
    proj = state["proj"]
    lm = _find_lib_manager(proj, args)
    lm.remove_library(args["library"])
    mark_touched(state)
    return {"removed": args["library"], "libraries": sorted(set(lm.get_libraries()))}


def op_lib_list(args, state):
    """List libraries referenced by the project's Library Manager."""
    proj = state["proj"]
    lm = _find_lib_manager(proj, args)
    refs = []
    try:
        for r in lm.references:
            refs.append({"name": r.name, "namespace": getattr(r, "namespace", ""),
                         "system": bool(getattr(r, "system_library", False))})
    except Exception:
        pass
    return {"libraries": sorted(set(lm.get_libraries())), "references": refs}


def op_add_file(args, state):
    """Attach an external file (e.g. MD documentation) to a project container.
    args: path (optional, default project root), file (absolute disk path),
    name (optional), mode: link|embed|link_and_embed (default link).
    Linked files stay on disk (single source of truth); embedded are stored
    inside the project."""
    import os as _os
    proj = state["proj"]
    file_path = args["file"]
    if not _os.path.isfile(file_path):
        return {"error": "file not found: " + str(file_path)}
    container = proj if not args.get("path") else find_obj(proj, args["path"])
    mode_map = {"link": 0, "link_and_embed": 1, "embed": 2}
    mode = mode_map.get(str(args.get("mode", "link")).lower(), 0)
    kw = {}
    if args.get("name"):
        kw["name"] = args["name"]
    fobj = container.create_external_file_object(file_path, reference_mode=mode, **kw)
    mark_touched(state)
    try:
        nm = fobj.get_name()
    except Exception:
        nm = args.get("name") or _os.path.basename(file_path)
    return {"added": str(nm), "mode": args.get("mode", "link"),
            "container": args.get("path", "<project root>")}


def op_build(args, state):
    """Compile the application and return build messages.
    app.build() requires the PRIMARY project; a warm secondary is promoted
    for the duration of the build and demoted back afterwards."""
    proj = state["proj"]
    proj_path = str(getattr(proj, "path", ""))
    promoted = False
    try:
        prim = projects.primary
        if prim is None or str(getattr(prim, "path", "")) != proj_path:
            try:
                proj.close()
            except Exception:
                pass
            proj = projects.open(proj_path, primary=True)
            state["proj"] = proj
            promoted = True
    except Exception:
        pass
    app = find_obj(proj, args["application"])
    res = app.build()
    msgs = []
    try:
        for m in system.get_messages("97f48d64-a2a3-4856-b640-75c046e37ea9"):
            msgs.append(str(m))
    except Exception:
        pass
    build_ok, warn_count = _parse_build_summary(msgs)
    if promoted:
        try:
            proj.close()
        except Exception:
            pass
        try:
            state["proj"] = projects.open(proj_path, primary=False)
        except Exception:
            pass
    return {"build_result": build_ok, "warnings": warn_count, "messages": msgs[-60:]}

# ---------------------------------------------------------------


# ---------------------------------------------------------------------------
# Online cycle (read/write/force on a running device; ScriptOnline API)
# ---------------------------------------------------------------------------

def _online_get(args):
    oa = ONLINE.get(args["application"])
    if oa is None:
        raise Exception("not logged in: call online_login for " + args["application"])
    return oa


def op_online_login(args, state):
    """Login to the device application. Downloads if the runtime app
    differs (OnlineChangeOption.Try). Session is cached for later ops.
    Online API requires the PRIMARY project: warm-secondary is promoted
    for the login; demoted back unless keep_primary=true (online sessions
    may survive demotion; if later ops fail, re-login with keep_primary)."""
    proj = state["proj"]
    proj_path = str(getattr(proj, "path", ""))
    promoted = False
    prim = projects.primary
    if prim is None or str(getattr(prim, "path", "")) != proj_path:
        try:
            proj.close()
        except Exception:
            pass
        proj = projects.open(proj_path, primary=True)
        state["proj"] = proj
        promoted = True
    app = find_obj(proj, args["application"])
    old = ONLINE.pop(args["application"], None)
    if old is not None:
        try:
            old.logout()
        except Exception:
            pass
        try:
            old.Dispose()
        except Exception:
            pass
    oa = online.create_online_application(app)
    try:
        oa.login(0, False)  # 0 = OnlineChangeOption.Never: full download if app differs (enum member is not reachable via IronPython; only 0 converts)
    except Exception:
        try:
            oa.Dispose()
        except Exception:
            pass
        raise
    ONLINE[args["application"]] = oa
    if promoted and not args.get("keep_primary"):
        try:
            proj.close()
        except Exception:
            pass
        try:
            state["proj"] = projects.open(proj_path, primary=False)
        except Exception:
            pass
    return {"logged_in": bool(oa.is_logged_in),
            "application_state": str(oa.application_state),
            "operation_state": str(oa.operation_state),
            "keep_primary": bool(args.get("keep_primary", False))}


def op_online_logout(args, state):
    oa = ONLINE.pop(args["application"], None)
    if oa is None:
        return {"logged_out": False, "note": "no cached session"}
    try:
        oa.logout()
    finally:
        try:
            oa.Dispose()
        except Exception:
            pass
    return {"logged_out": True}


def op_online_status(args, state=None):
    out = []
    for key in sorted(ONLINE.keys()):
        oa = ONLINE[key]
        try:
            out.append({"application": key,
                        "logged_in": bool(oa.is_logged_in),
                        "application_state": str(oa.application_state),
                        "operation_state": str(oa.operation_state),
                        "forced_expressions": [str(e) for e in oa.get_forced_expressions()]})
        except Exception as e:
            out.append({"application": key, "error": str(e)})
    return {"sessions": out}


def op_online_read(args, state):
    oa = _online_get(args)
    exprs = list(args["expressions"])
    vals = oa.read_values(tuple(exprs))
    return {"values": dict((e, str(v)) for e, v in zip(exprs, list(vals)))}


def op_online_write(args, state):
    oa = _online_get(args)
    vals = args["values"]
    for expr in vals:
        oa.set_prepared_value(expr, str(vals[expr]))
    oa.write_prepared_values()
    return {"written": sorted(vals.keys())}


def op_online_force(args, state):
    oa = _online_get(args)
    vals = args["values"]
    for expr in vals:
        oa.set_prepared_value(expr, str(vals[expr]))
    oa.force_prepared_values()
    return {"forced": sorted(vals.keys())}


def op_online_unforce(args, state):
    oa = _online_get(args)
    exprs = args.get("expressions")
    if exprs:
        for e in exprs:
            oa.set_unforce_value(e, False)
        oa.write_prepared_values()
        return {"unforced": list(exprs)}
    oa.unforce_all_values()
    return {"unforced": "all"}


# ---------------------------------------------------------------------------
# Native (non-textual) objects: Alarm Configuration/Groups/Templates,
# UnitConversion and friends. XML round-trip via IArchivable.
# import_native ADDS a copy -- check for duplicates after import.
# ---------------------------------------------------------------------------

def op_native_export(args, state):
    proj = state["proj"]
    obj = find_obj(proj, args["path"])
    obj.export_native(args["file"])
    return {"exported": args["file"], "object": args["path"]}


def op_native_import(args, state):
    proj = state["proj"]
    container = proj if not args.get("parent") else find_obj(proj, args["parent"])
    container.import_native(args["file"])
    mark_touched(state)
    return {"imported": args["file"],
            "note": "import_native ADDS a copy next to existing objects"}


# ---------------------------------------------------------------------------
# Snapshots: file-level backup of .project/.library before risky edits.
# ---------------------------------------------------------------------------

def _snapshots_dir():
    return os.path.join(BRIDGE_DIR, "snapshots")


def op_snapshot(args, state=None):
    import shutil
    import time
    src = args["project"]
    if not os.path.isfile(src):
        return {"error": "file not found: " + str(src)}
    base = os.path.splitext(os.path.basename(src))[0]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst_dir = os.path.join(_snapshots_dir(), base + "-" + stamp)
    if not os.path.isdir(dst_dir):
        os.makedirs(dst_dir)
    dst = os.path.join(dst_dir, os.path.basename(src))
    shutil.copy2(src, dst)
    return {"snapshot": dst, "source": src}


def op_snapshot_list(args, state=None):
    import time
    out = []
    sdir = _snapshots_dir()
    if os.path.isdir(sdir):
        for name in sorted(os.listdir(sdir), reverse=True):
            pth = os.path.join(sdir, name)
            if os.path.isdir(pth):
                out.append({"name": name,
                            "created": time.strftime("%Y-%m-%d %H:%M:%S",
                                                     time.localtime(os.path.getmtime(pth)))})
    return {"snapshots": out}


def op_snapshot_restore(args, state=None):
    import shutil
    if not args.get("confirm"):
        return {"error": "destructive: pass confirm=true"}
    sdir = os.path.join(_snapshots_dir(), args["snapshot"])
    if not os.path.isdir(sdir):
        return {"error": "snapshot not found: " + args["snapshot"]}
    files = [f for f in os.listdir(sdir)
             if f.lower().endswith(".project") or f.lower().endswith(".library")]
    if not files:
        return {"error": "no .project/.library file in snapshot"}
    proj = get_project(args["project"])
    if proj is not None:
        try:
            proj.close()
        except Exception:
            pass
    shutil.copy2(os.path.join(sdir, files[0]), args["project"])
    return {"restored": args["project"], "from": args["snapshot"]}


def op_tree(args, state):
    """Recursive tree of object names/types.
    args: path (optional, default root), max_depth (default 6)."""
    proj = state["proj"]
    root = proj if not args.get("path") else find_obj(proj, args["path"])
    max_depth = int(args.get("max_depth", 6))
    lines = []

    def walk(o, d):
        if d > max_depth:
            return
        try:
            nm = o.get_name()
        except Exception:
            nm = "?"
        try:
            tp = str(o.type)
        except Exception:
            tp = "<no type>"
        lines.append("  " * d + nm + " ~ " + tp)
        try:
            kids = o.get_children(recursive=False)
        except Exception:
            kids = []
        for c in kids:
            walk(c, d + 1)

    walk(root, 0)
    return {"tree": lines}


def set_text(doc, text):
    n = doc.length
    if n:
        doc.remove(0, n)
    doc.append(text)

OPS = {
    "list_open_projects": (op_list_open_projects, False),
    "open_project": (op_open_project, False),
    "create_project": (op_create_project, False),
    "reflect": (op_reflect, False),
    "eval": (op_eval, True),
    "batch": (op_batch, True),
    "delete_object": (op_delete_object, True),
    "validate_library": (op_validate_library, False),
    "add_device": (op_add_device, True),
    "close_project": (op_close_project, True),
    "project_info": (op_project_info, True),
    "list_objects": (op_list_objects, True),
    "get_object": (op_get_object, True),
    "dump_tree": (op_dump_tree, True),
    "tree": (op_tree, True),
    "create_pou": (op_create_pou, True),
    "create_interface": (op_create_interface, True),
    "create_member": (op_create_member, True),
    "create_dut": (op_create_dut, True),
    "create_gvl": (op_create_gvl, True),
    "set_code": (op_set_code, True),
    "save_project": (op_save_project, True),
    "ensure_task": (op_ensure_task, True),
    "build": (op_build, True),
    "lib_install": (op_lib_install, False),
    "lib_add": (op_lib_add, True),
    "lib_remove": (op_lib_remove, True),
    "lib_list": (op_lib_list, True),
    "add_file": (op_add_file, True),
    "online_login": (op_online_login, True),
    "online_logout": (op_online_logout, True),
    "online_status": (op_online_status, False),
    "online_read": (op_online_read, True),
    "online_write": (op_online_write, True),
    "online_force": (op_online_force, True),
    "online_unforce": (op_online_unforce, True),
    "native_export": (op_native_export, True),
    "native_import": (op_native_import, True),
    "snapshot": (op_snapshot, False),
    "snapshot_list": (op_snapshot_list, False),
    "snapshot_restore": (op_snapshot_restore, False),
}

def _open_project_auto(proj_path):
    """Open a project; tolerate a warm session where another project is
    already primary (only one primary project per CODESYS session)."""
    proj = get_project(proj_path)
    if proj is not None:
        return proj
    try:
        return projects.open(proj_path, primary=True)
    except Exception:
        return projects.open(proj_path, primary=False)

def run_task(task, state=None):
    """Execute one task dict in this ScriptEngine session. Returns the result
    dict. Does NOT read/write any files. When args.keep_open is true the
    project is left open in the session (warm-daemon mode); otherwise it is
    saved (only if touched or dirty) and closed as before."""
    result = {"ok": False, "error": None, "data": None}
    try:
        op = task.get("op")
        args = task.get("args", {})
        if op not in OPS:
            raise Exception("Unknown operation: %r" % op)
        fn, needs_project = OPS[op]
        if state is None:
            state = {"proj": None}
        state["touched"] = False
        if needs_project:
            proj_path = args.get("project")
            if not proj_path:
                raise Exception("Missing required arg: project (path to .project file)")
            proj = get_project(proj_path)
            if proj is None:
                proj = _open_project_auto(proj_path)
            state["proj"] = proj
        result["data"] = fn(args, state) if needs_project else fn(args)
        if needs_project and state.get("proj") is not None:
            # persist changes unless told otherwise; skip the save entirely on
            # read-only tasks (touched flag + project dirty state both clean)
            if not args.get("no_save", False):
                if state.get("touched") or bool(getattr(state["proj"], "dirty", False)):
                    state["proj"].save()
            if not args.get("keep_open", False):
                state["proj"].close()
                state["proj"] = None
        result["ok"] = True
    except Exception:
        result["error"] = traceback.format_exc()
    return result


def main():
    result = {"ok": False, "error": None, "data": None}
    try:
        with open(TASK_FILE, "r") as f:
            task = json.load(f)
        result = run_task(task)
    except Exception:
        result["error"] = traceback.format_exc()

    with open(RESULT_FILE, "w") as f:
        json.dump(result, f)


if os.environ.get("CB_DAEMON") != "1":
    main()
