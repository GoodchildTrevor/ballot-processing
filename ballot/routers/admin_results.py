import io
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session
from ballot.database import get_db
from ballot.models import Nomination, NominationType, Nominee, Film, Vote, Ranking, Voter
from ballot.auth import require_admin
import openpyxl

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="ballot/templates")


def _annotate_rows(rows: list, count: int | None) -> list:
    """Add dense_rank and is_nominee using DENSE_RANK logic."""
    if not rows:
        return rows

    # DENSE_RANK: одинаковый score → одинаковый ранг
    rank = 1
    prev_score = None
    for i, row in enumerate(rows):
        if prev_score is None or row["score"] != prev_score:
            rank = i + 1
        row["position"] = rank
        row["is_nominee"] = bool(count and rank <= count)
        prev_score = row["score"]

    return rows


def get_results(db: Session):
    nominations = db.query(Nomination).order_by(Nomination.sort_order, Nomination.id).all()
    results = []
    for nom in nominations:
        if nom.type == NominationType.RANK:
            rows_raw = (
                db.query(Film.title, func.sum(11 - Ranking.rank).label("score"))
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
                label = nominee.film.title
                if nominee.person:
                    label = f"{nominee.person.name} ({nominee.film.title})"
                voter_names = ", ".join(
                    sorted(db.get(Voter, v.voter_id).name for v in nominee.votes)
                )
                rows.append({"label": label, "score": votes, "voters": voter_names, "voter_list": []})
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
        if item["nom"].type == NominationType.RANK:
            ws.append(["Участник", "Очки", "Проголосовали (место)",
                       "Номинант" if item["nom"].nominees_count else ""])
        else:
            ws.append(["Участник", "Голоса", "Проголосовали",
                       "Номинант" if item["nom"].nominees_count else ""])
        for row in item["rows"]:
            extra = ["✅ Номинант" if row["is_nominee"] else ""] if item["nom"].nominees_count else []
            ws.append([row["label"], row["score"], row["voters"]] + extra)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=results.xlsx"},
    )
