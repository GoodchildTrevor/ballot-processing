from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from ballot.database import get_db
from ballot.models import Voter, Vote, Ranking, Nomination, NominationType
from ballot.auth import require_admin

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="ballot/templates")


@router.get("/voters", response_class=HTMLResponse)
def list_voters(request: Request, db: Session = Depends(get_db)):
    voters = (
        db.query(Voter)
        .options(
            joinedload(Voter.votes).joinedload(Vote.nominee),
            joinedload(Voter.rankings).joinedload(Ranking.voter),
        )
        .order_by(Voter.name)
        .all()
    )
    # Build per-voter ballot summary
    nominations = db.query(Nomination).order_by(Nomination.sort_order, Nomination.id).all()
    voter_ballots = []
    for voter in voters:
        ballot = []
        for nom in nominations:
            if nom.type == NominationType.PICK:
                chosen = [
                    v.nominee for v in voter.votes
                    if v.nominee and v.nominee.nomination_id == nom.id
                ]
                if chosen:
                    ballot.append({"nom": nom, "type": "pick", "items": chosen})
            else:
                ranks = [
                    r for r in voter.rankings if r.nomination_id == nom.id
                ]
                ranks.sort(key=lambda r: r.rank)
                if ranks:
                    ballot.append({"nom": nom, "type": "rank", "items": ranks})
        voter_ballots.append({"voter": voter, "ballot": ballot})
    return templates.TemplateResponse(request, "admin/voters.html", {"voter_ballots": voter_ballots})
