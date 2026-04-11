# Copyright (c) 2026 Mathieu Cadi — Openema SARL
# Released under the MIT License — https://github.com/openema/oeil
# Application: Oeil — Open-source AI edge camera surveillance
# Date: April 11, 2026

"""Oeil — Auth Router"""
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel
from config import settings

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")
ALGORITHM = "HS256"


def create_token(data: dict) -> str:
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(minutes=settings.OW_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(payload, settings.OW_SECRET_KEY, algorithm=ALGORITHM)


async def current_user(token: str = Depends(oauth2_scheme)) -> str:
    try:
        payload = jwt.decode(token, settings.OW_SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if username != settings.OW_ADMIN_USER:
            raise HTTPException(401, "Invalid token")
        return username
    except JWTError:
        raise HTTPException(401, "Invalid token")


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/token")
async def token(form: OAuth2PasswordRequestForm = Depends()):
    if form.username != settings.OW_ADMIN_USER or form.password != settings.OW_ADMIN_PASS:
        raise HTTPException(401, "Invalid credentials")
    return {"access_token": create_token({"sub": form.username}), "token_type": "bearer"}


@router.post("/login")
async def login(body: LoginBody):
    if body.username != settings.OW_ADMIN_USER or body.password != settings.OW_ADMIN_PASS:
        raise HTTPException(401, "Invalid credentials")
    return {
        "access_token": create_token({"sub": body.username}),
        "token_type": "bearer",
        "username": body.username,
    }


@router.get("/me")
async def me(user=Depends(current_user)):
    return {"username": user, "role": "admin"}
