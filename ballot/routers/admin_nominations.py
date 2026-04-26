from typing import Optional
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from ballot.database import get_db
from ballot.models import (
    Contest, Film, Nomination, NominationType, Nominee, NomineePerson, Person, Round
)
from ballot.auth import require_subadmin
from ballot.utils import _normalize
import io, openpyxl

router = APIRouter(prefix="/admin", dependencies=[Depends(require_subadmin)])
templates = Jinja2Templates(directory="ballot/templates")


def _all_years(db: Session) -> list[int]:
    """
    Get all distinct years from nominations.

    Extracts unique year_filter values from nominations and returns them sorted
    in descending order.

    :param db: Database session
    :returns: List of distinct years in descending order
    """
    rows = db.query(Nomination.year_filter).distinct().all()
    return sorted({r[0] for r in rows if r[0]}, reverse=True)


def _get_or_create_person(db: Session, name: str, url: Optional[str] = None) -> Person:
    """
    Get or create a person in the database.

    Finds an existing person by normalized name (NFKC), or creates a new one if not found.
    Updates person URL if provided and person doesn't already have one.

    :param db: Database session
    :param name: Person name
    :param url: Optional URL for the person
    :returns: Person object (existing or newly created)
    """
    name = _normalize(name)
    url = _normalize(url) if url else None

    # Fetch all persons and compare normalized to handle \xa0 in existing records
    all_persons = db.query(Person).all()
    p = next((x for x in all_persons if _normalize(x.name) == name), None)

    if not p:
        p = Person(name=name, url=url or None)
        db.add(p)
        db.flush()
    else:
        # Fix dirty name in place
        if p.name != name:
            p.name = name
        if url and not p.url:
            p.url = url
    return p


def _get_or_create_film(
    db: Session,
    title: str,
    year: Optional[int]
) -> Optional[Film]:
    """
    Get or create a film in the database.

    Finds an existing film by normalized title (NFKC) and optionally year,
    or creates a new one if not found. Requires year for new films.

    :param db: Database session
    :param title: Film title
    :param year: Optional film year
    :returns: Film object or None if title is empty or year is missing for new films
    """
    title = _normalize(title)
    if not title:
        return None

    # Fetch candidates and compare normalized to handle dirty existing records
    q = db.query(Film)
    if year:
        q = q.filter(Film.year == year)
    candidates = q.all()
    f = next((x for x in candidates if _normalize(x.title) == title), None)

    if not f:
        if not year:
            return None
        f = Film(title=title, year=year)
        db.add(f)
        db.flush()
    else:
        # Fix dirty title in place
        if f.title != title:
            f.title = title
    return f


def _set_nominee_persons(
    db: Session,
    nominee: Nominee,
    person_ids: list[int],
    person_urls: dict[int, str]
) -> None:
    """
    Replace all NomineePerson rows for a nominee with the given list.

    Deletes existing person associations and creates new ones based on the provided
    person IDs. Also updates person URLs if provided and person doesn't already have one.

    :param db: Database session
    :param nominee: Nominee object to update
    :param person_ids: List of person IDs to associate with the nominee
    :param person_urls: Dictionary mapping person IDs to URLs
    """
    # Delete existing
    for np in list(nominee.persons):
        db.delete(np)
    db.flush()
    # Insert new
    for pid in person_ids:
        url = person_urls.get(pid)
        person = db.get(Person, pid)
        if person:
            if url and url.strip() and not person.url:
                person.url = url.strip()
            db.add(NomineePerson(nominee_id=nominee.id, person_id=pid))
    db.flush()


def _film_to_dict(film: Film) -> dict:
    """
    Convert film object to dictionary.

    Creates a simple dictionary representation of a film for JSON serialization.

    :param film: Film object
    :returns: Dictionary with film id, title, and year
    """
    return {"id": film.id, "title": film.title, "year": film.year}


def _person_to_dict(person: Person) -> dict:
    """
    Convert person object to dictionary.

    Creates a simple dictionary representation of a person for JSON serialization.

    :param person: Person object
    :returns: Dictionary with person id, name, and url
    """
    return {"id": person.id, "name": person.name, "url": person.url}


# ─────────────────────────────────────────────────────────────
# LIST
# ─────────────────────────────────────────────────────────────

