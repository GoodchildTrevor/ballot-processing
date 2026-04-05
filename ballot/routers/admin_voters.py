from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from ballot.database import get_db
from ballot.models import Voter
from ballot.auth import require_admin

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="ballot/templates")


@router.get("/voters", response_class=HTMLResponse)
def list_voters(request: Request, db: Session = Depends(get_db)):
    voters = db.query(Voter).order_by(Voter.name).all()
    return templates.TemplateResponse(request, "admin/voters.html", {"voters": voters})
