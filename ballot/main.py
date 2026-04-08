from fastapi import FastAPI, Form, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from ballot.database import engine, Base, run_migrations, get_db
import ballot.models  # noqa: F401
from ballot.models import Voter
from ballot.routers import vote, admin_films, admin_nominations, admin_voters, admin_results, admin_persons

run_migrations()
Base.metadata.create_all(bind=engine)

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
    return templates.TemplateResponse(request, "index.html", {})


@app.post("/", response_class=HTMLResponse)
def login(
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db),
):
    name = name.strip()
    voter = db.query(Voter).filter(Voter.name == name).first()
    if not voter:
        return templates.TemplateResponse(
            request, "index.html",
            {"error": f"Ник «{name}» не найден. Обратитесь к организатору."},
            status_code=400,
        )
    response = RedirectResponse(url="/vote", status_code=303)
    # Cookie value must be ASCII-safe — store numeric ID, not the name
    response.set_cookie(key="voter_id", value=str(voter.id), httponly=True, samesite="lax")
    return response


@app.get("/admin")
def admin_root():
    return RedirectResponse(url="/admin/films", status_code=302)
