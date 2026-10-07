"""G1, the static gate: what a generated test may contain, checked on its syntax tree only.

The code is parsed with `ast` and never imported or executed. A candidate passes when:

- the module holds imports (`pytest`, public `pg_sdk` names, `__future__`) and exactly one
  test function `test_*(game)` marked `@pytest.mark.spec("ID", ...)` with known spec IDs;
- nothing banned appears: `exec`, `eval`, `compile`, `open`, `__import__`, `getattr`/`setattr`/
  `delattr`, `globals`/`locals`/`vars`, `breakpoint`, `input`, any name or attribute starting
  with `_`, `while True`, `try` (it can swallow failures), `global`/`nonlocal`, nested `def`,
  `class`, `async`/`await`/`yield`, `pytest.skip`/`xfail`/`importorskip`;
- every attribute or call reached from `game` resolves to a manifest member, and every call has
  an argument list its signature accepts (this is what designs hallucinated APIs out);
- there is at least one assertion, none is a tautology (a literal, `x == x`, `is not None` on a
  value the manifest says is never None), and at least one checks something the player sees
  (a value from a screen page, not only `game.player` or `game.setup`);
- it stays within the size limits.

It also derives the pages the test touches (for G3's relevance filter) and its spec IDs.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict

from pg_core.gates.base import GateResult, Reason, reason
from pg_core.pages import APP, HELPERS

GATE = "G1"

BANNED_CALLS = frozenset(
    {
        "exec",
        "eval",
        "compile",
        "open",
        "__import__",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
        "breakpoint",
        "input",
        "exit",
        "quit",
    }
)
BUILTINS = frozenset(
    {
        "len",
        "int",
        "str",
        "float",
        "bool",
        "abs",
        "min",
        "max",
        "sum",
        "sorted",
        "reversed",
        "list",
        "set",
        "tuple",
        "dict",
        "range",
        "enumerate",
        "zip",
        "any",
        "all",
        "round",
        "isinstance",
        "print",
    }
)
BANNED_PYTEST = frozenset(
    {"skip", "xfail", "importorskip", "exit", "main", "register_assert_rewrite"}
)
ALLOWED_PYTEST = frozenset({"approx", "raises", "fail", "mark"})
BUILTIN_METHODS: Mapping[str, frozenset[str]] = {
    "str": frozenset(
        {
            "lower",
            "upper",
            "strip",
            "lstrip",
            "rstrip",
            "startswith",
            "endswith",
            "split",
            "replace",
            "isdigit",
            "count",
            "find",
            "casefold",
            "join",
        }
    ),
    "list": frozenset(
        {"index", "count", "copy", "append", "extend", "sort", "reverse", "pop", "insert", "remove"}
    ),
    "dict": frozenset({"get", "keys", "values", "items", "copy"}),
    "set": frozenset({"add", "union", "intersection", "difference", "issubset", "issuperset"}),
}


class Limits(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_lines: int = 80
    max_actions: int = 40


class StaticReport(BaseModel):
    """What G1 learned about a candidate besides its verdict."""

    model_config = ConfigDict(frozen=True)

    test_name: str | None = None
    spec_ids: tuple[str, ...] = ()
    pages_used: tuple[str, ...] = ()
    calls: tuple[str, ...] = ()  # resolved SDK calls, e.g. "StoreSection.buy"
    assertions: int = 0
    visible_assertions: int = 0
    lines: int = 0


# --- types --------------------------------------------------------------------------------


@dataclass(frozen=True)
class T:
    """A static type: a manifest class/record/enum, a builtin, or unknown."""

    name: str
    args: tuple[T, ...] = ()
    optional: bool = False


UNKNOWN = T("unknown")


def parse_type(text: str) -> T:
    text = text.strip()
    parts = _split_top(text, "|")
    if len(parts) > 1:
        rest = [p for p in parts if p.strip() != "None"]
        inner = parse_type("|".join(rest)) if len(rest) == 1 else UNKNOWN
        return T(inner.name, inner.args, optional=True)
    if "[" in text and text.endswith("]"):
        head, inner_text = text.split("[", 1)
        args = tuple(parse_type(a) for a in _split_top(inner_text[:-1], ","))
        return T(head.strip(), args)
    return T(text)


def _split_top(text: str, sep: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current = ""
    for ch in text:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)
    return [p.strip() for p in parts]


@dataclass(frozen=True)
class Value:
    type: T
    roots: frozenset[str] = frozenset()  # pages under `game` this value came from
    method_of: str | None = None  # set when the value is an unbound SDK method


# --- the checker --------------------------------------------------------------------------


@dataclass
class _Checker:
    manifest: Mapping[str, Any]
    problems: list[Reason] = field(default_factory=list)
    env: dict[str, Value] = field(default_factory=dict)
    imported: set[str] = field(default_factory=set)
    pages: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)
    actions: int = 0
    assertions: int = 0
    visible_assertions: int = 0

    def fail(self, code: str, message: str) -> None:
        if all(p.message != message for p in self.problems):
            self.problems.append(reason(code, message))

    # --- manifest lookups -----------------------------------------------------------------

    @property
    def classes(self) -> Mapping[str, Any]:
        return self.manifest["classes"]  # type: ignore[no-any-return]

    @property
    def records(self) -> Mapping[str, Any]:
        return self.manifest["records"]  # type: ignore[no-any-return]

    @property
    def enums(self) -> Mapping[str, list[str]]:
        return self.manifest["enums"]  # type: ignore[no-any-return]

    @property
    def exports(self) -> frozenset[str]:
        return frozenset(self.manifest["exports"])

    # --- statements -----------------------------------------------------------------------

    def statements(self, body: Iterable[ast.stmt]) -> None:
        for stmt in body:
            self.statement(stmt)

    def statement(self, stmt: ast.stmt) -> None:
        if isinstance(stmt, ast.Assert):
            self.assertion(stmt.test)
            if stmt.msg is not None:
                self.expr(stmt.msg)
        elif isinstance(stmt, ast.Assign):
            value = self.expr(stmt.value)
            for target in stmt.targets:
                self.bind(target, value)
        elif isinstance(stmt, ast.AnnAssign):
            value = self.expr(stmt.value) if stmt.value is not None else Value(UNKNOWN)
            self.bind(stmt.target, value)
        elif isinstance(stmt, ast.AugAssign):
            self.expr(stmt.value)
            self.expr(stmt.target)
        elif isinstance(stmt, ast.Expr):
            self.expr(stmt.value)
        elif isinstance(stmt, ast.If):
            self.expr(stmt.test)
            self.statements(stmt.body)
            self.statements(stmt.orelse)
        elif isinstance(stmt, ast.For):
            iterable = self.expr(stmt.iter)
            self.bind(stmt.target, self.element_of(iterable))
            self.statements(stmt.body)
            self.statements(stmt.orelse)
        elif isinstance(stmt, ast.While):
            if isinstance(stmt.test, ast.Constant) and stmt.test.value:
                self.fail("banned_while_true", "`while True` loops are not allowed")
            self.expr(stmt.test)
            self.statements(stmt.body)
        elif isinstance(stmt, ast.With):
            for item in stmt.items:
                self.with_item(item)
            self.statements(stmt.body)
        elif isinstance(stmt, ast.Pass | ast.Break | ast.Continue):
            pass
        elif isinstance(stmt, ast.Try | ast.TryStar):
            self.fail(
                "banned_try", "`try` is not allowed: it can swallow failures (use pytest.raises)"
            )
        elif isinstance(stmt, ast.Global | ast.Nonlocal):
            self.fail("banned_scope", "`global`/`nonlocal` are not allowed")
        elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            self.fail("banned_definition", f"nested definition `{stmt.name}` is not allowed")
        elif isinstance(stmt, ast.Import | ast.ImportFrom):
            self.fail("banned_import", "imports belong at the top of the module")
        else:
            self.fail("banned_statement", f"`{type(stmt).__name__}` statements are not allowed")

    def with_item(self, item: ast.withitem) -> None:
        context = item.context_expr
        is_raises = (
            isinstance(context, ast.Call)
            and isinstance(context.func, ast.Attribute)
            and isinstance(context.func.value, ast.Name)
            and context.func.value.id == "pytest"
            and context.func.attr == "raises"
        )
        if not is_raises:
            self.fail("banned_with", "only `with pytest.raises(...)` is allowed")
        self.expr(context)
        if is_raises:
            self.assertions += 1
            self.visible_assertions += 1  # the body's screen action is what raises
        if item.optional_vars is not None:
            self.bind(item.optional_vars, Value(UNKNOWN))

    def assertion(self, test: ast.expr) -> None:
        self.assertions += 1
        value = self.expr(test)
        if isinstance(test, ast.Constant | ast.JoinedStr):
            self.fail("tautology_literal", f"`assert {ast.unparse(test)}` asserts a literal")
            return
        if isinstance(test, ast.Tuple | ast.List | ast.Set | ast.Dict) and _nonempty(test):
            self.fail("tautology_literal", f"`assert {ast.unparse(test)}` is always true")
            return
        if isinstance(test, ast.Compare) and len(test.ops) == 1:
            left, right = test.left, test.comparators[0]
            same = ast.dump(left) == ast.dump(right)
            if same and isinstance(test.ops[0], ast.Eq | ast.Is | ast.LtE | ast.GtE):
                self.fail(
                    "tautology_self", f"`assert {ast.unparse(test)}` compares a value to itself"
                )
                return
            never_none = isinstance(right, ast.Constant) and right.value is None
            if never_none and isinstance(test.ops[0], ast.IsNot | ast.NotEq):
                left_type = self.expr(left).type
                if left_type != UNKNOWN and not left_type.optional and left_type.name != "Any":
                    self.fail(
                        "tautology_never_none",
                        f"`assert {ast.unparse(test)}`: the SDK never returns None there",
                    )
                    return
        if value.roots - HELPERS:
            self.visible_assertions += 1

    def bind(self, target: ast.expr, value: Value) -> None:
        if isinstance(target, ast.Name):
            if target.id.startswith("_"):
                self.fail("banned_private", f"name `{target.id}` starts with `_`")
            if target.id == "game":
                self.fail("rebind_game", "the `game` fixture must not be reassigned")
            self.env[target.id] = value
        elif isinstance(target, ast.Tuple | ast.List):
            parts = value.type.args if value.type.name == "tuple" else ()
            for i, element in enumerate(target.elts):
                part = parts[i] if i < len(parts) else UNKNOWN
                self.bind(element, Value(part, value.roots))
        else:
            self.expr(target)

    def element_of(self, value: Value) -> Value:
        t = value.type
        if t.name in ("list", "set", "tuple", "reversed") and len(t.args) == 1:
            return Value(t.args[0], value.roots)
        if t.name == "dict" and t.args:
            return Value(t.args[0], value.roots)
        if t.name == "enumerate" and t.args:
            return Value(T("tuple", (T("int"), t.args[0])), value.roots)
        return Value(UNKNOWN, value.roots)

    # --- expressions ----------------------------------------------------------------------

    def expr(self, node: ast.expr) -> Value:
        if isinstance(node, ast.Constant):
            return Value(T(type(node.value).__name__))
        if isinstance(node, ast.Name):
            return self.name(node)
        if isinstance(node, ast.Attribute):
            return self.attribute(node)
        if isinstance(node, ast.Call):
            return self.call(node)
        if isinstance(node, ast.Subscript):
            container = self.expr(node.value)
            self.expr(node.slice)
            element = self.element_of(container)
            if isinstance(node.slice, ast.Slice):
                return container
            return element
        if isinstance(node, ast.Lambda):
            saved = dict(self.env)
            for arg in node.args.args:
                self.env[arg.arg] = Value(UNKNOWN)
            self.expr(node.body)
            self.env = saved
            return Value(UNKNOWN)
        if isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp):
            return self.comprehension(node)
        if isinstance(node, ast.NamedExpr):
            value = self.expr(node.value)
            self.bind(node.target, value)
            return value
        if isinstance(node, ast.Await | ast.Yield | ast.YieldFrom):
            self.fail("banned_async", "`await`/`yield` are not allowed")
            return Value(UNKNOWN)
        if isinstance(node, ast.Starred):
            return self.expr(node.value)
        roots: set[str] = set()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                roots |= self.expr(child).roots
        if isinstance(node, ast.Compare | ast.BoolOp) or (
            isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not)
        ):
            return Value(T("bool"), frozenset(roots))
        if isinstance(node, ast.List | ast.Tuple | ast.Set):
            return Value(T(type(node).__name__.lower(), (UNKNOWN,)), frozenset(roots))
        return Value(UNKNOWN, frozenset(roots))

    def comprehension(
        self, node: ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp
    ) -> Value:
        saved = dict(self.env)
        roots: set[str] = set()
        for generator in node.generators:
            iterable = self.expr(generator.iter)
            roots |= iterable.roots
            self.bind(generator.target, self.element_of(iterable))
            for condition in generator.ifs:
                roots |= self.expr(condition).roots
        if isinstance(node, ast.DictComp):
            roots |= self.expr(node.key).roots | self.expr(node.value).roots
            result = Value(T("dict", (UNKNOWN, UNKNOWN)), frozenset(roots))
        else:
            element = self.expr(node.elt)
            roots |= element.roots
            result = Value(T("list", (element.type,)), frozenset(roots))
        self.env = saved
        return result

    def name(self, node: ast.Name) -> Value:
        ident = node.id
        if ident.startswith("_"):
            self.fail("banned_private", f"name `{ident}` starts with `_`")
            return Value(UNKNOWN)
        if ident in BANNED_CALLS:
            self.fail("banned_call", f"`{ident}` is not allowed")
            return Value(UNKNOWN)
        if ident in self.env:
            return self.env[ident]
        if ident in BUILTINS or ident in ("True", "False", "None"):
            return Value(T("builtin:" + ident))
        if ident == "pytest" and "pytest" in self.imported:
            return Value(T("module:pytest"))
        if ident in self.imported:
            return Value(T("export:" + ident))
        self.fail("unknown_name", f"unknown name `{ident}`")
        return Value(UNKNOWN)

    def attribute(self, node: ast.Attribute) -> Value:
        if node.attr.startswith("_"):
            self.fail("banned_private", f"attribute `{node.attr}` starts with `_`")
            return Value(UNKNOWN)
        base = self.expr(node.value)
        name = base.type.name
        if name == "Game":
            roots = base.roots | {node.attr if node.attr in self.page_names else APP}
            return self.member(name, node.attr, frozenset(roots))
        if name in self.classes:
            return self.member(name, node.attr, base.roots)
        if name in self.records:
            fields = self.records[name]["fields"]
            if node.attr not in fields:
                self.fail("unknown_sdk_member", f"`{name}` has no field `{node.attr}`")
                return Value(UNKNOWN, base.roots)
            return Value(parse_type(fields[node.attr]), base.roots)
        if name.startswith("export:"):
            export = name.split(":", 1)[1]
            if export in self.enums:
                values = self.enums[export]
                if node.attr.lower() not in values:
                    self.fail("unknown_sdk_member", f"`{export}` has no member `{node.attr}`")
                return Value(T(export))
            self.fail("unknown_sdk_member", f"`{export}.{node.attr}` is not part of pg_sdk")
            return Value(UNKNOWN)
        if name in self.enums:
            if node.attr not in ("value", "name"):
                self.fail("unknown_sdk_member", f"`{name}` values have no `{node.attr}`")
            return Value(T("str"), base.roots)
        if name == "module:pytest":
            if node.attr in BANNED_PYTEST:
                self.fail("banned_pytest", f"`pytest.{node.attr}` is not allowed")
            elif node.attr not in ALLOWED_PYTEST:
                self.fail("unknown_name", f"`pytest.{node.attr}` is not allowed")
            return Value(T("pytest:" + node.attr))
        builtin_methods = BUILTIN_METHODS.get(name)
        if builtin_methods is not None:
            if node.attr not in builtin_methods:
                self.fail("unknown_attribute", f"`{name}` has no method `{node.attr}` here")
            return Value(T("method:" + name + "." + node.attr, base.type.args), base.roots)
        if base.type == UNKNOWN:
            known = {f for r in self.records.values() for f in r["fields"]}
            known |= {m for methods in BUILTIN_METHODS.values() for m in methods}
            if node.attr not in known:
                self.fail(
                    "unknown_attribute",
                    f"cannot resolve `.{node.attr}` on `{ast.unparse(node.value)}`",
                )
            return Value(UNKNOWN, base.roots)
        self.fail("unknown_attribute", f"`{name}` values have no attribute `{node.attr}`")
        return Value(UNKNOWN, base.roots)

    @property
    def page_names(self) -> frozenset[str]:
        members = self.classes["Game"]["members"]
        return frozenset(n for n, m in members.items() if m["kind"] == "property")

    def member(self, cls: str, attr: str, roots: frozenset[str]) -> Value:
        members = self.classes[cls]["members"]
        if attr not in members:
            self.fail(
                "unknown_sdk_member", f"`{cls}` has no member `{attr}` (not in the SDK manifest)"
            )
            return Value(UNKNOWN, roots)
        info = members[attr]
        self.pages.update(roots)
        if info["kind"] == "property":
            return Value(parse_type(info["returns"]), roots)
        return Value(T("sdk_method"), roots, method_of=f"{cls}.{attr}")

    def call(self, node: ast.Call) -> Value:
        func = self.expr(node.func)
        arg_values = [self.expr(a) for a in node.args]
        kw_values = [self.expr(k.value) for k in node.keywords]
        roots = func.roots.union(*(v.roots for v in arg_values), *(v.roots for v in kw_values))
        if func.method_of is not None:
            return self.sdk_call(node, func.method_of, frozenset(roots))
        name = func.type.name
        if name.startswith("builtin:"):
            return Value(self.builtin_result(name.split(":", 1)[1], arg_values), frozenset(roots))
        if name.startswith("pytest:"):
            return Value(T(name), frozenset(roots))
        if name.startswith("method:"):
            return Value(UNKNOWN, frozenset(roots))
        if name.startswith("export:"):
            export = name.split(":", 1)[1]
            if export in self.manifest["exceptions"]:
                return Value(T(export), frozenset(roots))
            self.fail("banned_constructor", f"constructing `{export}` in a test is not allowed")
            return Value(UNKNOWN, frozenset(roots))
        if func.type == UNKNOWN:
            return Value(UNKNOWN, frozenset(roots))
        self.fail("not_callable", f"`{ast.unparse(node.func)}` is not callable")
        return Value(UNKNOWN, frozenset(roots))

    def sdk_call(self, node: ast.Call, qualified: str, roots: frozenset[str]) -> Value:
        cls, method = qualified.split(".")
        info = self.classes[cls]["members"][method]
        params = info["params"]
        names = [p["name"] for p in params]
        required = [p["name"] for p in params if p["required"]]
        if any(isinstance(a, ast.Starred) for a in node.args) or any(
            k.arg is None for k in node.keywords
        ):
            self.fail("bad_arguments", f"`{qualified}`: *args/**kwargs are not allowed")
        positional = len(node.args)
        keywords = [k.arg for k in node.keywords if k.arg is not None]
        unknown = [k for k in keywords if k not in names]
        if positional > len(names):
            self.fail(
                "bad_arguments",
                f"`{qualified}` takes at most {len(names)} argument(s), got {positional}",
            )
        elif unknown:
            self.fail("bad_arguments", f"`{qualified}` has no parameter {', '.join(unknown)}")
        else:
            given = set(names[:positional]) | set(keywords)
            missing = [r for r in required if r not in given]
            if missing:
                self.fail("bad_arguments", f"`{qualified}` is missing {', '.join(missing)}")
        self.actions += 1
        self.calls.append(qualified)
        self.pages.update(roots)
        return Value(parse_type(info["returns"]), roots)

    def builtin_result(self, name: str, args: list[Value]) -> T:
        first = args[0].type if args else UNKNOWN
        if name in ("len", "int", "round", "sum"):
            return T("int")
        if name in ("str",):
            return T("str")
        if name in ("float",):
            return T("float")
        if name in ("bool", "any", "all", "isinstance"):
            return T("bool")
        if name in ("sorted", "list", "reversed"):
            return (
                T("list", first.args or (UNKNOWN,))
                if first.name in ("list", "set", "tuple")
                else T("list", (UNKNOWN,))
            )
        if name in ("min", "max"):
            return first.args[0] if first.name in ("list", "set") and first.args else UNKNOWN
        if name == "set":
            return T("set", first.args or (UNKNOWN,))
        if name == "enumerate":
            return T("enumerate", first.args or (UNKNOWN,))
        if name == "range":
            return T("list", (T("int"),))
        return UNKNOWN


def _nonempty(node: ast.Tuple | ast.List | ast.Set | ast.Dict) -> bool:
    return bool(node.keys if isinstance(node, ast.Dict) else node.elts)


# --- the module -----------------------------------------------------------------------------


def _check_imports(tree: ast.Module, checker: _Checker) -> None:
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "pytest" and alias.asname is None:
                    checker.imported.add("pytest")
                elif alias.name == "pg_sdk" and alias.asname is None:
                    checker.fail("banned_import", "use `from pg_sdk import ...`")
                else:
                    checker.fail("banned_import", f"import of `{alias.name}` is not allowed")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "__future__" and node.level == 0:
                continue
            if module != "pg_sdk" or node.level != 0:
                checker.fail(
                    "banned_import", f"import from `{'.' * node.level}{module}` is not allowed"
                )
                continue
            for alias in node.names:
                if alias.name == "*" or alias.asname is not None:
                    checker.fail("banned_import", "star or renamed pg_sdk imports are not allowed")
                elif alias.name not in checker.exports:
                    checker.fail("unknown_sdk_member", f"`pg_sdk` exports no `{alias.name}`")
                else:
                    checker.imported.add(alias.name)


def _spec_ids(func: ast.FunctionDef, checker: _Checker) -> tuple[str, ...]:
    ids: list[str] = []
    markers = 0
    for decorator in func.decorator_list:
        text = ast.unparse(decorator)
        if not text.startswith("pytest.mark.spec(") or not isinstance(decorator, ast.Call):
            checker.fail("banned_decorator", f"decorator `@{text}` is not allowed")
            continue
        markers += 1
        for arg in decorator.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                ids.append(arg.value)
            else:
                checker.fail("bad_spec_marker", "spec IDs must be string literals")
    if markers != 1 or not ids:
        checker.fail("missing_spec_marker", "the test needs one @pytest.mark.spec(...) with IDs")
    return tuple(ids)


def check_static(
    code: str,
    manifest: Mapping[str, Any],
    known_spec_ids: frozenset[str],
    limits: Limits | None = None,
) -> tuple[GateResult, StaticReport]:
    """Run G1 on one candidate's source code."""
    limits = limits or Limits()
    lines = sum(1 for line in code.splitlines() if line.strip())
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        result = GateResult(
            gate=GATE, passed=False, reasons=(reason("syntax_error", f"does not parse: {exc.msg}"),)
        )
        return result, StaticReport(lines=lines)

    checker = _Checker(manifest=manifest)
    _check_imports(tree, checker)
    functions: list[ast.FunctionDef] = []
    for index, node in enumerate(tree.body):
        if isinstance(node, ast.Import | ast.ImportFrom):
            continue
        if index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # module docstring
        if isinstance(node, ast.FunctionDef):
            functions.append(node)
        else:
            checker.fail("banned_module_code", f"top-level `{type(node).__name__}` is not allowed")

    test_name: str | None = None
    spec_ids: tuple[str, ...] = ()
    if len(functions) != 1 or not functions[0].name.startswith("test_"):
        checker.fail(
            "one_test_function", "the module must define exactly one test function `test_*`"
        )
    if functions:
        func = functions[0]
        test_name = func.name
        args = func.args
        params = [a.arg for a in args.posonlyargs + args.args]
        if params != ["game"] or args.vararg or args.kwarg or args.kwonlyargs:
            checker.fail("fixture_signature", "the test must take exactly one parameter: `game`")
        spec_ids = _spec_ids(func, checker)
        unknown_specs = [s for s in spec_ids if s not in known_spec_ids]
        if unknown_specs:
            checker.fail("unknown_spec", f"unknown spec ID(s): {', '.join(unknown_specs)}")
        checker.env["game"] = Value(T("Game"))
        checker.statements(func.body)
        for sub in ast.walk(func):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id in BANNED_CALLS
            ):
                checker.fail("banned_call", f"`{sub.func.id}` is not allowed")

    if checker.assertions == 0:
        checker.fail("no_assertion", "the test has no assertion")
    elif checker.visible_assertions == 0:
        checker.fail(
            "no_visible_assertion",
            "no assertion checks something the player sees (only game.player / game.setup values)",
        )
    if lines > limits.max_lines:
        checker.fail("too_long", f"{lines} lines; the limit is {limits.max_lines}")
    if checker.actions > limits.max_actions:
        checker.fail(
            "too_many_actions", f"{checker.actions} SDK calls; the limit is {limits.max_actions}"
        )

    report = StaticReport(
        test_name=test_name,
        spec_ids=spec_ids,
        pages_used=tuple(sorted(checker.pages)),
        calls=tuple(checker.calls),
        assertions=checker.assertions,
        visible_assertions=checker.visible_assertions,
        lines=lines,
    )
    result = GateResult(
        gate=GATE,
        passed=not checker.problems,
        reasons=tuple(checker.problems),
        metrics={
            "lines": lines,
            "actions": checker.actions,
            "assertions": checker.assertions,
            "visible_assertions": checker.visible_assertions,
        },
    )
    return result, report


def spec_ids_from_markdown(texts: Iterable[str]) -> frozenset[str]:
    """Spec IDs defined in specs/*.md lines of the form `- STORE-3: ...` (retired ones excluded)."""
    ids: set[str] = set()
    for text in texts:
        for match in re.finditer(r"^- ([A-Z]+-\d+): (.*)$", text, re.MULTILINE):
            if "(retired)" not in match.group(2):
                ids.add(match.group(1))
    return frozenset(ids)
