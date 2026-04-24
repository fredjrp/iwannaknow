from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import json
import os

app = FastAPI()

# IMPORTANT: This allows your Flutter Web app to access the backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, replace with your Flutter Web URL
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/layout/{screen_name}")
async def get_layout(screen_name: str):
    file_path = f"layouts/{screen_name}.json"
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            return json.load(f)
    return {"error": "Layout not found", "status": 404}

@app.get("/")
async def health_check():
    return {"status": "Delo Backend is Active"}