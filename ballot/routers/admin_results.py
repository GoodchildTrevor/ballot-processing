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
            film_voters_map = {}
            for r in db.query(Ranking).filter(Ranking.nomination_id == nom.id).all():
                film = db.get(Film, r.film_id)
                voter = db.get(Voter, r.voter_id)
                if film and voter:
                    film_voters_map.setdefault(film.title, []).append((voter.name, r.rank))

            rows = []
            for r in rows_raw:
                voter_entries = film_voters_map.get(r.title, [])
                voter_entries.sort(key=lambda x: x[0])
                rows.append({
                    "label": r.title,
                    "score": r.score,
                    "voter_list": [{"name": n, "rank": rank} for n, rank in voter_entries],
                    "voters": ", ".join(f"{n} ({rank})" for n, rank in voter_entries),
                })
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

            scored = []
            for nominee, votes in rows_raw:
                label = nominee.film.title
                if nominee.person:
                    label = f"{nominee.person.name} ({nominee.film.title})"
                voter_names = ", ".join(
                    sorted(db.get(Voter, v.voter_id).name for v in nominee.votes)
                )
                scored.append({"label": label, "score": votes, "voters": voter_names, "voter_list": []})

            pick_max = nom.pick_max
            if pick_max and scored:
                sorted_scores = sorted(set(r["score"] for r in scored), reverse=True)
                score_to_dense_rank = {s: i + 1 for i, s in enumerate(sorted_scores)}
                for row in scored:
                    row["dense_rank"] = score_to_dense_rank[row["score"]]
                    row["is_nominee"] = row["dense_rank"] <= pick_max
            else:
                for row in scored:
                    row["dense_rank"] = None
                    row["is_nominee"] = False

            results.append({"nom": nom, "rows": scored})
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
            ws.append(["\u0423\u0447\u0430\u0441\u0442\u043d\u0438\u043a", "\u041e\u0447\u043a\u0438", "\u041f\u0440\u043e\u0433\u043e\u043b\u043e\u0441\u043e\u0432\u0430\u043b\u0438 (\u043c\u0435\u0441\u0442\u043e)"])
        else:
            ws.append(["\u0423\u0447\u0430\u0441\u0442\u043d\u0438\u043a", "\u0413\u043e\u043b\u043e\u0441\u0430", "\u041f\u0440\u043e\u0433\u043e\u043b\u043e\u0441\u043e\u0432\u0430\u043b\u0438",
                       "\u041d\u043e\u043c\u0438\u043d\u0430\u043d\u0442" if item["nom"].pick_max else ""])
        for row in item["rows"]:
            extra = []
            if item["nom"].type == NominationType.PICK and item["nom"].pick_max:
                extra = ["\u2705 \u041d\u043e\u043c\u0438\u043d\u0430\u043d\u0442" if row.get("is_nominee") else ""]
            ws.append([row["label"], row["score"], row["voters"]] + extra)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=results.xlsx"},
    )
