from typing import Optional
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
    return templates.TemplateResponse(request, "admin/nominations.html", {"nominations": nominations})


@router.post("/nominations")
def create_nomination(
    name: str = Form(...),
    type: NominationType = Form(...),
    pick_limit: Optional[int] = Form(None),
    year_filter: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    limit = pick_limit if type == NominationType.PICK else None
    db.add(Nomination(name=name, type=type, pick_limit=limit, year_filter=year_filter))
    db.commit()
    return RedirectResponse(url="/admin/nominations", status_code=303)


@router.get("/nominations/{nom_id}", response_class=HTMLResponse)
def nomination_detail(nom_id: int, request: Request, db: Session = Depends(get_db)):
    nom = db.get(Nomination, nom_id)
    if not nom:
        return HTMLResponse("Номинация не найдена.", status_code=404)
    # Apply year_filter if set
    film_q = db.query(Film).order_by(Film.title)
    if nom.year_filter:
        film_q = film_q.filter(Film.year == nom.year_filter)
    films = film_q.all()
    persons = db.query(Person).order_by(Person.name).all()
    return templates.TemplateResponse(
        request, "admin/nomination_detail.html",
        {"nom": nom, "films": films, "persons": persons},
    )


@router.post("/nominations/{nom_id}/nominees")
def add_nominee_via_nomination(
    nom_id: int,
    film_id: int = Form(...),
    # Accept as str then convert — empty string means None
    person_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    nom = db.get(Nomination, nom_id)
    if not nom:
        return RedirectResponse(url="/admin/nominations", status_code=303)

    # Normalise person_id: empty string or RANK type -> None
    pid: Optional[int] = None
    if nom.type == NominationType.PICK and person_id and person_id.strip():
        try:
            pid = int(person_id)
        except ValueError:
            pid = None

    db.add(Nominee(nomination_id=nom_id, film_id=film_id, person_id=pid))
    db.commit()
    return RedirectResponse(url=f"/admin/nominations/{nom_id}", status_code=303)
