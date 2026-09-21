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
            _finish_load(proj)
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
    # SP21 Patch 1 finding: objects of the task tree (tasks themselves and
    # the Task Configuration) cannot be removed via the ScriptEngine API -
    # remove() throws ArgumentNullException/NullReferenceException, so fail
    # with an honest message instead of four doomed attempts.
    try:
        if bool(getattr(obj, "is_task", False)) or \
           bool(getattr(obj, "is_task_configuration", False)):
            return {"error": "deleting task-tree objects is not supported by "
                             "the CODESYS ScriptEngine on SP21 Patch 1 "
                             "(remove() throws). Delete '%s' in the CODESYS "
                             "GUI." % args["path"]}
    except Exception:
        pass
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

def _swap_primary_for(desired_path):
    """SP21 daemon mode: only ONE project may be primary. If another project
    currently holds the primary slot, save+close it and return its path so
    the caller can reopen desired_path as primary and restore the displaced
    project afterwards (_reopen_primary). Returns None when the slot is free
    or already held by desired_path."""
    try:
        prim = projects.primary
    except Exception:
        return None
    if prim is None:
        return None
    try:
        prim_path = str(getattr(prim, "path", ""))
        if not prim_path or prim_path.lower() == str(desired_path).lower():
            return None
        try:
            if bool(getattr(prim, "dirty", False)):
                prim.save()
        except Exception:
            pass
        try:
            prim.close()
        except Exception:
            pass
        return prim_path
    except Exception:
        return None


def _reopen_primary(proj_path):
    """Restore a project as PRIMARY (close+reopen if already open)."""
    if not proj_path:
        return
    try:
        p = get_project(proj_path)
        if p is not None:
            try:
                p.close()
            except Exception:
                pass
        p = projects.open(proj_path, primary=True)
        _finish_load(p)
    except Exception:
        try:
            p = projects.open(proj_path, primary=False)
            _finish_load(p)
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
    displaced = None
    if lib is None:
        displaced = _swap_primary_for(lib_path)
        lib = projects.open(lib_path, primary=True)
        _finish_load(lib)
        opened_here = True
    try:
        entries = _pool_check(lib)
    finally:
        _restore_library(lib, opened_here, was_open, lib_path)
        if displaced:
            _reopen_primary(displaced)
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
            _finish_load(tgt)
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
            _finish_load(tgt)
            state["proj"] = tgt
    except Exception:
        pass
    lib_opened_here = False
    _displaced = None
    if lib is None:
        _displaced = _swap_primary_for(lib_path)
        lib = projects.open(lib_path, primary=True)
        _finish_load(lib)
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
        if _displaced:
            _reopen_primary(_displaced)
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
    if _displaced:
        _reopen_primary(_displaced)
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
            op = projects.open(lib_path, primary=False)
            _finish_load(op)
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
            _finish_load(proj)
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
            _finish_load(state["proj"])
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
        _finish_load(proj)
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
            _finish_load(state["proj"])
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
    # SP21 Patch 1: doc.remove(0, n)+append() NREs in a warm ScriptEngine
    # session for some objects. Signatures (ScriptTextDocument):
    #   replace(String) | replace(pos,count,text) | remove(pos,count) |
    #   insert(pos,text) | append(text). 'text' property is read-only.
    errs = []
    try:
        doc.replace(text)
        return
    except Exception as e:
        errs.append("replace(all): %s" % e)
    try:
        doc.replace(0, doc.length, text)
        return
    except Exception as e:
        errs.append("replace(0,n): %s" % e)
    try:
        n = doc.length
        if n:
            doc.remove(0, n)
        doc.append(text)
        return
    except Exception as e:
        errs.append("remove+append: %s" % e)
    raise Exception("set_text: all strategies failed | " + " ; ".join(errs))


# ---------------------------------------------------------------------------
# Ops ported from Codesys-MCP-SP21-plus (MIT, phobicdotno): project lifecycle,
# task/device config, symbol configuration, online lifecycle, NVL, multi-app.
# IronPython 2.7 only: no f-strings, %-formatting throughout.
# ---------------------------------------------------------------------------

def _full_path(obj):
    """Slash-separated path from the project root, walking parents."""
    parts = []
    node = obj
    for _guard in range(64):
        if node is None or hasattr(node, "active_application"):
            break
        try:
            name = node.get_name()
        except Exception:
            name = None
        if not name:
            break
        parts.insert(0, str(name))
        try:
            node = node.parent
        except Exception:
            break
    return "/".join(parts)


def _run_as_primary(state, fn):
    """Run fn(primary_project) with the project promoted to PRIMARY (several
    ScriptEngine APIs require it), demote back afterwards. If ANOTHER project
    currently holds the primary slot, close it first and reopen it as primary
    afterwards (only one primary project per CODESYS session).
    Returns (fn_result, promoted)."""
    proj = state["proj"]
    proj_path = str(getattr(proj, "path", ""))
    prim = projects.primary
    promoted = False
    other_primary_path = None
    if prim is not None and str(getattr(prim, "path", "")) != proj_path:
        other_primary_path = str(getattr(prim, "path", ""))
    if prim is None or other_primary_path is not None:
        if other_primary_path is not None:
            try:
                prim.close()
            except Exception:
                pass
        try:
            proj.close()
        except Exception:
            pass
        proj = projects.open(proj_path, primary=True)
        _finish_load(proj)
        state["proj"] = proj
        promoted = True
    else:
        _finish_load(proj)
    try:
        return fn(proj), promoted
    finally:
        if promoted:
            try:
                proj.close()
            except Exception:
                pass
            try:
                state["proj"] = projects.open(proj_path, primary=False)
                _finish_load(state["proj"])
            except Exception:
                pass
            if other_primary_path:
                try:
                    op = projects.open(other_primary_path, primary=True)
                    _finish_load(op)
                except Exception:
                    try:
                        op = projects.open(other_primary_path, primary=False)
                        _finish_load(op)
                    except Exception:
                        pass


def _sa_name_of(obj):
    try:
        n = obj.get_name()
        return str(n) if n is not None else ""
    except Exception:
        return ""


def _walk_all(root):
    """Collect every node recursively via get_children(False) - the
    single-call get_children(True) throws NullReference on some SP/projects."""
    out = []
    stack = [root]
    while stack:
        node = stack.pop()
        try:
            children = node.get_children(False)
        except Exception:
            continue
        for ch in children:
            out.append(ch)
            stack.append(ch)
    return out


def _enumerate_applications(proj):
    """Every application in the project with hosting device and active flag.
    Ported from Codesys-MCP-SP21-plus select_application helper."""
    active = None
    try:
        active = proj.active_application
    except Exception:
        pass
    out = []
    children = _walk_all(proj)
    for c in children:
        if not bool(getattr(c, "is_application", False)):
            continue
        dev = None
        node = c
        for _guard in range(32):
            if node is None or hasattr(node, "active_application"):
                break
            if bool(getattr(node, "is_device", False)):
                dev = node
                break
            try:
                node = node.parent
            except Exception:
                node = None
                break
        is_active = False
        try:
            is_active = bool(c.is_active_application)
        except Exception:
            try:
                is_active = str(c.guid) == str(active.guid)
            except Exception:
                pass
        dev_type = ""
        if dev is not None:
            try:
                ident = dev.get_device_identification()
                dev_type = " ".join(
                    "%s=%s" % (a, getattr(ident, a))
                    for a in ("type", "id", "version")
                    if getattr(ident, a, None) is not None)
            except Exception:
                pass
        out.append({"name": _sa_name_of(c), "path": _full_path(c),
                    "device": _sa_name_of(dev) if dev is not None else "",
                    "device_type": dev_type, "is_active": is_active, "obj": c})
    return out


def _app_of(proj, args):
    """Resolve application: explicit path wins, else the active/first one."""
    if args.get("application"):
        return find_obj(proj, args["application"])
    try:
        app = proj.active_application
        if app is not None:
            return app
    except Exception:
        pass
    apps = _enumerate_applications(proj)
    if apps:
        return apps[0]["obj"]
    raise Exception("No application found in project")


def op_list_applications(args, state):
    """List all applications (multi-device projects) with device and active flag."""
    payload = []
    for a in _enumerate_applications(state["proj"]):
        payload.append({"name": a["name"], "path": a["path"],
                        "device": a["device"], "device_type": a["device_type"],
                        "is_active": a["is_active"]})
    return {"applications": payload, "count": len(payload)}


def op_set_active_application(args, state):
    """Make the given application the project's active one (persists on save)."""
    proj = state["proj"]
    apps = _enumerate_applications(proj)
    before = ""
    for a in apps:
        if a["is_active"]:
            before = a["path"]
            break
    wanted = (args.get("application") or "").replace("\\", "/").strip("/").lower()
    if not wanted:
        raise Exception("application arg required (full path, trailing path, device or app name)")
    matches = [a for a in apps if a["path"].lower() == wanted]
    if not matches:
        matches = [a for a in apps if a["path"].lower().endswith("/" + wanted)]
    if not matches:
        matches = [a for a in apps if a["device"].lower() == wanted]
    if not matches:
        matches = [a for a in apps if a["name"].lower() == wanted]
    if not matches:
        raise Exception("Application '%s' not found. Available: %s"
                        % (args.get("application"), ", ".join(a["path"] for a in apps) or "<none>"))
    if len(matches) > 1:
        raise Exception("Application '%s' is ambiguous (%d matches) - use the full path. Available: %s"
                        % (args.get("application"), len(matches), ", ".join(a["path"] for a in matches)))
    chosen = matches[0]
    mark_touched(state)
    if not chosen["is_active"]:
        try:
            proj.active_application = chosen["obj"]
        except Exception as e:
            raise Exception("failed to set active application: %s" % e)
    return {"before": before or "<none>", "after": chosen["path"],
            "changed": (not chosen["is_active"]) or (before != chosen["path"])}


