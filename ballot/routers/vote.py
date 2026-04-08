from typing import Optional
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from ballot.database import get_db
from ballot.models import Nomination, NominationType, Nominee, Vote, Ranking, Voter
from ballot.auth import require_voter

router = APIRouter(dependencies=[Depends(require_voter)])
templates = Jinja2Templates(directory="ballot/templates")


def _nominee_label(nominee: Nominee) -> str:
    """Human-readable label: Song (Film), Person (Film), or Film (year)."""
    if nominee.song:
        return f"{nominee.song} ({nominee.film.title})"
    if nominee.person:
        return f"{nominee.person.name} ({nominee.film.title})"
    return f"{nominee.film.title} ({nominee.film.year})"


@router.get("/vote", response_class=HTMLResponse)
def vote_page(request: Request, db: Session = Depends(get_db)):
    voter: Voter = request.state.voter
    nominations = db.query(Nomination).order_by(Nomination.sort_order, Nomination.id).all()
    existing_votes = {v.nominee_id for v in voter.votes}
    existing_ranks = {
        r.nomination_id: r.rank for r in voter.rankings
    }

    noms_data = []
    for nom in nominations:
        if nom.type == NominationType.PICK:
            items = [
                {"id": n.id, "label": _nominee_label(n)}
                for n in nom.nominees
            ]
            noms_data.append({
                "nom": nom,
                "items": items,
                "voted_ids": list(existing_votes & {n["id"] for n in items}),
            })
        else:
            films = [
                {"id": n.film_id, "title": n.film.title, "year": n.film.year}
                for n in nom.nominees
            ]
            noms_data.append({
                "nom": nom,
                "films": films,
                "current_rank": existing_ranks.get(nom.id),
            })

    draft = voter.draft or {}
    return templates.TemplateResponse(request, "vote.html", {
        "voter": voter,
        "noms_data": noms_data,
        "draft": draft,
    })


@router.post("/vote/draft")
async def save_draft(request: Request, db: Session = Depends(get_db)):
    voter: Voter = request.state.voter
    body = await request.json()
    voter.draft = body
    db.commit()
    return {"ok": True}


@router.post("/vote")
async def submit_vote(request: Request, db: Session = Depends(get_db)):
    voter: Voter = request.state.voter
    form = await request.form()

    # Clear previous votes and rankings
    db.query(Vote).filter(Vote.voter_id == voter.id).delete()
    db.query(Ranking).filter(Ranking.voter_id == voter.id).delete()

    nominations = db.query(Nomination).all()
    for nom in nominations:
        if nom.type == NominationType.PICK:
            key = f"pick_{nom.id}"
            raw = form.getlist(key)
            for val in raw:
                try:
                    nid = int(val)
                    if db.get(Nominee, nid):
                        db.add(Vote(voter_id=voter.id, nominee_id=nid))
                except ValueError:
                    pass
        else:
            for nominee in nom.nominees:
                key = f"rank_{nom.id}_{nominee.film_id}"
                val = form.get(key)
                if val:
                    try:
                        rank = int(val)
                        if 1 <= rank <= len(nom.nominees):
                            db.add(Ranking(
                                voter_id=voter.id,
                                nomination_id=nom.id,
                                film_id=nominee.film_id,
                                rank=rank,
                            ))
                    except ValueError:
                        pass

    from datetime import datetime, timezone
    voter.voted_at = datetime.now(timezone.utc)
    voter.draft = None
    db.commit()
    return RedirectResponse(url="/thank-you", status_code=303)


@router.get("/thank-you", response_class=HTMLResponse)
def thank_you(request: Request):
    return templates.TemplateResponse(request, "thank_you.html", {})
