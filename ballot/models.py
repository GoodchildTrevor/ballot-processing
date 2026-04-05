import enum
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, ForeignKey, DateTime,
    Enum as SAEnum, UniqueConstraint
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
    nominees = relationship("Nominee", back_populates="film")


class Person(Base):
    __tablename__ = "persons"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    nominees = relationship("Nominee", back_populates="person")


class Nomination(Base):
    __tablename__ = "nominations"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    type = Column(SAEnum(NominationType), nullable=False)
    pick_limit = Column(Integer, nullable=True)   # NULL for RANK; 1-5 for PICK
    year_filter = Column(Integer, nullable=True)  # NULL = no filter; int = only films of that year
    nominees = relationship("Nominee", back_populates="nomination")


class Nominee(Base):
    __tablename__ = "nominees"
    id = Column(Integer, primary_key=True)
    nomination_id = Column(Integer, ForeignKey("nominations.id"), nullable=False)
    film_id = Column(Integer, ForeignKey("films.id"), nullable=False)
    person_id = Column(Integer, ForeignKey("persons.id"), nullable=True)
    nomination = relationship("Nomination", back_populates="nominees")
    film = relationship("Film", back_populates="nominees")
    person = relationship("Person", back_populates="nominees")
    votes = relationship("Vote", back_populates="nominee")


class Voter(Base):
    __tablename__ = "voters"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    voted_at = Column(DateTime, nullable=True)
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
