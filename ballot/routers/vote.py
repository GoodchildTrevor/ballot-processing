import io
from typing import Optional
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import openpyxl
from ballot.database import get_db
from ballot.models import Nomination, NominationType, Nominee, Vote, Ranking, Voter
from ballot.auth import require_voter

router = APIRouter(dependencies=[Depends(require_voter)])
templates = Jinja2Templates(directory="ballot/templates")


def _nominee_sort_key(nominee) -> str:
    """Alphabetical sort key: person name > item title > film title."""
    if nominee.person:
        return (nominee.person.name or "").lower()
    if nominee.item:
        return (nominee.item or "").lower()
    return (nominee.film.title if nominee.film else "").lower()


def _sort_nominations(nominations):
    for nom in nominations:
        nom.nominees = sorted(nom.nominees, key=_nominee_sort_key)
    return nominations


def _is_voting_open(nominations: list) -> tuple[bool, Optional[object]]:
    """Return (True, None) if all deadlines allow voting, else (False, earliest_expired_nom)."""
    now = datetime.now()
    for nom in nominations:
        if nom.vote_deadline and now > nom.vote_deadline:
            return False, nom
    return True, None


@router.get("/vote", response_class=HTMLResponse)
def vote_page(request: Request, db: Session = Depends(get_db)):
    voter: Voter = request.state.voter
    nominations = db.query(Nomination).order_by(Nomination.sort_order, Nomination.id).all()

    open_, expired_nom = _is_voting_open(nominations)
    if not open_:
        return templates.TemplateResponse(
            request, "voting_closed.html",
            {"nom": expired_nom},
            status_code=403,
        )

    _sort_nominations(nominations)
    draft = voter.draft or {}
    draft_restored = bool(draft)
    return templates.TemplateResponse(request, "vote.html", {
        "voter": voter,
        "nominations": nominations,
        "draft": draft,
        "draft_restored": draft_restored,
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
    nominations = db.query(Nomination).all()

    open_, expired_nom = _is_voting_open(nominations)
    if not open_:
        return templates.TemplateResponse(
            request, "voting_closed.html",
            {"nom": expired_nom},
            status_code=403,
        )

    form = await request.form()

    db.query(Vote).filter(Vote.voter_id == voter.id).delete()
    db.query(Ranking).filter(Ranking.voter_id == voter.id).delete()

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

    voter.voted_at = datetime.now(timezone.utc)
    voter.draft = None
    db.commit()
    return RedirectResponse(url="/thank-you", status_code=303)


@router.get("/thank-you", response_class=HTMLResponse)
def thank_you(request: Request, db: Session = Depends(get_db)):
    voter: Voter = request.state.voter
    has_votes = bool(voter.voted_at)
    return templates.TemplateResponse(request, "thank_you.html", {"has_votes": has_votes})


@router.get("/my-ballot/export")
def export_my_ballot(request: Request, db: Session = Depends(get_db)):
    """Download the current voter's own ballot as an Excel file."""
    voter: Voter = request.state.voter
    if not voter.voted_at:
        return RedirectResponse(url="/thank-you", status_code=303)

    nominations = db.query(Nomination).order_by(Nomination.sort_order, Nomination.id).all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Бюллетень"
    ws.append(["Номинация", "Тип", "Номинант", "Голос / Место"])

    pick_votes = {
        v.nominee_id for v in db.query(Vote).filter(Vote.voter_id == voter.id).all()
    }
    rankings = {
        (r.nomination_id, r.film_id): r.rank
        for r in db.query(Ranking).filter(Ranking.voter_id == voter.id).all()
    }

    for nom in nominations:
        if nom.type == NominationType.PICK:
            voted_nominees = [n for n in nom.nominees if n.id in pick_votes]
            if voted_nominees:
                for n in voted_nominees:
                    if n.persons_label:
                        label = f"{n.persons_label} ({n.film.title})"
                    elif n.item:
                        label = f"{n.item} ({n.film.title})"
                    else:
                        label = n.film.title
                    ws.append([nom.name, "PICK", label, "✔"])
            else:
                ws.append([nom.name, "PICK", "— пропущено", ""])
        else:
            ranked = [
                (rankings[(nom.id, n.film_id)], n.film.title)
                for n in nom.nominees
                if (nom.id, n.film_id) in rankings
            ]
            if ranked:
                for rank, title in sorted(ranked):
                    ws.append([nom.name, "RANK", title, rank])
            else:
                ws.append([nom.name, "RANK", "— пропущено", ""])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    safe_name = voter.name.replace(" ", "_")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=ballot_{safe_name}.xlsx"},
    )
