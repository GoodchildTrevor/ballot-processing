from typing import Optional
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from ballot.database import get_db
from ballot.models import Film, Nominee, Person
from ballot.auth import require_admin

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="ballot/templates")


@router.get("/films", response_class=HTMLResponse)
def list_films(request: Request, db: Session = Depends(get_db)):
    films = db.query(Film).order_by(Film.year.desc(), Film.title).all()
    persons = db.query(Person).order_by(Person.name).all()
    return templates.TemplateResponse(request, "admin/films.html", {
        "films": films,
        "persons": persons,
    })


@router.post("/films")
def create_film(
    title: str = Form(...),
    year: int = Form(...),
    url: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    db.add(Film(title=title.strip(), year=year, url=url.strip() if url and url.strip() else None))
    db.commit()
    return RedirectResponse(url="/admin/films", status_code=303)


@router.post("/films/{film_id}/edit")
def edit_film(
    film_id: int,
    title: str = Form(...),
    year: int = Form(...),
    url: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    film = db.get(Film, film_id)
    if film:
        film.title = title.strip()
        film.year = year
        film.url = url.strip() if url and url.strip() else None
        db.commit()
    return RedirectResponse(url="/admin/films", status_code=303)


@router.post("/films/{film_id}/delete")
def delete_film(film_id: int, db: Session = Depends(get_db)):
    film = db.get(Film, film_id)
    if film:
        db.query(Nominee).filter(Nominee.film_id == film_id).delete()
        db.delete(film)
        db.commit()
    return RedirectResponse(url="/admin/films", status_code=303)


@router.get("/nominees/{nominee_id}/edit")
def edit_nominee_get(nominee_id: int, request: Request, db: Session = Depends(get_db)):
    nominee = db.get(Nominee, nominee_id)
    if not nominee:
        return RedirectResponse(url="/admin/films", status_code=303)
    films = db.query(Film).order_by(Film.title).all()
    persons = db.query(Person).order_by(Person.name).all()
    return templates.TemplateResponse(request, "admin/nominee_edit.html", {
        "nominee": nominee,
        "films": films,
        "persons": persons,
    })


@router.post("/nominees/{nominee_id}/edit")
def edit_nominee(
    nominee_id: int,
    film_id: int = Form(...),
    person_id: Optional[str] = Form(None),
    song: Optional[str] = Form(None),
    song_url: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    nominee = db.get(Nominee, nominee_id)
    if nominee:
        nominee.film_id = film_id
        nominee.person_id = int(person_id) if person_id and person_id.strip() else None
        nominee.song = song.strip() if song and song.strip() else None
        nominee.song_url = song_url.strip() if song_url and song_url.strip() else None
        db.commit()
    back = "/admin/nominations/" + str(nominee.nomination_id) if nominee else "/admin/films"
    return RedirectResponse(url=back, status_code=303)


@router.post("/nominees/{nominee_id}/delete")
def delete_nominee(
    nominee_id: int,
    back: str = Form("films"),
    db: Session = Depends(get_db),
):
    nominee = db.get(Nominee, nominee_id)
    if nominee:
        nom_id = nominee.nomination_id
        db.delete(nominee)
        db.commit()
        if back == "nomination":
            return RedirectResponse(url=f"/admin/nominations/{nom_id}", status_code=303)
    return RedirectResponse(url="/admin/films", status_code=303)
