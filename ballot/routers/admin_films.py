import re
from typing import Optional
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from ballot.database import get_db
from ballot.models import Film, Nominee, Nomination, Person, Round, RoundType
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


@router.post("/films/bulk")
def bulk_create_films(
    year: int = Form(...),
    lines: str = Form(...),
    db: Session = Depends(get_db),
):
    created, skipped = 0, 0
    for line in lines.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        url = None
        if "|" in line:
            parts = line.split("|", 1)
            line = parts[0].strip()
            url = parts[1].strip() or None
        title = line.strip()
        if not title:
            continue
        exists = db.query(Film).filter(Film.title == title, Film.year == year).first()
        if exists:
            skipped += 1
            continue
        db.add(Film(title=title, year=year, url=url))
        created += 1
    db.commit()
    return RedirectResponse(
        url=f"/admin/films?bulk_created={created}&bulk_skipped={skipped}",
        status_code=303,
    )


@router.get("/films/{film_id}", response_class=HTMLResponse)
def film_detail(film_id: int, request: Request, db: Session = Depends(get_db)):
    film = (
        db.query(Film)
        .options(
            joinedload(Film.nominees).joinedload(Nominee.nomination).joinedload(Nomination.round),
            joinedload(Film.nominees).joinedload(Nominee.person),
        )
        .filter(Film.id == film_id)
        .first()
    )
    if not film:
        return HTMLResponse("Фильм не найден.", status_code=404)

    longlists_count = 0
    nominations_count = 0
    finals = []
    longlists = []
    for n in film.nominees:
        rnd = n.nomination.round if n.nomination else None
        if rnd and rnd.round_type == RoundType.FINAL:
            nominations_count += 1
            finals.append(n)
        else:
            # LONGLIST round or no round at all — both count as longlists
            longlists_count += 1
            longlists.append(n)

    nominations = db.query(Nomination).order_by(Nomination.sort_order, Nomination.id).all()
    persons = db.query(Person).order_by(Person.name).all()
    return templates.TemplateResponse(request, "admin/film_detail.html", {
        "film": film,
        "nominations": nominations,
        "persons": persons,
        "longlists_count": longlists_count,
        "nominations_count": nominations_count,
        "finals": finals,
        "longlists": longlists,
    })


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


@router.post("/films/{film_id}/nominees")
def add_nominee_from_film(
    film_id: int,
    nomination_id: int = Form(...),
    person_id: Optional[str] = Form(None),
    item: Optional[str] = Form(None),
    item_url: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    from ballot.models import NominationType
    nom = db.get(Nomination, nomination_id)
    if not nom:
        return RedirectResponse(url=f"/admin/films/{film_id}", status_code=303)
    pid = int(person_id) if person_id and person_id.strip() else None
    item_val = item.strip() if item and item.strip() else None
    item_url_val = item_url.strip() if item_url and item_url.strip() else None
    existing = db.query(Nominee).filter_by(
        nomination_id=nomination_id, film_id=film_id, person_id=pid, item=item_val
    ).first()
    if not existing:
        db.add(Nominee(
            nomination_id=nomination_id,
            film_id=film_id,
            person_id=pid,
            item=item_val,
            item_url=item_url_val,
        ))
        db.commit()
    return RedirectResponse(url=f"/admin/films/{film_id}", status_code=303)


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
    item: Optional[str] = Form(None),
    item_url: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    nominee = db.get(Nominee, nominee_id)
    if nominee:
        nominee.film_id = film_id
        nominee.person_id = int(person_id) if person_id and person_id.strip() else None
        nominee.item = item.strip() if item and item.strip() else None
        nominee.item_url = item_url.strip() if item_url and item_url.strip() else None
        db.commit()
    back = f"/admin/nominations/{nominee.nomination_id}" if nominee else "/admin/films"
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