def op_rename_object(args, state):
    """Rename an object; optionally rewrite references in every textual object
    (regex word-boundary replace, target object itself excluded)."""
    proj = state["proj"]
    obj = find_obj(proj, args["path"])
    old = obj.get_name()
    new = args["new_name"]
    if not new:
        raise Exception("new_name is required")
    mark_touched(state)
    if hasattr(obj, "set_name"):
        obj.set_name(new)
    elif hasattr(obj, "rename"):
        obj.rename(new)
    else:
        raise Exception("object has no set_name/rename API")
    refs = 0
    if args.get("update_references", True) and old != new:
        pattern = re.compile(r"\b%s\b" % re.escape(old))
        all_objs = _walk_all(proj)
        target_guid = ""
        try:
            target_guid = str(obj.guid)
        except Exception:
            pass
        for other in all_objs:
            if target_guid:
                try:
                    if str(getattr(other, "guid", "")) == target_guid:
                        continue
                except Exception:
                    pass
            for part in ("declaration", "implementation"):
                try:
                    has = bool(getattr(other, "has_textual_" + part, False))
                except Exception:
                    has = False
                if not has:
                    continue
                try:
                    doc = getattr(other, "textual_" + part)
                    text = doc.text
                    if text and pattern.search(text):
                        new_text = pattern.sub(new, text)
                        try:
                            doc.replace(new_text)
                        except Exception:
                            set_text(doc, new_text)
                        refs += 1
                except Exception:
                    pass
    return {"old_name": old, "new_name": new, "references_updated": refs}


def op_move_object(args, state):
    """Move an object to another parent (empty new_parent = project root)."""
    proj = state["proj"]
    obj = find_obj(proj, args["path"])
    parent_arg = args.get("new_parent") or ""
    new_parent = find_obj(proj, parent_arg) if parent_arg else proj
    index = int(args.get("index", -1))
    mark_touched(state)
    obj.move(new_parent, index)
    return {"moved": args["path"], "new_parent": parent_arg or "<project root>", "index": index}


def op_export_plcopen_xml(args, state):
    """Export an object subtree (or the whole project) to PLCopen XML.
    SP21 signature drift: ALL args by keyword; verify the file afterwards."""
    proj = state["proj"]
    out = args["file"]
    recursive = bool(args.get("recursive", True))
    if args.get("path"):
        objects = [find_obj(proj, args["path"])]
    else:
        objects = list(proj.get_children(False))
    try:
        proj.export_xml(objects=objects, reporter=None, path=out,
                        recursive=recursive, export_folder_structure=True)
    except TypeError:
        proj.export_xml(objects, None, out, recursive, True)
    if not os.path.isfile(out):
        raise Exception("export_xml returned without error but no file exists at '%s' (signature drift?)" % out)
    return {"exported": out, "size": os.path.getsize(out),
            "objects": len(objects), "recursive": recursive}


def op_import_plcopen_xml(args, state):
    """Import PLCopen XML; objects are ADDED to the tree (no replace)."""
    proj = state["proj"]
    mark_touched(state)
    fs = bool(args.get("import_folder_structure", True))
    try:
        proj.import_xml(dataOrPath=args["file"], reporter=None, import_folder_structure=fs)
    except TypeError:
        proj.import_xml(args["file"], None, fs)
    return {"imported": args["file"], "note": "imported objects were ADDED next to existing ones"}


def op_dump_pou_code(args, state):
    """Bulk-dump declaration+implementation of every textual object under a
    root (default: whole project). Returns {path: {type, declaration, implementation}}."""
    proj = state["proj"]
    root = find_obj(proj, args["root"]) if args.get("root") else proj
    max_objects = int(args.get("max_objects", 2000))
    items = {}
    count = 0
    stack = [(root, _full_path(root) if root is not proj else "")]
    truncated = False
    while stack:
        node, path = stack.pop(0)
        try:
            children = node.get_children(False)
        except Exception:
            children = []
        for ch in children:
            if count >= max_objects:
                truncated = True
                stack = []
                break
            try:
                name = ch.get_name()
            except Exception:
                continue
            cpath = (path + "/" + name) if path else name
            try:
                has_d = bool(getattr(ch, "has_textual_declaration", False))
                has_i = bool(getattr(ch, "has_textual_implementation", False))
            except Exception:
                has_d = has_i = False
            if has_d or has_i:
                entry = {"type": str(getattr(ch, "type", "?"))}
                try:
                    entry["declaration"] = ch.textual_declaration.text if has_d else ""
                    entry["implementation"] = ch.textual_implementation.text if has_i else ""
                except Exception as e:
                    entry["error"] = str(e)
                items[cpath] = entry
                count += 1
            stack.append((ch, cpath))
    return {"objects": items, "count": count, "truncated": truncated}


def op_save_project_as(args, state):
    proj = state["proj"]
    mark_touched(state)
    pw = args.get("password") or ""
    if pw:
        proj.save_as(args["file"], pw)
    else:
        proj.save_as(args["file"])
    return {"saved_as": args["file"]}


def op_save_project_archive(args, state):
    proj = state["proj"]
    comment = args.get("comment") or ""
    if comment:
        proj.save_archive(args["file"], comment)
    else:
        proj.save_archive(args["file"])
    size = None
    try:
        size = os.path.getsize(args["file"])
    except Exception:
        pass
    return {"archive": args["file"], "size": size}


def op_clean_all(args, state):
    state["proj"].clean_all()
    return {"cleaned": True}


def op_get_compiler_version(args, state):
    return {"compiler_version": str(state["proj"].get_compilerversion())}


def op_set_compiler_version_newest(args, state):
    proj = state["proj"]
    proj.set_compilerversion_to_newest()
    mark_touched(state)
    return {"compiler_version": str(proj.get_compilerversion())}


def op_application_build_action(args, state):
    """generate_code / rebuild / clean on the application (no message dump;
    use 'build' for a full compile with messages)."""
    proj = state["proj"]
    app_path = _full_path(_app_of(proj, args))
    action = (args.get("action") or "generate_code").lower()
    method = {"generate_code": "generate_code", "rebuild": "rebuild", "clean": "clean"}.get(action)
    if method is None:
        raise Exception("action must be generate_code|rebuild|clean")
    def work(p):
        app = find_obj(p, app_path)
        if not hasattr(app, method):
            raise Exception("application does not support %s() on this SP" % method)
        getattr(app, method)()
        return _safe_name(app)
    name, promoted = _run_as_primary(state, work)
    return {"action": action, "application": name, "primary_promoted": promoted}



# ---------------------------------------------------------------------------
# Task configuration (extended) + device parameters/state (ported)
# ---------------------------------------------------------------------------

def _task_config_of(app):
    for ch in app.get_children(False):
        try:
            if getattr(ch, "is_task_configuration", False):
                return ch
        except Exception:
            pass
    return None


def _task_info(task):
    info = {"name": task.get_name()}
    for attr in ("kind_of_task", "priority", "interval", "interval_unit", "event"):
        try:
            info[attr] = str(getattr(task, attr))
        except Exception:
            info[attr] = None
    calls = []
    try:
        for p in task.pous:
            try:
                calls.append(str(p.name))
            except Exception:
                try:
                    calls.append(str(p))
                except Exception:
                    pass
    except Exception:
        pass
    info["pou_calls"] = calls
    return info


def op_list_tasks(args, state):
    """List tasks of the application's Task Configuration with their POU calls."""
    app = _app_of(state["proj"], args)
    tc = _task_config_of(app)
    if tc is None:
        return {"application": _safe_name(app), "tasks": []}
    tasks = []
    for ch in tc.get_children(False):
        try:
            if getattr(ch, "is_task", False):
                tasks.append(_task_info(ch))
        except Exception:
            pass
    return {"application": _safe_name(app),
            "task_configuration": _safe_name(tc), "tasks": tasks}


def op_configure_task(args, state):
    """Create/update a task and attach/detach POU calls.
    SP21 pitfall: removing a call via task.pous mutation silently fails -
    the call object is removed as a child of the task instead."""
    proj = state["proj"]
    app = _app_of(proj, args)
    task_name = args.get("task") or "MainTask"
    mark_touched(state)
    tc = _task_config_of(app)
    if tc is None:
        tc = app.create_task_configuration()
    task = None
    for ch in tc.get_children(False):
        try:
            if getattr(ch, "is_task", False) and ch.get_name() == task_name:
                task = ch
                break
        except Exception:
            pass
    created = False
    if task is None:
        if not args.get("create", True):
            raise Exception("task '%s' not found (create=false)" % task_name)
        task = tc.create_task(task_name)
        created = True
    changes = []
    warnings = []
    kind = args.get("kind") or ""
    if kind:
        k = None
        try:
            import scriptengine as _se
            k = getattr(_se.KindOfTask, kind)
        except Exception:
            try:
                k = getattr(KindOfTask, kind)
            except Exception:
                k = None
        if k is None:
            raise Exception("unknown task kind '%s'" % kind)
        task.kind_of_task = k
        changes.append("kind=%s" % kind)
    for attr in ("priority", "interval", "interval_unit", "event"):
        val = args.get(attr)
        if val is None or str(val) == "":
            continue
        try:
            setattr(task, attr, val)
            changes.append("%s=%s" % (attr, val))
        except Exception as e:
            warnings.append("%s: %s" % (attr, e))
    added = []
    if args.get("pou"):
        name = args["pou"]
        existing = _task_info(task).get("pou_calls", [])
        if name not in existing:
            task.pous.add(name)
            added.append(name)
    removed = []
    if args.get("remove_pou"):
        name = args["remove_pou"]
        # SP21 Patch 1 finding (verified empirically): POU calls cannot be
        # removed from a task via the ScriptEngine API.
        #   - task.pous.remove(elem) is a SILENT NO-OP (verified by re-read)
        #   - task.remove() / TaskConfiguration.remove() throw
        #     ArgumentNullException("source") / NullReferenceException
        #   - pous.replace(idx, '') blanks the call but breaks the build
        #     ("identifier expected"), so it is not a viable removal either.
        # Honest limitation: report without mutating the project.
        removed.append({"name": name, "removed": False, "how": None,
                        "error": "removing POU calls from tasks is not "
                                 "supported by the CODESYS ScriptEngine on "
                                 "SP21 Patch 1 (pous.remove() is a silent "
                                 "no-op, task.remove() throws). Remove the "
                                 "call in the CODESYS GUI."})
    return {"task": task_name, "created": created, "changes": changes,
            "warnings": warnings, "pou_added": added, "pou_removed": removed,
            "state": _task_info(task)}


