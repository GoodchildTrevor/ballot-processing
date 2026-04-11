import io
import urllib.parse
from typing import Optional
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import openpyxl
from ballot.database import get_db
from ballot.models import Nomination, NominationType, Nominee, Film, Person, Round
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


def _clamp_pick_max(pmax: Optional[int], nc: Optional[int]) -> Optional[int]:
    if pmax is not None and nc is not None:
        return min(pmax, nc)
    return pmax


def _apply_person_url(person: Person, url: Optional[str]) -> None:
    """Update person.url if a non-empty url is provided."""
    clean = url.strip() if url and url.strip() else None
    if clean:
        person.url = clean


@router.get("/nominations", response_class=HTMLResponse)
def list_nominations(request: Request, db: Session = Depends(get_db)):
    nominations = db.query(Nomination).order_by(Nomination.sort_order, Nomination.id).all()
    years = _get_years(db)
    rounds = db.query(Round).order_by(Round.sort_order, Round.id).all()
    return templates.TemplateResponse(request, "admin/nominations.html", {
        "nominations": nominations,
        "years": years,
        "rounds": rounds,
    })


@router.post("/nominations")
def create_nomination(
    name: str = Form(...),
    type: NominationType = Form(...),
    pick_min: Optional[str] = Form(None),
    pick_max: Optional[str] = Form(None),
    nominees_count: Optional[str] = Form(None),
    year_filter: str = Form(...),
    round_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    nc = _parse_int(nominees_count)
    if type == NominationType.PICK:
        pmin = _parse_int(pick_min)
        pmax = _clamp_pick_max(_parse_int(pick_max), nc)
    else:
        pmin = None
        pmax = None
    yf = _parse_int(year_filter)
    rid = _parse_int(round_id)
    last = db.query(Nomination).order_by(Nomination.sort_order.desc()).first()
    order = (last.sort_order + 1) if last else 0
    db.add(Nomination(
        name=name, type=type,
        pick_min=pmin, pick_max=pmax,
        nominees_count=nc,
        year_filter=yf,
        round_id=rid,
        sort_order=order,
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
    round_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    nom = db.get(Nomination, nom_id)
    if not nom:
        return RedirectResponse(url="/admin/nominations", status_code=303)
    nc = _parse_int(nominees_count)
    nom.name = name.strip()
    nom.type = type
    nom.nominees_count = nc
    nom.year_filter = _parse_int(year_filter)
    nom.round_id = _parse_int(round_id)
    if type == NominationType.PICK:
        nom.pick_min = _parse_int(pick_min)
        nom.pick_max = _clamp_pick_max(_parse_int(pick_max), nc)
    else:
        nom.pick_min = None
        nom.pick_max = None
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


@router.get("/nominations/export-longlist")
def export_longlist(db: Session = Depends(get_db)):
    nominations = db.query(Nomination).order_by(Nomination.sort_order, Nomination.id).all()
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for nom in nominations:
        ws = wb.create_sheet(title=nom.name[:31])
        ws.append(["Фильм", "Год", "Персона", "Элемент", "Ссылка на фильм"])
        nominees_sorted = sorted(
            nom.nominees,
            key=lambda n: (n.person.name.lower() if n.person else n.film.title.lower())
        )
        for n in nominees_sorted:
            ws.append([
                n.film.title,
                n.film.year,
                n.persons_label,
                n.item if n.item else "",
                n.film.url or "",
            ])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=longlist.xlsx"},
    )


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
    added_film_ids = {n.film_id for n in nom.nominees}
    return templates.TemplateResponse(
        request, "admin/nomination_detail.html",
        {
            "nom": nom,
            "films": films,
            "persons": persons,
            "years": years,
            "added_film_ids": added_film_ids,
            "error": request.query_params.get("error"),
            "bulk_created": request.query_params.get("bulk_created"),
            "bulk_skipped": request.query_params.get("bulk_skipped"),
            "bulk_not_found": request.query_params.get("bulk_not_found"),
            "bulk_films_created": request.query_params.get("bulk_films_created"),
            "bulk_persons_created": request.query_params.get("bulk_persons_created"),
        },
    )


@router.post("/nominations/{nom_id}/nominees")
def add_nominee_via_nomination(
    nom_id: int,
    film_id: int = Form(...),
    person_id: Optional[str] = Form(None),
    person_url: Optional[str] = Form(None),
    item: Optional[str] = Form(None),
    item_url: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    nom = db.get(Nomination, nom_id)
    if not nom:
        return RedirectResponse(url="/admin/nominations", status_code=303)
    pid = _parse_int(person_id) if nom.type == NominationType.PICK else None
    item_val = item.strip() if item and item.strip() else None
    item_url_val = item_url.strip() if item_url and item_url.strip() else None

    # Update person URL if provided
    if pid and nom.type == NominationType.PICK:
        person = db.get(Person, pid)
        if person:
            _apply_person_url(person, person_url)

    existing = db.query(Nominee).filter(
        Nominee.nomination_id == nom_id,
        Nominee.film_id == film_id,
        Nominee.person_id == pid,
        Nominee.item == item_val,
    ).first()
    if existing:
        return RedirectResponse(
            url=f"/admin/nominations/{nom_id}?error=duplicate&film_id={film_id}",
            status_code=303,
        )

    db.add(Nominee(
        nomination_id=nom_id,
        film_id=film_id,
        person_id=pid,
        item=item_val,
        item_url=item_url_val,
    ))
    db.commit()
    return RedirectResponse(url=f"/admin/nominations/{nom_id}", status_code=303)


@router.post("/nominations/{nom_id}/nominees/bulk")
def bulk_add_nominees(
    nom_id: int,
    lines: str = Form(...),
    db: Session = Depends(get_db),
):
    """
    Bulk-add nominees to a nomination.
    Line format: Film | Person | Item | Item URL | Person URL
    All fields except Film are optional. Skip with empty delimiter: ||.

    Film: looked up by exact title (case-insensitive); auto-created if not found (requires year_filter).
    Person: looked up by exact name (case-insensitive); auto-created if not found.
    Person URL (5th field): applied to the person record on create or update.
    """
    nom = db.get(Nomination, nom_id)
    if not nom:
        return RedirectResponse(url="/admin/nominations", status_code=303)

    film_q = db.query(Film)
    if nom.year_filter:
        film_q = film_q.filter(Film.year == nom.year_filter)
    all_films = film_q.all()
    film_index: dict[str, Film] = {f.title.lower(): f for f in all_films}

    all_persons = db.query(Person).all()
    person_index: dict[str, Person] = {p.name.lower(): p for p in all_persons}

    created = 0
    skipped = 0
    films_created: list[str] = []
    persons_created: list[str] = []
    no_year_lines: list[str] = []

    for raw_line in lines.strip().splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        parts = [p.strip() for p in raw_line.split("|")]
        film_title    = parts[0] if len(parts) > 0 else ""
        person_name   = parts[1] if len(parts) > 1 else ""
        item_val      = parts[2] if len(parts) > 2 else ""
        item_url_val  = parts[3] if len(parts) > 3 else ""
        person_url_val = parts[4] if len(parts) > 4 else ""

        film_title     = film_title.strip()
        person_name    = person_name.strip()
        item_val       = item_val.strip() or None
        item_url_val   = item_url_val.strip() or None
        person_url_val = person_url_val.strip() or None

        if not film_title:
            continue

        # --- Film lookup / auto-create ---
        film = film_index.get(film_title.lower())
        if film is None:
            if not nom.year_filter:
                no_year_lines.append(film_title)
                continue
            film = Film(title=film_title, year=nom.year_filter)
            db.add(film)
            db.flush()
            film_index[film_title.lower()] = film
            films_created.append(film_title)

        # --- Person lookup / auto-create (PICK only) ---
        pid: Optional[int] = None
        if person_name and nom.type == NominationType.PICK:
            person = person_index.get(person_name.lower())
            if person is None:
                person = Person(name=person_name, url=person_url_val)
                db.add(person)
                db.flush()
                person_index[person_name.lower()] = person
                persons_created.append(person_name)
            else:
                # Update URL if provided and person doesn't have one yet
                _apply_person_url(person, person_url_val)
            pid = person.id

        # --- Duplicate check ---
        existing = db.query(Nominee).filter(
            Nominee.nomination_id == nom_id,
            Nominee.film_id == film.id,
            Nominee.person_id == pid,
            Nominee.item == item_val,
        ).first()
        if existing:
            skipped += 1
            continue

        db.add(Nominee(
            nomination_id=nom_id,
            film_id=film.id,
            person_id=pid,
            item=item_val,
            item_url=item_url_val,
        ))
        created += 1

    db.commit()

    params = f"bulk_created={created}&bulk_skipped={skipped}"
    if films_created:
        params += "&bulk_films_created=" + urllib.parse.quote(str(len(films_created)))
    if persons_created:
        params += "&bulk_persons_created=" + urllib.parse.quote(str(len(persons_created)))
    if no_year_lines:
        params += "&bulk_not_found=" + urllib.parse.quote(", ".join(no_year_lines))
    return RedirectResponse(url=f"/admin/nominations/{nom_id}?{params}", status_code=303)


@router.post("/nominees/{nominee_id}/edit")
def edit_nominee(
    nominee_id: int,
    film_id: int = Form(...),
    person_id: Optional[str] = Form(None),
    person_url: Optional[str] = Form(None),
    item: Optional[str] = Form(None),
    item_url: Optional[str] = Form(None),
    back: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    nominee = db.get(Nominee, nominee_id)
    if not nominee:
        return RedirectResponse(url="/admin/nominations", status_code=303)
    nominee.film_id = film_id
    pid = _parse_int(person_id)
    nominee.person_id = pid
    # Update person URL if person selected and URL provided
    if pid:
        person = db.get(Person, pid)
        if person:
            _apply_person_url(person, person_url)
    nominee.item = item.strip() if item and item.strip() else None
    nominee.item_url = item_url.strip() if item_url and item_url.strip() else None
    db.commit()
    dest = f"/admin/nominations/{nominee.nomination_id}"
    return RedirectResponse(url=dest, status_code=303)


@router.post("/nominees/{nominee_id}/delete")
def delete_nominee(
    nominee_id: int,
    back: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    nominee = db.get(Nominee, nominee_id)
    if nominee:
        nom_id = nominee.nomination_id
        db.delete(nominee)
        db.commit()
        if back == "nomination":
            return RedirectResponse(url=f"/admin/nominations/{nom_id}", status_code=303)
    return RedirectResponse(url="/admin/nominations", status_code=303)
