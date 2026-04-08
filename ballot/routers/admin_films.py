from typing import Optional
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from ballot.database import get_db
from ballot.models import Film, Nominee, Nomination, Person, NominationType
from ballot.auth import require_admin

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="ballot/templates")


@router.get("/films", response_class=HTMLResponse)
def list_films(request: Request, db: Session = Depends(get_db)):
    films = db.query(Film).order_by(Film.year.desc(), Film.title).all()
    return templates.TemplateResponse(request, "admin/films.html", {"films": films})


@router.post("/films")
def create_film(
    title: str = Form(...),
    year: int = Form(...),
    db: Session = Depends(get_db),
):
    existing = db.query(Film).filter(Film.title == title, Film.year == year).first()
    if existing:
        return RedirectResponse(
            url=f"/admin/films?error=duplicate&title={title}&year={year}", status_code=303
        )
    db.add(Film(title=title, year=year))
    db.commit()
    return RedirectResponse(url="/admin/films", status_code=303)


@router.post("/films/{film_id}/edit")
def edit_film(
    film_id: int,
    title: str = Form(...),
    year: int = Form(...),
    db: Session = Depends(get_db),
):
    film = db.get(Film, film_id)
    if film:
        film.title = title.strip()
        film.year = year
        db.commit()
    return RedirectResponse(url="/admin/films", status_code=303)


@router.get("/films/{film_id}", response_class=HTMLResponse)
def film_detail(film_id: int, request: Request, db: Session = Depends(get_db)):
    film = db.get(Film, film_id)
    if not film:
        return HTMLResponse("Фильм не найден.", status_code=404)
    nominations = db.query(Nomination).order_by(Nomination.sort_order, Nomination.id).all()
    persons = db.query(Person).order_by(Person.name).all()
    return templates.TemplateResponse(
        request, "admin/film_detail.html",
        {"film": film, "nominations": nominations, "persons": persons},
    )


@router.post("/films/{film_id}/nominees")
def add_nominee(
    film_id: int,
    nomination_id: int = Form(...),
    person_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    nom = db.get(Nomination, nomination_id)
    if not nom:
        return RedirectResponse(url=f"/admin/films/{film_id}", status_code=303)
    pid: Optional[int] = None
    if nom.type == NominationType.PICK and person_id and person_id.strip():
        try:
            pid = int(person_id)
        except ValueError:
            pid = None
    db.add(Nominee(nomination_id=nomination_id, film_id=film_id, person_id=pid))
    db.commit()
    return RedirectResponse(url=f"/admin/films/{film_id}", status_code=303)


@router.post("/nominees/{nominee_id}/edit")
def edit_nominee(
    nominee_id: int,
    person_id: Optional[str] = Form(None),
    film_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Update person (and optionally film) on a nominee. Redirects to referring nomination page."""
    nominee = db.get(Nominee, nominee_id)
    if not nominee:
        return RedirectResponse(url="/admin/nominations", status_code=303)
    # update person
    if person_id is not None:
        if person_id.strip():
            try:
                nominee.person_id = int(person_id)
            except ValueError:
                nominee.person_id = None
        else:
            nominee.person_id = None
    # update film
    if film_id is not None and film_id.strip():
        try:
            nominee.film_id = int(film_id)
        except ValueError:
            pass
    db.commit()
    return RedirectResponse(url=f"/admin/nominations/{nominee.nomination_id}", status_code=303)


@router.post("/nominees/{nominee_id}/delete")
def delete_nominee(
    nominee_id: int,
    back: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    nominee = db.get(Nominee, nominee_id)
    nom_id = nominee.nomination_id if nominee else None
    film_id_val = nominee.film_id if nominee else None
    if nominee:
        db.delete(nominee)
        db.commit()
    # respect 'back' hint from the form
    if back == "nomination" and nom_id:
        return RedirectResponse(url=f"/admin/nominations/{nom_id}", status_code=303)
    if film_id_val:
        return RedirectResponse(url=f"/admin/films/{film_id_val}", status_code=303)
    return RedirectResponse(url="/admin/films", status_code=303)
