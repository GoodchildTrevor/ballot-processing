import enum
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, ForeignKey, DateTime, Boolean,
    Enum as SAEnum, UniqueConstraint, JSON
)
from sqlalchemy.orm import relationship
from ballot.database import Base


class NominationType(str, enum.Enum):
    RANK = "RANK"
    PICK = "PICK"


class RoundType(str, enum.Enum):
    LONGLIST = "LONGLIST"
    FINAL    = "FINAL"


class ContestStatus(str, enum.Enum):
    DRAFT           = "DRAFT"
    LONGLIST_ACTIVE = "LONGLIST_ACTIVE"
    LONGLIST_CLOSED = "LONGLIST_CLOSED"
    FINAL_ACTIVE    = "FINAL_ACTIVE"
    FINAL_CLOSED    = "FINAL_CLOSED"


# ---------------------------------------------------------------------------
# Contest
# ---------------------------------------------------------------------------

class Contest(Base):
    """One contest per year. Owns two rounds (longlist + final) and a set
    of activated nomination templates."""
    __tablename__ = "contests"
    id     = Column(Integer, primary_key=True)
    year   = Column(Integer, nullable=False, unique=True)
    name   = Column(String,  nullable=False)          # e.g. "Конкурс 1994"
    status = Column(SAEnum(ContestStatus),
                   nullable=False, default=ContestStatus.DRAFT)

    rounds              = relationship("Round",             back_populates="contest",
                                       order_by="Round.tour")
    contest_nominations = relationship("ContestNomination", back_populates="contest",
                                       cascade="all, delete-orphan",
                                       order_by="ContestNomination.sort_order")


# ---------------------------------------------------------------------------
# NominationTemplate  — global catalogue, year-independent
# ---------------------------------------------------------------------------

class NominationTemplate(Base):
    """Global reusable nomination category. Admins maintain this list once;
    each contest picks which templates to activate."""
    __tablename__ = "nomination_templates"
    id          = Column(Integer, primary_key=True)
    name        = Column(String,  nullable=False)
    description = Column(String,  nullable=True)   # shown to voters in the ballot
    type        = Column(SAEnum(NominationType), nullable=False)
    sort_order  = Column(Integer, default=0,  nullable=False)
    is_archived = Column(Boolean, default=False, nullable=False)

    # --- longlist parameters (copied into RoundNomination on longlist creation) ---
    longlist_nominees_count = Column(Integer, nullable=True)   # how many nominees admin adds
    longlist_pick_min       = Column(Integer, nullable=True)
    longlist_pick_max       = Column(Integer, nullable=True)

    # --- final parameters ---
    # Target number of promotees. Actual count may be higher due to DENSE_RANK ties.
    # NULL means "promote all" (used e.g. for SPECIAL/PICK with no fixed cutoff).
    final_promotes_count = Column(Integer, nullable=True)

    contest_nominations = relationship("ContestNomination", back_populates="template")


# ---------------------------------------------------------------------------
# ContestNomination  — which templates are active for a given contest/year
# ---------------------------------------------------------------------------

class ContestNomination(Base):
    """Join between Contest and NominationTemplate. Stores the per-contest
    sort order (inherited from template.sort_order on creation, editable after)."""
    __tablename__ = "contest_nominations"
    id          = Column(Integer, primary_key=True)
    contest_id  = Column(Integer, ForeignKey("contests.id"),             nullable=False)
    template_id = Column(Integer, ForeignKey("nomination_templates.id"), nullable=False)
    sort_order  = Column(Integer, default=0, nullable=False)

    contest   = relationship("Contest",            back_populates="contest_nominations")
    template  = relationship("NominationTemplate", back_populates="contest_nominations")
    # Each ContestNomination produces exactly 1 Nomination per Round tour.
    nominations = relationship("Nomination",       back_populates="contest_nomination")

    __table_args__ = (
        UniqueConstraint("contest_id", "template_id", name="uq_contest_template"),
    )


# ---------------------------------------------------------------------------
# Round
# ---------------------------------------------------------------------------

class Round(Base):
    __tablename__ = "rounds"
    id         = Column(Integer, primary_key=True)
    label      = Column(String, nullable=False)          # e.g. "Лонг-лист 1994"
    round_type = Column(SAEnum(RoundType), nullable=False, default=RoundType.LONGLIST)
    year       = Column(Integer, nullable=False)
    deadline   = Column(DateTime, nullable=True)
    is_active  = Column(Boolean, default=False, nullable=False)
    sort_order = Column(Integer, default=0,     nullable=False)

    # --- new: link to parent Contest ---
    # nullable=True to keep legacy rounds (pre-Contest) alive without a FK violation.
    contest_id = Column(Integer, ForeignKey("contests.id"), nullable=True)
    # 1 = LONGLIST, 2 = FINAL
    tour       = Column(Integer, default=1, nullable=False)

    contest        = relationship("Contest",          back_populates="rounds")
    nominations    = relationship("Nomination",       back_populates="round")
    participations = relationship("RoundParticipation", back_populates="round",
                                  cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# Voter + participation
# ---------------------------------------------------------------------------

class Voter(Base):
    __tablename__ = "voters"
    id   = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)

    votes          = relationship("Vote",               back_populates="voter")
    rankings       = relationship("Ranking",            back_populates="voter")
    participations = relationship("RoundParticipation", back_populates="voter",
                                  cascade="all, delete-orphan")


