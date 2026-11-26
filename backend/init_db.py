from backend.db import engine
from backend.models import Base

if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    from backend.universe import seed
    seed(engine)
    print("Database tables created.")