@router.get("/nominations", response_class=HTMLResponse)
def list_nominations(
    request: Request,
    contest_id: Optional[int] = Query(None),
    round_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    years = _all_years(db)

    latest_deadline = (
        db.query(
            Round.contest_id,
            func.max(Round.deadline).label("latest_deadline")
        )
        .group_by(Round.contest_id)
        .subquery()
    )

    contests = (
        db.query(Contest)
        .outerjoin(latest_deadline, latest_deadline.c.contest_id == Contest.id)
        .order_by(latest_deadline.c.latest_deadline.desc())
        .all()
    )

    selected_contest = None
    selected_round = None
    all_rounds: list[Round] = []

    if contests:
        if contest_id:
            selected_contest = db.get(Contest, contest_id)
        if not selected_contest:
            selected_contest = contests[0]

        all_rounds = (
            db.query(Round)
            .filter(Round.contest_id == selected_contest.id)
            .order_by(Round.tour)
            .all()
        )

        if round_id:
            selected_round = next((r for r in all_rounds if r.id == round_id), None)
        if not selected_round and all_rounds:
            selected_round = all_rounds[0]

    if selected_round:
        nominations = (
            db.query(Nomination)
            .options(joinedload(Nomination.nominees), joinedload(Nomination.round))
            .filter(Nomination.round_id == selected_round.id)
            .order_by(Nomination.sort_order, Nomination.id)
            .all()
        )
    else:
        nominations = []

    return templates.TemplateResponse(request, "admin/nominations.html", {
        "nominations": nominations,
        "years": years,
        "contests": contests,
        "selected_contest": selected_contest,
        "selected_round": selected_round,
        "all_rounds": all_rounds,
    })


# ─────────────────────────────────────────────────────────────
# CREATE / EDIT / DELETE / MOVE  (nominations)
# ─────────────────────────────────────────────────────────────

@router.post("/nominations")
async def create_nomination(
    request: Request,
    db: Session = Depends(get_db)
) -> RedirectResponse:
    form = await request.form()
    nom_type = NominationType(form.get("type", "RANK"))
    nominees_count_raw = form.get("nominees_count")
    pick_min_raw = form.get("pick_min")
    pick_max_raw = form.get("pick_max")
    round_id_raw = form.get("round_id")
    year_filter_raw = form.get("year_filter")
    acting_group_raw = form.get("acting_group")

    nom = Nomination(
        name=form["name"],
        type=nom_type,
        nominees_count=int(nominees_count_raw) if nominees_count_raw else None,
        pick_min=int(pick_min_raw) if pick_min_raw else None,
        pick_max=int(pick_max_raw) if pick_max_raw else None,
        round_id=int(round_id_raw) if round_id_raw else None,
        year_filter=int(year_filter_raw) if year_filter_raw else None,
        acting_group=acting_group_raw.strip() if acting_group_raw else None,
    )
    db.add(nom)
    db.commit()

    contest_id = form.get("contest_id")
    parts = []
    if contest_id:
        parts.append(f"contest_id={contest_id}")
    if round_id_raw:
        parts.append(f"round_id={round_id_raw}")
    return RedirectResponse(
        url="/admin/nominations" + ("?" + "&".join(parts) if parts else ""), status_code=303
    )


@router.post("/nominations/{nom_id}/edit")
async def edit_nomination(
    nom_id: int,
    request: Request,
    db: Session = Depends(get_db)
) -> RedirectResponse:
    form = await request.form()
    nom = db.get(Nomination, nom_id)
    if nom:
        nom.name = form["name"]
        nom.type = NominationType(form.get("type", "RANK"))
        nc = form.get("nominees_count")
        nom.nominees_count = int(nc) if nc else None
        pm = form.get("pick_min")
        nom.pick_min = int(pm) if pm else None
        px = form.get("pick_max")
        nom.pick_max = int(px) if px else None
        yf = form.get("year_filter")
        nom.year_filter = int(yf) if yf else None
        ag = form.get("acting_group")
        nom.acting_group = ag.strip() if ag else None
        db.commit()

    contest_id = form.get("contest_id")
    round_id = form.get("round_id")
    parts = []
    if contest_id:
        parts.append(f"contest_id={contest_id}")
    if round_id:
        parts.append(f"round_id={round_id}")
    return RedirectResponse(
        url="/admin/nominations" + ("?" + "&".join(parts) if parts else ""), status_code=303
    )


@router.post("/nominations/{nom_id}/delete")
async def delete_nomination(
    nom_id: int,
    request: Request,
    db: Session = Depends(get_db)
) -> RedirectResponse:
    form = await request.form()
    nom = db.get(Nomination, nom_id)
    if nom:
        db.delete(nom)
        db.commit()
    contest_id = form.get("contest_id")
    round_id = form.get("round_id")
    parts = []
    if contest_id:
        parts.append(f"contest_id={contest_id}")
    if round_id:
        parts.append(f"round_id={round_id}")
    return RedirectResponse(url="/admin/nominations" + ("?" + "&".join(parts) if parts else ""), status_code=303)


@router.post("/nominations/{nom_id}/move")
async def move_nomination(
    nom_id: int,
    request: Request,
    db: Session = Depends(get_db)
) -> RedirectResponse:
    form = await request.form()
    direction = form.get("direction", "up")
    nom = db.get(Nomination, nom_id)
    if nom:
        siblings = (
            db.query(Nomination)
            .filter(Nomination.round_id == nom.round_id)
            .order_by(Nomination.sort_order, Nomination.id)
            .all()
        )
        idx = next((i for i, n in enumerate(siblings) if n.id == nom_id), None)
        if idx is not None:
            swap_idx = idx - 1 if direction == "up" else idx + 1
            if 0 <= swap_idx < len(siblings):
                other = siblings[swap_idx]
                nom.sort_order, other.sort_order = other.sort_order, nom.sort_order
                db.commit()

    contest_id = form.get("contest_id")
    round_id = form.get("round_id")
    parts = []
    if contest_id:
        parts.append(f"contest_id={contest_id}")
    if round_id:
        parts.append(f"round_id={round_id}")
    return RedirectResponse(url="/admin/nominations" + ("?" + "&".join(parts) if parts else ""), status_code=303)


# ─────────────────────────────────────────────────────────────
# EXPORT
# ─────────────────────────────────────────────────────────────

@router.get("/nominations/export-longlist")
def export_longlist(
    contest_id: Optional[int] = Query(None),
    round_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Export longlists to Excel. Each nomination is a separate sheet.
    Filters by contest and round if provided."""
    q = (
        db.query(Nomination)
        .options(
            joinedload(Nomination.nominees).joinedload(Nominee.film),
            joinedload(Nomination.nominees).joinedload(Nominee.person),
            joinedload(Nomination.nominees).joinedload(Nominee.persons).joinedload(NomineePerson.person),
        )
        .order_by(Nomination.sort_order, Nomination.id)
    )

    if round_id:
        q = q.filter(Nomination.round_id == round_id)
    elif contest_id:
        round_ids = [r.id for r in db.query(Round).filter(Round.contest_id == contest_id).all()]
        q = q.filter(Nomination.round_id.in_(round_ids))

    nominations = q.all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Сводка"
    ws.append(["Номинация", "Количество номинантов"])

    for nom in nominations:
        ws.append([nom.name, len(nom.nominees)])

        nom_ws = wb.create_sheet(title=nom.name[:31])
        nom_ws.append(["Фильм", "Год", "Персоны", "Item", "Item URL"])

        for nominee in sorted(nom.nominees, key=lambda n: (n.film.title if n.film else "", n.item or "")):
            persons_str = ", ".join([p.name for p in nominee.all_persons]) if nominee.all_persons else (nominee.person.name if nominee.person else "")
            nom_ws.append([
                nominee.film.title if nominee.film else "",
                nominee.film.year if nominee.film else "",
                persons_str,
                nominee.item or "",
                nominee.item_url or "",
            ])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = "longlist.xlsx"
    if contest_id:
        contest = db.get(Contest, contest_id)
        if contest:
            fname = f"longlist_{contest.year}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


# ─────────────────────────────────────────────────────────────
# NOMINATION DETAIL
# ─────────────────────────────────────────────────────────────

@router.get("/nominations/{nom_id}", response_class=HTMLResponse)
def nomination_detail(
    nom_id: int,
    request: Request,
    error: Optional[str] = Query(None),
    bulk_created: Optional[int] = Query(None),
    bulk_skipped: Optional[int] = Query(None),
    bulk_films_created: Optional[int] = Query(None),
    bulk_persons_created: Optional[int] = Query(None),
    bulk_not_found: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    nom = (
        db.query(Nomination)
        .options(
            joinedload(Nomination.nominees).joinedload(Nominee.film),
            joinedload(Nomination.nominees).joinedload(Nominee.person),
            joinedload(Nomination.nominees).joinedload(Nominee.persons).joinedload(NomineePerson.person),
        )
        .filter(Nomination.id == nom_id)
        .first()
    )
    if not nom:
        return HTMLResponse("Номинация не найдена.", status_code=404)

    films_q = db.query(Film).order_by(Film.title)
    if nom.year_filter:
        films_q = films_q.filter(Film.year == nom.year_filter)
    films = films_q.all()
    added_film_ids = {n.film_id for n in nom.nominees}

    persons = db.query(Person).order_by(Person.name).all()
    years = _all_years(db)

    return templates.TemplateResponse(request, "admin/nomination_detail.html", {
        "nom": nom,
        "films": [_film_to_dict(f) for f in films],
        "added_film_ids": added_film_ids,
        "persons": [_person_to_dict(p) for p in persons],
        "years": years,
        "error": error,
        "bulk_created": bulk_created,
        "bulk_skipped": bulk_skipped,
        "bulk_films_created": bulk_films_created,
        "bulk_persons_created": bulk_persons_created,
        "bulk_not_found": bulk_not_found,
    })


# ─────────────────────────────────────────────────────────────
# ADD SINGLE NOMINEE
# ─────────────────────────────────────────────────────────────

@router.post("/nominations/{nom_id}/nominees")
async def add_nominee(
    nom_id: int,
    request: Request,
    db: Session = Depends(get_db)
) -> RedirectResponse:
    form = await request.form()
    film_id_raw = form.get("film_id")
    if not film_id_raw:
        return RedirectResponse(url=f"/admin/nominations/{nom_id}?error=no_film", status_code=303)

    film_id = int(film_id_raw)

    person_ids: list[int] = []
    person_urls: dict[int, str] = {}
    for field, url_field in (("person_id", "person_url"), ("person_id_2", "person_url_2")):
        raw = form.get(field, "").strip()
        if raw:
            pid = int(raw)
            person_ids.append(pid)
            url_val = form.get(url_field, "").strip()
            if url_val:
                person_urls[pid] = url_val

    item = form.get("item", "").strip() or None
    item_url = form.get("item_url", "").strip() or None

    existing = db.query(Nominee).filter_by(
        nomination_id=nom_id,
        film_id=film_id,
        person_id=person_ids[0] if person_ids else None,
        item=item,
    ).first()
    if existing:
        return RedirectResponse(url=f"/admin/nominations/{nom_id}?error=duplicate", status_code=303)

    nominee = Nominee(
        nomination_id=nom_id,
        film_id=film_id,
        person_id=person_ids[0] if person_ids else None,
        item=item,
        item_url=item_url,
    )
    db.add(nominee)
    db.flush()

    for pid in person_ids:
        url_val = person_urls.get(pid)
        person = db.get(Person, pid)
        if person:
            if url_val and not person.url:
                person.url = url_val
            db.add(NomineePerson(nominee_id=nominee.id, person_id=pid))

    db.commit()
    return RedirectResponse(url=f"/admin/nominations/{nom_id}", status_code=303)


# ─────────────────────────────────────────────────────────────
# BULK ADD NOMINEES
# ─────────────────────────────────────────────────────────────

@router.post("/nominations/{nom_id}/nominees/bulk")
async def bulk_add_nominees(nom_id: int, request: Request, db: Session = Depends(get_db)):
    nom = db.get(Nomination, nom_id)
    if not nom:
        return RedirectResponse(url="/admin/nominations", status_code=303)

    form = await request.form()
    lines_raw = form.get("lines", "")

    created = skipped = films_created = persons_created = 0
    not_found: list[str] = []

    for line in lines_raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue

        parts = [p.strip() for p in line.split("|")]
        film_title  = _normalize(parts[0]) if len(parts) > 0 else ""
        person_name = _normalize(parts[1]) if len(parts) > 1 else ""
        person_url  = _normalize(parts[2]) if len(parts) > 2 else ""
        item        = _normalize(parts[3]) if len(parts) > 3 else ""
        item_url    = _normalize(parts[4]) if len(parts) > 4 else ""

        if not film_title:
            continue

        film = _get_or_create_film(db, film_title, nom.year_filter)
        if not film:
            not_found.append(film_title)
            continue
        if db.new and film in db.new:
            films_created += 1

        person_objs: list[Person] = []
        if person_name:
            all_persons = db.query(Person).all()
            existing_person = next(
                (x for x in all_persons if _normalize(x.name) == person_name), None
            )
            was_new = existing_person is None
            p = _get_or_create_person(db, person_name, person_url)
            if was_new:
                persons_created += 1
            person_objs.append(p)

        existing = db.query(Nominee).filter_by(
            nomination_id=nom_id,
            film_id=film.id,
            person_id=person_objs[0].id if person_objs else None,
            item=item or None,
        ).first()
        if existing:
            skipped += 1
            continue

        nominee = Nominee(
            nomination_id=nom_id,
            film_id=film.id,
            person_id=person_objs[0].id if person_objs else None,
            item=item or None,
            item_url=item_url or None,
        )
        db.add(nominee)
        db.flush()

        for p in person_objs:
            db.add(NomineePerson(nominee_id=nominee.id, person_id=p.id))

        db.flush()
        created += 1

    db.commit()

    params = f"bulk_created={created}&bulk_skipped={skipped}"
    if films_created:
        params += f"&bulk_films_created={films_created}"
    if persons_created:
        params += f"&bulk_persons_created={persons_created}"
    if not_found:
        params += f"&bulk_not_found={','.join(not_found)}"
    return RedirectResponse(url=f"/admin/nominations/{nom_id}?{params}", status_code=303)


@router.post("/nominations/{nomination_id}/nominees/bulk-delete")
def bulk_delete_nominees(
    nomination_id: int,
    ids: list[int] = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    db.query(Nominee).filter(
        Nominee.id.in_(ids),
        Nominee.nomination_id == nomination_id,
    ).delete(synchronize_session=False)
    db.commit()
    return RedirectResponse(
        url=f"/admin/nominations/{nomination_id}?bulk_deleted={len(ids)}",
        status_code=303,
    )


# ─────────────────────────────────────────────────────────────
# EDIT SINGLE NOMINEE (inline in detail table)
# ─────────────────────────────────────────────────────────────

@router.post("/nominees/{nominee_id}/edit")
async def edit_nominee(
    nominee_id: int,
    request: Request,
    db: Session = Depends(get_db)
) -> RedirectResponse:
    form = await request.form()
    nominee = db.get(Nominee, nominee_id)
    if not nominee:
        return RedirectResponse(url="/admin/nominations", status_code=303)

    film_id_raw = form.get("film_id")
    if film_id_raw:
        nominee.film_id = int(film_id_raw)

    nominee.item = form.get("item", "").strip() or None
    nominee.item_url = form.get("item_url", "").strip() or None

    person_ids: list[int] = []
    person_urls: dict[int, str] = {}
    for field, url_field in (("person_id", "person_url"), ("person_id_2", "person_url_2")):
        raw = form.get(field, "").strip()
        if raw:
            pid = int(raw)
            person_ids.append(pid)
            url_val = form.get(url_field, "").strip()
            if url_val:
                person_urls[pid] = url_val

    nominee.person_id = person_ids[0] if person_ids else None

    _set_nominee_persons(db, nominee, person_ids, person_urls)

    db.commit()
    return RedirectResponse(url=f"/admin/nominations/{nominee.nomination_id}", status_code=303)


# ─────────────────────────────────────────────────────────────
# DELETE NOMINEE
# ─────────────────────────────────────────────────────────────

@router.post("/nominees/{nominee_id}/delete")
async def delete_nominee(
    nominee_id: int,
    request: Request,
    db: Session = Depends(get_db)
) -> RedirectResponse:
    form = await request.form()
    nominee = db.get(Nominee, nominee_id)
    if not nominee:
        return RedirectResponse(url="/admin/nominations", status_code=303)
    nom_id = nominee.nomination_id
    db.delete(nominee)
    db.commit()
    back = form.get("back", "nomination")
    if back == "nomination":
        return RedirectResponse(url=f"/admin/nominations/{nom_id}", status_code=303)
    return RedirectResponse(url="/admin/films", status_code=303)
