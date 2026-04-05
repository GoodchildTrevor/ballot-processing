from typing import Optional
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from ballot.database import get_db
from ballot.models import Person, Nominee
from ballot.auth import require_admin

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="ballot/templates")


@router.get("/persons", response_class=HTMLResponse)
def list_persons(request: Request, db: Session = Depends(get_db)):
    persons = db.query(Person).order_by(Person.name).all()
    return templates.TemplateResponse(request, "admin/persons.html", {"persons": persons})


@router.post("/persons")
def create_person(name: str = Form(...), db: Session = Depends(get_db)):
    name = name.strip()
    if not name:
        return RedirectResponse(url="/admin/persons", status_code=303)
    existing = db.query(Person).filter(Person.name == name).first()
    if not existing:
        db.add(Person(name=name))
        db.commit()
    return RedirectResponse(url="/admin/persons", status_code=303)


@router.post("/persons/{person_id}/delete")
def delete_person(person_id: int, db: Session = Depends(get_db)):
    person = db.get(Person, person_id)
    if person:
        # Detach from nominees before deleting
        db.query(Nominee).filter(Nominee.person_id == person_id).update({"person_id": None})
        db.delete(person)
        db.commit()
    return RedirectResponse(url="/admin/persons", status_code=303)


@router.post("/persons/{person_id}/edit")
def edit_person(
    person_id: int,
    name: str = Form(...),
    db: Session = Depends(get_db),
):
    person = db.get(Person, person_id)
    if person:
        person.name = name.strip()
        db.commit()
    return RedirectResponse(url="/admin/persons", status_code=303)
