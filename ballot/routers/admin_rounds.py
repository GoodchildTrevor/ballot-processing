"""Admin router for Contest + Round management."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
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
    Film, Voter,
)
from ballot.routers.admin_results import get_results

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates_env = Jinja2Templates(directory="ballot/templates")


def _parse_deadline(v: Optional[str]) -> Optional[datetime]:
    if v and v.strip():
        try:
            return datetime.fromisoformat(v.strip())
        except ValueError:
            pass
    return None


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
    """Show longlist results with checkboxes pre-filled using the exact same
    logic as the results page (get_results + is_nominee flag)."""
    longlist = db.get(Round, round_id)
    if not longlist or longlist.round_type != RoundType.LONGLIST:
        return RedirectResponse(url="/admin/rounds", status_code=303)

    # Reuse identical logic from results page
    results = get_results(db, round_ids={longlist.id})

    # For each nomination we need nominee_id per label so the form can
    # submit nominee PKs. Build label -> nominee_id map per nomination.
    nominations_by_id = {
        nom.id: nom
        for nom in db.query(Nomination)
        .options(
            joinedload(Nomination.nominees).joinedload(Nominee.film),
            joinedload(Nomination.nominees).joinedload(Nominee.person),
            joinedload(Nomination.nominees).joinedload(Nominee.persons),
        )
        .filter(Nomination.round_id == longlist.id)
        .all()
    }

    items = []
    for item in results:
        nom = item["nom"]
        nom_obj = nominations_by_id.get(nom.id, nom)

        # Build label -> nominee map
        label_to_nominee: dict[str, Nominee] = {}
        for n in nom_obj.nominees:
            label_to_nominee[_nominee_label(n)] = n
            # RANK nominations: label is just film title
            if n.film:
                label_to_nominee[n.film.title] = n

        target = nom.nominees_count  # already set from template at longlist creation

        rows = []
        for i, row in enumerate(item["rows"]):
            nominee = label_to_nominee.get(row["label"])
            rows.append({
                "nominee_id": nominee.id if nominee else None,
                "label": row["label"],
                "score": row["score"],
                "voters": row["voters"],
                "position": row["position"],
                "row_num": i + 1,
                # auto_selected = exactly what results page marks green
                "auto_selected": row["is_nominee"],
            })

        items.append({"nom": nom_obj, "rows": rows, "target": target})

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
