"""Voter-facing routes — round-aware.

URL scheme
----------
/vote                     GET  – redirect to the active round's vote page
/{year}/vote              GET  – ballot page for the active round of a given year
/{year}/vote              POST – submit ballot for that round
/{year}/draft             POST – autosave draft for that round
/{year}/ballot-export     POST – download ballot as xlsx (client-side rows)
/rounds/{round_id}/vote   GET  – ballot page by round id (kept for compat)
/rounds/{round_id}/vote   POST – submit ballot by round id (kept for compat)
/rounds/{round_id}/draft  POST – autosave draft by round id
/thank-you                GET  – confirmation
/my-ballot/{round_id}/export  GET – download own ballot as xlsx
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
import openpyxl
from openpyxl.styles import Font

from ballot.auth import require_voter
from ballot.database import get_db
from ballot.models import (
    Nomination, NominationType,
    Nominee, Vote, Ranking, Voter,
    Round, RoundParticipation,
)

router = APIRouter(dependencies=[Depends(require_voter)])
templates = Jinja2Templates(directory="ballot/templates")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _content_disposition(filename: str) -> str:
    """Return a Content-Disposition header value safe for non-ASCII filenames.
    Uses RFC 5987 encoding so Cyrillic characters don\'t cause latin-1 errors.
    """
    ascii_name = filename.encode("ascii", "ignore").decode()
    encoded_name = quote(filename, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8\'\'{encoded_name}"


def _get_or_create_participation(
    db: Session, round_id: int, voter_id: int
) -> RoundParticipation:
    p = db.query(RoundParticipation).filter_by(
        round_id=round_id, voter_id=voter_id
    ).first()
    if not p:
        p = RoundParticipation(round_id=round_id, voter_id=voter_id)
        db.add(p)
        db.flush()
    return p


def _nominations_for_round(db: Session, round_id: int) -> list[Nomination]:
    return (
        db.query(Nomination)
        .options(
            joinedload(Nomination.nominees).joinedload(Nominee.film),
            joinedload(Nomination.nominees).joinedload(Nominee.person),
        )
        .filter(Nomination.round_id == round_id)
        .order_by(Nomination.sort_order, Nomination.id)
        .all()
    )


def _find_active_round_for_year(db: Session, year: int) -> Round | None:
    return (
        db.query(Round)
        .filter(Round.year == year, Round.is_active == True)  # noqa: E712
        .order_by(Round.sort_order)
        .first()
    )


def _render_vote_page(request, db, rnd, voter):
    nominations = _nominations_for_round(db, rnd.id)
    participation = _get_or_create_participation(db, rnd.id, voter.id)
    db.commit()
    draft = participation.draft or {}
    draft_restored = bool(draft)
    return templates.TemplateResponse(request, "vote.html", {
        "voter": voter,
        "round": rnd,
        "nominations": nominations,
        "draft": draft,
        "draft_restored": draft_restored,
    })


def _check_round_open(request, rnd) -> HTMLResponse | None:
    if not rnd or not rnd.is_active:
        return templates.TemplateResponse(
            request, "voting_closed.html",
            {"nom": None, "message": "Этот раунд не активен."},
            status_code=403,
        )
    if rnd.deadline and datetime.now() > rnd.deadline:
        return templates.TemplateResponse(
            request, "voting_closed.html",
            {"nom": None, "round": rnd, "message": "Дедлайн голосования прошёл."},
            status_code=403,
        )
    return None


# ---------------------------------------------------------------------------
# /vote
# ---------------------------------------------------------------------------

@router.get("/vote", response_class=HTMLResponse)
def vote_redirect(request: Request, db: Session = Depends(get_db)):
    active = db.query(Round).filter(Round.is_active == True).order_by(Round.sort_order).first()  # noqa: E712
    if not active:
        return templates.TemplateResponse(
            request, "voting_closed.html",
            {"nom": None, "message": "Активных раундов нет."},
            status_code=403,
        )
    return RedirectResponse(url=f"/{active.year}/vote", status_code=302)


@router.get("/{year}/vote", response_class=HTMLResponse)
def vote_page_year(year: int, request: Request, db: Session = Depends(get_db)):
    voter: Voter = request.state.voter
    rnd = _find_active_round_for_year(db, year)
    if not rnd:
        return templates.TemplateResponse(
            request, "voting_closed.html",
            {"nom": None, "message": f"Активных раундов для {year} года нет."},
            status_code=403,
        )
    err = _check_round_open(request, rnd)
    if err:
        return err
    return _render_vote_page(request, db, rnd, voter)


@router.post("/{year}/vote")
async def submit_vote_year(year: int, request: Request, db: Session = Depends(get_db)):
    voter: Voter = request.state.voter
    rnd = _find_active_round_for_year(db, year)
    if not rnd:
        return templates.TemplateResponse(
            request, "voting_closed.html",
            {"nom": None, "message": f"Активных раундов для {year} года нет."},
            status_code=403,
        )
    err = _check_round_open(request, rnd)
    if err:
        return err
    return await _do_submit(request, db, rnd, voter)


@router.post("/{year}/draft")
async def save_draft_year(year: int, request: Request, db: Session = Depends(get_db)):
    voter: Voter = request.state.voter
    rnd = _find_active_round_for_year(db, year)
    if not rnd:
        return {"ok": False, "error": "no active round"}
    body = await request.json()
    p = _get_or_create_participation(db, rnd.id, voter.id)
    p.draft = body
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /{year}/ballot-export
# ---------------------------------------------------------------------------

@router.post("/{year}/ballot-export")
async def ballot_export(year: int, request: Request, db: Session = Depends(get_db)):
    voter: Voter = request.state.voter
    body: dict[str, Any] = await request.json()
    rows: list[list] = body.get("rows", [])

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Бюллетень"

    for i, row in enumerate(rows):
        ws.append(row)
        if i == 0:
            for cell in ws[1]:
                cell.font = Font(bold=True)

    for col in ws.columns:
        max_len = max((len(str(c.value or "")) for c in col), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 60)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    fname = f"ballot_{year}_{voter.name}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": _content_disposition(fname)},
    )


# ---------------------------------------------------------------------------
# Compat routes
# ---------------------------------------------------------------------------

@router.get("/rounds/{round_id}/vote", response_class=HTMLResponse)
def vote_page(round_id: int, request: Request, db: Session = Depends(get_db)):
    rnd = db.get(Round, round_id)
    err = _check_round_open(request, rnd)
    if err:
        return err
    return RedirectResponse(url=f"/{rnd.year}/vote", status_code=301)


@router.post("/rounds/{round_id}/draft")
async def save_draft(round_id: int, request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    p = _get_or_create_participation(db, round_id, request.state.voter.id)
    p.draft = body
    db.commit()
    return {"ok": True}


@router.post("/rounds/{round_id}/vote")
async def submit_vote_compat(round_id: int, request: Request, db: Session = Depends(get_db)):
    voter: Voter = request.state.voter
    rnd = db.get(Round, round_id)
    err = _check_round_open(request, rnd)
    if err:
        return err
    return await _do_submit(request, db, rnd, voter)


# ---------------------------------------------------------------------------
# Shared submit logic
# ---------------------------------------------------------------------------

async def _do_submit(request: Request, db: Session, rnd: Round, voter: Voter):
    nominations = _nominations_for_round(db, rnd.id)
    form = await request.form()

    round_nominee_ids = {n.id for nom in nominations for n in nom.nominees}

    for nom in nominations:
        if nom.type == NominationType.RANK:
            db.query(Ranking).filter(
                Ranking.voter_id == voter.id,
                Ranking.nomination_id == nom.id,
            ).delete()
        else:
            nominee_ids = [n.id for n in nom.nominees]
            if nominee_ids:
                db.query(Vote).filter(
                    Vote.voter_id == voter.id,
                    Vote.nominee_id.in_(nominee_ids),
                ).delete(synchronize_session="fetch")

    is_final = (rnd.round_type.value == "FINAL")

    for nom in nominations:
        if nom.type == NominationType.PICK:
            key = f"pick_{nom.id}"
            raw = form.getlist(key)
            if nom.pick_max and len(raw) > nom.pick_max:
                raw = raw[:nom.pick_max]
            for val in raw:
                try:
                    nid = int(val)
                    if nid in round_nominee_ids:
                        db.add(Vote(voter_id=voter.id, nominee_id=nid, is_runner_up=False))
                except ValueError:
                    pass
            if is_final and nom.has_runner_up:
                ru_val = form.get(f"runnerup_{nom.id}")
                if ru_val:
                    try:
                        nid = int(ru_val)
                        if nid in round_nominee_ids:
                            db.add(Vote(voter_id=voter.id, nominee_id=nid, is_runner_up=True))
                    except ValueError:
                        pass
        else:
            for nominee in nom.nominees:
                val = form.get(f"rank_{nom.id}_{nominee.film_id}")
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

    participation = _get_or_create_participation(db, rnd.id, voter.id)
    participation.voted_at = datetime.now(timezone.utc)
    participation.draft = None
    db.commit()
    return RedirectResponse(url=f"/thank-you?round_id={rnd.id}", status_code=303)


# ---------------------------------------------------------------------------
# /thank-you
# ---------------------------------------------------------------------------

@router.get("/thank-you", response_class=HTMLResponse)
def thank_you(request: Request, db: Session = Depends(get_db)):
    voter: Voter = request.state.voter
    round_id_str = request.query_params.get("round_id")
    has_votes = False
    round_id = None
    if round_id_str:
        try:
            round_id = int(round_id_str)
            p = db.query(RoundParticipation).filter_by(
                round_id=round_id, voter_id=voter.id
            ).first()
            has_votes = bool(p and p.voted_at)
        except ValueError:
            pass
    return templates.TemplateResponse(
        request, "thank_you.html",
        {"has_votes": has_votes, "round_id": round_id},
    )


# ---------------------------------------------------------------------------
# /my-ballot/{round_id}/export
# ---------------------------------------------------------------------------

@router.get("/my-ballot/{round_id}/export")
def export_my_ballot(round_id: int, request: Request, db: Session = Depends(get_db)):
    voter: Voter = request.state.voter
    p = db.query(RoundParticipation).filter_by(
        round_id=round_id, voter_id=voter.id
    ).first()
    if not p or not p.voted_at:
        return RedirectResponse(url=f"/thank-you?round_id={round_id}", status_code=303)

    nominations = _nominations_for_round(db, round_id)
    rnd = db.get(Round, round_id)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Бюллетень"
    ws.append(["Номинация", "Тип", "Номинант", "Голос / Место"])
    for cell in ws[1]:
        cell.font = Font(bold=True)

    nominee_ids = {n.id for nom in nominations for n in nom.nominees}
    pick_votes = {
        v.nominee_id: v.is_runner_up
        for v in db.query(Vote).filter(
            Vote.voter_id == voter.id,
            Vote.nominee_id.in_(nominee_ids),
        ).all()
    }
    rankings = {
        (r.nomination_id, r.film_id): r.rank
        for r in db.query(Ranking).filter(
            Ranking.voter_id == voter.id,
            Ranking.nomination_id.in_({nom.id for nom in nominations}),
        ).all()
    }

    for nom in nominations:
        if nom.type == NominationType.PICK:
            main_votes = [n for n in nom.nominees if n.id in pick_votes and not pick_votes[n.id]]
            ru_votes   = [n for n in nom.nominees if n.id in pick_votes and pick_votes[n.id]]
            for n in main_votes:
                label = (n.persons_label and f"{n.persons_label} ({n.film.title})") \
                        or (n.item and f"{n.item} ({n.film.title})") \
                        or n.film.title
                ws.append([nom.name, "PICK", label, "✔"])
            for n in ru_votes:
                label = (n.persons_label and f"{n.persons_label} ({n.film.title})") \
                        or (n.item and f"{n.item} ({n.film.title})") \
                        or n.film.title
                ws.append([nom.name, "PICK", label, "runner-up"])
            if not main_votes and not ru_votes:
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

    label_safe = rnd.label if rnd else str(round_id)
    fname = f"ballot_{voter.name}_{label_safe}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": _content_disposition(fname)},
    )
