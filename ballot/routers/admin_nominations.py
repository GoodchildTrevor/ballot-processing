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


def _parse_int(v: Optional[str]) -> Optional[int]:
    if v and v.strip():
        try:
            return int(v)
        except ValueError:
            pass
    return None


def _get_years(db: Session) -> list[int]:
    rows = db.query(Film.year).distinct().order_by(Film.year.desc()).all()
    return [r[0] for r in rows]


@router.get("/nominations", response_class=HTMLResponse)
def list_nominations(request: Request, db: Session = Depends(get_db)):
    nominations = db.query(Nomination).order_by(Nomination.sort_order, Nomination.id).all()
    years = _get_years(db)
    return templates.TemplateResponse(request, "admin/nominations.html", {
        "nominations": nominations,
        "years": years,
    })


@router.post("/nominations")
def create_nomination(
    name: str = Form(...),
    type: NominationType = Form(...),
    pick_min: Optional[str] = Form(None),
    pick_max: Optional[str] = Form(None),
    nominees_count: Optional[str] = Form(None),
    year_filter: str = Form(...),
    db: Session = Depends(get_db),
):
    pmin = _parse_int(pick_min) if type == NominationType.PICK else None
    pmax = _parse_int(pick_max) if type == NominationType.PICK else None
    nc = _parse_int(nominees_count)
    yf = _parse_int(year_filter)
    last = db.query(Nomination).order_by(Nomination.sort_order.desc()).first()
    order = (last.sort_order + 1) if last else 0
    db.add(Nomination(
        name=name, type=type,
        pick_min=pmin, pick_max=pmax,
        nominees_count=nc,
        year_filter=yf, sort_order=order,
    ))
    db.commit()
    return RedirectResponse(url="/admin/nominations", status_code=303)


@router.post("/nominations/{nom_id}/edit")
def edit_nomination(
    nom_id: int,
    name: str = Form(...),
    type: NominationType = Form(...),
    pick_min: Optional[str] = Form(None),
    pick_max: Optional[str] = Form(None),
    nominees_count: Optional[str] = Form(None),
    year_filter: str = Form(...),
    db: Session = Depends(get_db),
):
    nom = db.get(Nomination, nom_id)
    if not nom:
        return RedirectResponse(url="/admin/nominations", status_code=303)
    nom.name = name.strip()
    nom.type = type
    nom.pick_min = _parse_int(pick_min) if type == NominationType.PICK else None
    nom.pick_max = _parse_int(pick_max) if type == NominationType.PICK else None
    nom.nominees_count = _parse_int(nominees_count)
    nom.year_filter = _parse_int(year_filter)
    db.commit()
    return RedirectResponse(url="/admin/nominations", status_code=303)


@router.post("/nominations/{nom_id}/delete")
def delete_nomination(nom_id: int, db: Session = Depends(get_db)):
    nom = db.get(Nomination, nom_id)
    if nom:
        db.query(Nominee).filter(Nominee.nomination_id == nom_id).delete()
        db.delete(nom)
        db.commit()
    return RedirectResponse(url="/admin/nominations", status_code=303)


@router.post("/nominations/{nom_id}/move")
def move_nomination(
    nom_id: int,
    direction: str = Form(...),
    db: Session = Depends(get_db),
):
    noms = db.query(Nomination).order_by(Nomination.sort_order, Nomination.id).all()
    idx = next((i for i, n in enumerate(noms) if n.id == nom_id), None)
    if idx is None:
        return RedirectResponse(url="/admin/nominations", status_code=303)
    swap_idx = idx - 1 if direction == "up" else idx + 1
    if 0 <= swap_idx < len(noms):
        noms[idx].sort_order, noms[swap_idx].sort_order = noms[swap_idx].sort_order, noms[idx].sort_order
        for i, n in enumerate(sorted(noms, key=lambda x: x.sort_order)):
            n.sort_order = i
        db.commit()
    return RedirectResponse(url="/admin/nominations", status_code=303)


@router.get("/nominations/{nom_id}", response_class=HTMLResponse)
def nomination_detail(nom_id: int, request: Request, db: Session = Depends(get_db)):
    nom = db.get(Nomination, nom_id)
    if not nom:
        return HTMLResponse("Номинация не найдена.", status_code=404)
    film_q = db.query(Film).order_by(Film.title)
    if nom.year_filter:
        film_q = film_q.filter(Film.year == nom.year_filter)
    films = film_q.all()
    persons = db.query(Person).order_by(Person.name).all()
    years = _get_years(db)
    return templates.TemplateResponse(
        request, "admin/nomination_detail.html",
        {"nom": nom, "films": films, "persons": persons, "years": years},
    )


@router.post("/nominations/{nom_id}/nominees")
def add_nominee_via_nomination(
    nom_id: int,
    film_id: int = Form(...),
    person_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    nom = db.get(Nomination, nom_id)
    if not nom:
        return RedirectResponse(url="/admin/nominations", status_code=303)
    pid = _parse_int(person_id) if nom.type == NominationType.PICK else None
    db.add(Nominee(nomination_id=nom_id, film_id=film_id, person_id=pid))
    db.commit()
    return RedirectResponse(url=f"/admin/nominations/{nom_id}", status_code=303)
