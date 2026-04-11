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


# ---------------------------------------------------------------------------
# Round
# ---------------------------------------------------------------------------

class Round(Base):
    __tablename__ = "rounds"
    id          = Column(Integer, primary_key=True)
    label       = Column(String, nullable=False)          # e.g. "Лонг-лист 2024"
    round_type  = Column(SAEnum(RoundType), nullable=False, default=RoundType.LONGLIST)
    year        = Column(Integer, nullable=False)
    deadline    = Column(DateTime, nullable=True)         # set manually by admin
    is_active   = Column(Boolean, default=False, nullable=False)
    sort_order  = Column(Integer, default=0, nullable=False)

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
    """Tracks per-round voter state (draft, voted_at).  Replaces Voter.voted_at/draft."""
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
# Nomination
# ---------------------------------------------------------------------------

class Nomination(Base):
    __tablename__ = "nominations"
    id            = Column(Integer, primary_key=True)
    name          = Column(String,  nullable=False)
    type          = Column(SAEnum(NominationType), nullable=False)
    pick_min      = Column(Integer, nullable=True)
    pick_max      = Column(Integer, nullable=True)
    nominees_count = Column(Integer, nullable=True)
    year_filter   = Column(Integer, nullable=True)
    sort_order    = Column(Integer, nullable=False, default=0)
    round_id      = Column(Integer, ForeignKey("rounds.id"), nullable=True)
    has_runner_up = Column(Boolean, default=False, nullable=False)

    round    = relationship("Round",    back_populates="nominations")
    nominees = relationship("Nominee",  back_populates="nomination")
    winner   = relationship("Winner",   back_populates="nomination", uselist=False)


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
    id            = Column(Integer, primary_key=True)
    nomination_id = Column(Integer, ForeignKey("nominations.id"), nullable=False)
    film_id       = Column(Integer, ForeignKey("films.id"),       nullable=False)
    person_id     = Column(Integer, ForeignKey("persons.id"),     nullable=True)
    item          = Column(String, nullable=True)
    item_url      = Column(String, nullable=True)
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
    is_runner_up = Column(Boolean, default=False, nullable=False)

    voter   = relationship("Voter",   back_populates="votes")
    nominee = relationship("Nominee", back_populates="votes")

    __table_args__ = (
        UniqueConstraint("voter_id", "nominee_id", name="uq_vote_voter_nominee"),
    )


class Ranking(Base):
    __tablename__ = "rankings"
    id            = Column(Integer, primary_key=True)
    voter_id      = Column(Integer, ForeignKey("voters.id"),        nullable=False)
    nomination_id = Column(Integer, ForeignKey("nominations.id"),   nullable=False)
    film_id       = Column(Integer, ForeignKey("films.id"),         nullable=False)
    rank          = Column(Integer, nullable=False)

    voter  = relationship("Voter",  back_populates="rankings")
    film   = relationship("Film",   back_populates="rankings")


# ---------------------------------------------------------------------------
# Winner
# ---------------------------------------------------------------------------

class Winner(Base):
    """Stores the admin-designated winner for a nomination.

    One row per nomination (UNIQUE on nomination_id).  nominee_id is nullable
    so a winner record can be created in a 'pending' state (no nominee chosen
    yet) and later assigned.  is_public controls whether the public results
    page reveals the winner before the official announcement.
    """
    __tablename__ = "winners"
    id            = Column(Integer, primary_key=True)
    nomination_id = Column(Integer, ForeignKey("nominations.id"), nullable=False)
    nominee_id    = Column(Integer, ForeignKey("nominees.id"),    nullable=True)
    announced_at  = Column(DateTime, nullable=True)
    is_public     = Column(Boolean,  default=False, nullable=False)

    nomination = relationship("Nomination", back_populates="winner")
    nominee    = relationship("Nominee")

    __table_args__ = (
        UniqueConstraint("nomination_id", name="uq_winner_nomination"),
    )
