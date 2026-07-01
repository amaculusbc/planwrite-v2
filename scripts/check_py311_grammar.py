"""Pre-deploy guard: the Railway image runs python:3.11-slim while local dev is 3.13.

Parses every app module with 3.11 grammar rules so PEP 701 f-strings (and other
3.12+ syntax) fail here instead of crashing the app on import after deploy.
"""
import ast
import pathlib
import sys

bad = []
for path in pathlib.Path("app").rglob("*.py"):
    try:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 11))
    except SyntaxError as exc:
        bad.append(f"{path}:{exc.lineno}: {exc.msg}")

if bad:
    print("\n".join(bad))
    sys.exit(1)
print("3.11 grammar: clean")
