"""Admin router for global NominationTemplate management.

Routes
------
GET  /admin/templates              – list all templates
POST /admin/templates              – create template
POST /admin/templates/{id}/edit    – update template
POST /admin/templates/{id}/archive – toggle is_archived
POST /admin/templates/{id}/delete  – delete (only if unused)
POST /admin/templates/{id}/move    – reorder (up/down)
"""
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


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@router.post("/templates")
def create_template(
    name: str = Form(...),
    description: Optional[str] = Form(None),
    type: NominationType = Form(...),
    longlist_nominees_count: Optional[int] = Form(None),
    longlist_pick_min: Optional[int] = Form(None),
    longlist_pick_max: Optional[int] = Form(None),
    final_promotes_count: Optional[int] = Form(None),
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
        longlist_nominees_count=longlist_nominees_count,
        longlist_pick_min=longlist_pick_min,
        longlist_pick_max=longlist_pick_max,
        final_promotes_count=final_promotes_count,
    ))
    db.commit()
    return RedirectResponse(url="/admin/templates", status_code=303)


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------

@router.post("/templates/{template_id}/edit")
def edit_template(
    template_id: int,
    name: str = Form(...),
    description: Optional[str] = Form(None),
    type: NominationType = Form(...),
    longlist_nominees_count: Optional[int] = Form(None),
    longlist_pick_min: Optional[int] = Form(None),
    longlist_pick_max: Optional[int] = Form(None),
    final_promotes_count: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    tmpl = db.get(NominationTemplate, template_id)
    if tmpl:
        tmpl.name = name.strip()
        tmpl.description = description.strip() if description else None
        tmpl.type = type
        tmpl.longlist_nominees_count = longlist_nominees_count
        tmpl.longlist_pick_min = longlist_pick_min
        tmpl.longlist_pick_max = longlist_pick_max
        tmpl.final_promotes_count = final_promotes_count
        db.commit()
    return RedirectResponse(url="/admin/templates", status_code=303)


# ---------------------------------------------------------------------------
# Archive toggle
# ---------------------------------------------------------------------------

@router.post("/templates/{template_id}/archive")
def archive_template(template_id: int, db: Session = Depends(get_db)):
    tmpl = db.get(NominationTemplate, template_id)
    if tmpl:
        tmpl.is_archived = not tmpl.is_archived
        db.commit()
    return RedirectResponse(url="/admin/templates", status_code=303)


# ---------------------------------------------------------------------------
# Delete (only if not used in any ContestNomination)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Move (reorder)
# ---------------------------------------------------------------------------

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
