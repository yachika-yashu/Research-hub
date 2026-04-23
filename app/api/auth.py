import os
import uuid
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_sso.sso.google import GoogleSSO
from sqlalchemy.orm import Session

from app.core.database import get_db, User
from app.core.auth import (
    get_password_hash, verify_password, create_access_token, 
    ACCESS_TOKEN_EXPIRE_MINUTES, derive_tenant_id,
    SECRET_KEY, ALGORITHM
)
from app.core.config import GOOGLE_OAUTH_ALLOW_INSECURE_HTTP, IS_PRODUCTION
from app.schemas.auth import UserCreate, UserResponse, Token

router = APIRouter()

# Initialize Google SSO
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
# This URI must match what's configured in Google Cloud Console
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback")

sso = GoogleSSO(
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    redirect_uri=GOOGLE_REDIRECT_URI,
    # Local callback testing can use HTTP, but production must terminate over HTTPS.
    allow_insecure_http=GOOGLE_OAUTH_ALLOW_INSECURE_HTTP
)

@router.post("/register", response_model=UserResponse)
def register_user(user_in: UserCreate, db: Session = Depends(get_db)):
    """Registers a new researcher and assigns them to a Team (Tenant)."""
    db_user = db.query(User).filter(User.username == user_in.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    tenant_id = derive_tenant_id(user_in.team_code)
    
    new_user = User(
        username=user_in.username,
        hashed_password=get_password_hash(user_in.password),
        team_code=user_in.team_code,
        tenant_id=tenant_id
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

@router.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(), 
    db: Session = Depends(get_db)
):
    """Secure OAuth2-compatible login for JWT generation."""
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username, "team_code": user.team_code}, 
        expires_delta=access_token_expires
    )
    
    return {
        "access_token": access_token, 
        "token_type": "bearer",
        "username": user.username,
        "team_code": user.team_code,
        "tenant_id": user.tenant_id
    }

@router.get("/google/login")
async def google_login():
    """Redirects the user to Google login page."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="Google SSO is not configured.")
    with sso:
        return await sso.get_login_redirect()

@router.get("/google/callback")
async def google_callback(request: Request, db: Session = Depends(get_db)):
    """Handles the callback from Google and issues a local JWT."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="Google SSO is not configured.")
    with sso:
        google_user = await sso.verify_and_process(request)
    
    if not google_user:
        raise HTTPException(status_code=400, detail="Google authentication failed")

    # Check if user exists by email (using email as username for SSO)
    user = db.query(User).filter(User.username == google_user.email).first()
    
    if not user:
        # Create a new researcher on the fly with a default team
        default_team = "google_research_group"
        user = User(
            username=google_user.email,
            hashed_password=get_password_hash(str(uuid.uuid4())), # Random password for SSO users
            team_code=default_team,
            tenant_id=derive_tenant_id(default_team)
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username, "team_code": user.team_code}, 
        expires_delta=access_token_expires
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "email": user.username,
        "tenant_id": user.tenant_id
    }