def _find_device(proj, device_path=""):
    """Device by tree path, or auto: host of the active application, else the
    first device in the tree. Ported from Codesys-MCP-SP21-plus."""
    if device_path:
        obj = find_obj(proj, device_path)
        if not bool(getattr(obj, "is_device", False)):
            raise Exception("'%s' is not a device" % device_path)
        return obj
    try:
        node = proj.active_application
    except Exception:
        node = None
    for _guard in range(32):
        if node is None or hasattr(node, "active_application"):
            break
        if bool(getattr(node, "is_device", False)):
            return node
        try:
            node = node.parent
        except Exception:
            node = None
            break
    for c in _walk_all(proj):
        try:
            if bool(getattr(c, "is_device", False)):
                return c
        except Exception:
            pass
    raise Exception("no device found in project")


def _param_info(p):
    info = {"name": str(getattr(p, "name", "?"))}
    has_sub = False
    try:
        has_sub = bool(p.has_sub_elements)
    except Exception:
        pass
    try:
        info["editable"] = bool(p.editable)
    except Exception:
        info["editable"] = None
    if has_sub:
        children = []
        try:
            for sub in list(p):
                children.append(_param_info(sub))
        except Exception:
            pass
        info["children"] = children
    else:
        try:
            info["value"] = str(p.value)
        except Exception:
            info["value"] = None
    return info


def op_list_device_parameters(args, state):
    """Dump device_parameters plus every connector's parameters and
    host_parameters (needed for fieldbus couplers, e.g. WAGO K-Bus)."""
    dev = _find_device(state["proj"], args.get("device") or "")
    out = {"device": _safe_name(dev), "path": _full_path(dev),
           "parameters": [], "connectors": []}
    try:
        for p in dev.device_parameters:
            out["parameters"].append(_param_info(p))
    except Exception as e:
        out["parameters_error"] = str(e)
    try:
        for conn in dev.connectors:
            cinfo = {"name": _safe_name(conn), "parameters": [],
                     "host_parameters": []}
            try:
                for p in conn.parameters:
                    cinfo["parameters"].append(_param_info(p))
            except Exception as e:
                cinfo["parameters_error"] = str(e)
            try:
                for p in conn.host_parameters:
                    cinfo["host_parameters"].append(_param_info(p))
            except Exception as e:
                cinfo["host_parameters_error"] = str(e)
            out["connectors"].append(cinfo)
    except Exception as e:
        out["connectors_error"] = str(e)
    return out


def _iter_all_params(dev):
    try:
        for p in dev.device_parameters:
            yield p
    except Exception:
        pass
    try:
        for conn in dev.connectors:
            for p in conn.parameters:
                yield p
            for p in conn.host_parameters:
                yield p
    except Exception:
        pass


def _set_param_value(p, value):
    """Scalar: direct assign. Composite ('has_sub_elements'): '[v0, v1]' list
    literal or 'SubName=Value'. Returns how the value was applied."""
    has_sub = False
    try:
        has_sub = bool(p.has_sub_elements)
    except Exception:
        pass
    if not has_sub:
        p.value = value
        return "value"
    sval = str(value).strip()
    if sval.startswith("[") and sval.endswith("]"):
        parts = [x.strip() for x in sval[1:-1].split(",")]
        subs = list(p)
        applied = 0
        for i, sub in enumerate(subs):
            if i < len(parts) and parts[i] != "":
                _set_param_value(sub, parts[i])
                applied += 1
        return "list[%d/%d]" % (applied, len(subs))
    if "=" in sval:
        k, _, v = sval.partition("=")
        k = k.strip()
        for sub in list(p):
            try:
                if str(getattr(sub, "name", "")) == k:
                    _set_param_value(sub, v.strip())
                    return "element:%s" % k
            except Exception:
                continue
        raise Exception("sub-element '%s' not found" % k)
    raise Exception("parameter has sub-elements; supply '[v0, v1]' or 'Name=Value'")


def op_set_device_parameter(args, state):
    """Set a device/connector parameter by name. Composite values use
    '[v0, v1]' or 'Name=Value' notation."""
    dev = _find_device(state["proj"], args.get("device") or "")
    name = args["parameter"]
    mark_touched(state)
    hits = [p for p in _iter_all_params(dev) if str(getattr(p, "name", "")) == name]
    if not hits:
        avail = sorted(set(str(getattr(p, "name", "?")) for p in _iter_all_params(dev)))
        raise Exception("parameter '%s' not found on device '%s'. Available: %s"
                        % (name, _safe_name(dev), ", ".join(avail) or "<none>"))
    how = _set_param_value(hits[0], args["value"])
    note = None
    if len(hits) > 1:
        note = "parameter name matched %d places; the first was written" % len(hits)
    return {"device": _safe_name(dev), "parameter": name,
            "value": str(args["value"]), "applied": how, "note": note}


def op_set_device_state(args, state):
    dev = _find_device(state["proj"], args.get("device") or "")
    action = (args.get("action") or "").lower()
    mark_touched(state)
    if action == "enable":
        dev.enable()
    elif action == "disable":
        dev.disable()
    elif action == "simulation_on":
        dev.set_simulation_mode(True)
    elif action == "simulation_off":
        dev.set_simulation_mode(False)
    else:
        raise Exception("action must be enable|disable|simulation_on|simulation_off")
    return {"device": _safe_name(dev), "action": action}


def op_device_info(args, state):
    """Device identification + configured/scanned names and addresses."""
    dev = _find_device(state["proj"], args.get("device") or "")
    out = {"name": _safe_name(dev), "path": _full_path(dev)}
    try:
        ident = dev.get_device_identification()
        for a in ("type", "id", "version"):
            try:
                out[a] = str(getattr(ident, a))
            except Exception:
                out[a] = None
    except Exception as e:
        out["identification_error"] = str(e)
    for attr in ("device_name", "device_address",
                 "scanned_device_name", "scanned_device_address"):
        try:
            v = getattr(dev, attr)
            if v is not None:
                out[attr] = str(v)
        except Exception:
            pass
    try:
        out["address"] = str(dev.get_address())
    except Exception:
        pass
    try:
        out["gateway"] = str(dev.get_gateway())
    except Exception:
        pass
    return out


def op_io_mappings_csv(args, state):
    """Export/import device IO mappings as CSV (needs scripting API 3.5.8.0+)."""
    dev = _find_device(state["proj"], args.get("device") or "")
    direction = (args.get("direction") or "").lower()
    mark_touched(state)
    if direction == "export":
        if not hasattr(dev, "export_io_mappings_as_csv"):
            raise Exception("export_io_mappings_as_csv unavailable (needs 3.5.8.0+)")
        dev.export_io_mappings_as_csv(args["file"])
        size = None
        try:
            size = os.path.getsize(args["file"])
        except Exception:
            pass
        return {"exported": args["file"], "device": _safe_name(dev), "size": size}
    if direction == "import":
        if not os.path.isfile(args["file"]):
            raise Exception("CSV file not found: " + args["file"])
        if not hasattr(dev, "import_io_mappings_from_csv"):
            raise Exception("import_io_mappings_from_csv unavailable (needs 3.5.8.0+)")
        dev.import_io_mappings_from_csv(args["file"])
        return {"imported": args["file"], "device": _safe_name(dev)}
    raise Exception("direction must be export|import")


# ---------------------------------------------------------------------------
# Symbol Configuration (OPC UA / symbol file) - ported
# ---------------------------------------------------------------------------

def _find_symbol_configs(node, depth=0, max_depth=10):
    out = []
    if depth > max_depth:
        return out
    try:
        if bool(getattr(node, "is_symbol_config", False)):
            out.append(node)
    except Exception:
        pass
    try:
        children = node.get_children(False)
    except Exception:
        children = []
    for ch in children:
        out.extend(_find_symbol_configs(ch, depth + 1, max_depth))
    return out


def _symbol_config_of(proj, args):
    app = None
    if args.get("application"):
        app = find_obj(proj, args["application"])
    else:
        try:
            app = proj.active_application
        except Exception:
            app = None
    if app is not None:
        found = _find_symbol_configs(app)
        if found:
            return found[0]
    found = _find_symbol_configs(proj)
    if not found:
        raise Exception("No Symbol Configuration in project - call symbol_config_create first")
    return found[0]


