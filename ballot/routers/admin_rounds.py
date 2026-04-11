"""Admin router for Round management.

Routes
------
GET  /admin/rounds                   – list all rounds
POST /admin/rounds                   – create a round
POST /admin/rounds/{id}/edit         – update label / deadline / is_active
POST /admin/rounds/{id}/delete       – delete (only if no votes)
GET  /admin/rounds/{id}/preview      – preview / edit draft final round
POST /admin/rounds/{id}/activate     – set is_active=True (requires deadline)
POST /admin/rounds/{id}/promote      – auto-create FINAL round from longlist
POST /admin/rounds/{id}/nominees/{nid}/toggle-shortlist  – flip is_shortlisted
"""
from __future__ import annotations

import io
from collections import defaultdict
from datetime import datetime
from statistics import mean
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from ballot.auth import require_admin
from ballot.database import get_db
from ballot.models import (
    Nomination, NominationType,
    Nominee, Vote, Ranking,
    Round, RoundType, RoundParticipation,
    Film,
)

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="ballot/templates")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_deadline(v: Optional[str]) -> Optional[datetime]:
    if v and v.strip():
        try:
            return datetime.fromisoformat(v.strip())
        except ValueError:
            pass
    return None


def _rank_scores(nom: Nomination, db: Session) -> list[tuple[Nominee, float]]:
    """Return nominees sorted by average rank (lower = better)."""
    rankings = db.query(Ranking).filter(Ranking.nomination_id == nom.id).all()
    scores: dict[int, list[int]] = defaultdict(list)
    film_to_nominee: dict[int, Nominee] = {n.film_id: n for n in nom.nominees}
    for r in rankings:
        scores[r.film_id].append(r.rank)
    result = []
    for nominee in nom.nominees:
        s = scores.get(nominee.film_id)
        avg = mean(s) if s else float("inf")
        result.append((nominee, avg))
    result.sort(key=lambda x: x[1])
    return result


def _pick_scores(nom: Nomination, db: Session) -> list[tuple[Nominee, int]]:
    """Return nominees sorted by vote count (higher = better)."""
    votes = db.query(Vote).join(Nominee).filter(
        Nominee.nomination_id == nom.id,
        Vote.is_runner_up == False,  # noqa: E712
    ).all()
    counts: dict[int, int] = defaultdict(int)
    for v in votes:
        counts[v.nominee_id] += 1
    result = [(n, counts.get(n.id, 0)) for n in nom.nominees]
    result.sort(key=lambda x: x[1], reverse=True)
    return result


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

