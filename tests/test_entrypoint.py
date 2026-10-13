import ast
import subprocess
import sys
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / "victor" / "__main__.py"


def test_nothing_is_defined_below_the_entry_point():
    """`python -m victor` runs main() when it reaches the entry block; anything defined after it does not exist yet."""
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    last = tree.body[-1]
    assert isinstance(last, ast.If) and "__name__" in ast.unparse(last.test), "the entry block must be the last statement"


def test_module_runs_as_a_program():
    out = subprocess.run([sys.executable, "-m", "victor", "--help"], capture_output=True, text=True, cwd=MAIN.parents[1])
    assert out.returncode == 0 and "loop" in out.stdout
