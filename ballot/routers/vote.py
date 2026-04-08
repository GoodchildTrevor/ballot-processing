import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from ballot.database import get_db
from ballot.models import Voter, Nomination, NominationType, Vote, Ranking

router = APIRouter()
templates = Jinja2Templates(directory="ballot/templates")


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@router.post("/")
async def enter_name(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        return templates.TemplateResponse(request, "index.html", {"error": "Введите ник."})
    voter = db.query(Voter).filter(Voter.name == name).first()
    if not voter:
        voter = Voter(name=name)
        db.add(voter)
        db.commit()
        db.refresh(voter)
    if voter.voted_at is not None:
        return templates.TemplateResponse(request, "index.html", {"error": "Вы уже проголосовали."})
    return RedirectResponse(url=f"/vote/{voter.id}", status_code=303)


@router.get("/vote/{voter_id}", response_class=HTMLResponse)
def ballot(voter_id: int, request: Request, db: Session = Depends(get_db)):
    voter = db.get(Voter, voter_id)
    if not voter:
        return HTMLResponse("Участник не найден.", status_code=404)
    if voter.voted_at is not None:
        return templates.TemplateResponse(request, "index.html", {"error": "Вы уже проголосовали."})
    nominations = db.query(Nomination).order_by(Nomination.sort_order, Nomination.id).all()
    return templates.TemplateResponse(
        request, "vote.html",
        {
            "voter": voter,
            "nominations": nominations,
            "draft": voter.draft or {},
        },
    )


@router.post("/vote/{voter_id}/draft")
async def save_draft(voter_id: int, request: Request, db: Session = Depends(get_db)):
    """Autosave draft ballot — called from JS on every change."""
    voter = db.get(Voter, voter_id)
    if not voter or voter.voted_at is not None:
        return JSONResponse({"ok": False}, status_code=403)
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False}, status_code=400)
    voter.draft = data
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/vote/{voter_id}")
async def submit_vote(voter_id: int, request: Request, db: Session = Depends(get_db)):
    voter = db.get(Voter, voter_id)
    if not voter or voter.voted_at is not None:
        return RedirectResponse(url="/", status_code=303)

    form = await request.form()
    nominations = db.query(Nomination).order_by(Nomination.sort_order, Nomination.id).all()

    errors = []
    for nom in nominations:
        if nom.type == NominationType.RANK:
            rank_max = nom.nominees_count or len(nom.nominees)
            vals = [form.get(f"rank_{nom.id}_{n.film_id}") for n in nom.nominees]
            filled = [v for v in vals if v]

            if 0 < len(filled) < rank_max:
                errors.append(
                    f"Номинация «{nom.name}»: заполните все {rank_max} мест или не выбирайте ничего."
                )
            elif filled and len(set(filled)) < len(filled):
                errors.append(
                    f"Номинация «{nom.name}»: два фильма на одном месте."
                )
            elif filled:
                bad = [v for v in filled if not (1 <= int(v) <= rank_max)]
                if bad:
                    errors.append(
                        f"Номинация «{nom.name}»: место должно быть от 1 до {rank_max}."
                    )

        elif nom.type == NominationType.PICK:
            chosen = form.getlist(f"pick_{nom.id}")
            pmin = nom.pick_min or 1
            pmax = nom.pick_max or 1
            n = len(chosen)
            if 0 < n < pmin:
                errors.append(
                    f"Номинация «{nom.name}»: выбрано {n}, нужно минимум {pmin} или отмените выбор."
                )
            if n > pmax:
                errors.append(
                    f"Номинация «{nom.name}»: можно выбрать не более {pmax}."
                )

    if errors:
        return templates.TemplateResponse(
            request, "vote.html",
            {"voter": voter, "nominations": nominations, "errors": errors, "draft": voter.draft or {}},
            status_code=422,
        )

    for nom in nominations:
        if nom.type == NominationType.RANK:
            for nominee in nom.nominees:
                val = form.get(f"rank_{nom.id}_{nominee.film_id}")
                if val:
                    db.add(Ranking(
                        voter_id=voter.id,
                        nomination_id=nom.id,
                        film_id=nominee.film_id,
                        rank=int(val),
                    ))
        elif nom.type == NominationType.PICK:
            chosen = form.getlist(f"pick_{nom.id}")
            pmax = nom.pick_max or 1
            for nominee_id in chosen[:pmax]:
                db.add(Vote(voter_id=voter.id, nominee_id=int(nominee_id)))

    voter.voted_at = datetime.now(timezone.utc)
    voter.draft = None  # clear draft on final submit
    db.commit()
    return templates.TemplateResponse(request, "thankyou.html", {"voter": voter})
