import os
from fastapi import FastAPI, Form, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from ballot.database import engine, Base, run_migrations, get_db
from ballot.auth import serializer
from ballot.models import Voter
from ballot.routers import (
    vote,
    admin_films,
    admin_nominations,
    admin_voters,
    admin_results,
    admin_persons,
    admin_rounds,
    admin_templates,
)

if os.environ.get("RUN_MIGRATIONS") == "1":
    run_migrations()
if os.environ.get("FORCE_CREATE_ALL") == "1":
    Base.metadata.create_all(bind=engine)

app = FastAPI(title="Ballot Processing")
templates = Jinja2Templates(directory="ballot/templates")

app.include_router(vote.router)
app.include_router(admin_films.router)
app.include_router(admin_nominations.router)
app.include_router(admin_voters.router)
app.include_router(admin_results.router)
app.include_router(admin_persons.router)
app.include_router(admin_rounds.router)
app.include_router(admin_templates.router)


@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    """
    Render the main login page.
    
    Displays the login form where voters can enter their name to authenticate.
    
    :param request: The incoming HTTP request
    :returns: HTML template response with login form
    """
    next_url = request.query_params.get("next", "")
    return templates.TemplateResponse("index.html", {"request": request, "next": next_url})


@app.post("/", response_class=HTMLResponse)
def login(
    request: Request,
    name: str = Form(...),
    next: str = Form(default=""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """
    Process voter login and create session cookie.
    
    Validates the voter name, creates a new voter if not found,
    and sets a signed cookie for authentication.
    
    :param request: The incoming HTTP request
    :param name: Voter name from form submission
    :param next: URL to redirect to after login
    :param db: Database session dependency
    :returns: Redirect response to either the original URL or /vote
    """
    name = name.strip()
    if not name:
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "error": "Введите ник.", "next": next},
            status_code=400,
        )
    voter = db.query(Voter).filter(Voter.name == name).first()
    if not voter:
        voter = Voter(name=name)
        db.add(voter)
        db.commit()
        db.refresh(voter)
    # Redirect to original URL if valid, otherwise /vote
    redirect_to = next if (next and next.startswith("/")) else "/vote"
    response = RedirectResponse(url=redirect_to, status_code=303)
    signed = serializer.dumps({"voter_id": voter.id})
    response.set_cookie(
        key="voter_id",
        value=signed,
        httponly=True,
        samesite="lax",
        secure=True,
    )
    return response


@app.get("/admin")
def admin_root() -> RedirectResponse:
    """
    Redirect admin root to films admin page.
    
    Provides a default redirect for the admin interface.
    
    :returns: Redirect response to /admin/films
    """
    return RedirectResponse(url="/admin/films", status_code=302)