class RoundParticipation(Base):
    """Tracks per-round voter state (draft, voted_at)."""
    __tablename__ = "round_participations"
    id       = Column(Integer, primary_key=True)
    round_id = Column(Integer, ForeignKey("rounds.id"),  nullable=False)
    voter_id = Column(Integer, ForeignKey("voters.id"),  nullable=False)
    voted_at = Column(DateTime, nullable=True)
    draft    = Column(JSON,     nullable=True)

    round = relationship("Round", back_populates="participations")
    voter = relationship("Voter", back_populates="participations")

    __table_args__ = (
        UniqueConstraint("round_id", "voter_id", name="uq_round_voter"),
    )


# ---------------------------------------------------------------------------
# Film / Person
# ---------------------------------------------------------------------------

class Film(Base):
    __tablename__ = "films"
    id       = Column(Integer, primary_key=True)
    title    = Column(String, nullable=False)
    year     = Column(Integer, nullable=False)
    url      = Column(String, nullable=True)
    nominees = relationship("Nominee",  back_populates="film")
    rankings = relationship("Ranking",  back_populates="film")


class Person(Base):
    __tablename__ = "persons"
    id   = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    url  = Column(String, nullable=True)
    nominees        = relationship("Nominee",       back_populates="person")
    nominee_persons = relationship("NomineePerson", back_populates="person")


# ---------------------------------------------------------------------------
# Nomination  (instance of a template within a specific Round)
# ---------------------------------------------------------------------------

class Nomination(Base):
    __tablename__ = "nominations"
    id             = Column(Integer, primary_key=True)
    name           = Column(String,  nullable=False)
    type           = Column(SAEnum(NominationType), nullable=False)
    pick_min       = Column(Integer, nullable=True)
    pick_max       = Column(Integer, nullable=True)
    nominees_count = Column(Integer, nullable=True)
    year_filter    = Column(Integer, nullable=True)
    sort_order     = Column(Integer, nullable=False, default=0)
    round_id       = Column(Integer, ForeignKey("rounds.id"), nullable=True)
    has_runner_up  = Column(Boolean, default=False, nullable=False)

    # --- new: back-reference to the ContestNomination this row was generated from ---
    # nullable=True so legacy nominations (created before the template system) are untouched.
    contest_nomination_id = Column(Integer,
                                   ForeignKey("contest_nominations.id"),
                                   nullable=True)

    round              = relationship("Round",             back_populates="nominations")
    contest_nomination = relationship("ContestNomination", back_populates="nominations")
    nominees           = relationship("Nominee",           back_populates="nomination")


# ---------------------------------------------------------------------------
# NomineePerson (many-to-many bridge)
# ---------------------------------------------------------------------------

class NomineePerson(Base):
    """Many-to-many bridge: one nominee can credit multiple persons."""
    __tablename__ = "nominee_persons"
    id         = Column(Integer, primary_key=True)
    nominee_id = Column(Integer, ForeignKey("nominees.id"), nullable=False)
    person_id  = Column(Integer, ForeignKey("persons.id"),  nullable=False)
    role       = Column(String, nullable=True)
    nominee    = relationship("Nominee",  back_populates="persons")
    person     = relationship("Person",   back_populates="nominee_persons")
    __table_args__ = (
        UniqueConstraint("nominee_id", "person_id", name="uq_nominee_person"),
    )


# ---------------------------------------------------------------------------
# Nominee
# ---------------------------------------------------------------------------

class Nominee(Base):
    __tablename__ = "nominees"
    id             = Column(Integer, primary_key=True)
    nomination_id  = Column(Integer, ForeignKey("nominations.id"), nullable=False)
    film_id        = Column(Integer, ForeignKey("films.id"),       nullable=False)
    person_id      = Column(Integer, ForeignKey("persons.id"),     nullable=True)
    item           = Column(String, nullable=True)
    item_url       = Column(String, nullable=True)
    is_shortlisted = Column(Boolean, default=False, nullable=False)

    nomination = relationship("Nomination",    back_populates="nominees")
    film       = relationship("Film",           back_populates="nominees")
    person     = relationship("Person",         back_populates="nominees")
    votes      = relationship("Vote",           back_populates="nominee")
    persons    = relationship("NomineePerson",  back_populates="nominee",
                               cascade="all, delete-orphan")

    @property
    def all_persons(self):
        if self.persons:
            return [np.person for np in self.persons]
        if self.person:
            return [self.person]
        return []

    @property
    def persons_label(self) -> str:
        return ", ".join(p.name for p in self.all_persons)


# ---------------------------------------------------------------------------
# Vote / Ranking
# ---------------------------------------------------------------------------

class Vote(Base):
    __tablename__ = "votes"
    id           = Column(Integer, primary_key=True)
    voter_id     = Column(Integer, ForeignKey("voters.id"),   nullable=False)
    nominee_id   = Column(Integer, ForeignKey("nominees.id"), nullable=False)
    # True for the runner-up vote in FINAL PICK nominations (used only on tie-break)
    is_runner_up = Column(Boolean, default=False, nullable=False)

    voter   = relationship("Voter",   back_populates="votes")
    nominee = relationship("Nominee", back_populates="votes")

    __table_args__ = (
        UniqueConstraint("voter_id", "nominee_id", name="uq_vote_voter_nominee"),
    )


class Ranking(Base):
    __tablename__ = "rankings"
    id            = Column(Integer, primary_key=True)
    voter_id      = Column(Integer, ForeignKey("voters.id"),      nullable=False)
    nomination_id = Column(Integer, ForeignKey("nominations.id"), nullable=False)
    film_id       = Column(Integer, ForeignKey("films.id"),       nullable=False)
    rank          = Column(Integer, nullable=False)

    voter  = relationship("Voter",  back_populates="rankings")
    film   = relationship("Film",   back_populates="rankings")
