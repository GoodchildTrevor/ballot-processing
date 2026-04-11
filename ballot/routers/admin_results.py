import io
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload
from ballot.database import get_db
from ballot.models import (
    Nomination, NominationType, Nominee, Film, Vote, Ranking, Voter, Winner
)
from ballot.auth import require_admin
import openpyxl

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="ballot/templates")


def _annotate_rows(rows: list, count: int | None) -> list:
    """Add dense_rank and is_nominee using DENSE_RANK logic."""
    if not rows:
        return rows
    rank = 1
    prev_score = None
    for i, row in enumerate(rows):
        if prev_score is None or row["score"] != prev_score:
            rank = i + 1
        row["position"] = rank
        row["is_nominee"] = bool(count and rank <= count)
        prev_score = row["score"]
    return rows


def _nominee_label(nominee) -> str:
    """Build display label for a PICK nominee: person > item > film."""
    film_part = f"{nominee.film.title} ({nominee.film.year})" if nominee.film else "?"
    if getattr(nominee, 'persons_label', None):
        return f"{nominee.persons_label} — {film_part}"
    if getattr(nominee, 'person', None) and nominee.person:
        return f"{nominee.person.name} — {film_part}"
    if getattr(nominee, 'item', None) and nominee.item:
        return f"{nominee.item} — {film_part}"
    return nominee.film.title if nominee.film else "?"


def get_results(db: Session):
    nominations = (
        db.query(Nomination)
        .options(
            joinedload(Nomination.winner).joinedload(Winner.nominee)
        )
        .order_by(Nomination.sort_order, Nomination.id)
        .all()
    )
    results = []
    for nom in nominations:
        if nom.type == NominationType.RANK:
            rows_raw = (
                db.query(Film.title, Film.id, func.sum(11 - Ranking.rank).label("score"))
                .join(Ranking, Ranking.film_id == Film.id)
                .filter(Ranking.nomination_id == nom.id)
                .group_by(Film.id)
                .order_by(func.sum(11 - Ranking.rank).desc())
                .all()
            )
            film_voters_map = {}
            for r in db.query(Ranking).filter(Ranking.nomination_id == nom.id).all():
                film = db.get(Film, r.film_id)
                voter = db.get(Voter, r.voter_id)
                if film and voter:
                    film_voters_map.setdefault(film.title, []).append((voter.name, r.rank))

            rows = []
            for r in rows_raw:
                voter_entries = sorted(film_voters_map.get(r.title, []), key=lambda x: x[0])
                rows.append({
                    "label": r.title,
                    "score": r.score,
                    "voter_list": [{"name": n, "rank": rank} for n, rank in voter_entries],
                    "voters": ", ".join(f"{n} ({rank})" for n, rank in voter_entries),
                })
            rows = _annotate_rows(rows, nom.nominees_count)
            results.append({"nom": nom, "rows": rows})
        else:
            rows_raw = (
                db.query(Nominee, func.count(Vote.id).label("votes"))
                .outerjoin(Vote, Vote.nominee_id == Nominee.id)
                .filter(Nominee.nomination_id == nom.id)
                .group_by(Nominee.id)
                .order_by(func.count(Vote.id).desc())
                .all()
            )
            rows = []
            for nominee, votes in rows_raw:
                label = _nominee_label(nominee)
                voter_names = ", ".join(
                    sorted(db.get(Voter, v.voter_id).name for v in nominee.votes)
                )
                rows.append({
                    "label": label,
                    "score": votes,
                    "voters": voter_names,
                    "voter_list": [],
                    "nominee_id": nominee.id,
                })
            rows = _annotate_rows(rows, nom.nominees_count)
            results.append({"nom": nom, "rows": rows})
    return results


@router.get("/results", response_class=HTMLResponse)
def show_results(request: Request, db: Session = Depends(get_db)):
    results = get_results(db)
    return templates.TemplateResponse(request, "admin/results.html", {"results": results})


@router.get("/results/export")
def export_results(db: Session = Depends(get_db)):
    results = get_results(db)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for item in results:
        ws = wb.create_sheet(title=item["nom"].name[:31])
        winner_label = ""
        if item["nom"].winner and item["nom"].winner.nominee:
            w = item["nom"].winner
            winner_label = _nominee_label(w.nominee)

        if item["nom"].type == NominationType.RANK:
            ws.append(["Участник", "Очки", "Проголосовали (место)",
                       "Номинант" if item["nom"].nominees_count else "",
                       "Победитель"])
        else:
            ws.append(["Участник", "Голоса", "Проголосовали",
                       "Номинант" if item["nom"].nominees_count else "",
                       "Победитель"])
        for row in item["rows"]:
            extra = ["\u2705 Номинант" if row["is_nominee"] else ""] if item["nom"].nominees_count else [""]
            is_winner = winner_label and row["label"] == winner_label
            extra.append("\U0001f3c6 Победитель" if is_winner else "")
            ws.append([row["label"], row["score"], row["voters"]] + extra)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=results.xlsx"},
    )


# ---------------------------------------------------------------------------
# Winner management
# ---------------------------------------------------------------------------

@router.post("/nominations/{nomination_id}/winner")
def set_winner(
    nomination_id: int,
    nominee_id: int = Form(...),
    db: Session = Depends(get_db),
):
    """Create or update the winner for a nomination."""
    nom = db.get(Nomination, nomination_id)
    if not nom:
        return RedirectResponse(url="/admin/results", status_code=303)

    # Verify nominee belongs to this nomination
    nominee = db.get(Nominee, nominee_id) if nominee_id else None
    if nominee and nominee.nomination_id != nomination_id:
        return RedirectResponse(url="/admin/results", status_code=303)

    winner = db.query(Winner).filter_by(nomination_id=nomination_id).first()
    if winner:
        winner.nominee_id = nominee_id
        winner.announced_at = datetime.now(timezone.utc)
    else:
        db.add(Winner(
            nomination_id=nomination_id,
            nominee_id=nominee_id,
            announced_at=datetime.now(timezone.utc),
            is_public=False,
        ))
    db.commit()
    return RedirectResponse(url="/admin/results", status_code=303)


@router.post("/nominations/{nomination_id}/winner/clear")
def clear_winner(nomination_id: int, db: Session = Depends(get_db)):
    """Remove the winner designation for a nomination."""
    winner = db.query(Winner).filter_by(nomination_id=nomination_id).first()
    if winner:
        db.delete(winner)
        db.commit()
    return RedirectResponse(url="/admin/results", status_code=303)


@router.post("/nominations/{nomination_id}/winner/toggle-public")
def toggle_winner_public(nomination_id: int, db: Session = Depends(get_db)):
    """Toggle is_public for the winner of a nomination."""
    winner = db.query(Winner).filter_by(nomination_id=nomination_id).first()
    if winner:
        winner.is_public = not winner.is_public
        db.commit()
    return RedirectResponse(url="/admin/results", status_code=303)
