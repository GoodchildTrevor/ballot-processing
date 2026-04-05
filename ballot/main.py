from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from ballot.database import engine, Base
import ballot.models  # noqa: F401 — registers all models
from ballot.routers import vote, admin_films, admin_nominations, admin_voters, admin_results

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Ballot Processing")

app.include_router(vote.router)
app.include_router(admin_films.router)
app.include_router(admin_nominations.router)
app.include_router(admin_voters.router)
app.include_router(admin_results.router)
