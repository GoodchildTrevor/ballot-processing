from typing import Optional
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func

from ballot.database import get_db
from ballot.models import (
    Contest, Round, RoundParticipation,
    Voter, Vote, Ranking, Nomination, NominationType, Nominee,
)
from ballot.auth import require_admin
from datetime import datetime, timezone

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="ballot/templates")


def _voter_voted_at(voter: Voter, round_ids: set[int] | None, db: Session) -> Optional[datetime]:
    """
    Get the most recent voted_at for a voter within the given rounds.
    
    :param voter: Voter object
    :param round_ids: Set of round IDs
    :param db: Database session
    :returns: Most recent voted_at or None if no participation record exists
    """
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
    round_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """
    Display list of voters in HTML format.
    
    :param request: FastAPI request object
    :param contest_id: Optional contest ID to filter voters
    :param round_id: Optional round ID to filter voters
    :param db: Database session
    :returns: HTML response with voters list page
    """
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
    round_ids: set[int] | None = None

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

        if selected_round:
            round_ids = {selected_round.id}
        elif all_rounds:
            selected_round = all_rounds[0]
            round_ids = {selected_round.id}
        else:
            round_ids = set()

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

    if round_ids:
        nominations = (
            db.query(Nomination)
            .filter(Nomination.round_id.in_(round_ids))
            .order_by(Nomination.sort_order, Nomination.id)
            .all()
        )
    else:
        nominations = []

    voter_ballots = []
    for voter in voters:
        voted_at = _voter_voted_at(voter, round_ids, db)
        ballot = []
        for nom in nominations:
            if nom.type == NominationType.PICK:
                # Separate regular votes and runner-ups, sort regular first
                votes_list = [
                    (v.nominee, v.is_runner_up) for v in voter.votes
                    if v.nominee and v.nominee.nomination_id == nom.id
                ]
                # Sort: regular votes (is_runner_up=False) first, then runner-ups
                votes_list.sort(key=lambda x: x[1])
                if votes_list:
                    ballot.append({"nom": nom, "type": "pick", "items": votes_list})
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
        "selected_round": selected_round,
    })


