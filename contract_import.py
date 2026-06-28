"""Import pufworks_contracts from sibling PUFworks-contracts checkout."""
import sys
from pathlib import Path


def _contracts_python_root():
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "PUFworks-contracts" / "python",
    ]
    for path in candidates:
        init = path / "pufworks_contracts" / "__init__.py"
        if init.is_file():
            return path.resolve()
    return None


def load():
    root = _contracts_python_root()
    if root is None:
        raise ImportError(
            "PUFworks-contracts not found. Expected sibling at "
            "../PUFworks-contracts/python (workshop layout under C:\\Projects)."
        )
    root_s = str(root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)
    import pufworks_contracts  # noqa: WPS433 — intentional lazy import

    return pufworks_contracts
