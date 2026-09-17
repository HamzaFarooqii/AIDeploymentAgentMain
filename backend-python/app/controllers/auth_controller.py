from fastapi import HTTPException, status
from datetime import datetime
from pymongo.errors import DuplicateKeyError
from ..config.database import db
from ..models.user import UserCreate, UserLogin, UserResponse, Token
from ..utils.auth import get_password_hash, verify_password, create_access_token
import os


async def register_user_handler(user_data: UserCreate):
    """Register a new user"""
    try:
        users_collection = db.get_collection("users")
        
        # Check if username exists
        existing_user = await users_collection.find_one({"username": user_data.username})
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already registered"
            )
        
        # Check if email exists
        existing_email = await users_collection.find_one({"email": user_data.email})
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )
        
        # Create user workspace folder
        user_workspace = os.path.join("uploads", f"user_{user_data.username}")
        os.makedirs(user_workspace, exist_ok=True)
        
        # Hash password - limit to 72 bytes for bcrypt compatibility
        hashed_password = get_password_hash(user_data.password[:72])
        
        # Create user document
        user_doc = {
            "username": user_data.username,
            "email": user_data.email,
            "hashed_password": hashed_password,
            "full_name": user_data.full_name,
            "is_active": True,
            "is_admin": False,
            "workspace_path": user_workspace,
            "created_at": datetime.now(),
            "updated_at": datetime.now()
        }
        
        try:
            result = await users_collection.insert_one(user_doc)
        except DuplicateKeyError as e:
            # Belt-and-suspenders: the find_one checks above already cover the
            # common case, but they're not atomic with this insert, so two
            # concurrent registrations can both pass the checks and race here.
            # The unique indexes on users.username/email are what actually
            # enforce uniqueness; translate the resulting DB error into a
            # clean HTTP response instead of a generic 500.
            key_pattern = getattr(e, "details", None) or {}
            key_pattern = key_pattern.get("keyPattern", {})
            if "username" in key_pattern:
                detail = "Username already registered"
            elif "email" in key_pattern:
                detail = "Email already registered"
            else:
                detail = "Username or email already registered"
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=detail
            )

        print(f"✅ User registered: {user_data.username}")
        
        return {
            "success": True,
            "message": "User registered successfully",
            "user": {
                "user_id": str(result.inserted_id),
                "username": user_data.username,
                "email": user_data.email,
                "full_name": user_data.full_name
            }
        }
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Registration error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Registration failed: {str(e)}"
        )


async def login_user_handler(user_credentials: UserLogin):
    """Login user and return JWT token"""
    try:
        users_collection = db.get_collection("users")
        
        # Find user
        user = await users_collection.find_one({"username": user_credentials.username})
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password"
            )
        
        # Verify password - limit to 72 bytes for bcrypt compatibility
        if not verify_password(user_credentials.password[:72], user["hashed_password"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password"
            )
        
        if not user.get("is_active", True):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is inactive"
            )
        
        # Create access token with user data embedded
        access_token = create_access_token(data={
            "sub": str(user["_id"]),
            "username": user["username"],
            "email": user["email"]
        })
        
        # User response
        user_response = UserResponse(
            user_id=str(user["_id"]),
            username=user["username"],
            email=user["email"],
            full_name=user.get("full_name"),
            is_active=user["is_active"],
            created_at=user["created_at"]
        )
        
        print(f"✅ User logged in: {user['username']}")
        
        return Token(
            access_token=access_token,
            user=user_response
        )
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Login error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Login failed: {str(e)}"
        )


async def get_current_user_info_handler(current_user: dict):
    """Get current user information"""
    return {
        "success": True,
        "user": {
            "user_id": str(current_user["_id"]),
            "username": current_user["username"],
            "email": current_user["email"],
            "full_name": current_user.get("full_name"),
            "is_active": current_user["is_active"],
            "is_admin": current_user.get("is_admin", False),
            "workspace_path": current_user.get("workspace_path"),
            "created_at": current_user.get("created_at")  # Fixed: use .get() for JWT-only auth
        }
    }