import os
import secrets
from itsdangerous import BadData, URLSafeSerializer
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
COOKIE_SALT = "voter-cookie"
serializer = URLSafeSerializer(os.environ["SECRET_KEY"], salt=COOKIE_SALT)


def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """
    Require admin credentials to access an endpoint.
    
    Verifies that the provided credentials match the configured admin credentials.
    Uses constant-time comparison to prevent timing attacks.
    
    :param credentials: HTTP basic auth credentials provided by the client
    :returns: The authenticated admin username
    :raises HTTPException: If credentials don't match admin credentials, 
                          with 401 status code and WWW-Authenticate header
    """
    ok_user = secrets.compare_digest(credentials.username.encode(), ADMIN_USER.encode())
    ok_pass = secrets.compare_digest(credentials.password.encode(), ADMIN_PASS.encode())
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def require_subadmin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """
    Require subadmin or admin credentials to access an endpoint.
    
    Verifies that the provided credentials match either the admin credentials
    or the subadmin credentials. Allows both full admin and subadmin accounts.
    Uses constant-time comparison to prevent timing attacks.
    
    :param credentials: HTTP basic auth credentials provided by the client
    :returns: The authenticated username (either admin or subadmin)
    :raises HTTPException: If credentials don't match admin or subadmin credentials,
                          with 401 status code and WWW-Authenticate header
    """
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


def require_voter(request: Request, db: Session = Depends(get_db)) -> Voter:
    """
    Read signed voter cookie, load Voter from DB, and set request.state.voter.
    
    Extracts voter_id from a signed cookie, validates it, retrieves the corresponding
    Voter from the database, and attaches it to the request state. Redirects to login
    page if cookie is invalid or voter doesn't exist.
    
    :param request: The incoming HTTP request
    :param db: Database session dependency
    :returns: The authenticated Voter object
    :raises HTTPException: If cookie validation fails or voter not found,
                          with 307 redirect to login page
    """
    raw = request.cookies.get("voter_id", "")
    try:
        payload = serializer.loads(raw)
        voter_id = int(payload["voter_id"])
        if voter_id <= 0:
            raise ValueError("voter_id must be positive")
    except (BadData, KeyError, TypeError, ValueError):
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