@router.get("/rounds", response_class=HTMLResponse)
def list_rounds(request: Request, db: Session = Depends(get_db)):
    rounds = db.query(Round).order_by(Round.sort_order, Round.id).all()
    return templates.TemplateResponse(
        request, "admin/rounds.html", {"rounds": rounds}
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@router.post("/rounds")
def create_round(
    label: str = Form(...),
    round_type: RoundType = Form(...),
    year: int = Form(...),
    deadline: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    last = db.query(Round).order_by(Round.sort_order.desc()).first()
    order = (last.sort_order + 1) if last else 0
    db.add(Round(
        label=label.strip(),
        round_type=round_type,
        year=year,
        deadline=_parse_deadline(deadline),
        is_active=False,
        sort_order=order,
    ))
    db.commit()
    return RedirectResponse(url="/admin/rounds", status_code=303)


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------

@router.post("/rounds/{round_id}/edit")
def edit_round(
    round_id: int,
    label: str = Form(...),
    deadline: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    rnd = db.get(Round, round_id)
    if rnd:
        rnd.label    = label.strip()
        rnd.deadline = _parse_deadline(deadline)
        db.commit()
    return RedirectResponse(url="/admin/rounds", status_code=303)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@router.post("/rounds/{round_id}/delete")
def delete_round(round_id: int, db: Session = Depends(get_db)):
    rnd = db.get(Round, round_id)
    if rnd:
        db.delete(rnd)
        db.commit()
    return RedirectResponse(url="/admin/rounds", status_code=303)


# ---------------------------------------------------------------------------
# Activate (requires deadline set)
# ---------------------------------------------------------------------------

@router.post("/rounds/{round_id}/activate")
def activate_round(round_id: int, db: Session = Depends(get_db)):
    rnd = db.get(Round, round_id)
    if rnd and rnd.deadline:
        rnd.is_active = True
        db.commit()
    return RedirectResponse(url="/admin/rounds", status_code=303)


@router.post("/rounds/{round_id}/deactivate")
def deactivate_round(round_id: int, db: Session = Depends(get_db)):
    rnd = db.get(Round, round_id)
    if rnd:
        rnd.is_active = False
        db.commit()
    return RedirectResponse(url="/admin/rounds", status_code=303)


# ---------------------------------------------------------------------------
# Promote: create a FINAL draft from a LONGLIST round
# ---------------------------------------------------------------------------

@router.post("/rounds/{round_id}/promote")
def promote_to_final(round_id: int, db: Session = Depends(get_db)):
    longlist = db.get(Round, round_id)
    if not longlist or longlist.round_type != RoundType.LONGLIST:
        return RedirectResponse(url="/admin/rounds", status_code=303)

    # Create the FINAL round (inactive, no deadline yet)
    last = db.query(Round).order_by(Round.sort_order.desc()).first()
    final = Round(
        label=f"Финал {longlist.year}",
        round_type=RoundType.FINAL,
        year=longlist.year,
        deadline=None,
        is_active=False,
        sort_order=(last.sort_order + 1) if last else 0,
    )
    db.add(final)
    db.flush()

    nominations = (
        db.query(Nomination)
        .options(joinedload(Nomination.nominees).joinedload(Nominee.film))
        .filter(Nomination.round_id == longlist.id)
        .order_by(Nomination.sort_order, Nomination.id)
        .all()
    )

    for nom in nominations:
        if nom.type == NominationType.RANK:
            scored = _rank_scores(nom, db)
            # All nominees pass; admin can remove on preview
            shortlisted = [n for n, _ in scored]
        else:
            # Top nominees by vote count
            scored_pick = _pick_scores(nom, db)
            # For PICK: take all that received at least 1 vote; fallback = all
            shortlisted = [n for n, c in scored_pick if c > 0] or [n for n, _ in scored_pick]

        # Mark is_shortlisted on longlist nominees
        for nominee in shortlisted:
            nominee.is_shortlisted = True

        final_nom = Nomination(
            round_id=final.id,
            name=nom.name,
            sort_order=nom.sort_order,
            year_filter=nom.year_filter,
            type=nom.type,
            # RANK: keep same nominees_count
            nominees_count=len(shortlisted) if nom.type == NominationType.RANK else None,
            # FINAL PICK: strictly 1 + runner-up
            pick_min=1 if nom.type == NominationType.PICK else None,
            pick_max=1 if nom.type == NominationType.PICK else None,
            has_runner_up=True if nom.type == NominationType.PICK else False,
        )
        db.add(final_nom)
        db.flush()

        for nominee in shortlisted:
            db.add(Nominee(
                nomination_id=final_nom.id,
                film_id=nominee.film_id,
                person_id=nominee.person_id,
                item=nominee.item,
                item_url=nominee.item_url,
                is_shortlisted=True,
            ))

    db.commit()
    return RedirectResponse(url=f"/admin/rounds/{final.id}/preview", status_code=303)


# ---------------------------------------------------------------------------
# Preview / edit draft final round
# ---------------------------------------------------------------------------

@router.get("/rounds/{round_id}/preview", response_class=HTMLResponse)
def preview_round(round_id: int, request: Request, db: Session = Depends(get_db)):
    rnd = db.get(Round, round_id)
    if not rnd:
        return RedirectResponse(url="/admin/rounds", status_code=303)

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
    films = db.query(Film).order_by(Film.title).all()

    return templates.TemplateResponse(
        request, "admin/round_preview.html",
        {"rnd": rnd, "nominations": nominations, "films": films},
    )


# ---------------------------------------------------------------------------
# Toggle is_shortlisted on a nominee (used in preview)
# ---------------------------------------------------------------------------

@router.post("/rounds/{round_id}/nominees/{nominee_id}/toggle-shortlist")
def toggle_shortlist(
    round_id: int,
    nominee_id: int,
    db: Session = Depends(get_db),
):
    nominee = db.get(Nominee, nominee_id)
    if nominee:
        nominee.is_shortlisted = not nominee.is_shortlisted
        db.commit()
    return RedirectResponse(url=f"/admin/rounds/{round_id}/preview", status_code=303)
