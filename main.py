from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db  # <-- this is the import you asked about
from routers.salesreps import router as sales_reps_router
from routers.branches import router as branches_router



app = FastAPI(title="DealerPulse API")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev server
        "http://localhost:3000",  # Alternative dev port
        "http://localhost:8000",  # Backend same-origin
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sales_reps_router)
app.include_router(branches_router)



@app.get("/")
def root():
    return {"status": "ok"}


