import io
from typing import Optional
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, case
from sqlalchemy.orm import Session
from ballot.database import get_db
from ballot.models import (
    Contest, Round,
    Nomination, NominationType, Nominee, Film, Vote, Ranking, Voter,
)
from ballot.auth import require_admin
import openpyxl
from urllib.parse import quote

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="ballot/templates")

def _build_content_disposition(filename: str) -> str:
    # ASCII fallback для старых клиентов и чтобы пройти latin-1 в Starlette
    ascii_fallback = "".join(ch if ord(ch) < 128 else "_" for ch in filename)
    if not ascii_fallback.strip():
        ascii_fallback = "results.csv"

    # RFC 5987 / RFC 6266
    encoded = quote(filename, safe="")
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'


def _annotate_rows(rows: list, count: int | None, has_runner_up: bool = False) -> list:
    if not rows:
        return rows
    rank = 1
    prev_score = None
    prev_runner_ups = None
    for i, row in enumerate(rows):
        runner_ups = row.get("runner_ups", 0) if has_runner_up else None
        if prev_score is None or row["score"] != prev_score:
            # Different votes - new rank
            rank = i + 1
        elif has_runner_up and runner_ups != prev_runner_ups:
            # Same votes but different runner-ups - new rank
            rank = i + 1
        # else: same votes AND same runner-ups (if applicable) - same rank
        row["position"] = rank
        row["is_nominee"] = bool(count and rank <= count)
        prev_score = row["score"]
        prev_runner_ups = runner_ups
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
    q = db.query(Nomination)
    if round_ids is not None:
        q = q.filter(Nomination.round_id.in_(round_ids))
    nominations = q.order_by(Nomination.round_id, Nomination.sort_order, Nomination.id).all()

    round_cache: dict[int, Round] = {}
    if round_ids:
        for rnd in db.query(Round).filter(Round.id.in_(round_ids)).all():
            round_cache[rnd.id] = rnd

    results = []
    for nom in nominations:
        rnd = round_cache.get(nom.round_id) if nom.round_id else None
        if nom.type == NominationType.RANK:
            # Count total nominees in this nomination for dynamic scoring
            nominees_count = db.query(func.count(Nominee.id)).filter(
                Nominee.nomination_id == nom.id
            ).scalar() or 10
            # Score: last place = 1 point, first place = nominees_count points
            rows_raw = (
                db.query(Film.title, func.sum(nominees_count + 1 - Ranking.rank).label("score"))
                .join(Ranking, Ranking.film_id == Film.id)
                .filter(Ranking.nomination_id == nom.id)
                .group_by(Film.id)
                .order_by(func.sum(nominees_count + 1 - Ranking.rank).desc())
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
            # Count regular votes and runner-up votes separately
            rows_raw = (
                db.query(
                    Nominee,
                    func.sum(case((Vote.is_runner_up == False, 1), else_=0)).label("votes"),
                    func.sum(case((Vote.is_runner_up == True, 1), else_=0)).label("runner_ups")
                )
                .outerjoin(Vote, Vote.nominee_id == Nominee.id)
                .filter(Nominee.nomination_id == nom.id)
                .group_by(Nominee.id)
                .order_by(func.sum(case((Vote.is_runner_up == False, 1), else_=0)).desc(),
                          func.sum(case((Vote.is_runner_up == True, 1), else_=0)).desc())
                .all()
            )
            rows = []
            for nominee, votes, runner_ups in rows_raw:
                label = _nominee_label(nominee)
                voter_names = ", ".join(
                    sorted(db.get(Voter, v.voter_id).name for v in nominee.votes if not v.is_runner_up)
                )
                runner_up_names = ", ".join(
                    sorted(db.get(Voter, v.voter_id).name for v in nominee.votes if v.is_runner_up)
                )
                rows.append({
                    "label": label,
                    "score": votes or 0,
                    "runner_ups": runner_ups or 0,
                    "voters": voter_names,
                    "runner_up_voters": runner_up_names,
                    "voter_list": []
                })
            rows = _annotate_rows(rows, nom.nominees_count, nom.has_runner_up)
            results.append({"nom": nom, "round": rnd, "rows": rows})

    # Post-processing: merge acting groups (aggregate scores by person across linked templates)
    results = merge_acting_groups(results)
    return results


def merge_acting_groups(results: list[dict]) -> list[dict]:
    """
    For nominations that share the same NominationTemplate.acting_group,
    sum votes per person_id and keep the person only in the nomination with the highest score.
    The winner receives the total sum; other entries for that person are zeroed.
    """
    from collections import defaultdict

    # group items by acting_group
    group_map: dict[str, list[dict]] = defaultdict(list)
    for item in results:
        nom = item.get("nom")
        tmpl = getattr(nom.contest_nomination, "template", None) if nom and nom.contest_nomination else None
        ag = getattr(tmpl, "acting_group", None) if tmpl else None
        if ag:
            group_map[ag].append(item)

    for group, items in group_map.items():
        # person_id -> {nomination_id: score}
        person_votes: dict[int, dict[int, int]] = defaultdict(dict)
        for item in items:
            for row in item.get("rows", []):
                pid = row.get("person_id")
                if pid:
                    person_votes[pid][item["nom"].id] = int(row.get("score", 0) or 0)

        for pid, votes_by_nom in person_votes.items():
            if len(votes_by_nom) <= 1:
                continue
            total = sum(votes_by_nom.values())
            best_nom_id = max(votes_by_nom, key=votes_by_nom.get)
            for item in items:
                for row in item.get("rows", []):
                    if row.get("person_id") != pid:
                        continue
                    if item["nom"].id == best_nom_id:
                        row["score"] = total
                        row["merged"] = True
                        if isinstance(row.get("cols"), list) and len(row["cols"]) > 1:
                            row["cols"][1] = total
                    else:
                        row["score"] = 0
                        if isinstance(row.get("cols"), list) and len(row["cols"]) > 1:
                            row["cols"][1] = 0
    return results


@router.get("/results", response_class=HTMLResponse)
def show_results(
    request: Request,
    contest_id: Optional[int] = Query(None),
    round_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    contests = db.query(Contest).order_by(Contest.year.desc()).all()

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

    results = get_results(db, round_ids)
    return templates.TemplateResponse(request, "admin/results.html", {
        "results": results,
        "contests": contests,
        "selected_contest": selected_contest,
        "selected_round": selected_round,
        "all_rounds": all_rounds,
    })


@router.get("/results/export")
def export_results(
    contest_id: Optional[int] = Query(None),
    round_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    round_ids: set[int] | None = None
    filename = "results.xlsx"
    if round_id:
        rnd = db.get(Round, round_id)
        if rnd:
            round_ids = {rnd.id}
            # Use ASCII-safe filename
            safe_label = rnd.label.encode('ascii', 'replace').decode('ascii').replace('?', '_')
            filename = f"results_{rnd.year}_{safe_label}.xlsx".replace(" ", "_")
    elif contest_id:
        contest = db.get(Contest, contest_id)
        if contest:
            rounds = db.query(Round).filter(Round.contest_id == contest_id).all()
            round_ids = {r.id for r in rounds}
            safe_name = contest.name.encode('ascii', 'replace').decode('ascii').replace('?', '_')
            filename = f"results_{contest.year}_{safe_name}.xlsx".replace(" ", "_")

    results = get_results(db, round_ids)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for item in results:
        ws = wb.create_sheet(title=item["nom"].name[:31])
        if item["nom"].type == NominationType.RANK:
            include_nominee = bool(item["nom"].nominees_count and (not item.get("round") or item["round"].tour != 2))
            ws.append(["Участник", "Очки", "Проголосовали (место)",
                       "Номинант" if include_nominee else ""])
        else:
            has_runner_up = item["nom"].has_runner_up
            header = ["Участник", "Голоса"]
            if has_runner_up:
                header.append("Runner Ups")
            header.append("Проголосовали")
            if item["nom"].nominees_count:
                header.append("Номинант")
            ws.append(header)
        for row in item["rows"]:
            include_nominee = bool(item["nom"].nominees_count and (not item.get("round") or item["round"].tour != 2))
            extra = ["✅ Номинант" if row["is_nominee"] else ""] if include_nominee else []
            if item["nom"].type == NominationType.RANK:
                ws.append([row["label"], row["score"], row["voters"]] + extra)
            else:
                has_runner_up = item["nom"].has_runner_up
                if has_runner_up:
                    voters_display = row["voters"]
                    if row.get("runner_up_voters"):
                        voters_display = f"{row['voters']} | 🏃 {row['runner_up_voters']}" if row['voters'] else f"🏃 {row['runner_up_voters']}"
                    ws.append([row["label"], row["score"], row.get("runner_ups", 0), voters_display] + extra)
                else:
                    ws.append([row["label"], row["score"], row["voters"]] + extra)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    disposition = _build_content_disposition(filename)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": disposition
        },
    )
