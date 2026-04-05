import io
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session
from ballot.database import get_db
from ballot.models import Nomination, NominationType, Nominee, Film, Vote, Ranking
from ballot.auth import require_admin
import openpyxl

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="ballot/templates")


def get_results(db: Session):
    nominations = db.query(Nomination).all()
    results = []
    for nom in nominations:
        if nom.type == NominationType.RANK:
            rows = (
                db.query(Film.title, func.sum(11 - Ranking.rank).label("score"))
                .join(Ranking, Ranking.film_id == Film.id)
                .filter(Ranking.nomination_id == nom.id)
                .group_by(Film.id)
                .order_by(func.sum(11 - Ranking.rank).desc())
                .all()
            )
            results.append({"nom": nom, "rows": [(r.title, r.score) for r in rows]})
        else:
            rows = (
                db.query(Nominee, func.count(Vote.id).label("votes"))
                .outerjoin(Vote, Vote.nominee_id == Nominee.id)
                .filter(Nominee.nomination_id == nom.id)
                .group_by(Nominee.id)
                .order_by(func.count(Vote.id).desc())
                .all()
            )
            result_rows = []
            for nominee, votes in rows:
                label = nominee.film.title
                if nominee.person:
                    label = f"{nominee.person.name} ({nominee.film.title})"
                result_rows.append((label, votes))
            results.append({"nom": nom, "rows": result_rows})
    return results


@router.get("/results", response_class=HTMLResponse)
def show_results(request: Request, db: Session = Depends(get_db)):
    results = get_results(db)
    return templates.TemplateResponse("admin/results.html", {"request": request, "results": results})


@router.get("/results/export")
def export_results(db: Session = Depends(get_db)):
    results = get_results(db)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for item in results:
        ws = wb.create_sheet(title=item["nom"].name[:31])
        ws.append(["Участник", "Очки / Голоса"])
        for label, score in item["rows"]:
            ws.append([label, score])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=results.xlsx"},
    )