_ACCESS_MEMBER_NAMES = {
    "none": ("None", "NoAccess"),
    "readonly": ("ReadOnly", "Read"),
    "writeonly": ("WriteOnly", "Write"),
    "readwrite": ("ReadWrite",),
}


def _resolve_symbol_access(access_str, sample_value):
    """Genuine SymbolAccess enum member. Enum member NAMES differ between
    SPs ('Read' vs 'ReadOnly') and int values are rejected by the C# setter,
    so the member is looked up BY NAME on the enum type taken from a genuine
    value (e.g. var.maximal_access). Ported from Codesys-MCP-SP21-plus."""
    name = (access_str or "").strip().lower()
    int_map = {"none": 0, "readonly": 1, "writeonly": 2, "readwrite": 3}
    if name not in int_map:
        raise Exception("Unknown access '%s'. Allowed: None, ReadOnly, WriteOnly, ReadWrite" % access_str)
    if sample_value is not None:
        enum_cls = type(sample_value)
        members = []
        try:
            members = [m for m in dir(enum_cls) if not m.startswith("_")]
        except Exception:
            members = []
        for want in _ACCESS_MEMBER_NAMES[name]:
            for m in members:
                if m.lower() == want.lower():
                    try:
                        return getattr(enum_cls, m)
                    except Exception:
                        pass
        try:
            import System
            return System.Enum.ToObject(enum_cls, int_map[name])
        except Exception:
            pass
    try:
        import scriptengine as _se
        enum_cls = getattr(_se, "SymbolAccess")
        for want in _ACCESS_MEMBER_NAMES[name]:
            for m in dir(enum_cls):
                if not m.startswith("_") and m.lower() == want.lower():
                    return getattr(enum_cls, m)
    except Exception:
        pass
    return int_map[name]


def _find_signature_in(collection, fqn, library_id=None):
    if collection is None:
        return None
    if hasattr(collection, "find"):
        try:
            hit = collection.find(fqn, library_id) if library_id else collection.find(fqn)
            if hit is not None:
                return hit
        except Exception:
            pass
    try:
        return collection[fqn]
    except Exception:
        pass
    try:
        for s in collection:
            try:
                if s.full_qualified_name == fqn:
                    return s
            except Exception:
                continue
    except Exception:
        pass
    return None


def _serialize_sig_vars(sig):
    variables = []
    try:
        for v in sig.variables:
            entry = {}
            for prop in ("name", "type", "configured_access",
                         "maximal_access", "effective_access"):
                try:
                    val = getattr(v, prop, None)
                    if val is not None:
                        entry[prop] = str(val)
                except Exception:
                    pass
            variables.append(entry)
    except Exception:
        pass
    return variables


def op_symbol_config_list(args, state):
    """All (or configured-only) signatures/datatypes of the Symbol
    Configuration with per-variable access. compile=true forces a build first
    (slow but authoritative)."""
    sc = _symbol_config_of(state["proj"], args)
    do_compile = bool(args.get("compile", False))
    configured_only = bool(args.get("configured_only", False))
    if configured_only:
        sigs = sc.get_only_configured_signatures() or []
        dts = sc.get_only_configured_datatypes() or []
    else:
        sigs = sc.get_all_signatures(do_compile) or []
        dts = []
    out_sigs = []
    for s in sigs:
        entry = {"fqn": str(getattr(s, "full_qualified_name", "")),
                 "name": str(getattr(s, "name", ""))}
        lib = getattr(s, "library_id", None)
        if lib is not None:
            entry["library_id"] = str(lib)
        entry["variables"] = _serialize_sig_vars(s)
        out_sigs.append(entry)
    out_dts = []
    for d in dts:
        entry = {"fqn": str(getattr(d, "full_qualified_name", "")),
                 "name": str(getattr(d, "name", ""))}
        entry["variables"] = _serialize_sig_vars(d)
        out_dts.append(entry)
    return {"symbol_config": _safe_name(sc), "compile": do_compile,
            "configured_only": configured_only,
            "signatures": out_sigs, "signature_count": len(out_sigs),
            "datatypes": out_dts, "datatype_count": len(out_dts)}


def op_symbol_config_create(args, state):
    """Create a Symbol Configuration under the application (idempotent)."""
    proj = state["proj"]
    app = _app_of(proj, args)
    existing = _find_symbol_configs(app)
    if existing:
        return {"created": False, "symbol_config": _safe_name(existing[0]),
                "note": "already exists under this application"}
    if not hasattr(app, "create_symbol_config"):
        raise Exception("application has no create_symbol_config() on this SP")
    layout = (args.get("layout") or "compatibility").lower()
    guid_str = "0141eb75-141b-4ea1-9a8c-75f952b22a6c" if layout.startswith("opt") \
        else "00000000-0000-0000-0000-000000000000"
    try:
        from System import Guid
        layout_guid = Guid(guid_str)
    except Exception:
        layout_guid = guid_str
    mark_touched(state)
    sc = app.create_symbol_config(bool(args.get("export_comments", True)),
                                  bool(args.get("support_opcua", True)), layout_guid)
    return {"created": True, "symbol_config": _safe_name(sc), "layout": layout}


def op_symbol_config_set_access(args, state):
    """Set configured_access for one variable (variable arg) or in bulk for
    all variables (omit variable) of a signature. IMPORTANT: mutation works
    only on signatures from get_all_signatures(), never on the configured
    (read-only) view."""
    sc = _symbol_config_of(state["proj"], args)
    fqn = args["signature"]
    library_id = args.get("library_id") or None
    sig = None
    try:
        sig = _find_signature_in(sc.get_all_signatures(False), fqn, library_id)
    except Exception:
        pass
    if sig is None:
        try:
            sig = _find_signature_in(sc.get_all_signatures(True), fqn, library_id)
        except Exception:
            pass
    if sig is None:
        raise Exception("Signature '%s' not found (library_id=%s). Use symbol_config_list with compile=true."
                        % (fqn, library_id or "<none>"))
    var_name = args.get("variable") or ""
    if var_name:
        targets = []
        try:
            for v in sig.variables:
                if v.name == var_name:
                    targets = [v]
                    break
        except Exception:
            pass
        if not targets:
            avail = []
            try:
                avail = [str(v.name) for v in sig.variables]
            except Exception:
                pass
            raise Exception("Variable '%s' not found in '%s'. Available: %s"
                            % (var_name, fqn, ", ".join(avail) or "<none>"))
    else:
        try:
            targets = list(sig.variables)
        except Exception:
            targets = []
    if not targets:
        raise Exception("Signature '%s' has no variables" % fqn)
    sample = None
    try:
        sample = targets[0].maximal_access
    except Exception:
        pass
    access = _resolve_symbol_access(args["access"], sample)
    changed = []
    skipped = []
    for v in targets:
        vname = "?"
        try:
            vname = str(v.name)
        except Exception:
            pass
        try:
            v.configured_access = access
            changed.append(vname)
        except Exception as e:
            skipped.append({"name": vname, "reason": str(e)})
    if changed:
        mark_touched(state)
    return {"signature": fqn, "access": str(access), "changed": changed,
            "changed_count": len(changed), "skipped": skipped}


def op_symbol_config_settings_get(args, state):
    """Read every knob of the Symbol Configuration (feature flags, filters,
    direct I/O access, layout calculators)."""
    sc = _symbol_config_of(state["proj"], args)
    settings = {}
    for prop in ("content_feature_flags", "effective_content_feature_flags",
                 "symbol_attribute_filter_type",
                 "effective_symbol_attribute_filter_type",
                 "symbol_attribute_filter_data",
                 "symbol_comment_filter_type",
                 "effective_symbol_comment_filter_type"):
        try:
            v = getattr(sc, prop, None)
            settings[prop] = None if v is None else str(v)
        except Exception as e:
            settings[prop] = "<unavailable: %s>" % e
    try:
        settings["enable_direct_io_access"] = bool(sc.enable_direct_io_access)
    except Exception:
        settings["enable_direct_io_access"] = None
    try:
        obs = sc.check_effective_direct_io_access()
        settings["direct_io_obstacles"] = str(obs)
        try:
            settings["direct_io_obstacle_explanations"] = [
                str(x) for x in (sc.get_direct_io_obstacle_explanations(obs) or [])]
        except Exception:
            settings["direct_io_obstacle_explanations"] = []
    except Exception:
        settings["direct_io_obstacles"] = None
    layout = {}
    try:
        calc = sc.client_side_layout_calculator
        if calc is not None:
            for a in ("name", "type_guid", "description"):
                try:
                    layout[a] = str(getattr(calc, a))
                except Exception:
                    layout[a] = None
    except Exception:
        pass
    settings["layout_calculator"] = layout
    calcs = []
    try:
        for calc in (sc.available_client_side_layout_calculators or []):
            entry = {}
            for a in ("name", "type_guid", "description"):
                try:
                    entry[a] = str(getattr(calc, a))
                except Exception:
                    entry[a] = None
            calcs.append(entry)
    except Exception:
        pass
    settings["available_layout_calculators"] = calcs
    return {"symbol_config": _safe_name(sc), "settings": settings}


def _enum_member(enum_cls, name):
    for m in dir(enum_cls):
        if m.startswith("_"):
            continue
        if m.lower() == name.lower():
            return getattr(enum_cls, m)
    raise Exception("enum member '%s' not found; available: %s"
                    % (name, ", ".join(m for m in dir(enum_cls) if not m.startswith("_"))))


