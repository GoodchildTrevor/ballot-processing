from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from ballot.database import get_db
from ballot.models import Nomination, NominationType, Nominee, Film, Person
from ballot.auth import require_admin

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="ballot/templates")


@router.get("/nominations", response_class=HTMLResponse)
def list_nominations(request: Request, db: Session = Depends(get_db)):
    nominations = db.query(Nomination).all()
    return templates.TemplateResponse(
        "admin/nominations.html", {"request": request, "nominations": nominations}
    )


@router.post("/nominations")
def create_nomination(
    name: str = Form(...),
    type: NominationType = Form(...),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    nom = Nomination(name=name, type=type)
    db.add(nom)
    db.commit()
    return RedirectResponse(url="/admin/nominations", status_code=303)


@router.get("/nominations/{nom_id}", response_class=HTMLResponse)
def nomination_detail(nom_id: int, request: Request, db: Session = Depends(get_db)):
    nom = db.get(Nomination, nom_id)
    if not nom:
        return HTMLResponse("Nomination not found", status_code=404)
    films = db.query(Film).order_by(Film.title).all()
    persons = db.query(Person).order_by(Person.name).all()
    return templates.TemplateResponse(
        "admin/nomination_detail.html",
        {"request": request, "nom": nom, "films": films, "persons": persons},
    )
