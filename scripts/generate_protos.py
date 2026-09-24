from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

from grpc_tools import protoc

ROOT = Path(__file__).resolve().parents[1]
PROTO_ROOT = ROOT / "proto"
OUTPUT = ROOT / "src" / "tableforge" / "_generated"
PACKAGE = "tableforge._generated"


def main() -> int:
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir(parents=True)
    well_known = Path(protoc.__file__).resolve().parent / "_proto"
    sources = sorted(str(path) for path in (PROTO_ROOT / "tableforge" / "v1").glob("*.proto"))
    result = protoc.main(
        [
            "grpc_tools.protoc",
            f"-I{PROTO_ROOT}",
            f"-I{well_known}",
            f"--python_out={OUTPUT}",
            f"--grpc_python_out={OUTPUT}",
            f"--mypy_out={OUTPUT}",
            f"--mypy_grpc_out={OUTPUT}",
            *sources,
        ]
    )
    if result != 0:
        return int(result)
    for directory in (OUTPUT, OUTPUT / "tableforge", OUTPUT / "tableforge" / "v1"):
        (directory / "__init__.py").write_text("", encoding="utf-8")
    pattern = re.compile(r"^from tableforge\.v1 import", re.MULTILINE)
    for path in (OUTPUT / "tableforge" / "v1").iterdir():
        if path.suffix in {".py", ".pyi"}:
            text = path.read_text(encoding="utf-8")
            text = pattern.sub(f"from {PACKAGE}.tableforge.v1 import", text)
            text = text.replace("import tableforge.v1.", f"import {PACKAGE}.tableforge.v1.")
            text = text.replace(" tableforge.v1.", f" {PACKAGE}.tableforge.v1.")
            path.write_text(text, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