def op_symbol_config_settings_set(args, state):
    """Partial update of the Symbol Configuration knobs; only supplied fields
    are written. content_flags is the integer bitmask; layout is
    'compatibility' (default GUID) or 'optimized'."""
    sc = _symbol_config_of(state["proj"], args)
    changes = []
    if args.get("content_flags") is not None:
        target_int = int(args["content_flags"])
        try:
            sc.content_feature_flags = target_int
            changes.append("content_feature_flags=%d" % target_int)
        except Exception:
            import System
            from scriptengine import SymbolConfigContentFeatureFlags as cff_enum
            ev = System.Enum.ToObject(cff_enum, target_int)
            sc.content_feature_flags = ev
            changes.append("content_feature_flags=%s" % ev)
    if args.get("attr_filter_type"):
        from scriptengine import SymbolAttributeFilterTypes as af_enum
        ev = _enum_member(af_enum, args["attr_filter_type"])
        sc.symbol_attribute_filter_type = ev
        changes.append("symbol_attribute_filter_type=%s" % ev)
    if args.get("attr_filter_data"):
        sc.symbol_attribute_filter_data = args["attr_filter_data"]
        changes.append("symbol_attribute_filter_data=%r" % args["attr_filter_data"])
    if args.get("comment_filter_type"):
        from scriptengine import SymbolCommentFilterType as cmt_enum
        ev = _enum_member(cmt_enum, args["comment_filter_type"])
        sc.symbol_comment_filter_type = ev
        changes.append("symbol_comment_filter_type=%s" % ev)
    if args.get("direct_io") is not None:
        want = bool(args["direct_io"])
        if want and hasattr(sc, "check_effective_direct_io_access"):
            blocked = False
            obs_str = ""
            try:
                obs_str = str(sc.check_effective_direct_io_access()).lower()
                blocked = obs_str not in ("none", "directioaccessobstacles.none", "0")
            except Exception:
                blocked = False
            if blocked:
                expl = []
                try:
                    expl = [str(x) for x in (
                        sc.get_direct_io_obstacle_explanations(
                            sc.check_effective_direct_io_access()) or [])]
                except Exception:
                    pass
                raise Exception("direct I/O blocked by obstacles (%s): %s" % (obs_str, " | ".join(expl)))
        sc.enable_direct_io_access = want
        changes.append("enable_direct_io_access=%s" % want)
    if args.get("layout"):
        label = args["layout"].strip().lower()
        if label in ("", "compatibility", "default", "compat"):
            guid_str = "00000000-0000-0000-0000-000000000000"
        elif label in ("optimized", "optimised", "optimal"):
            guid_str = "0141eb75-141b-4ea1-9a8c-75f952b22a6c"
        else:
            guid_str = args["layout"]
        try:
            from System import Guid
            sc.client_side_layout_calculator_guid = Guid(guid_str)
        except Exception:
            sc.client_side_layout_calculator_guid = guid_str
        changes.append("layout=%s" % guid_str)
    if changes:
        mark_touched(state)
    return {"symbol_config": _safe_name(sc), "changes_applied": changes,
            "change_count": len(changes)}


def op_symbol_config_export_xsd(args, state):
    """Write the Symbol Configuration's XSD schema (symbol file format) to disk."""
    sc = _symbol_config_of(state["proj"], args)
    out = args["file"]
    if not hasattr(sc, "get_symbol_configuration_xsd"):
        raise Exception("get_symbol_configuration_xsd unavailable on this SP")
    data = sc.get_symbol_configuration_xsd()
    if data is None:
        raise Exception("get_symbol_configuration_xsd() returned None")
    try:
        blob = bytes(bytearray(data))
    except Exception:
        blob = str(data).encode("utf-8")
    with open(out, "wb") as f:
        f.write(blob)
    return {"exported": out, "size": os.path.getsize(out)}


# ---------------------------------------------------------------------------
# Online / device lifecycle (ported). Ops that act on a running PLC reuse the
# cached online session from online_login (keyed by the application arg).
# ---------------------------------------------------------------------------

def _gateway_for(target):
    import scriptengine as _se
    online_mod = getattr(_se, "online", None)
    if online_mod is None:
        raise Exception("scriptengine.online unavailable on this SP")
    target_guid = str(target.get_gateway())
    for gw in online_mod.gateways:
        try:
            if str(gw.guid) == target_guid:
                return gw
        except Exception:
            continue
    raise Exception("device gateway %s not found in scriptengine.online.gateways" % target_guid)


def _scan_target_info(t):
    out = {}
    for f in ("device_name", "type_name", "vendor_name", "address",
              "parent_address", "device_id"):
        try:
            v = getattr(t, f, None)
            out[f] = "" if v is None else str(v)
        except Exception:
            out[f] = ""
    return out


def op_scan_network(args, state):
    """Live (or cached) network scan on the device's gateway. Blocking,
    typically 5-10 s for a live scan. Missing/unconfigured gateway is
    reported as an honest result, not an exception."""
    proj = state["proj"]
    dev = _find_device(proj, args.get("device") or "")
    try:
        gw = _gateway_for(dev)
    except Exception as e:
        return {"gateway": None, "gateway_guid": None, "cache_used": False,
                "target": {"name": _safe_name(dev),
                           "address": str(dev.get_address())},
                "results": [], "count": 0, "reachable": False,
                "error": "gateway unavailable: %s" % e}
    results = None
    cache_used = False
    if args.get("use_cache") and hasattr(gw, "get_cached_network_scan_result"):
        try:
            c = gw.get_cached_network_scan_result()
            if c is not None and len(list(c)) > 0:
                results = c
                cache_used = True
        except Exception:
            results = None
    if results is None:
        try:
            results = gw.perform_network_scan()
        except Exception as e:
            return {"gateway": str(getattr(gw, "name", "?")),
                    "gateway_guid": str(gw.guid), "cache_used": False,
                    "target": {"name": _safe_name(dev),
                               "address": str(dev.get_address())},
                    "results": [], "count": 0, "reachable": False,
                    "error": "network scan failed: %s" % e}
    items = [_scan_target_info(t) for t in (results or [])]
    return {"gateway": str(getattr(gw, "name", "?")),
            "gateway_guid": str(gw.guid), "cache_used": cache_used,
            "target": {"name": _safe_name(dev), "address": str(dev.get_address())},
            "results": items, "count": len(items)}


def op_device_reachable(args, state):
    """Pre-flight: is the device's cached address visible in the gateway scan?
    Uses the cached scan first (instant); live scan only when no cache."""
    proj = state["proj"]
    dev = _find_device(proj, args.get("device") or "")
    cached_address = str(dev.get_address())
    gw = _gateway_for(dev)
    items = []
    cache_used = False
    if hasattr(gw, "get_cached_network_scan_result"):
        try:
            c = gw.get_cached_network_scan_result()
            if c is not None:
                items = [_scan_target_info(t) for t in c]
                cache_used = len(items) > 0
        except Exception:
            items = []
    if not items:
        items = [_scan_target_info(t) for t in (gw.perform_network_scan() or [])]
    matched = [i for i in items if i.get("address") == cached_address]
    return {"reachable": len(matched) > 0, "cached_address": cached_address,
            "device": _safe_name(dev), "scan_source": "cache" if cache_used else "live",
            "matched": matched, "candidates": items, "candidate_count": len(items)}


def op_device_rebind(args, state):
    """Re-bind the device to a scan result (new address after reboot/DHCP).
    Match priority: match_address override > exact device_name > exact
    device_id > single candidate > refuse with the candidate list."""
    proj = state["proj"]
    dev = _find_device(proj, args.get("device") or "")
    gw = _gateway_for(dev)
    cached_address = str(dev.get_address())
    want_name = (args.get("match_name") or "").strip()
    want_id = (args.get("match_device_id") or "").strip()
    want_address = (args.get("match_address") or "").strip()
    items = []
    if not want_address:
        items = [_scan_target_info(t) for t in (gw.perform_network_scan() or [])]
    pick = None
    reason = "no-match"
    if want_address:
        pick = {"address": want_address, "device_name": "(forced)"}
        reason = "forced-address"
    elif want_name:
        hits = [i for i in items if i.get("device_name", "").lower() == want_name.lower()]
        if len(hits) == 1:
            pick = hits[0]
            reason = "by-name"
        elif len(hits) > 1:
            reason = "ambiguous-name"
    if pick is None and not want_address and not want_name and want_id:
        hits = [i for i in items if i.get("device_id") == want_id]
        if len(hits) == 1:
            pick = hits[0]
            reason = "by-device-id"
        elif len(hits) > 1:
            reason = "ambiguous-device-id"
    if pick is None and not want_address and not want_name and not want_id and len(items) == 1:
        pick = items[0]
        reason = "only-candidate"
    if pick is None:
        return {"rebound": False, "reason": reason, "cached_address": cached_address,
                "candidates": items, "candidate_count": len(items)}
    new_address = pick.get("address") or ""
    if not new_address:
        raise Exception("selected candidate has an empty address: %r" % pick)
    mark_touched(state)
    # Always re-apply the binding (refreshes scanned_* properties and the live
    # session that login()/download() depend on). IP-form addresses
    # (ip[:port]) need set_gateway_and_ip_address (block driver by IP).
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}(:\d+)?$", new_address):
        dev.set_gateway_and_ip_address(gw, new_address)
    else:
        dev.set_gateway_and_address(gw, new_address)
    return {"rebound": True, "reason": reason, "old_address": cached_address,
            "new_address": new_address, "matched_candidate": pick}


