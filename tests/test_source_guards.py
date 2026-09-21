"""Guards against mistakes that produce silence rather than errors.

These do not test behaviour; they read the source. That is justified only for
failures which are invisible at runtime, and there is exactly one so far.

`dbus.service.BusName` releases the bus name from `__del__`. Calling it
without keeping the result acquires the name and hands it straight back when
the temporary is collected. Nothing raises. The daemon logs that it owns the
name, the feed script calls into it, the bus answers "no such service", and
KWin discards that answer because `callDBus` is fire-and-forget. The symptom
is a daemon that starts cleanly and does nothing at all -- which cost a full
round of diagnosis on real hardware to find, twice narrowing a silence that
had no error anywhere in it.

The probe that proved this path works happened to write `name = BusName(...)`.
The daemon did not. One character of difference, and no way to see it.
"""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

#: Calls whose result must be bound to a name, and why.
MUST_BE_KEPT = {
    "BusName": "dbus.service.BusName releases the name when garbage collected",
}


def python_sources():
    for path in sorted((ROOT / "src").rglob("*.py")):
        yield path
    for path in sorted((ROOT / "tools").glob("*.py")):
        yield path
    yield ROOT / "bin" / "wcsd"


def discarded_calls(tree):
    """Calls whose value is thrown away -- bare expression statements."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        name = (func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name) else "")
        yield node.lineno, name


class DiscardedResultTest(unittest.TestCase):
    def test_no_source_throws_away_a_result_it_must_keep(self):
        offences = []
        for path in python_sources():
            tree = ast.parse(path.read_text(), filename=str(path))
            for lineno, name in discarded_calls(tree):
                if name in MUST_BE_KEPT:
                    offences.append(
                        f"{path.relative_to(ROOT)}:{lineno}: {name}() result "
                        f"discarded -- {MUST_BE_KEPT[name]}"
                    )
        self.assertEqual(offences, [], "\n" + "\n".join(offences))

    def test_the_guard_actually_catches_the_shape_it_is_meant_to(self):
        # Without this, a rename would silently turn the check into a no-op.
        tree = ast.parse("import dbus\ndbus.service.BusName('a.b', bus)\n")
        self.assertIn("BusName", [name for _, name in discarded_calls(tree)])

    def test_the_guard_accepts_the_corrected_form(self):
        tree = ast.parse("import dbus\nkeep = dbus.service.BusName('a.b', bus)\n")
        self.assertNotIn("BusName", [name for _, name in discarded_calls(tree)])

    def test_every_source_file_parses(self):
        # Cheap, and it covers bin/wcsd, which no import reaches.
        for path in python_sources():
            ast.parse(path.read_text(), filename=str(path))


if __name__ == "__main__":
    unittest.main()


class NoWritingIntoTheCheckoutTest(unittest.TestCase):
    """A tool must not drop files into the directory it was run from.

    `--write-rule` defaulted to a bare filename, so it wrote into the working
    directory -- in practice the git checkout. The author found an untracked
    `71-wayland-cursor-smoother.rules` and had to ask whether it mattered. It
    did not: the copy that takes effect is the one installed under
    /etc/udev/rules.d, and that one is a staging file. Leaving something in a
    repository that looks like it might be part of the project is its own
    small cost.
    """

    @staticmethod
    def load(relative):
        import importlib.util
        path = ROOT / relative
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_rule_is_staged_outside_the_working_directory(self):
        probe = self.load("tools/probe_evdev_access.py")
        default = Path(probe.DEFAULT_RULE_OUTPUT)
        self.assertTrue(default.is_absolute(), default)
        self.assertNotIn(ROOT, default.parents, default)

    def test_the_config_default_goes_to_stdout_rather_than_a_file(self):
        # wcsd --write-config with no argument prints; writing a file is an
        # explicit choice, which is the same principle from the other side.
        source = (ROOT / "bin" / "wcsd").read_text()
        self.assertIn('"--write-config", nargs="?", const="-"', source)
