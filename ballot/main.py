from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from ballot.database import engine, Base, run_migrations
import ballot.models  # noqa: F401
from ballot.routers import vote, admin_films, admin_nominations, admin_voters, admin_results, admin_persons

run_migrations()                    # ALTER TABLE for new columns on existing DB
Base.metadata.create_all(bind=engine)  # CREATE TABLE for brand-new tables

app = FastAPI(title="Ballot Processing")
templates = Jinja2Templates(directory="ballot/templates")

app.include_router(vote.router)
app.include_router(admin_films.router)
app.include_router(admin_nominations.router)
app.include_router(admin_voters.router)
app.include_router(admin_results.router)
app.include_router(admin_persons.router)


@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    """Login / voter-select page. No auth required."""
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/admin", response_class=HTMLResponse)
def admin_root(request: Request):
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/admin/films", status_code=302)
