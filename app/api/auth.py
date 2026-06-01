import os
import uuid
import urllib.parse
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_sso.sso.google import GoogleSSO
from sqlalchemy.orm import Session

from app.core.database import get_db, User
from app.core.auth import (
    get_password_hash, verify_password, create_access_token,
    ACCESS_TOKEN_EXPIRE_MINUTES, derive_tenant_id,
)
from app.core.config import GOOGLE_OAUTH_ALLOW_INSECURE_HTTP
from app.schemas.auth import UserCreate, UserResponse, Token

router = APIRouter()

# Initialize Google SSO
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
# This URI must match what's configured in Google Cloud Console
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback")
# Where to bounce the user back to after we issue the JWT — the Streamlit app.
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:8501")

sso = GoogleSSO(
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    redirect_uri=GOOGLE_REDIRECT_URI,
    # Local callback testing can use HTTP, but production must terminate over HTTPS.
    allow_insecure_http=GOOGLE_OAUTH_ALLOW_INSECURE_HTTP
)
#endpoint to register a new researcher
@router.post("/register", response_model=UserResponse)
def register_user(user_in: UserCreate, db: Session = Depends(get_db)):
    """Registers a new researcher and assigns them to a Team (Tenant)."""
    db_user = db.query(User).filter(User.username == user_in.username).first() #check if username already exists
    if db_user:
        raise HTTPException(status_code=400, detail="Username already registered")

    tenant_id = derive_tenant_id(user_in.team_code) #derive tenant id from team code

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

#login endpoint to generate JWT token
@router.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(), #OAuth2PasswordRequestForm expects username and password in the request body using form-data encoding so frontend must send data in this format or else it will throw error
    db: Session = Depends(get_db)
):
    """Secure OAuth2-compatible login for JWT generation."""
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password): #this compares Plain password (input)Hashed password (stored in DB)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    #If valid -> create JWT token using create_access_token function which is imported from app.core.auth
    access_token = create_access_token(
        data={"sub": user.username, "team_code": user.team_code}, #sub and team_code are the claims which are added to the JWT token
        expires_delta=access_token_expires
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "username": user.username,
        "team_code": user.team_code,
        "tenant_id": user.tenant_id
    }
#google login endpoint
@router.get("/google/login")#Redirect user to Google login page ie/google/login
async def google_login():
    """Redirects the user to Google login page."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="Google SSO is not configured.")
    with sso:
        return await sso.get_login_redirect() # redirect user to google login page, not finish login process, after this it completely on google weather to login,register and then finally call back your app to finish login process, i have added /google/callback route to handle this callback
#get_login_redirect() = START login
#verify_and_process() = FINISH login

def _team_from_email(email: str) -> str:
    """Derive a team code from the user's email domain so each org gets its own
    tenant by default. Personal-mail domains fall back to a shared bucket; users
    can be moved into a real team later via an admin flow."""
    domain = (email.split("@", 1)[1] if "@" in email else "").lower()
    personal = {"gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com"}
    if not domain or domain in personal:
        return "google_personal"
    return domain.replace(".", "_")


@router.get("/google/callback")
async def google_callback(request: Request, db: Session = Depends(get_db)):
    """Verifies Google's callback, upserts the user, and redirects back to the
    Streamlit dashboard with the JWT in the URL fragment-style query string."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="Google SSO is not configured.")
    with sso:
        google_user = await sso.verify_and_process(request)

    if not google_user:
        raise HTTPException(status_code=400, detail="Google authentication failed")

    user = db.query(User).filter(User.username == google_user.email).first()
    if not user:
        team_code = _team_from_email(google_user.email)
        user = User(
            username=google_user.email,
            hashed_password=get_password_hash(str(uuid.uuid4())),
            team_code=team_code,
            tenant_id=derive_tenant_id(team_code),
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    access_token = create_access_token(
        data={"sub": user.username, "team_code": user.team_code},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    params = urllib.parse.urlencode({
        "token": access_token,
        "username": user.username,
        "tenant_id": user.tenant_id,
        "team_code": user.team_code,
    })
    return RedirectResponse(url=f"{DASHBOARD_URL}/?{params}", status_code=302)