def _login_probe(oa, wait_seconds=30):
    """SP-version-drift tolerant login (LoginMode vs OnlineChangeOption, one-
    vs two-arg shapes). Prefers WithDownload semantics. Ported from
    Codesys-MCP-SP21-plus."""
    import scriptengine as _se
    try:
        if bool(oa.is_logged_in):
            return
    except Exception:
        pass
    if not hasattr(oa, "login"):
        raise Exception("online application has no login()")
    enum_candidates = []
    for src_name in ("LoginMode", "OnlineChangeOption"):
        src = getattr(_se, src_name, None)
        if src is None and hasattr(oa, src_name):
            src = getattr(oa, src_name)
        if src is None:
            continue
        try:
            members = sorted([m for m in dir(src) if not m.startswith("_")])
        except Exception:
            members = []
        for preferred in ("WithDownload", "ForceDownload", "Try", "TryOnlineChange",
                          "OnlineChangeOnly", "None_", "None"):
            if preferred in members:
                try:
                    enum_candidates.append(getattr(src, preferred))
                except Exception:
                    pass
    shapes = []
    for val in enum_candidates:
        shapes.append((val, False))
        shapes.append((val, True))
        shapes.append((val,))
    shapes.extend([(False,), (True,), ()])
    last_err = None
    for shape in shapes:
        try:
            oa.login(*shape)
            last_err = None
            break
        except Exception as e:
            last_err = e
    if last_err is not None:
        raise Exception("all login() call shapes failed; last error: %s" % last_err)
    stable = ("run", "stop", "connected", "halt", "breakpoint")
    for _i in range(int(wait_seconds)):
        try:
            st = str(oa.application_state).lower()
        except Exception:
            st = "unknown"
        if st in stable:
            break
        try:
            system.delay(1000)
        except Exception:
            pass


def op_download_application(args, state):
    """Login (SP-drift tolerant) and download the application to the device.
    The online session stays cached under the application key for follow-up
    start/stop/read/write ops."""
    proj = state["proj"]
    app = _app_of(proj, args)
    app_path = _full_path(app)
    key = args.get("application") or app_path
    old = ONLINE.pop(key, None)
    if old is not None:
        try:
            old.logout()
        except Exception:
            pass
        try:
            old.Dispose()
        except Exception:
            pass
    def work(p):
        a = find_obj(p, app_path)
        oa = online.create_online_application(a)
        _login_probe(oa, int(args.get("login_wait", 30)))
        if hasattr(oa, "download"):
            oa.download()
        elif hasattr(oa, "create_boot_application"):
            oa.create_boot_application()
        else:
            raise Exception("no download API on the online application object")
        ONLINE[key] = oa
        return oa
    oa, promoted = _run_as_primary(state, work)
    return {"downloaded": True, "application": app_path,
            "state_after": str(getattr(oa, "application_state", "unknown")),
            "session_key": key, "primary_promoted": promoted}


def op_application_start_stop(args, state):
    """Start/stop the logged-in application on the device."""
    oa = _online_get(args)
    action = (args.get("action") or "").lower()
    if action == "start":
        oa.start()
    elif action == "stop":
        oa.stop()
    else:
        raise Exception("action must be start|stop")
    return {"action": action,
            "state_after": str(getattr(oa, "application_state", "unknown"))}


def op_application_state(args, state):
    """State of the cached online session (login/application/operation state)."""
    oa = _online_get(args)
    return {"application": args.get("application", ""),
            "state": str(getattr(oa, "application_state", "unknown")),
            "operation_state": str(getattr(oa, "operation_state", "unknown")),
            "logged_in": bool(getattr(oa, "is_logged_in", False))}


def op_application_reset(args, state):
    """warm/cold reset of the logged-in application. 'origin' additionally
    wipes retain/persistent data and requires confirm=true."""
    oa = _online_get(args)
    level = (args.get("level") or "warm").lower()
    if level not in ("warm", "cold", "origin"):
        raise Exception("level must be warm|cold|origin")
    if level == "origin" and not args.get("confirm"):
        return {"error": "destructive op: reset to origin wipes retain/persistent data; "
                         "pass confirm=true (requires explicit user approval)"}
    import scriptengine as _se
    opt = getattr(_se, "ResetOption", None)
    if opt is None:
        raise Exception("scriptengine.ResetOption unavailable on this SP")
    oa.reset(getattr(opt, {"warm": "Warm", "cold": "Cold", "origin": "Original"}[level]))
    return {"level": level,
            "state_after": str(getattr(oa, "application_state", "unknown"))}


def op_online_change_check(args, state):
    """Whether an online change (without download) is currently possible."""
    app = _app_of(state["proj"], args)
    if not hasattr(app, "is_online_change_possible"):
        raise Exception("is_online_change_possible unavailable (needs scripting API 3.5.10.0+)")
    possible = app.is_online_change_possible
    if callable(possible):
        possible = possible()
    return {"application": _safe_name(app), "online_change_possible": bool(possible)}


def op_boot_application_create(args, state):
    """Create a boot application. online=true writes it onto the connected
    device (needs a cached online session); offline writes <output> (or the
    default .app next to the project) and requires generated code."""
    proj = state["proj"]
    if args.get("online"):
        oa = _online_get(args)
        oa.create_boot_application()
        return {"mode": "online", "created_on_device": True}
    app = _app_of(proj, args)
    out = args.get("output") or None
    # The boot-application generator NREs on an application whose code has
    # not been generated yet in this session - run generate_code first.
    try:
        if hasattr(app, "generate_code"):
            app.generate_code()
    except Exception:
        pass
    try:
        if out:
            app.create_boot_application(out)
        else:
            app.create_boot_application()
    except Exception as e:
        raise Exception("create_boot_application failed (%s). The generator "
                        "needs generated code and a resolvable device; run "
                        "application_build_action(generate_code) first or "
                        "create the boot application from the CODESYS GUI."
                        % e)
    return {"mode": "offline", "application": _safe_name(app), "output": out}


def op_source_download(args, state):
    """Download the project source archive into the device. compact=true tries
    the device-level compact variant first (falls back to full on SP21, where
    it otherwise fails with access-denied in Program Files)."""
    proj = state["proj"]
    oa = _online_get(args)
    stale = os.path.join(os.path.dirname(str(proj.path)), "Archive.prj")
    if os.path.exists(stale):
        try:
            os.remove(stale)
        except Exception:
            pass
    compact = bool(args.get("compact", False))
    done = False
    note = ""
    if compact:
        try:
            dev = oa.get_online_device()
            if hasattr(dev, "download_source"):
                dev.download_source(True)
                done = True
        except Exception as e:
            note = "compact device-level download failed (%s); fell back to full" % e
    if not done:
        oa.source_download()
        if compact and not note:
            note = "compact unavailable; full source archive downloaded"
    return {"downloaded": True, "compact": compact, "note": note}


def op_source_upload(args, state):
    """Upload the source archive stored on the device into a local file."""
    oa = _online_get(args)
    archive = args["archive"]
    parent = os.path.dirname(archive)
    if parent and not os.path.isdir(parent):
        raise Exception("target directory does not exist: " + parent)
    oa.get_online_device().upload_source(archive)
    size = None
    try:
        size = os.path.getsize(archive)
    except Exception:
        pass
    return {"uploaded_to": archive, "size": size}


def op_plc_file_list(args, state):
    """List files/directories in a PLC filesystem directory."""
    oa = _online_get(args)
    directory = args.get("directory") or ""
    entries = []
    dev = oa.get_online_device()
    for info in list(dev.get_file_list_of_directory(directory) or []):
        try:
            entries.append({
                "kind": "dir" if info.is_directory else "file",
                "name": str(info.name),
                "size": 0 if info.is_directory else str(info.size),
                "modified": str(info.last_modification_time)})
        except Exception as e:
            entries.append({"kind": "?", "name": str(getattr(info, "name", "?")),
                            "error": str(e)})
    return {"directory": directory or "<root>", "entries": entries,
            "count": len(entries)}


def op_plc_file_transfer(args, state):
    """Transfer a file PC<->PLC. direction 'to_plc' = download_file,
    'from_plc' = upload_file (CODESYS naming)."""
    oa = _online_get(args)
    direction = (args.get("direction") or "").lower()
    local = args["local"]
    plc = args["plc"]
    overwrite = bool(args.get("overwrite", False))
    dev = oa.get_online_device()
    if direction == "to_plc":
        if not os.path.isfile(local):
            raise Exception("local file does not exist: " + local)
        dev.download_file(local, plc, overwrite)
    elif direction == "from_plc":
        dev.upload_file(plc, local, overwrite)
    else:
        raise Exception("direction must be to_plc|from_plc")
    return {"direction": direction, "local": local, "plc": plc,
            "overwrite": overwrite}


def op_plc_file_delete(args, state):
    """Delete a file (or directory) on the PLC filesystem. Destructive:
    requires confirm=true."""
    if not args.get("confirm"):
        return {"error": "destructive op: pass confirm=true (requires explicit user approval)"}
    oa = _online_get(args)
    path = args["path"]
    if not path:
        raise Exception("path is required")
    is_dir = bool(args.get("is_directory", False))
    dev = oa.get_online_device()
    if is_dir:
        dev.delete_directory(path, bool(args.get("recursive", False)))
    else:
        dev.delete_file(path)
    return {"deleted": path, "is_directory": is_dir}


def op_signature_crc(args, state):
    """Signature CRC of an object (needs a successful build first)."""
    obj = find_obj(state["proj"], args["path"])
    if not hasattr(obj, "get_signature_crc"):
        raise Exception("object '%s' has no get_signature_crc()" % _safe_name(obj))
    crc = obj.get_signature_crc()
    return {"object": _safe_name(obj), "path": args["path"],
            "signature_crc": None if crc is None else str(crc)}


