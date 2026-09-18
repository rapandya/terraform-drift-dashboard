from pathlib import Path
from fastapi import APIRouter, Response
from fastapi.responses import FileResponse

router = APIRouter(tags=["UI"])

# Path resolution: ui.py -> routers -> terraform_drift_dashboard -> src -> project_root
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
STATIC_DIR = PROJECT_ROOT / "static"


@router.get("/", response_class=FileResponse)
async def read_root():
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Silences browser 404 logs if no favicon file exists."""
    favicon_path = STATIC_DIR / "favicon.ico"
    if favicon_path.exists():
        return FileResponse(favicon_path)
    return Response(status_code=204)