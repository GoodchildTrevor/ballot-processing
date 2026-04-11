import enum
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, ForeignKey, DateTime,
    Enum as SAEnum, UniqueConstraint, JSON
)
from sqlalchemy.orm import relationship
from ballot.database import Base


class NominationType(str, enum.Enum):
    RANK = "RANK"
    PICK = "PICK"


class Film(Base):
    __tablename__ = "films"
    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    url = Column(String, nullable=True)
    nominees = relationship("Nominee", back_populates="film")
    rankings = relationship("Ranking", back_populates="film")


class Person(Base):
    __tablename__ = "persons"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    url = Column(String, nullable=True)
    # legacy direct link kept for backward compat
    nominees = relationship("Nominee", back_populates="person")
    # new multi-person link
    nominee_persons = relationship("NomineePerson", back_populates="person")


class Nomination(Base):
    __tablename__ = "nominations"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    type = Column(SAEnum(NominationType), nullable=False)
    pick_min = Column(Integer, nullable=True)
    pick_max = Column(Integer, nullable=True)
    nominees_count = Column(Integer, nullable=True)
    year_filter = Column(Integer, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    # Deadline for this nomination's voting stage.
    # If set, votes are rejected after this timestamp.
    vote_deadline = Column(DateTime, nullable=True)
    nominees = relationship("Nominee", back_populates="nomination")


class NomineePerson(Base):
    """Many-to-many bridge: one nominee can credit multiple persons."""
    __tablename__ = "nominee_persons"
    id = Column(Integer, primary_key=True)
    nominee_id = Column(Integer, ForeignKey("nominees.id"), nullable=False)
    person_id = Column(Integer, ForeignKey("persons.id"), nullable=False)
    # Optional label: 'director', 'writer', etc.
    role = Column(String, nullable=True)
    nominee = relationship("Nominee", back_populates="persons")
    person = relationship("Person", back_populates="nominee_persons")
    __table_args__ = (
        UniqueConstraint("nominee_id", "person_id", name="uq_nominee_person"),
    )


class Nominee(Base):
    __tablename__ = "nominees"
    id = Column(Integer, primary_key=True)
    nomination_id = Column(Integer, ForeignKey("nominations.id"), nullable=False)
    film_id = Column(Integer, ForeignKey("films.id"), nullable=False)
    # Legacy single person FK (still written for PICK nominations with one person)
    person_id = Column(Integer, ForeignKey("persons.id"), nullable=True)
    # Renamed from song / song_url
    item = Column(String, nullable=True)
    item_url = Column(String, nullable=True)
    nomination = relationship("Nomination", back_populates="nominees")
    film = relationship("Film", back_populates="nominees")
    person = relationship("Person", back_populates="nominees")
    votes = relationship("Vote", back_populates="nominee")
    persons = relationship("NomineePerson", back_populates="nominee",
                           cascade="all, delete-orphan")

    @property
    def all_persons(self):
        """Return NomineePerson list if populated, else wrap legacy person_id."""
        if self.persons:
            return [np.person for np in self.persons]
        if self.person:
            return [self.person]
        return []

    @property
    def persons_label(self) -> str:
        """Human-readable comma-joined person names."""
        names = [p.name for p in self.all_persons]
        return ", ".join(names)


class Voter(Base):
    __tablename__ = "voters"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    voted_at = Column(DateTime, nullable=True)
    draft = Column(JSON, nullable=True)
    votes = relationship("Vote", back_populates="voter")
    rankings = relationship("Ranking", back_populates="voter")


class Vote(Base):
    __tablename__ = "votes"
    id = Column(Integer, primary_key=True)
    voter_id = Column(Integer, ForeignKey("voters.id"), nullable=False)
    nominee_id = Column(Integer, ForeignKey("nominees.id"), nullable=False)
    voter = relationship("Voter", back_populates="votes")
    nominee = relationship("Nominee", back_populates="votes")
    __table_args__ = (
        UniqueConstraint("voter_id", "nominee_id", name="uq_vote_voter_nominee"),
    )


class Ranking(Base):
    __tablename__ = "rankings"
    id = Column(Integer, primary_key=True)
    voter_id = Column(Integer, ForeignKey("voters.id"), nullable=False)
    nomination_id = Column(Integer, ForeignKey("nominations.id"), nullable=False)
    film_id = Column(Integer, ForeignKey("films.id"), nullable=False)
    rank = Column(Integer, nullable=False)
    voter = relationship("Voter", back_populates="rankings")
    film = relationship("Film", back_populates="rankings")
