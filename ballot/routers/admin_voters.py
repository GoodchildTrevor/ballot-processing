from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
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


@router.post("/voters/{voter_id}/delete-vote")
def delete_voter_vote(voter_id: int, db: Session = Depends(get_db)):
    voter = db.get(Voter, voter_id)
    if voter:
        db.query(Vote).filter(Vote.voter_id == voter_id).delete()
        db.query(Ranking).filter(Ranking.voter_id == voter_id).delete()
        voter.voted_at = None
        db.commit()
    return RedirectResponse(url="/admin/voters", status_code=303)


@router.get("/voters/{voter_id}/edit-vote", response_class=HTMLResponse)
def edit_vote_form(voter_id: int, request: Request, db: Session = Depends(get_db)):
    voter = db.get(Voter, voter_id)
    if not voter:
        return HTMLResponse("Участник не найден.", status_code=404)
    nominations = db.query(Nomination).order_by(Nomination.sort_order, Nomination.id).all()
    rank_noms = [n for n in nominations if n.type == NominationType.RANK]
    pick_noms = [n for n in nominations if n.type == NominationType.PICK]

    # Current rankings: {nomination_id: {film_id: rank}}
    current_rankings = {}
    for r in voter.rankings:
        current_rankings.setdefault(r.nomination_id, {})[r.film_id] = r.rank

    # Current picks: {nomination_id: [nominee_id, ...]}
    current_picks = {}
    for v in voter.votes:
        if v.nominee:
            current_picks.setdefault(v.nominee.nomination_id, []).append(v.nominee_id)

    return templates.TemplateResponse(request, "admin/voter_edit.html", {
        "voter": voter,
        "rank_noms": rank_noms,
        "pick_noms": pick_noms,
        "current_rankings": current_rankings,
        "current_picks": current_picks,
    })


@router.post("/voters/{voter_id}/edit-vote")
async def edit_vote_submit(voter_id: int, request: Request, db: Session = Depends(get_db)):
    voter = db.get(Voter, voter_id)
    if not voter:
        return RedirectResponse(url="/admin/voters", status_code=303)

    # Wipe old votes
    db.query(Vote).filter(Vote.voter_id == voter_id).delete()
    db.query(Ranking).filter(Ranking.voter_id == voter_id).delete()
    db.flush()

    form = await request.form()
    nominations = db.query(Nomination).all()

    from ballot.models import Ranking as RankingModel, Vote as VoteModel
    from datetime import datetime, timezone

    for nom in nominations:
        if nom.type == NominationType.RANK:
            for nominee in nom.nominees:
                val = form.get(f"rank_{nom.id}_{nominee.film_id}")
                if val:
                    db.add(RankingModel(
                        voter_id=voter.id,
                        nomination_id=nom.id,
                        film_id=nominee.film_id,
                        rank=int(val),
                    ))
        elif nom.type == NominationType.PICK:
            chosen = form.getlist(f"pick_{nom.id}")
            pmax = nom.pick_max or 1
            chosen = chosen[:pmax]
            for nominee_id in chosen:
                db.add(VoteModel(voter_id=voter.id, nominee_id=int(nominee_id)))

    if voter.voted_at is None:
        voter.voted_at = datetime.now(timezone.utc)
    db.commit()
    return RedirectResponse(url="/admin/voters", status_code=303)
