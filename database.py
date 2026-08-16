import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Loads variables from the .env file in the project root into the
# process environment, so os.getenv() below can see them.
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL is None:
    raise RuntimeError(
        "DATABASE_URL is not set. Check that a .env file exists "
        "in the project root and that it defines DATABASE_URL."
    )

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# FastAPI dependency — inject with `db: Session = Depends(get_db)` in any route.
# Opens one connection per request and always closes it, even if the route raises.
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()