"""CLI entrypoints for development."""

import uvicorn


def dev() -> None:
    """Run the dev server with reload."""
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
