"""Voter-facing routes — round-aware."""
from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
import openpyxl
from openpyxl.cell import MergedCell
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from ballot.auth import require_voter
from ballot.database import get_db
from ballot.models import (
    Nomination, NominationType,
    Nominee, Vote, Ranking, Voter,
    Round, RoundParticipation, RoundType,
)

router = APIRouter(dependencies=[Depends(require_voter)])
templates = Jinja2Templates(directory="ballot/templates")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _content_disposition(filename: str) -> str:
    ascii_name = filename.encode("ascii", "ignore").decode()
    encoded_name = quote(filename, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}"


def _auto_width(ws):
    for col in ws.columns:
        first = next((c for c in col if not isinstance(c, MergedCell)), None)
        if first is None:
            continue
        max_len = max((len(str(c.value or "")) for c in col if not isinstance(c, MergedCell)), default=10)
        ws.column_dimensions[first.column_letter].width = min(max_len + 4, 80)


def _sheet_title(name: str) -> str:
    for ch in r'\/:*?[]':
        name = name.replace(ch, '')
    return name[:31]


def _write_cell_with_link(ws, row: int, col: int, label: str, url: str | None, link_font: Font):
    """Write label into cell; if url provided, make it a hyperlink."""
    cell = ws.cell(row=row, column=col, value=label)
    if url:
        cell.hyperlink = url
        cell.font = link_font
    return cell


def _build_xlsx_per_nomination(nominations_data: list[dict]) -> io.BytesIO:
    """Build xlsx where each nomination is a separate sheet."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    header_font  = Font(bold=True, color="FFFFFF")
    header_fill  = PatternFill("solid", fgColor="4F46E5")
    header_align = Alignment(horizontal="center")
    link_font    = Font(color="1155CC", underline="single")

    seen_titles: dict[str, int] = {}
    for nom in nominations_data:
        raw_title = _sheet_title(nom["name"])
        if raw_title in seen_titles:
            seen_titles[raw_title] += 1
            title = _sheet_title(raw_title)[:28] + f" {seen_titles[raw_title]}"
        else:
            seen_titles[raw_title] = 1
            title = raw_title

        ws = wb.create_sheet(title=title)

        ws.append([nom["name"]])
        ws.merge_cells(start_row=1, start_column=1, end_row=1,
                       end_column=max(len(nom["header"]), 1))
        ws.cell(1, 1).font = Font(bold=True, size=13)
        ws.cell(1, 1).alignment = Alignment(horizontal="left")
        ws.append([])  # blank row

        ws.append(nom["header"])
        for i in range(1, len(nom["header"]) + 1):
            cell = ws.cell(3, i)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_align

        data_row = 4
        for row in nom["rows"]:
            if isinstance(row, dict):
                cols = row.get("cols", [])
                urls = row.get("urls", [None] * len(cols))
            else:
                cols = row
                urls = [None] * len(cols)

            for col_idx, (val, url) in enumerate(zip(cols, urls), start=1):
                _write_cell_with_link(ws, data_row, col_idx, val, url or None, link_font)
            data_row += 1

        _auto_width(ws)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


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
    """Return the active round for the year, preferring FINAL over LONGLIST."""
    rounds = (
        db.query(Round)
        .filter(Round.year == year, Round.is_active == True)  # noqa: E712
        .all()
    )
    if not rounds:
        return None
    # Prefer FINAL, then LONGLIST
    for rnd in rounds:
        if rnd.round_type == RoundType.FINAL:
            return rnd
    return rounds[0]


def _find_latest_active_round(db: Session) -> Round | None:
    """Return the most relevant active round across all years.

    Priority:
    1. FINAL round — most recent year first
    2. LONGLIST round — most recent year first
    """
    active = (
        db.query(Round)
        .filter(Round.is_active == True)  # noqa: E712
        .order_by(Round.year.desc())
        .all()
    )
    if not active:
        return None
    # prefer FINAL of the most recent year that has a FINAL
    for rnd in active:
        if rnd.round_type == RoundType.FINAL:
            return rnd
    # fallback: most recent LONGLIST
    return active[0]


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


def _deadline_passed(rnd: Round) -> bool:
    """Return True if the round has a deadline and it has passed.

    Handles both naive (no tzinfo) and aware datetimes stored in DB.
    For naive datetimes, interpret them as local time and convert to UTC
    for correct comparison with current UTC time.
    """
    if not rnd.deadline:
        return False
    dl = rnd.deadline
    if dl.tzinfo is None:
        # Treat naive datetimes as local time
        local_tz = datetime.now().astimezone().tzinfo
        dl = dl.replace(tzinfo=local_tz)
    # Compare in UTC
    dl_utc = dl.astimezone(timezone.utc)
    return datetime.now(timezone.utc) > dl_utc


def _check_round_open(request, rnd) -> HTMLResponse | None:
    if not rnd or not rnd.is_active:
        return templates.TemplateResponse(
            request, "voting_closed.html",
            {"nom": None, "message": "Этот раунд не активен."},
            status_code=403,
        )
    if _deadline_passed(rnd):
        return templates.TemplateResponse(
            request, "voting_closed.html",
            {"nom": None, "round": rnd, "message": "Дедлайн голосования прошёл."},
            status_code=403,
        )
    return None


# ---------------------------------------------------------------------------
# /vote  — redirects to the most relevant active round
# ---------------------------------------------------------------------------

@router.get("/vote", response_class=HTMLResponse)
def vote_redirect(request: Request, db: Session = Depends(get_db)):
    active = _find_latest_active_round(db)
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
    nominations_data: list[dict] = body.get("nominations", [])

    buf = _build_xlsx_per_nomination(nominations_data)
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

    nominations_data = []
    for nom in nominations:
        if nom.type == NominationType.PICK:
            header = ["Номинант", "Голос"]
            rows = []
            for n in nom.nominees:
                if n.id in pick_votes:
                    label = (
                        (n.persons_label and f"{n.persons_label} ({n.film.title})")
                        or (n.item and f"{n.item} ({n.film.title})")
                        or n.film.title
                    )
                    url = (
                        (n.person.url if n.person else None)
                        or getattr(n, 'item_url', None)
                        or (n.film.url if n.film else None)
                    )
                    vote_label = "runner-up" if pick_votes[n.id] else "✔"
                    rows.append({"cols": [label, vote_label], "urls": [url, None]})
            if not rows:
                rows = [{"cols": ["— пропущено", ""], "urls": [None, None]}]
        else:
            header = ["Место", "Фильм"]
            ranked = [
                (
                    rankings[(nom.id, n.film_id)],
                    n.film.title,
                    n.film.url if n.film else None,
                )
                for n in nom.nominees
                if (nom.id, n.film_id) in rankings
            ]
            if ranked:
                rows = [
                    {"cols": [rank, title], "urls": [None, url]}
                    for rank, title, url in sorted(ranked)
                ]
            else:
                rows = [{"cols": ["", "— пропущено"], "urls": [None, None]}]

        nominations_data.append({
            "name": nom.name,
            "type": nom.type.value,
            "header": header,
            "rows": rows,
        })

    buf = _build_xlsx_per_nomination(nominations_data)
    label_safe = rnd.label if rnd else str(round_id)
    fname = f"ballot_{voter.name}_{label_safe}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": _content_disposition(fname)},
    )
