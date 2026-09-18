from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from terraform_drift_dashboard.routers import scanner, ui

app = FastAPI(title="Terraform Drift Dashboard")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = PROJECT_ROOT / "static"

# Mount static folder
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Include Routers
app.include_router(ui.router)
app.include_router(scanner.router)


def start():
    """CLI Entrypoint executed by `uv run terraform-drift-dashboard`."""
    import uvicorn
    uvicorn.run(
        "terraform_drift_dashboard.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    start()
