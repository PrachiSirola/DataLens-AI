"""Protected dashboard route — placeholder for the data analyst features."""

from fastapi import APIRouter, Depends
from app.middleware.auth import get_current_user

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/")
async def dashboard_home(user: dict = Depends(get_current_user)):
    """Protected endpoint — only accessible after login."""
    return {
        "message": f"Welcome to your workspace, {user['name']}!",
        "user_id": user["id"],
        "features": [
            "Upload CSV / PDF / DOCX files",
            "Natural language data queries",
            "Interactive charts & reports",
            "Export to CSV / Excel / PDF",
        ],
    }
