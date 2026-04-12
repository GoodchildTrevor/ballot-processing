from typing import Optional
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from ballot.database import get_db
from ballot.models import (
    Contest, Round, RoundParticipation,
    Voter, Vote, Ranking, Nomination, NominationType, Nominee,
)
from ballot.auth import require_admin
from datetime import datetime, timezone

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="ballot/templates")


def _voter_voted_at(voter: Voter, round_ids: set[int] | None, db: Session) -> datetime | None:
    """Return the most recent voted_at for this voter within the given rounds."""
    q = db.query(RoundParticipation).filter(
        RoundParticipation.voter_id == voter.id,
        RoundParticipation.voted_at.isnot(None),
    )
    if round_ids is not None:
        q = q.filter(RoundParticipation.round_id.in_(round_ids))
    p = q.order_by(RoundParticipation.voted_at.desc()).first()
    if p:
        return p.voted_at

    # Legacy fallback: votes exist but no participation record
    vq = db.query(Vote).filter(Vote.voter_id == voter.id)
    rq = db.query(Ranking).filter(Ranking.voter_id == voter.id)
    if round_ids is not None:
        nom_ids = [
            n.id for n in db.query(Nomination.id)
            .filter(Nomination.round_id.in_(round_ids))
            .all()
        ]
        vq = vq.join(Nominee).filter(Nominee.nomination_id.in_(nom_ids))
        rq = rq.filter(Ranking.nomination_id.in_(nom_ids))
    if vq.first() is not None or rq.first() is not None:
        return datetime(2000, 1, 1, 0, 0, 0)
    return None