@router.post("/voters/{voter_id}/delete-vote")
def delete_voter_vote(
    voter_id: int,
    contest_id: Optional[int] = Query(None),
    round_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """
    Delete a voter's vote for a specific round.
    
    :param voter_id: ID of the voter
    :param contest_id: Optional contest ID
    :param round_id: Optional round ID
    :param db: Database session
    :returns: RedirectResponse to the voters list page
    """
    voter = db.get(Voter, voter_id)
    if voter and round_id:
        nom_ids = [
            n.id for n in db.query(Nomination)
            .filter(Nomination.round_id == round_id)
            .all()
        ]
        nominee_ids = [
            n.id for n in db.query(Nominee)
            .filter(Nominee.nomination_id.in_(nom_ids))
            .all()
        ] if nom_ids else []
        if nominee_ids:
            db.query(Vote).filter(
                Vote.voter_id == voter_id,
                Vote.nominee_id.in_(nominee_ids),
            ).delete(synchronize_session="fetch")
        if nom_ids:
            db.query(Ranking).filter(
                Ranking.voter_id == voter_id,
                Ranking.nomination_id.in_(nom_ids),
            ).delete(synchronize_session="fetch")
        db.query(RoundParticipation).filter(
            RoundParticipation.voter_id == voter_id,
            RoundParticipation.round_id == round_id,
        ).update({"voted_at": None, "draft": None})
        db.commit()
    elif voter:
        db.query(Vote).filter(Vote.voter_id == voter_id).delete()
        db.query(Ranking).filter(Ranking.voter_id == voter_id).delete()
        db.query(RoundParticipation).filter(
            RoundParticipation.voter_id == voter_id
        ).update({"voted_at": None, "draft": None})
        db.commit()

    parts = []
    if contest_id:
        parts.append(f"contest_id={contest_id}")
    if round_id:
        parts.append(f"round_id={round_id}")
    redirect = "/admin/voters" + ("?" + "&".join(parts) if parts else "")
    return RedirectResponse(url=redirect, status_code=303)


@router.get("/voters/{voter_id}/edit-vote", response_class=HTMLResponse)
def edit_vote_form(
    voter_id: int,
    request: Request,
    contest_id: Optional[int] = Query(None),
    round_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """
    Display the edit vote form for a voter.
    
    :param voter_id: ID of the voter
    :param request: FastAPI request object
    :param contest_id: Optional contest ID
    :param round_id: Optional round ID
    :param db: Database session
    :returns: HTML response with edit vote form
    """
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

    if round_id:
        nominations = (
            db.query(Nomination)
            .options(
                joinedload(Nomination.nominees).joinedload(Nominee.film),
                joinedload(Nomination.nominees).joinedload(Nominee.person),
            )
            .filter(Nomination.round_id == round_id)
            .order_by(Nomination.sort_order, Nomination.id)
            .all()
        )
    elif contest_id:
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
    current_runner_ups = {}
    for v in voter.votes:
        if v.nominee:
            if v.is_runner_up:
                current_runner_ups.setdefault(v.nominee.nomination_id, []).append(v.nominee_id)
            else:
                current_picks.setdefault(v.nominee.nomination_id, []).append(v.nominee_id)

    return templates.TemplateResponse(request, "admin/voter_edit.html", {
        "voter": voter,
        "rank_noms": rank_noms,
        "pick_noms": pick_noms,
        "current_rankings": current_rankings,
        "current_picks": current_picks,
        "current_runner_ups": current_runner_ups,
        "contest_id": contest_id,
        "round_id": round_id,
    })


@router.post("/voters/{voter_id}/edit-vote")
async def edit_vote_submit(
    voter_id: int,
    request: Request,
    contest_id: Optional[int] = Query(None),
    round_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """
    Processes and commits the changes made to a voter's ballot via the form.

    It first clears existing votes/rankings (scoped by round/contest) and then
    re-creates the records based on the form submission. Finally, it updates
    the RoundParticipation timestamp.

    :param voter_id: The ID of the voter submitting the ballot changes.
    :param request: FastAPI request object containing the form data (rankings/picks).
    :param contest_id: Optional ID of the contest (used for scoping).
    :param round_id: Optional ID of the round (scopes the update and timestamp).
    :param db: The SQLAlchemy database session object.
    :return: Redirects to the main voter list page with appropriate filters.
    """
    voter = db.get(Voter, voter_id)
    if not voter:
        return RedirectResponse(url="/admin/voters", status_code=303)

    if round_id:
        nom_ids = [n.id for n in db.query(Nomination).filter(Nomination.round_id == round_id).all()]
        nominee_ids = [n.id for n in db.query(Nominee).filter(Nominee.nomination_id.in_(nom_ids)).all()] if nom_ids else []
        if nominee_ids:
            db.query(Vote).filter(Vote.voter_id == voter_id, Vote.nominee_id.in_(nominee_ids)).delete(synchronize_session="fetch")
        if nom_ids:
            db.query(Ranking).filter(Ranking.voter_id == voter_id, Ranking.nomination_id.in_(nom_ids)).delete(synchronize_session="fetch")
    else:
        db.query(Vote).filter(Vote.voter_id == voter_id).delete()
        db.query(Ranking).filter(Ranking.voter_id == voter_id).delete()
    db.flush()

    form = await request.form()

    if round_id:
        nominations = (
            db.query(Nomination)
            .options(joinedload(Nomination.nominees))
            .filter(Nomination.round_id == round_id)
            .all()
        )
    elif contest_id:
        rounds = db.query(Round).filter(Round.contest_id == contest_id).all()
        nominations = (
            db.query(Nomination)
            .options(joinedload(Nomination.nominees))
            .filter(Nomination.round_id.in_([r.id for r in rounds]))
            .all()
        )
    else:
        nominations = db.query(Nomination).options(joinedload(Nomination.nominees)).all()

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
            runner_ups = form.getlist(f"runner_up_{nom.id}")
            for nominee_id in chosen:
                try:
                    nid = int(nominee_id)
                    if db.get(Nominee, nid):
                        db.add(Vote(voter_id=voter.id, nominee_id=nid, is_runner_up=False))
                except ValueError:
                    pass
            for nominee_id in runner_ups:
                try:
                    nid = int(nominee_id)
                    if db.get(Nominee, nid):
                        db.add(Vote(voter_id=voter.id, nominee_id=nid, is_runner_up=True))
                except ValueError:
                    pass

    # Update participation for the specific round
    target_round_id = round_id
    if not target_round_id and contest_id:
        rounds = db.query(Round).filter(Round.contest_id == contest_id).all()
        if rounds:
            target_round_id = rounds[0].id

    if target_round_id:
        p = db.query(RoundParticipation).filter_by(
            voter_id=voter_id, round_id=target_round_id
        ).first()
        if not p:
            p = RoundParticipation(voter_id=voter_id, round_id=target_round_id)
            db.add(p)
        p.voted_at = datetime.now(timezone.utc)
    else:
        p = (
            db.query(RoundParticipation)
            .filter(RoundParticipation.voter_id == voter_id)
            .order_by(RoundParticipation.id.desc())
            .first()
        )
        if p:
            p.voted_at = datetime.now(timezone.utc)
    db.commit()

    parts = []
    if contest_id:
        parts.append(f"contest_id={contest_id}")
    if round_id:
        parts.append(f"round_id={round_id}")
    redirect = "/admin/voters" + ("?" + "&".join(parts) if parts else "")
    return RedirectResponse(url=redirect, status_code=303)