def op_set_exclude_from_build(args, state):
    """Exclude an object from the build (or re-include)."""
    proj = state["proj"]
    obj = find_obj(proj, args["path"])
    exclude = bool(args.get("exclude", True))
    mark_touched(state)
    bp = getattr(obj, "build_properties", None)
    if bp is not None and hasattr(bp, "exclude_from_build"):
        bp.exclude_from_build = exclude
        via = "build_properties"
    elif hasattr(obj, "exclude_from_build"):
        obj.exclude_from_build = exclude
        via = "flat"
    else:
        raise Exception("object '%s' has no build properties - exclude_from_build "
                        "is not applicable to it" % _safe_name(obj))
    try:
        effective = str(obj.effectively_excluded_from_build)
    except Exception:
        effective = "unknown"
    return {"object": _safe_name(obj), "exclude_from_build": exclude,
            "via": via, "effectively_excluded": effective}


def op_device_user_add(args, state):
    """Add/update a user in the PLC runtime's live User Management (needed for
    OPC UA authentication on SP16+). Opens its own device session."""
    proj = state["proj"]
    dev = _find_device(proj, args.get("device") or "")
    user = args.get("user") or ""
    if not user:
        raise Exception("user is required")
    password = args.get("password") or ""
    import scriptengine as _se
    online_mod = getattr(_se, "online", None)
    if online_mod is None or not hasattr(online_mod, "create_online_device"):
        raise Exception("scriptengine.online.create_online_device unavailable on this SP")
    online_device = online_mod.create_online_device(dev)
    try:
        if not (getattr(online_device, "connected", False)
                or getattr(online_device, "shared_connected", False)):
            online_device.connect()
    except Exception as e:
        raise Exception("online_device.connect() failed: %s" % e)
    try:
        live_um = online_device.create_live_user_management()
    except Exception as e:
        raise Exception("create_live_user_management() failed (%s). The device may not "
                        "support the live API (pre-SP16), or User Management needs "
                        "initialization via the IDE first." % e)
    try:
        live_um.upload()
    except Exception:
        pass
    existing = []
    try:
        for u in live_um.users:
            try:
                existing.append(str(u.name))
            except Exception:
                pass
    except Exception:
        pass
    action = None
    if user in existing:
        live_um.set_user_password(user, password)
        action = "updated"
    else:
        try:
            live_um.add_user(user, password,
                             bool(args.get("can_change_password", True)),
                             bool(args.get("must_change_password", False)))
            action = "added"
        except Exception as e:
            msg = str(e).lower()
            if "already existing" in msg or "already exists" in msg:
                live_um.set_user_password(user, password)
                action = "updated-after-add-rejected"
            else:
                raise
    final = []
    try:
        for u in live_um.users:
            try:
                final.append(str(u.name))
            except Exception:
                pass
    except Exception:
        pass
    return {"user": user, "action": action, "users_before": existing,
            "users_after": final}


def op_grant_object_access(args, state):
    """Set project-side Access Control permissions for a group on an object
    (the IDE's Properties -> Access Control dialog). Needed e.g. before a
    downloaded OPC UA server will expose UserIdentityToken policies."""
    proj = state["proj"]
    um = None
    try:
        um = proj.user_management
    except Exception as e:
        raise Exception("project.user_management failed: %s" % e)
    if um is None:
        raise Exception("project.user_management is None - user management may not be "
                        "initialized for this project")
    group_name = args.get("group") or "Everyone"
    try:
        group = um.groups[group_name]
    except Exception:
        avail = []
        try:
            for g in um.groups:
                try:
                    avail.append(str(g.name))
                except Exception:
                    pass
        except Exception:
            pass
        raise Exception("group '%s' not found. Available: %s" % (group_name, ", ".join(avail) or "<none>"))
    obj = find_obj(proj, args["path"])
    import scriptengine as _se
    state_str = (args.get("state") or "Granted").strip().lower()
    if state_str in ("granted", "grant", "allow", "allowed", "true", "1"):
        state_enum = _se.PermissionState.Granted
    elif state_str in ("denied", "deny", "block", "blocked"):
        state_enum = _se.PermissionState.Denied
    elif state_str in ("default", "unset", ""):
        state_enum = _se.PermissionState.Default
    else:
        raise Exception("state must be Granted|Denied|Default, got '%s'" % args.get("state"))
    kind_aliases = {"view": "View", "modify": "Modify", "remove": "Remove",
                    "addremovechildren": "AddRemoveChildren"}
    raw = [k.strip() for k in (args.get("permissions") or "").split(",") if k.strip()]
    if not raw:
        raw = ["View", "Modify", "Remove", "AddRemoveChildren"]
    mark_touched(state)
    applied = []
    for k in raw:
        canonical = kind_aliases.get(k.lower(), k)
        try:
            kind_enum = getattr(_se.ObjectPermissionKind, canonical)
        except AttributeError:
            raise Exception("unknown permission kind '%s'. Valid: View, Modify, "
                            "Remove, AddRemoveChildren" % k)
        perm = um.get_object_permission(obj, kind_enum)
        perm.set_permission_state(group, state_enum)
        applied.append(canonical)
    return {"object": _safe_name(obj), "path": _full_path(obj), "group": group_name,
            "state": state_str, "permissions_applied": applied}


# ---------------------------------------------------------------------------
# NVL (network variables) via the Automation Platform API. The scripting API
# has no NVL support; these ops reflect the same API the IDE dialog uses.
# ---------------------------------------------------------------------------

def _nvl_set_param(nvp, names, value):
    """Try INetVarProperties4.SetParameterValue with several spellings of a
    protocol parameter name. Returns the spelling that worked or None."""
    for candidate in names:
        try:
            nvp.SetParameterValue(candidate, str(value))
            ok = True
            try:
                res = nvp.GetParameterValue(candidate, None)
                if isinstance(res, tuple):
                    ok = bool(res[0])
                else:
                    ok = bool(res)
            except Exception:
                pass
            if ok:
                return candidate
        except Exception:
            pass
    return None


def _nvl_assign(nvp, prop, value, timespan=False):
    try:
        setattr(nvp, prop, value)
        return True
    except Exception:
        pass
    if timespan:
        try:
            from System import TimeSpan
            s = str(value).strip()
            m = re.match(r"^T\#(\d+(?:\.\d+)?)\s*(ms|s|m|h)$", s, re.IGNORECASE)
            if m:
                num = float(m.group(1))
                factor = {"ms": 1.0, "s": 1000.0, "m": 60000.0, "h": 3600000.0}[m.group(2).lower()]
                setattr(nvp, prop, TimeSpan.FromMilliseconds(num * factor))
                return True
            setattr(nvp, prop, TimeSpan.Parse(s))
            return True
        except Exception:
            return False
    return False


def op_nvl_sender_set(args, state):
    """Turn a GVL into an NVL sender (UDP) or update its network properties.
    Ported from Codesys-MCP-SP21-plus (Automation Platform API)."""
    proj = state["proj"]
    gvl = find_obj(proj, args["gvl"])
    gvl_guid = gvl.guid
    mark_touched(state)
    import clr
    import System
    for _name in ("SystemInstances", "Objects", "ObjectsWin"):
        _found = None
        for _asm in System.AppDomain.CurrentDomain.GetAssemblies():
            if _asm.GetName().Name == _name:
                _found = _asm
                break
        clr.AddReference(_found if _found is not None else _name)
    from _3S.CoDeSys.Core import SystemInstances
    try:
        handle = proj.handle
    except Exception as e:
        raise Exception("ScriptProject.handle unavailable on this SP: %s" % e)
    om = SystemInstances.ObjectMgr
    mo = om.GetObjectToModify(handle, gvl_guid)
    obj = mo.Object
    nvp = None
    try:
        nvp = obj.NetVarProperties
    except Exception:
        nvp = None
    if nvp is None:
        if not hasattr(obj, "CreateNetVarProperties"):
            raise Exception("object has no CreateNetVarProperties (not an IGVLObject2?)")
        nvp = obj.CreateNetVarProperties()
    _nvl_assign(nvp, "ProtocolName", "UDP")
    _nvl_assign(nvp, "TaskName", args.get("task") or "")
    _nvl_assign(nvp, "ListIdentifier", str(args.get("list_identifier", 1)))
    _nvl_assign(nvp, "TransmitCyclic", bool(args.get("cyclic", True)))
    _nvl_assign(nvp, "Interval", args.get("interval") or "T#100ms", timespan=True)
    _nvl_assign(nvp, "TransmitOnChange", bool(args.get("on_change", False)))
    _nvl_assign(nvp, "MinimumGap", args.get("min_gap") or "T#10ms", timespan=True)
    _nvl_assign(nvp, "TransmitOnEvent", False)
    _nvl_assign(nvp, "PackVariables", bool(args.get("pack_variables", False)))
    _nvl_assign(nvp, "Checksum", bool(args.get("checksum", False)))
    _nvl_assign(nvp, "Acknowledge", bool(args.get("acknowledge", False)))
    used_addr = _nvl_set_param(nvp, ["Broadcast Adr.", "Broadcast address",
                                     "BroadcastAddress", "Broadcast Adr", "Broadcast"],
                               args.get("broadcast_address") or "")
    used_port = _nvl_set_param(nvp, ["Port", "UDP Port", "Portnumber"],
                               args.get("port", 1202))
    if used_addr is None or used_port is None:
        raise Exception("could not set broadcast/port parameter on this SP")
    try:
        if hasattr(nvp, "CreateGuids"):
            nvp.CreateGuids()
    except Exception:
        pass
    # CreateNetVarProperties returns a DETACHED object on SP21 P5; attach it
    # through the concrete class's writable member (interface property is
    # get-only).
    try:
        if obj.NetVarProperties is None:
            from System.Reflection import BindingFlags
            t = obj.GetType()
            flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance
            attached = False
            for pname in ("NetvarSettings", "NetVarProperties"):
                pi = t.GetProperty(pname, flags)
                if pi is not None and pi.CanWrite:
                    pi.SetValue(obj, nvp, None)
                    attached = True
                    break
            if not attached:
                for fi in t.GetFields(flags):
                    if "netvar" in fi.Name.lower():
                        fi.SetValue(obj, nvp)
                        attached = True
                        break
    except Exception:
        pass
    om.SetObject(mo, True, None)
    try:
        proj.save()
    except Exception:
        pass
    check = om.GetObjectToRead(handle, gvl_guid).Object
    try:
        cnvp = check.NetVarProperties
    except Exception:
        cnvp = None
    if cnvp is None:
        raise Exception("NetVarProperties did not persist (still None after commit)")
    return {"gvl": args["gvl"], "persisted": True,
            "protocol": str(getattr(cnvp, "ProtocolName", "")),
            "list_identifier": str(getattr(cnvp, "ListIdentifier", "")),
            "task": str(getattr(cnvp, "TaskName", "")),
            "cyclic": bool(getattr(cnvp, "TransmitCyclic", False)),
            "interval": str(getattr(cnvp, "Interval", "")),
            "broadcast_param": used_addr, "port_param": used_port}