@router.get("/voters", response_class=HTMLResponse)
def list_voters(
    request: Request,
    contest_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    contests = (
        db.query(Contest)
        .order_by(Contest.year.desc())
        .all()
    )

    selected_contest = None
    round_ids: set[int] | None = None

    if contests:
        if contest_id:
            selected_contest = db.get(Contest, contest_id)
        if not selected_contest:
            selected_contest = contests[0]  # newest by default

        rounds = db.query(Round).filter(Round.contest_id == selected_contest.id).all()
        round_ids = {r.id for r in rounds}

    voters = (
        db.query(Voter)
        .options(
            joinedload(Voter.votes).joinedload(Vote.nominee).joinedload(Nominee.film),
            joinedload(Voter.votes).joinedload(Vote.nominee).joinedload(Nominee.person),
            joinedload(Voter.rankings).joinedload(Ranking.film),
        )
        .order_by(Voter.name)
        .all()
    )

    # Nominations scoped to selected contest's rounds
    if round_ids is not None:
        nominations = (
            db.query(Nomination)
            .filter(Nomination.round_id.in_(round_ids))
            .order_by(Nomination.sort_order, Nomination.id)
            .all()
        )
    else:
        nominations = (
            db.query(Nomination)
            .order_by(Nomination.sort_order, Nomination.id)
            .all()
        )

    nom_ids = {n.id for n in nominations}

    voter_ballots = []
    for voter in voters:
        voted_at = _voter_voted_at(voter, round_ids, db)
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
                ranks = sorted(
                    [r for r in voter.rankings if r.nomination_id == nom.id],
                    key=lambda r: r.rank,
                )
                if ranks:
                    ballot.append({"nom": nom, "type": "rank", "items": ranks})
        voter_ballots.append({"voter": voter, "ballot": ballot, "voted_at": voted_at})

    return templates.TemplateResponse(request, "admin/voters.html", {
        "voter_ballots": voter_ballots,
        "contests": contests,
        "selected_contest": selected_contest,
    })


@router.post("/voters/{voter_id}/delete-vote")
def delete_voter_vote(
    voter_id: int,
    contest_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    voter = db.get(Voter, voter_id)
    if voter:
        db.query(Vote).filter(Vote.voter_id == voter_id).delete()
        db.query(Ranking).filter(Ranking.voter_id == voter_id).delete()
        db.query(RoundParticipation).filter(
            RoundParticipation.voter_id == voter_id
        ).update({"voted_at": None, "draft": None})
        db.commit()
    redirect = f"/admin/voters?contest_id={contest_id}" if contest_id else "/admin/voters"
    return RedirectResponse(url=redirect, status_code=303)


@router.get("/voters/{voter_id}/edit-vote", response_class=HTMLResponse)
def edit_vote_form(
    voter_id: int,
    request: Request,
    contest_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    voter = (
        db.query(Voter)
        .options(
            joinedload(Voter.votes).joinedload(Vote.nominee),
            joinedload(Voter.rankings),
        )
        .filter(Voter.id == voter_id)
        .first()
    )
    if not voter:
        return HTMLResponse("Участник не найден.", status_code=404)

    # Scope nominations to contest if provided
    if contest_id:
        rounds = db.query(Round).filter(Round.contest_id == contest_id).all()
        round_ids = [r.id for r in rounds]
        nominations = (
            db.query(Nomination)
            .options(
                joinedload(Nomination.nominees).joinedload(Nominee.film),
                joinedload(Nomination.nominees).joinedload(Nominee.person),
            )
            .filter(Nomination.round_id.in_(round_ids))
            .order_by(Nomination.sort_order, Nomination.id)
            .all()
        )
    else:
        nominations = (
            db.query(Nomination)
            .options(
                joinedload(Nomination.nominees).joinedload(Nominee.film),
                joinedload(Nomination.nominees).joinedload(Nominee.person),
            )
            .order_by(Nomination.sort_order, Nomination.id)
            .all()
        )

    rank_noms = [n for n in nominations if n.type == NominationType.RANK]
    pick_noms = [n for n in nominations if n.type == NominationType.PICK]

    current_rankings = {}
    for r in voter.rankings:
        current_rankings.setdefault(r.nomination_id, {})[r.film_id] = r.rank

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
        "contest_id": contest_id,
    })


@router.post("/voters/{voter_id}/edit-vote")
async def edit_vote_submit(
    voter_id: int,
    request: Request,
    contest_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    voter = db.get(Voter, voter_id)
    if not voter:
        return RedirectResponse(url="/admin/voters", status_code=303)

    db.query(Vote).filter(Vote.voter_id == voter_id).delete()
    db.query(Ranking).filter(Ranking.voter_id == voter_id).delete()
    db.flush()

    form = await request.form()

    if contest_id:
        rounds = db.query(Round).filter(Round.contest_id == contest_id).all()
        round_ids = [r.id for r in rounds]
        nominations = (
            db.query(Nomination)
            .options(joinedload(Nomination.nominees))
            .filter(Nomination.round_id.in_(round_ids))
            .all()
        )
    else:
        nominations = (
            db.query(Nomination)
            .options(joinedload(Nomination.nominees))
            .all()
        )

    for nom in nominations:
        if nom.type == NominationType.RANK:
            for nominee in nom.nominees:
                val = form.get(f"rank_{nom.id}_{nominee.film_id}")
                if val:
                    try:
                        db.add(Ranking(
                            voter_id=voter.id,
                            nomination_id=nom.id,
                            film_id=nominee.film_id,
                            rank=int(val),
                        ))
                    except ValueError:
                        pass
        elif nom.type == NominationType.PICK:
            chosen = form.getlist(f"pick_{nom.id}")
            for nominee_id in chosen:
                try:
                    nid = int(nominee_id)
                    if db.get(Nominee, nid):
                        db.add(Vote(voter_id=voter.id, nominee_id=nid))
                except ValueError:
                    pass

    p = (
        db.query(RoundParticipation)
        .filter(RoundParticipation.voter_id == voter_id)
        .order_by(RoundParticipation.id.desc())
        .first()
    )
    if p:
        p.voted_at = datetime.now(timezone.utc)
    db.commit()

    redirect = f"/admin/voters?contest_id={contest_id}" if contest_id else "/admin/voters"
    return RedirectResponse(url=redirect, status_code=303)
