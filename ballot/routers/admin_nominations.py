from typing import Optional
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from ballot.database import get_db
from ballot.models import Contest, Nomination, NominationType, Nominee, Round
from ballot.auth import require_admin
import io, openpyxl

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="ballot/templates")


def _all_years(db: Session) -> list[int]:
    rows = db.query(Nomination.year_filter).distinct().all()
    return sorted({r[0] for r in rows if r[0]}, reverse=True)


@router.get("/nominations", response_class=HTMLResponse)
def list_nominations(
    request: Request,
    contest_id: Optional[int] = Query(None),
    round_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    years = _all_years(db)
    contests = db.query(Contest).order_by(Contest.year.desc()).all()

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

    # Load nominations for the selected round
    if selected_round:
        nominations = (
            db.query(Nomination)
            .options(
                joinedload(Nomination.nominees),
                joinedload(Nomination.round),
            )
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


@router.post("/nominations")
async def create_nomination(
    request: Request,
    db: Session = Depends(get_db),
):
    form = await request.form()
    nom_type = NominationType(form.get("type", "RANK"))
    nominees_count_raw = form.get("nominees_count")
    nominees_count = int(nominees_count_raw) if nominees_count_raw else None
    pick_min_raw = form.get("pick_min")
    pick_max_raw = form.get("pick_max")
    round_id_raw = form.get("round_id")
    year_filter_raw = form.get("year_filter")

    nom = Nomination(
        name=form["name"],
        type=nom_type,
        nominees_count=nominees_count,
        pick_min=int(pick_min_raw) if pick_min_raw else None,
        pick_max=int(pick_max_raw) if pick_max_raw else None,
        round_id=int(round_id_raw) if round_id_raw else None,
        year_filter=int(year_filter_raw) if year_filter_raw else None,
    )
    db.add(nom)
    db.commit()

    contest_id = form.get("contest_id")
    redirect = "/admin/nominations"
    parts = []
    if contest_id:
        parts.append(f"contest_id={contest_id}")
    if round_id_raw:
        parts.append(f"round_id={round_id_raw}")
    if parts:
        redirect += "?" + "&".join(parts)
    return RedirectResponse(url=redirect, status_code=303)


@router.post("/nominations/{nom_id}/edit")
async def edit_nomination(
    nom_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
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
        db.commit()

    contest_id = form.get("contest_id")
    round_id = form.get("round_id")
    parts = []
    if contest_id:
        parts.append(f"contest_id={contest_id}")
    if round_id:
        parts.append(f"round_id={round_id}")
    redirect = "/admin/nominations" + ("?" + "&".join(parts) if parts else "")
    return RedirectResponse(url=redirect, status_code=303)


@router.post("/nominations/{nom_id}/delete")
async def delete_nomination(
    nom_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
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
    redirect = "/admin/nominations" + ("?" + "&".join(parts) if parts else "")
    return RedirectResponse(url=redirect, status_code=303)


@router.post("/nominations/{nom_id}/move")
async def move_nomination(
    nom_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
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
    redirect = "/admin/nominations" + ("?" + "&".join(parts) if parts else "")
    return RedirectResponse(url=redirect, status_code=303)


@router.get("/nominations/export-longlist")
def export_longlist(
    year: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    q = (
        db.query(Nomination)
        .options(joinedload(Nomination.nominees).joinedload(Nominee.film))
        .order_by(Nomination.sort_order, Nomination.id)
    )
    if year:
        q = q.filter(Nomination.year_filter == year)
    nominations = q.all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Long-list"
    ws.append(["Номинация", "Номинант", "Год"])
    for nom in nominations:
        for nominee in nom.nominees:
            film_title = nominee.film.title if nominee.film else ""
            film_year = nominee.film.year if nominee.film else ""
            ws.append([nom.name, film_title, film_year])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"longlist_{year}.xlsx" if year else "longlist.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


@router.get("/nominations/{nom_id}", response_class=HTMLResponse)
def nomination_detail(
    nom_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    nom = (
        db.query(Nomination)
        .options(
            joinedload(Nomination.nominees).joinedload(Nominee.film),
            joinedload(Nomination.nominees).joinedload(Nominee.person),
        )
        .filter(Nomination.id == nom_id)
        .first()
    )
    if not nom:
        return HTMLResponse("Номинация не найдена.", status_code=404)
    return templates.TemplateResponse(request, "admin/nomination_detail.html", {"nom": nom})
