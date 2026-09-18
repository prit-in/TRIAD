"""Version 1 of the REST API (PRD section 25).

One router per resource group, all mounted under `/api/v1`. The prefix lives here rather than
in each module, so a second version can be added beside this one without touching a handler.
"""

from fastapi import APIRouter

from backend.app.api.v1 import approvals, auth, datasets, insights, models, pipelines

api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(auth.router)
api_v1.include_router(datasets.router)
api_v1.include_router(pipelines.router)
api_v1.include_router(models.router)
api_v1.include_router(approvals.router)
api_v1.include_router(insights.router)

__all__ = ["api_v1"]
