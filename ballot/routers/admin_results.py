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
            # voters who ranked each film
            film_voters = {}
            for r in db.query(Ranking).filter(Ranking.nomination_id == nom.id).all():
                film_voters.setdefault(r.film_id, []).append(r)
            rows = []
            for r in rows_raw:
                film = db.query(Film).filter(Film.title == r.title).first()
                fv = film_voters.get(film.id, []) if film else []
                voter_names = ", ".join(
                    sorted(set(db.get(Voter, rv.voter_id).name for rv in fv))
                )
                rows.append({"label": r.title, "score": r.score, "voters": voter_names})
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
                rows.append({"label": label, "score": votes, "voters": voter_names})
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
        ws.append(["Участник", "Очки / Голоса", "Проголосовали"])
        for row in item["rows"]:
            ws.append([row["label"], row["score"], row["voters"]])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=results.xlsx"},
    )
