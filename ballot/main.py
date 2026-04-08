from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from ballot.database import engine, Base, run_migrations
import ballot.models  # noqa: F401
from ballot.routers import vote, admin_films, admin_nominations, admin_voters, admin_results, admin_persons

run_migrations()                    # ALTER TABLE for new columns on existing DB
Base.metadata.create_all(bind=engine)  # CREATE TABLE for brand-new tables

app = FastAPI(title="Ballot Processing")

app.include_router(vote.router)
app.include_router(admin_films.router)
app.include_router(admin_nominations.router)
app.include_router(admin_voters.router)
app.include_router(admin_results.router)
app.include_router(admin_persons.router)


@app.get("/admin")
def admin_root():
    return RedirectResponse(url="/admin/films", status_code=302)
