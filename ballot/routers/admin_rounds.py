"""Admin router for Contest + Round management."""
from __future__ import annotations

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
    Contest, ContestNomination, ContestStatus,
    Nomination, NominationType,
    Nominee, Vote, Ranking,
    NominationTemplate,
    Round, RoundType, RoundParticipation,
    Film,
)

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates_env = Jinja2Templates(directory="ballot/templates")


def _parse_deadline(v: Optional[str]) -> Optional[datetime]:
    if v and v.strip():
        try:
            return datetime.fromisoformat(v.strip())
        except ValueError:
            pass
    return None


def _rank_scores(nom: Nomination, db: Session) -> list[tuple[Nominee, float]]:
    rankings = db.query(Ranking).filter(Ranking.nomination_id == nom.id).all()
    scores: dict[int, list[int]] = defaultdict(list)
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


def _dense_rank_cutoff(
    scored: list[tuple[Nominee, float | int]],
    target: int,
) -> list[Nominee]:
    if not scored:
        return []
    result: list[Nominee] = []
    boundary_score: float | int | None = None
    rank = 0
    prev_score: float | int | None = None
    for nominee, score in scored:
        if score != prev_score:
            rank += 1
            prev_score = score
        if rank <= target:
            result.append(nominee)
            boundary_score = score
        elif score == boundary_score:
            result.append(nominee)
        else:
            break
    return result


@router.get("/rounds", response_class=HTMLResponse)
def list_rounds(request: Request, db: Session = Depends(get_db)):
    contests = (
        db.query(Contest)
        .options(joinedload(Contest.rounds))
        .order_by(Contest.year.desc())
        .all()
    )
    standalone = (
        db.query(Round)
        .filter(Round.contest_id == None)  # noqa: E711
        .order_by(Round.sort_order, Round.id)
        .all()
    )
    all_templates = (
        db.query(NominationTemplate)
        .filter(NominationTemplate.is_archived == False)  # noqa: E712
        .order_by(NominationTemplate.sort_order, NominationTemplate.id)
        .all()
    )
    return templates_env.TemplateResponse(
        request, "admin/rounds.html",
        {"contests": contests, "standalone": standalone,
         "all_templates": all_templates},
    )


@router.post("/contests")
def create_contest(
    request: Request,
    year: int = Form(...),
    name: str = Form(...),
    deadline: Optional[str] = Form(None),
    template_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
):
    contest = Contest(
        year=year,
        name=name.strip(),
        status=ContestStatus.LONGLIST_ACTIVE,
    )
    db.add(contest)
    db.flush()

    last = db.query(Round).order_by(Round.sort_order.desc()).first()
    longlist_round = Round(
        label=f"Лонг-лист {year}",
        round_type=RoundType.LONGLIST,
        year=year,
        deadline=_parse_deadline(deadline),
        is_active=True,
        sort_order=(last.sort_order + 1) if last else 0,
        contest_id=contest.id,
        tour=1,
    )
    db.add(longlist_round)
    db.flush()

    tmpl_map = {
        t.id: t for t in db.query(NominationTemplate)
        .filter(NominationTemplate.id.in_(template_ids))
        .all()
    }
    for order, tid in enumerate(template_ids):
        tmpl = tmpl_map.get(tid)
        if not tmpl:
            continue
        cn = ContestNomination(
            contest_id=contest.id,
            template_id=tmpl.id,
            sort_order=order,
        )
        db.add(cn)
        db.flush()

        nom = Nomination(
            name=tmpl.name,
            type=tmpl.type,
            # final_promotes_count now serves as both nominees_count and promote target
            nominees_count=tmpl.final_promotes_count,
            pick_min=tmpl.longlist_pick_min,
            pick_max=tmpl.longlist_pick_max,
            year_filter=year,
            sort_order=order,
            round_id=longlist_round.id,
            contest_nomination_id=cn.id,
            has_runner_up=False,
        )
        db.add(nom)

    db.commit()
    return RedirectResponse(url="/admin/rounds", status_code=303)


