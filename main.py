from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware


from database import get_db  # <-- this is the import you asked about
from routers.salesreps import router as sales_reps_router
from routers.branches import router as branches_router
from routers.overview import router as overview_router
from routers.forecast import router as forecast_router
from routers.leaderboard import router as leaderboard_router
from routers.deliveries import router as deliveries_router
from routers.leadaging import router as lead_aging_router
from routers.whatif import router as whatif_router
from routers.anomaly import router  as anomoly_router

app = FastAPI(title="DealerPulse API")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        # "http://localhost:5173",  # Vite dev server
        # "http://localhost:3000",  # Alternative dev port
        # "http://localhost:8000",  # Backend same-origin
        "https://dealership-dashboard-five.vercel.app", #vercel
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sales_reps_router)
app.include_router(branches_router)
app.include_router(overview_router)
app.include_router(forecast_router)
app.include_router(leaderboard_router)
app.include_router(deliveries_router)
app.include_router(lead_aging_router)
app.include_router(whatif_router)
app.include_router(anomoly_router)


@app.get("/")
def root():
    return {"status": "ok"}


