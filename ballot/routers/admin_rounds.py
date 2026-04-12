"""Admin router for Contest + Round management."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Optional

import sqlalchemy
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
    Film, Voter,
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


def _nominee_label(nominee: Nominee) -> str:
    film_part = f"{nominee.film.title} ({nominee.film.year})" if nominee.film else "?"
    if getattr(nominee, 'persons_label', None):
        return f"{nominee.persons_label} — {film_part}"
    if getattr(nominee, 'person', None) and nominee.person:
        return f"{nominee.person.name} — {film_part}"
    if getattr(nominee, 'item', None) and nominee.item:
        return f"{nominee.item} — {film_part}"
    return nominee.film.title if nominee.film else "?"


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
    all_contest_noms = db.query(ContestNomination).all()
    contest_template_ids: dict[int, set[int]] = defaultdict(set)
    for cn in all_contest_noms:
        if cn.template_id is not None:
            contest_template_ids[cn.contest_id].add(cn.template_id)

    return templates_env.TemplateResponse(
        request, "admin/rounds.html",
        {
            "contests": contests,
            "standalone": standalone,
            "all_templates": all_templates,
            "contest_template_ids": dict(contest_template_ids),
        },
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


@router.post("/contests/{contest_id}/add-nominations")
def add_nominations_to_contest(
    contest_id: int,
    template_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
):
    contest = db.get(Contest, contest_id)
    if not contest or not template_ids:
        return RedirectResponse(url="/admin/rounds", status_code=303)

    longlist_round = (
        db.query(Round)
        .filter(Round.contest_id == contest_id, Round.round_type == RoundType.LONGLIST)
        .first()
    )
    if not longlist_round:
        return RedirectResponse(url="/admin/rounds", status_code=303)

    existing_count = (
        db.query(Nomination)
        .filter(Nomination.round_id == longlist_round.id)
        .count()
    )

    existing_template_ids = {
        cn.template_id
        for cn in db.query(ContestNomination)
        .filter(ContestNomination.contest_id == contest_id)
        .all()
    }

    tmpl_map = {
        t.id: t for t in db.query(NominationTemplate)
        .filter(NominationTemplate.id.in_(template_ids))
        .all()
    }

    added = 0
    for tid in template_ids:
        if tid in existing_template_ids:
            continue
        tmpl = tmpl_map.get(tid)
        if not tmpl:
            continue
        cn = ContestNomination(
            contest_id=contest.id,
            template_id=tmpl.id,
            sort_order=existing_count + added,
        )
        db.add(cn)
        db.flush()

        nom = Nomination(
            name=tmpl.name,
            type=tmpl.type,
            nominees_count=tmpl.final_promotes_count,
            pick_min=tmpl.longlist_pick_min,
            pick_max=tmpl.longlist_pick_max,
            year_filter=contest.year,
            sort_order=existing_count + added,
            round_id=longlist_round.id,
            contest_nomination_id=cn.id,
            has_runner_up=False,
        )
        db.add(nom)
        added += 1

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


# ── Promote: GET shows the selection page, POST/confirm creates the final ──

@router.get("/rounds/{round_id}/promote", response_class=HTMLResponse)
def promote_preview(
    round_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Show longlist results with checkboxes pre-filled by DENSE RANK cutoff."""
    longlist = db.get(Round, round_id)
    if not longlist or longlist.round_type != RoundType.LONGLIST:
        return RedirectResponse(url="/admin/rounds", status_code=303)

    nominations = (
        db.query(Nomination)
        .options(
            joinedload(Nomination.nominees).joinedload(Nominee.film),
            joinedload(Nomination.nominees).joinedload(Nominee.persons),
            joinedload(Nomination.nominees).joinedload(Nominee.person),
        )
        .filter(Nomination.round_id == longlist.id)
        .order_by(Nomination.sort_order, Nomination.id)
        .all()
    )

    items = []
    for nom in nominations:
        target = None
        if nom.contest_nomination and nom.contest_nomination.template:
            target = nom.contest_nomination.template.final_promotes_count

        if nom.type == NominationType.RANK:
            # Build rows_raw sorted by score DESC (score = sum(11 - rank))
            rows_raw = (
                db.query(
                    Film.title,
                    Film.id.label("film_id"),
                    sqlalchemy.func.sum(11 - Ranking.rank).label("score"),
                )
                .join(Ranking, Ranking.film_id == Film.id)
                .filter(Ranking.nomination_id == nom.id)
                .group_by(Film.id)
                .order_by(sqlalchemy.func.sum(11 - Ranking.rank).desc())
                .all()
            )

            film_to_nominee = {n.film_id: n for n in nom.nominees}

            # voter info per film: {film_id: [(voter_name, rank), ...]}
            film_voters_map: dict[int, list] = {}
            for r in db.query(Ranking).filter(Ranking.nomination_id == nom.id).all():
                voter = db.get(Voter, r.voter_id)
                if voter:
                    film_voters_map.setdefault(r.film_id, []).append((voter.name, r.rank))

            # Compute auto_ids directly from rows_raw using DENSE RANK on score
            # so the cutoff is identical to what's shown on screen.
            auto_ids: set[int] = set()
            if target:
                prev_score_val = None
                dense_rank_val = 0
                boundary: int | None = None
                for r in rows_raw:
                    if r.score != prev_score_val:
                        dense_rank_val += 1
                        prev_score_val = r.score
                    if dense_rank_val <= target:
                        nominee = film_to_nominee.get(r.film_id)
                        if nominee:
                            auto_ids.add(nominee.id)
                        boundary = r.score
                    elif r.score == boundary:
                        nominee = film_to_nominee.get(r.film_id)
                        if nominee:
                            auto_ids.add(nominee.id)
                    else:
                        break

            rows = []
            prev_score = None
            dense_pos = 0
            row_num = 0
            for r in rows_raw:
                row_num += 1
                if r.score != prev_score:
                    dense_pos = row_num
                    prev_score = r.score
                nominee = film_to_nominee.get(r.film_id)
                voter_entries = sorted(film_voters_map.get(r.film_id, []), key=lambda x: x[0])
                voters_str = ", ".join(f"{n} ({rk})" for n, rk in voter_entries)
                rows.append({
                    "nominee_id": nominee.id if nominee else None,
                    "label": r.title,
                    "score": r.score,
                    "voters": voters_str,
                    "position": dense_pos,
                    "row_num": row_num,
                    "auto_selected": (nominee.id in auto_ids) if nominee else False,
                })

        else:  # PICK
            scored_pick = _pick_scores(nom, db)  # [(nominee, vote_count)]

            # Compute auto_ids via DENSE RANK cutoff on vote counts
            auto_ids = set()
            if target:
                auto_ids = {n.id for n in _dense_rank_cutoff(
                    [(n, -c) for n, c in scored_pick], target
                )}
            else:
                auto_ids = {n.id for n, c in scored_pick if c > 0} \
                           or {n.id for n, _ in scored_pick}

            nominee_voters: dict[int, list[str]] = {}
            for v in db.query(Vote).join(Nominee).filter(
                Nominee.nomination_id == nom.id,
                Vote.is_runner_up == False,  # noqa: E712
            ).all():
                voter = db.get(Voter, v.voter_id)
                if voter:
                    nominee_voters.setdefault(v.nominee_id, []).append(voter.name)

            rows = []
            prev_score = None
            dense_pos = 0
            row_num = 0
            for nominee, count in scored_pick:
                row_num += 1
                if count != prev_score:
                    dense_pos = row_num
                    prev_score = count
                label = _nominee_label(nominee)
                voters_str = ", ".join(sorted(nominee_voters.get(nominee.id, [])))
                rows.append({
                    "nominee_id": nominee.id,
                    "label": label,
                    "score": count,
                    "voters": voters_str,
                    "position": dense_pos,
                    "row_num": row_num,
                    "auto_selected": nominee.id in auto_ids,
                })

        items.append({"nom": nom, "rows": rows, "target": target})

    return templates_env.TemplateResponse(
        request, "admin/promote.html",
        {"rnd": longlist, "nominations": items},
    )


@router.post("/rounds/{round_id}/promote/confirm")
def promote_confirm(
    round_id: int,
    selected_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
):
    """Create the final round with manually confirmed nominees."""
    longlist = db.get(Round, round_id)
    if not longlist or longlist.round_type != RoundType.LONGLIST:
        return RedirectResponse(url="/admin/rounds", status_code=303)

    selected_set = set(selected_ids)

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
        shortlisted = [n for n in nom.nominees if n.id in selected_set]

        for nominee in shortlisted:
            nominee.is_shortlisted = True

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
            contest_nomination_id=nom.contest_nomination_id,
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
