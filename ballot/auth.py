import os
import secrets
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session
from ballot.database import get_db
from ballot.models import Voter
from urllib.parse import quote

security = HTTPBasic()

ADMIN_USER = os.getenv("ADMIN_USER")
ADMIN_PASS = os.getenv("ADMIN_PASS")
SUBADMIN_USER = os.getenv("SUBADMIN_USER")
SUBADMIN_PASS = os.getenv("SUBADMIN_PASS")


def require_admin(credentials: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(credentials.username.encode(), ADMIN_USER.encode())
    ok_pass = secrets.compare_digest(credentials.password.encode(), ADMIN_PASS.encode())
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

def require_subadmin(credentials: HTTPBasicCredentials = Depends(security)):
    # Allow full admin or subadmin accounts
    ok_admin = secrets.compare_digest(credentials.username.encode(), ADMIN_USER.encode()) and \
               secrets.compare_digest(credentials.password.encode(), ADMIN_PASS.encode())
    ok_sub = secrets.compare_digest(credentials.username.encode(), SUBADMIN_USER.encode()) and \
             secrets.compare_digest(credentials.password.encode(), SUBADMIN_PASS.encode())
    if not (ok_admin or ok_sub):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def require_voter(request: Request, db: Session = Depends(get_db)):
    """Reads voter_id cookie (int), loads Voter from DB,
    stores it in request.state.voter. Redirects to / if missing or unknown."""
    raw = request.cookies.get("voter_id")
    if not raw:
        next_url = quote(str(request.url.path), safe="")
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": f"/?next={next_url}"},
        )
    try:
        voter_id = int(raw)
    except ValueError:
        next_url = quote(str(request.url.path), safe="")
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": f"/?next={next_url}"},
        )
    voter = db.get(Voter, voter_id)
    if not voter:
        next_url = quote(str(request.url.path), safe="")
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": f"/?next={next_url}"},
        )
    request.state.voter = voter
    return voter