@router.post("/contests/{contest_id}/delete")
def delete_contest(contest_id: int, db: Session = Depends(get_db)):
    contest = db.get(Contest, contest_id)
    if contest:
        db.delete(contest)
        db.commit()
    return RedirectResponse(url="/admin/rounds", status_code=303)


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
        tour=1,
    ))
    db.commit()
    return RedirectResponse(url="/admin/rounds", status_code=303)


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


@router.post("/rounds/{round_id}/delete")
def delete_round(round_id: int, db: Session = Depends(get_db)):
    rnd = db.get(Round, round_id)
    if rnd:
        db.delete(rnd)
        db.commit()
    return RedirectResponse(url="/admin/rounds", status_code=303)


@router.post("/rounds/{round_id}/activate")
def activate_round(round_id: int, db: Session = Depends(get_db)):
    rnd = db.get(Round, round_id)
    if rnd and rnd.deadline:
        rnd.is_active = True
        if rnd.contest:
            rnd.contest.status = (
                ContestStatus.LONGLIST_ACTIVE if rnd.tour == 1
                else ContestStatus.FINAL_ACTIVE
            )
        db.commit()
    return RedirectResponse(url="/admin/rounds", status_code=303)


@router.post("/rounds/{round_id}/deactivate")
def deactivate_round(round_id: int, db: Session = Depends(get_db)):
    rnd = db.get(Round, round_id)
    if rnd:
        rnd.is_active = False
        if rnd.contest:
            rnd.contest.status = (
                ContestStatus.LONGLIST_CLOSED if rnd.tour == 1
                else ContestStatus.FINAL_CLOSED
            )
        db.commit()
    return RedirectResponse(url="/admin/rounds", status_code=303)


@router.post("/rounds/{round_id}/promote")
def promote_to_final(round_id: int, db: Session = Depends(get_db)):
    longlist = db.get(Round, round_id)
    if not longlist or longlist.round_type != RoundType.LONGLIST:
        return RedirectResponse(url="/admin/rounds", status_code=303)

    last = db.query(Round).order_by(Round.sort_order.desc()).first()
    final = Round(
        label=f"Финал {longlist.year}",
        round_type=RoundType.FINAL,
        year=longlist.year,
        deadline=None,
        is_active=False,
        sort_order=(last.sort_order + 1) if last else 0,
        contest_id=longlist.contest_id,
        tour=2,
    )
    db.add(final)
    db.flush()

    if longlist.contest:
        longlist.contest.status = ContestStatus.LONGLIST_CLOSED

    nominations = (
        db.query(Nomination)
        .options(
            joinedload(Nomination.nominees).joinedload(Nominee.film),
            joinedload(Nomination.nominees).joinedload(Nominee.persons),
        )
        .filter(Nomination.round_id == longlist.id)
        .order_by(Nomination.sort_order, Nomination.id)
        .all()
    )

    for nom in nominations:
        target = None
        if nom.contest_nomination and nom.contest_nomination.template:
            target = nom.contest_nomination.template.final_promotes_count

        if nom.type == NominationType.RANK:
            scored = _rank_scores(nom, db)
            if target:
                shortlisted = _dense_rank_cutoff(scored, target)
            else:
                shortlisted = [n for n, _ in scored]
        else:
            scored_pick = _pick_scores(nom, db)
            if target:
                shortlisted = _dense_rank_cutoff(
                    [(n, -c) for n, c in scored_pick],
                    target,
                )
            else:
                shortlisted = [n for n, c in scored_pick if c > 0] \
                              or [n for n, _ in scored_pick]

        for nominee in shortlisted:
            nominee.is_shortlisted = True

        source_cn_id = nom.contest_nomination_id

        final_nom = Nomination(
            round_id=final.id,
            name=nom.name,
            sort_order=nom.sort_order,
            year_filter=nom.year_filter,
            type=nom.type,
            nominees_count=len(shortlisted) if nom.type == NominationType.RANK else None,
            pick_min=1 if nom.type == NominationType.PICK else None,
            pick_max=1 if nom.type == NominationType.PICK else None,
            has_runner_up=nom.type == NominationType.PICK,
            contest_nomination_id=source_cn_id,
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
    return templates_env.TemplateResponse(
        request, "admin/round_preview.html",
        {"rnd": rnd, "nominations": nominations, "films": films},
    )


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
