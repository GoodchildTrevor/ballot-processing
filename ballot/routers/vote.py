from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
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
    nominations = db.query(Nomination).all()
    rank_noms = [n for n in nominations if n.type == NominationType.RANK]
    pick_noms = [n for n in nominations if n.type == NominationType.PICK]
    return templates.TemplateResponse(
        request, "vote.html",
        {"voter": voter, "rank_noms": rank_noms, "pick_noms": pick_noms},
    )


@router.post("/vote/{voter_id}")
async def submit_vote(voter_id: int, request: Request, db: Session = Depends(get_db)):
    voter = db.get(Voter, voter_id)
    if not voter or voter.voted_at is not None:
        return RedirectResponse(url="/", status_code=303)

    form = await request.form()
    nominations = db.query(Nomination).all()

    errors = []
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
            limit = nom.pick_limit or 1
            # Server-side guard: silently trim to limit
            chosen = chosen[:limit]
            for nominee_id in chosen:
                db.add(Vote(voter_id=voter.id, nominee_id=int(nominee_id)))

    voter.voted_at = datetime.now(timezone.utc)
    db.commit()
    return templates.TemplateResponse(request, "thankyou.html", {"voter": voter})
