from typing import List, Optional
from itertools import combinations
from collections import defaultdict
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from rapidfuzz import fuzz
from ballot.database import get_db
from ballot.models import Film, Nominee, Nomination, Person, Ranking, Round, RoundType
from ballot.auth import require_subadmin
from ballot.utils import _normalize

router = APIRouter(prefix="/admin", dependencies=[Depends(require_subadmin)])
templates = Jinja2Templates(directory="ballot/templates")


@router.get("/films", response_class=HTMLResponse)
def list_films(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """
    List all films in the database.

    Retrieves all films ordered by year (descending) and title, along with all persons
    ordered by name, and renders the admin films page.

    :param request: The incoming HTTP request
    :param db: Database session dependency
    :returns: HTML template response with films and persons data
    """
    films = db.query(Film).order_by(Film.year.desc(), Film.title).all()
    persons = db.query(Person).order_by(Person.name).all()
    return templates.TemplateResponse(request, "admin/films.html", {
        "films": films,
        "persons": persons,
    })


@router.post("/films")
def create_film(
    title: str = Form(...),
    year: int = Form(...),
    url: Optional[str] = Form(None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """
    Create a new film in the database.

    Adds a new film with the provided title, year, and optional URL.
    Redirects back to the films list page after creation.

    :param title: Film title
    :param year: Film release year
    :param url: Optional URL for the film
    :param db: Database session dependency
    :returns: Redirect response to films list page
    """
    db.add(Film(title=title.strip(), year=year, url=url.strip() if url and url.strip() else None))
    db.commit()
    return RedirectResponse(url="/admin/films", status_code=303)


@router.get("/films/merge", response_class=HTMLResponse)
def merge_films_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """
    Show page with suspicious film pairs that might be duplicates.
    
    Groups films by year and finds pairs with similar titles using fuzzy matching.
    The canonical film (to keep) is determined by the one with more nominees.
    
    :param request: The incoming HTTP request
    :param db: Database session dependency
    :returns: HTML template response with pairs of similar films
    """
    films = db.query(Film).order_by(Film.year, Film.title).all()
    
    # Group by year
    by_year = defaultdict(list)
    for f in films:
        by_year[f.year].append(f)
    
    # Get threshold from query param (default 85)
    threshold = int(request.query_params.get("threshold", 85))
    
    pairs = []
    for year, group in by_year.items():
        for a, b in combinations(group, 2):
            score = fuzz.ratio(_normalize(a.title), _normalize(b.title))
            if score >= threshold:
                # Canonical — the one with more nominees
                a_count = db.query(Nominee).filter(Nominee.film_id == a.id).count()
                b_count = db.query(Nominee).filter(Nominee.film_id == b.id).count()
                keep, remove = (a, b) if a_count >= b_count else (b, a)
                pairs.append({
                    "keep": keep,
                    "remove": remove,
                    "score": score,
                    "keep_count": max(a_count, b_count),
                    "remove_count": min(a_count, b_count),
                })
    
    # Sort by score descending
    pairs.sort(key=lambda x: -x["score"])
    
    merged = request.query_params.get("merged")
    
    return templates.TemplateResponse(request, "admin/films_merge.html", {
        "pairs": pairs,
        "threshold": threshold,
        "merged": merged,
    })


@router.post("/films/merge")
async def merge_films_execute(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    """
    Execute the merge of selected film pairs.
    
    For each checked pair, moves all nominees from the 'remove' film to the 'keep' film,
    then deletes the 'remove' film.
    
    :param request: The incoming HTTP request with form data
    :param db: Database session dependency
    :returns: Redirect response back to merge page with merge count
    """
    form = await request.form()
    merged = 0
    for key, val in form.multi_items():
        if not key.startswith("merge_"):
            continue
        keep_id = int(key.removeprefix("merge_"))
        remove_id = int(val)
        
        keep = db.get(Film, keep_id)
        remove = db.get(Film, remove_id)
        if not keep or not remove:
            continue
        
        # Move all nominees to the keep film
        db.query(Nominee).filter(Nominee.film_id == remove_id).update(
            {"film_id": keep_id}, synchronize_session=False
        )

        # Rankings also reference films via NOT NULL FK, so re-link before delete.
        db.query(Ranking).filter(Ranking.film_id == remove_id).update(
            {"film_id": keep_id}, synchronize_session=False
        )

        # Use bulk delete to avoid ORM setting child FK columns to NULL on flush.
        db.query(Film).filter(Film.id == remove_id).delete(synchronize_session=False)
        db.flush()
        merged += 1
    
    db.commit()
    return RedirectResponse(
        url=f"/admin/films/merge?merged={merged}", status_code=303
    )


@router.post("/films/bulk")
def bulk_create_films(
    year: int = Form(...),
    lines: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """
    Bulk create films from text input.

    Processes multiple film entries from a text area, where each line can contain
    title|url format. Creates new films and skips existing ones.

    :param year: Year for all films being created
    :param lines: Text containing film entries (one per line)
    :param db: Database session dependency
    :returns: Redirect response with creation statistics
    """
    created, skipped = 0, 0
    for line in lines.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        url = None
        if "|" in line:
            parts = line.split("|", 1)
            line = parts[0].strip()
            url = parts[1].strip() or None
        title = line.strip()
        if not title:
            continue
        exists = db.query(Film).filter(Film.title == title, Film.year == year).first()
        if exists:
            skipped += 1
            continue
        db.add(Film(title=title, year=year, url=url))
        created += 1
    db.commit()
    return RedirectResponse(
        url=f"/admin/films?bulk_created={created}&bulk_skipped={skipped}",
        status_code=303,
    )


@router.post("/films/bulk-delete")
def bulk_delete_films(
    ids: List[int] = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    db.query(Nominee).filter(Nominee.film_id.in_(ids)).delete(synchronize_session=False)
    db.query(Film).filter(Film.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return RedirectResponse(
        url=f"/admin/films?bulk_deleted={len(ids)}",
        status_code=303,
    )


@router.get("/films/{film_id}", response_class=HTMLResponse)
def film_detail(film_id: int, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """
    Show detailed information about a specific film.

    Retrieves film with all related nominees, nominations, and persons, then categorizes
    them into finals and longlists for display.

    :param film_id: ID of the film to display
    :param request: The incoming HTTP request
    :param db: Database session dependency
    :returns: HTML template response with film details and related data
    """
    film = (
        db.query(Film)
        .options(
            joinedload(Film.nominees).joinedload(Nominee.nomination).joinedload(Nomination.round),
            joinedload(Film.nominees).joinedload(Nominee.person),
        )
        .filter(Film.id == film_id)
        .first()
    )
    if not film:
        return HTMLResponse("Фильм не найден.", status_code=404)

    longlists_count = 0
    nominations_count = 0
    finals = []
    longlists = []
    for n in film.nominees:
        rnd = n.nomination.round if n.nomination else None
        if rnd and rnd.round_type == RoundType.FINAL:
            nominations_count += 1
            finals.append(n)
        else:
            # LONGLIST round or no round at all — both count as longlists
            longlists_count += 1
            longlists.append(n)

    nominations = db.query(Nomination).order_by(Nomination.sort_order, Nomination.id).all()
    persons = db.query(Person).order_by(Person.name).all()
    return templates.TemplateResponse(request, "admin/film_detail.html", {
        "film": film,
        "nominations": nominations,
        "persons": persons,
        "longlists_count": longlists_count,
        "nominations_count": nominations_count,
        "finals": finals,
        "longlists": longlists,
    })


@router.post("/films/{film_id}/edit")
def edit_film(
    film_id: int,
    title: str = Form(...),
    year: int = Form(...),
    url: Optional[str] = Form(None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """
    Edit an existing film.

    Updates film title, year, and URL based on form input.
    Redirects back to films list after update.

    :param film_id: ID of the film to edit
    :param title: New film title
    :param year: New film release year
    :param url: New URL for the film
    :param db: Database session dependency
    :returns: Redirect response to films list page
    """
    film = db.get(Film, film_id)
    if film:
        film.title = title.strip()
        film.year = year
        film.url = url.strip() if url and url.strip() else None
        db.commit()
    return RedirectResponse(url="/admin/films", status_code=303)


@router.post("/films/{film_id}/delete")
def delete_film(film_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    """
    Delete a film from the database.

    Removes the film and all associated nominees, then redirects back to films list.

    :param film_id: ID of the film to delete
    :param db: Database session dependency
    :returns: Redirect response to films list page
    """
    film = db.get(Film, film_id)
    if film:
        db.query(Nominee).filter(Nominee.film_id == film_id).delete()
        db.query(Ranking).filter(Ranking.film_id == film_id).delete(synchronize_session=False)
        db.delete(film)
        db.commit()
    return RedirectResponse(url="/admin/films", status_code=303)


@router.post("/films/{film_id}/nominees")
def add_nominee_from_film(
    film_id: int,
    nomination_id: int = Form(...),
    person_id: Optional[str] = Form(None),
    item: Optional[str] = Form(None),
    item_url: Optional[str] = Form(None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """
    Add a nominee to a film for a specific nomination.

    Creates a new nominee record linking a film to a nomination with optional person and item details.
    Avoids creating duplicate nominees.

    :param film_id: ID of the film to add nominee to
    :param nomination_id: ID of the nomination category
    :param person_id: Optional ID of the person associated with this nominee
    :param item: Optional item description
    :param item_url: Optional URL for the item
    :param db: Database session dependency
    :returns: Redirect response back to film detail page
    """
    from ballot.models import NominationType
    nom = db.get(Nomination, nomination_id)
    if not nom:
        return RedirectResponse(url=f"/admin/films/{film_id}", status_code=303)
    pid = int(person_id) if person_id and person_id.strip() else None
    item_val = item.strip() if item and item.strip() else None
    item_url_val = item_url.strip() if item_url and item_url.strip() else None
    existing = db.query(Nominee).filter_by(
        nomination_id=nomination_id, film_id=film_id, person_id=pid, item=item_val
    ).first()
    if not existing:
        db.add(Nominee(
            nomination_id=nomination_id,
            film_id=film_id,
            person_id=pid,
            item=item_val,
            item_url=item_url_val,
        ))
        db.commit()
    return RedirectResponse(url=f"/admin/films/{film_id}", status_code=303)


@router.get("/nominees/{nominee_id}/edit")
def edit_nominee_get(nominee_id: int, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """
    Show edit form for a specific nominee.

    Retrieves the nominee and all available films and persons for the edit form.

    :param nominee_id: ID of the nominee to edit
    :param request: The incoming HTTP request
    :param db: Database session dependency
    :returns: HTML template response with nominee edit form
    """
    nominee = db.get(Nominee, nominee_id)
    if not nominee:
        return RedirectResponse(url="/admin/films", status_code=303)
    films = db.query(Film).order_by(Film.title).all()
    persons = db.query(Person).order_by(Person.name).all()
    return templates.TemplateResponse(request, "admin/nominee_edit.html", {
        "nominee": nominee,
        "films": films,
        "persons": persons,
    })


@router.post("/nominees/{nominee_id}/edit")
def edit_nominee(
    nominee_id: int,
    film_id: int = Form(...),
    person_id: Optional[str] = Form(None),
    item: Optional[str] = Form(None),
    item_url: Optional[str] = Form(None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """
    Update an existing nominee.

    Modifies the nominee's film, person, item, and item URL based on form input.
    Redirects back to either the nomination page or films list.

    :param nominee_id: ID of the nominee to update
    :param film_id: New film ID for the nominee
    :param person_id: Optional new person ID
    :param item: Optional new item description
    :param item_url: Optional new item URL
    :param db: Database session dependency
    :returns: Redirect response to appropriate page
    """
    nominee = db.get(Nominee, nominee_id)
    if nominee:
        nominee.film_id = film_id
        nominee.person_id = int(person_id) if person_id and person_id.strip() else None
        nominee.item = item.strip() if item and item.strip() else None
        nominee.item_url = item_url.strip() if item_url and item_url.strip() else None
        db.commit()
    back = f"/admin/nominations/{nominee.nomination_id}" if nominee else "/admin/films"
    return RedirectResponse(url=back, status_code=303)


@router.post("/nominees/{nominee_id}/delete")
def delete_nominee(
    nominee_id: int,
    back: str = Form("films"),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """
    Delete a nominee from the database.

    Removes the nominee and redirects back to either the nomination page or films list
    based on the 'back' parameter.

    :param nominee_id: ID of the nominee to delete
    :param back: Where to redirect after deletion ('films' or 'nomination')
    :param db: Database session dependency
    :returns: Redirect response to appropriate page
    """
    nominee = db.get(Nominee, nominee_id)
    if nominee:
        nom_id = nominee.nomination_id
        db.delete(nominee)
        db.commit()
        if back == "nomination":
            return RedirectResponse(url=f"/admin/nominations/{nom_id}", status_code=303)
    return RedirectResponse(url="/admin/films", status_code=303)
