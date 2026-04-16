from typing import Optional
from collections import defaultdict
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from ballot.database import get_db
from ballot.models import Person, Nominee, Nomination, Round, RoundType
from ballot.auth import require_admin

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="ballot/templates")


@router.get("/persons", response_class=HTMLResponse)
def list_persons(request: Request, db: Session = Depends(get_db)):
    persons = (
        db.query(Person)
        .options(joinedload(Person.nominees).joinedload(Nominee.nomination).joinedload(Nomination.round))
        .order_by(Person.name)
        .all()
    )
    return templates.TemplateResponse(request, "admin/persons.html", {"persons": persons})


@router.post("/persons")
def create_person(
    name: str = Form(...),
    url: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    name = name.strip()
    if not name:
        return RedirectResponse(url="/admin/persons", status_code=303)
    existing = db.query(Person).filter(Person.name == name).first()
    if not existing:
        db.add(Person(name=name, url=url.strip() if url and url.strip() else None))
        db.commit()
    return RedirectResponse(url="/admin/persons", status_code=303)


@router.post("/persons/bulk")
def bulk_create_persons(
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
        name = line.strip()
        if not name:
            continue
        exists = db.query(Person).filter(Person.name == name).first()
        if exists:
            skipped += 1
            continue
        db.add(Person(name=name, url=url))
        created += 1
    db.commit()
    return RedirectResponse(
        url=f"/admin/persons?bulk_created={created}&bulk_skipped={skipped}",
        status_code=303,
    )


@router.post("/persons/{person_id}/delete")
def delete_person(person_id: int, db: Session = Depends(get_db)):
    person = db.get(Person, person_id)
    if person:
        db.query(Nominee).filter(Nominee.person_id == person_id).update({"person_id": None})
        db.delete(person)
        db.commit()
    return RedirectResponse(url="/admin/persons", status_code=303)


@router.post("/persons/{person_id}/edit")
def edit_person(
    person_id: int,
    name: str = Form(...),
    url: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    person = db.get(Person, person_id)
    if person:
        person.name = name.strip()
        person.url = url.strip() if url and url.strip() else None
        db.commit()
    return RedirectResponse(url="/admin/persons", status_code=303)


@router.post("/persons/{person_id}/set-url")
async def set_person_url(
    person_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Quick URL setter — back=<url> redirects to caller page after save."""
    form = await request.form()
    url_val = (form.get("url") or "").strip()
    back = (form.get("back") or "/admin/persons").strip()

    person = db.get(Person, person_id)
    if person:
        person.url = url_val or None
        db.commit()

    return RedirectResponse(url=back, status_code=303)


@router.get("/persons/{person_id}", response_class=HTMLResponse)
def person_detail(person_id: int, request: Request, db: Session = Depends(get_db)):
    person = (
        db.query(Person)
        .filter(Person.id == person_id)
        .first()
    )
    if not person:
        return HTMLResponse("Персона не найдена.", status_code=404)

    nominees = (
        db.query(Nominee)
        .options(
            joinedload(Nominee.nomination).joinedload(Nomination.round),
            joinedload(Nominee.film),
        )
        .filter(Nominee.person_id == person_id)
        .all()
    )

    rounds_map: dict = defaultdict(lambda: defaultdict(list))
    no_round_nom: dict = defaultdict(list)

    longlists_count = 0
    nominations_count = 0

    for n in nominees:
        nom = n.nomination
        rnd = nom.round if nom else None
        if rnd:
            rounds_map[rnd][nom].append(n)
            if rnd.round_type == RoundType.FINAL:
                nominations_count += 1
            else:
                longlists_count += 1
        else:
            no_round_nom[nom].append(n)
            longlists_count += 1

    stats = []
    for rnd in sorted(rounds_map.keys(), key=lambda r: (r.sort_order, r.id)):
        noms_for_round = []
        for nom, noms in sorted(rounds_map[rnd].items(), key=lambda x: (x[0].sort_order, x[0].id)):
            noms_for_round.append({"nom": nom, "nominees": sorted(noms, key=lambda n: n.film.title)})
        stats.append({"round": rnd, "nominations": noms_for_round})

    if no_round_nom:
        noms_no_round = []
        for nom, noms in sorted(no_round_nom.items(), key=lambda x: (x[0].sort_order if x[0] else 0, x[0].id if x[0] else 0)):
            noms_no_round.append({"nom": nom, "nominees": sorted(noms, key=lambda n: n.film.title)})
        stats.append({"round": None, "nominations": noms_no_round})

    return templates.TemplateResponse(request, "admin/person_detail.html", {
        "person": person,
        "stats": stats,
        "longlists_count": longlists_count,
        "nominations_count": nominations_count,
    })
