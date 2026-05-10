import os
import hashlib
import bcrypt
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.database import get_db, User
from app.core.config import JWT_SECRET_KEY, JWT_ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES

# --- SECURITY CONFIG ---
SECRET_KEY = JWT_SECRET_KEY
ALGORITHM = JWT_ALGORITHM
# This line configures FastAPI to automatically extract JWT tokens from incoming requests and associate them with authentication dependencies, but it does not validate them.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/auth/token")
# function to hash the plain password into a hashed password
def get_password_hash(password: str) -> str:
    """Secure Bcrypt hashing"""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

#function to verify the plain password against the hashed password
def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against Bcrypt hash."""
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))

#function to create JWT token and add claims to it
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

#function to derive tenant id from team code for Qdrant isolation
def derive_tenant_id(team_code: str) -> str:
    """Consistently maps a Team Code to a Tenant ID for Qdrant isolation."""
    return hashlib.sha256(team_code.strip().lower().encode()).hexdigest()

#function to get current user from token
async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
        
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise credentials_exception
    return user
