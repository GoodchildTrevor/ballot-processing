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
    return templates.TemplateResponse("index.html", {"request": request})


@router.post("/")
async def enter_name(
    request: Request,
    db: Session = Depends(get_db),
):
    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "error": "Введите ник."},
        )
    voter = db.query(Voter).filter(Voter.name == name).first()
    if not voter:
        voter = Voter(name=name)
        db.add(voter)
        db.commit()
        db.refresh(voter)
    if voter.voted_at is not None:
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "error": "Вы уже проголосовали."},
        )
    return RedirectResponse(url=f"/vote/{voter.id}", status_code=303)


@router.get("/vote/{voter_id}", response_class=HTMLResponse)
def ballot(voter_id: int, request: Request, db: Session = Depends(get_db)):
    voter = db.get(Voter, voter_id)
    if not voter:
        return HTMLResponse("Участник не найден.", status_code=404)
    if voter.voted_at is not None:
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "error": "Вы уже проголосовали."},
        )
    nominations = db.query(Nomination).all()
    rank_noms = [n for n in nominations if n.type == NominationType.RANK]
    pick_noms = [n for n in nominations if n.type == NominationType.PICK]
    return templates.TemplateResponse(
        "vote.html",
        {"request": request, "voter": voter, "rank_noms": rank_noms, "pick_noms": pick_noms},
    )


@router.post("/vote/{voter_id}")
async def submit_vote(
    voter_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    voter = db.get(Voter, voter_id)
    if not voter or voter.voted_at is not None:
        return RedirectResponse(url="/", status_code=303)

    form = await request.form()
    nominations = db.query(Nomination).all()

    for nom in nominations:
        if nom.type == NominationType.RANK:
            for nominee in nom.nominees:
                key = f"rank_{nom.id}_{nominee.film_id}"
                val = form.get(key)
                if val:
                    ranking = Ranking(
                        voter_id=voter.id,
                        nomination_id=nom.id,
                        film_id=nominee.film_id,
                        rank=int(val),
                    )
                    db.add(ranking)
        elif nom.type == NominationType.PICK:
            key = f"pick_{nom.id}"
            nominee_id = form.get(key)
            if nominee_id:
                vote = Vote(voter_id=voter.id, nominee_id=int(nominee_id))
                db.add(vote)

    voter.voted_at = datetime.now(timezone.utc)
    db.commit()
    return templates.TemplateResponse("thankyou.html", {"request": request, "voter": voter})
