"""Admin router for global NominationTemplate management."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ballot.auth import require_admin
from ballot.database import get_db
from ballot.models import NominationTemplate, NominationType, ContestNomination

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="ballot/templates")


def _int(value: Optional[str]) -> Optional[int]:
    """Convert form string to int; return None for empty / missing values."""
    if value is None or value.strip() == "":
        return None
    return int(value)


@router.get("/templates", response_class=HTMLResponse)
def list_templates(request: Request, db: Session = Depends(get_db)):
    tmps = (
        db.query(NominationTemplate)
        .order_by(NominationTemplate.sort_order, NominationTemplate.id)
        .all()
    )
    return templates.TemplateResponse(
        request, "admin/templates.html", {"templates": tmps}
    )


@router.post("/templates")
def create_template(
    name: str = Form(...),
    description: Optional[str] = Form(None),
    type: NominationType = Form(...),
    longlist_pick_min: Optional[str] = Form(None),
    longlist_pick_max: Optional[str] = Form(None),
    final_promotes_count: Optional[str] = Form(None),
    acting_group: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    last = (
        db.query(NominationTemplate)
        .order_by(NominationTemplate.sort_order.desc())
        .first()
    )
    order = (last.sort_order + 1) if last else 0
    db.add(NominationTemplate(
        name=name.strip(),
        description=description.strip() if description else None,
        type=type,
        sort_order=order,
        acting_group=acting_group.strip() if acting_group else None,
        longlist_pick_min=_int(longlist_pick_min),
        longlist_pick_max=_int(longlist_pick_max),
        final_promotes_count=_int(final_promotes_count),
    ))
    db.commit()
    return RedirectResponse(url="/admin/templates", status_code=303)


@router.post("/templates/{template_id}/edit")
def edit_template(
    template_id: int,
    name: str = Form(...),
    description: Optional[str] = Form(None),
    type: NominationType = Form(...),
    longlist_pick_min: Optional[str] = Form(None),
    longlist_pick_max: Optional[str] = Form(None),
    final_promotes_count: Optional[str] = Form(None),
    acting_group: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    tmpl = db.get(NominationTemplate, template_id)
    if tmpl:
        tmpl.name = name.strip()
        tmpl.description = description.strip() if description else None
        tmpl.type = type
        tmpl.acting_group = acting_group.strip() if acting_group else None
        tmpl.longlist_pick_min = _int(longlist_pick_min)
        tmpl.longlist_pick_max = _int(longlist_pick_max)
        tmpl.final_promotes_count = _int(final_promotes_count)
        db.commit()
    return RedirectResponse(url="/admin/templates", status_code=303)


@router.post("/templates/{template_id}/archive")
def archive_template(template_id: int, db: Session = Depends(get_db)):
    tmpl = db.get(NominationTemplate, template_id)
    if tmpl:
        tmpl.is_archived = not tmpl.is_archived
        db.commit()
    return RedirectResponse(url="/admin/templates", status_code=303)


@router.post("/templates/{template_id}/delete")
def delete_template(template_id: int, db: Session = Depends(get_db)):
    used = (
        db.query(ContestNomination)
        .filter(ContestNomination.template_id == template_id)
        .first()
    )
    if not used:
        tmpl = db.get(NominationTemplate, template_id)
        if tmpl:
            db.delete(tmpl)
            db.commit()
    return RedirectResponse(url="/admin/templates", status_code=303)


@router.post("/templates/{template_id}/move")
def move_template(
    template_id: int,
    direction: str = Form(...),
    db: Session = Depends(get_db),
):
    all_tmpl = (
        db.query(NominationTemplate)
        .order_by(NominationTemplate.sort_order, NominationTemplate.id)
        .all()
    )
    idx = next((i for i, t in enumerate(all_tmpl) if t.id == template_id), None)
    if idx is None:
        return RedirectResponse(url="/admin/templates", status_code=303)

    swap_idx = idx - 1 if direction == "up" else idx + 1
    if 0 <= swap_idx < len(all_tmpl):
        a, b = all_tmpl[idx], all_tmpl[swap_idx]
        a.sort_order, b.sort_order = b.sort_order, a.sort_order
        db.commit()
    return RedirectResponse(url="/admin/templates", status_code=303)