def op_nvl_receiver_create(args, state):
    """Create (or update) an NVL receiver under an application and bind it to
    a sender GVL by GUID. Ported from Codesys-MCP-SP21-plus."""
    proj = state["proj"]
    parent = find_obj(proj, args["parent"])
    sender = find_obj(proj, args["sender_gvl"])
    sender_guid = sender.guid
    name = args.get("name") or "NVL_Receiver"
    mark_touched(state)
    import clr
    import System
    for _name in ("SystemInstances", "Objects", "ObjectsWin"):
        _found = None
        for _asm in System.AppDomain.CurrentDomain.GetAssemblies():
            if _asm.GetName().Name == _name:
                _found = _asm
                break
        clr.AddReference(_found if _found is not None else _name)
    from _3S.CoDeSys.Core import SystemInstances
    try:
        handle = proj.handle
    except Exception as e:
        raise Exception("ScriptProject.handle unavailable on this SP: %s" % e)
    om = SystemInstances.ObjectMgr
    existing = None
    for c in parent.get_children(False):
        try:
            if c.get_name() == name:
                existing = c
                break
        except Exception:
            pass
    created = False
    if existing is not None:
        obj_guid = existing.guid
    else:
        fm = om.ObjectFactoryManager
        factory = None
        names = []
        for f in fm.Factories:
            try:
                nm = str(f.Name)
                tn = f.ObjectType.FullName if f.ObjectType is not None else ""
            except Exception:
                continue
            names.append(nm)
            if "NVLObject" in tn or "Network Variable List" in nm:
                factory = f
                break
        if factory is None:
            raise Exception("no NVL receiver factory found. Factories: %s"
                            % ", ".join(sorted(set(names))))
        new_iobj = None
        try:
            new_iobj = factory.Create()
        except Exception:
            pass
        if new_iobj is None:
            t_obj = factory.ObjectType
            new_iobj = System.Activator.CreateInstance(t_obj)
        obj_guid = System.Guid.NewGuid()
        om.AddObject(handle, parent.guid, obj_guid, new_iobj, name, -1)
        try:
            factory.ObjectCreated(handle, obj_guid)
        except Exception:
            pass
        created = True
    mo = om.GetObjectToModify(handle, obj_guid)
    recv = mo.Object
    recv.SenderGVLGuid = sender_guid
    recv.TaskName = args.get("task") or ""
    try:
        nvp = recv.NetVarProperties
    except Exception:
        nvp = None
    # A receiver MIRRORS its sender's network properties through SenderGVLGuid;
    # its own protocol/task must stay empty (a second netvar manager on the
    # same task collides with the sender's generated instance).
    if nvp is not None:
        try:
            if str(getattr(nvp, "ProtocolName", "")):
                nvp.ProtocolName = ""
        except Exception:
            pass
    try:
        if hasattr(recv, "CreateGuids"):
            recv.CreateGuids()
    except Exception:
        pass
    om.SetObject(mo, True, None)
    try:
        proj.save()
    except Exception:
        pass
    check = om.GetObjectToRead(handle, obj_guid).Object
    return {"receiver": name, "parent": args["parent"], "created": created,
            "sender_guid": str(getattr(check, "SenderGVLGuid", "")),
            "sender_name": str(getattr(check, "SenderGVLName", "")),
            "task": str(getattr(check, "TaskName", ""))}
OPS = {
    "list_applications": (op_list_applications, True),
    "set_active_application": (op_set_active_application, True),
    "rename_object": (op_rename_object, True),
    "move_object": (op_move_object, True),
    "export_plcopen_xml": (op_export_plcopen_xml, True),
    "import_plcopen_xml": (op_import_plcopen_xml, True),
    "dump_pou_code": (op_dump_pou_code, True),
    "save_project_as": (op_save_project_as, True),
    "save_project_archive": (op_save_project_archive, True),
    "clean_all": (op_clean_all, True),
    "get_compiler_version": (op_get_compiler_version, True),
    "set_compiler_version_newest": (op_set_compiler_version_newest, True),
    "application_build_action": (op_application_build_action, True),
    "list_tasks": (op_list_tasks, True),
    "configure_task": (op_configure_task, True),
    "list_device_parameters": (op_list_device_parameters, True),
    "set_device_parameter": (op_set_device_parameter, True),
    "set_device_state": (op_set_device_state, True),
    "device_info": (op_device_info, True),
    "io_mappings_csv": (op_io_mappings_csv, True),
    "symbol_config_list": (op_symbol_config_list, True),
    "symbol_config_create": (op_symbol_config_create, True),
    "symbol_config_set_access": (op_symbol_config_set_access, True),
    "symbol_config_settings_get": (op_symbol_config_settings_get, True),
    "symbol_config_settings_set": (op_symbol_config_settings_set, True),
    "symbol_config_export_xsd": (op_symbol_config_export_xsd, True),
    "scan_network": (op_scan_network, True),
    "device_reachable": (op_device_reachable, True),
    "device_rebind": (op_device_rebind, True),
    "download_application": (op_download_application, True),
    "application_start_stop": (op_application_start_stop, True),
    "application_state": (op_application_state, True),
    "application_reset": (op_application_reset, True),
    "online_change_check": (op_online_change_check, True),
    "boot_application_create": (op_boot_application_create, True),
    "source_download": (op_source_download, True),
    "source_upload": (op_source_upload, True),
    "plc_file_list": (op_plc_file_list, True),
    "plc_file_transfer": (op_plc_file_transfer, True),
    "plc_file_delete": (op_plc_file_delete, True),
    "signature_crc": (op_signature_crc, True),
    "set_exclude_from_build": (op_set_exclude_from_build, True),
    "device_user_add": (op_device_user_add, True),
    "grant_object_access": (op_grant_object_access, True),
    "nvl_sender_set": (op_nvl_sender_set, True),
    "nvl_receiver_create": (op_nvl_receiver_create, True),
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

# Ops whose underlying CODESYS service (SymbolConfig plug-in, boot
# application generator, Automation Platform ObjectMgr, build signatures)
# silently requires the PRIMARY project: promote for the call, demote back.
PRIMARY_OPS = set([
    "symbol_config_create", "symbol_config_list", "symbol_config_set_access",
    "symbol_config_settings_get", "symbol_config_settings_set",
    "symbol_config_export_xsd",
    "boot_application_create", "signature_crc", "set_exclude_from_build",
    "online_change_check",
    "nvl_sender_set", "nvl_receiver_create",
    # SP21 finding: textual_declaration/implementation writes NRE unless the
    # project is PRIMARY - route the text-writing ops through the promotion
    # wrapper (no-op when the project already holds the primary slot).
    "set_code", "create_pou", "create_gvl", "create_dut",
    "create_member", "create_interface", "batch",
])

def _open_project_auto(proj_path):
    """Open a project; tolerate a warm session where another project is
    already primary (only one primary project per CODESYS session).
    SP21 finding: a freshly opened project can return an EMPTY tree until
    finish_load_project() is called - always invoke it (idempotent)."""
    proj = get_project(proj_path)
    if proj is not None:
        _finish_load(proj)
        return proj
    try:
        proj = projects.open(proj_path, primary=True)
    except Exception:
        proj = projects.open(proj_path, primary=False)
    _finish_load(proj)
    return proj


def _finish_load(proj):
    """Force full project load; without it a reopened project may expose an
    empty object tree (silent NREs / empty enumerations on SP21 Patch 1)."""
    try:
        fn = getattr(proj, "finish_load_project", None)
        if callable(fn):
            fn()
    except Exception:
        pass

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
        if needs_project and op in PRIMARY_OPS:
            def _call_primary(_p):
                data = fn(args, state)
                # save BEFORE the demote closes the primary project, otherwise
                # the in-memory changes would be lost on reopen
                if state.get("touched"):
                    try:
                        state["proj"].save()
                    except Exception:
                        pass
                return data
            result["data"], _promoted = _run_as_primary(state, _call_primary)
        else:
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
