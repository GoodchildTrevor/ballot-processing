import io
from typing import Optional
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session
from ballot.database import get_db
from ballot.models import (
    Contest, Round,
    Nomination, NominationType, Nominee, Film, Vote, Ranking, Voter,
)
from ballot.auth import require_admin
import openpyxl

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="ballot/templates")


def _annotate_rows(rows: list, count: int | None) -> list:
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
    film_part = f"{nominee.film.title} ({nominee.film.year})" if nominee.film else "?"
    if getattr(nominee, 'persons_label', None):
        return f"{nominee.persons_label} — {film_part}"
    if getattr(nominee, 'person', None) and nominee.person:
        return f"{nominee.person.name} — {film_part}"
    if getattr(nominee, 'item', None) and nominee.item:
        return f"{nominee.item} — {film_part}"
    return nominee.film.title if nominee.film else "?"


def get_results(db: Session, round_ids: set[int] | None = None):
    """Build results list. Each item includes 'round' so the template can group by tour."""
    q = db.query(Nomination)
    if round_ids is not None:
        q = q.filter(Nomination.round_id.in_(round_ids))
    nominations = (
        q.order_by(Nomination.round_id, Nomination.sort_order, Nomination.id)
        .all()
    )

    # Pre-fetch rounds to avoid N+1
    round_cache: dict[int, Round] = {}
    if round_ids:
        for rnd in db.query(Round).filter(Round.id.in_(round_ids)).all():
            round_cache[rnd.id] = rnd

    results = []
    for nom in nominations:
        rnd = round_cache.get(nom.round_id) if nom.round_id else None

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
            results.append({"nom": nom, "round": rnd, "rows": rows})
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
                rows.append({"label": label, "score": votes, "voters": voter_names, "voter_list": []})
            rows = _annotate_rows(rows, nom.nominees_count)
            results.append({"nom": nom, "round": rnd, "rows": rows})
    return results


@router.get("/results", response_class=HTMLResponse)
def show_results(
    request: Request,
    contest_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    contests = db.query(Contest).order_by(Contest.year.desc()).all()

    selected_contest = None
    round_ids: set[int] | None = None

    if contests:
        if contest_id:
            selected_contest = db.get(Contest, contest_id)
        if not selected_contest:
            selected_contest = contests[0]

        rounds = db.query(Round).filter(Round.contest_id == selected_contest.id).all()
        round_ids = {r.id for r in rounds}

    results = get_results(db, round_ids)
    return templates.TemplateResponse(request, "admin/results.html", {
        "results": results,
        "contests": contests,
        "selected_contest": selected_contest,
    })


@router.get("/results/export")
def export_results(
    contest_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    round_ids: set[int] | None = None
    filename = "results.xlsx"
    if contest_id:
        contest = db.get(Contest, contest_id)
        if contest:
            rounds = db.query(Round).filter(Round.contest_id == contest_id).all()
            round_ids = {r.id for r in rounds}
            filename = f"results_{contest.year}_{contest.name}.xlsx".replace(" ", "_")

    results = get_results(db, round_ids)
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
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
