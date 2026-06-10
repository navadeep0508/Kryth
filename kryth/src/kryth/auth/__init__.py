"""Authentication and Authorization system for KRYTH.

Provides:
- User management with secure password hashing (argon2-cffi)
- JWT-based session management
- Role-based access control (RBAC)
- CLI commands: /register, /login, /logout, /whoami, /users
"""

from .models import User, Session, Role, Permission
from .security import hash_password, verify_password, create_jwt, decode_jwt
from .storage import UserStorage, SessionStorage
from .rbac import has_permission, require_permission, require_role
from .cli import register_command, login_command, logout_command, whoami_command, users_command

__all__ = [
    "User",
    "Session",
    "Role",
    "Permission",
    "hash_password",
    "verify_password",
    "create_jwt",
    "decode_jwt",
    "UserStorage",
    "SessionStorage",
    "has_permission",
    "require_permission",
    "require_role",
    "register_command",
    "login_command",
    "logout_command",
    "whoami_command",
    "users_command",
]