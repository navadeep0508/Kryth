"""Data models for authentication and authorization."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Set, List
import uuid


class Role(Enum):
    """User roles with escalating privileges."""
    GUEST = "guest"           # No auth, limited access
    USER = "user"             # Authenticated basic user
    MODERATOR = "moderator"   # Elevated privileges
    ADMIN = "admin"           # Full system access
    SYSTEM = "system"         # Internal system operations


class Permission(Enum):
    """Granular permissions for fine-grained access control."""
    # User management
    USER_READ = "user:read"
    USER_CREATE = "user:create"
    USER_UPDATE = "user:update"
    USER_DELETE = "user:delete"
    USER_LIST = "user:list"

    # Session management
    SESSION_CREATE = "session:create"
    SESSION_READ = "session:read"
    SESSION_DELETE = "session:delete"

    # Agent operations
    AGENT_RUN = "agent:run"
    AGENT_SPAWN = "agent:spawn"
    AGENT_KILL = "agent:kill"

    # File operations
    FILE_READ = "file:read"
    FILE_WRITE = "file:write"
    FILE_DELETE = "file:delete"

    # Command execution
    COMMAND_RUN = "command:run"
    COMMAND_PRIVILEGED = "command:privileged"

    # System configuration
    CONFIG_READ = "config:read"
    CONFIG_WRITE = "config:write"

    # Browser automation
    BROWSER_USE = "browser:use"

    # Git operations
    GIT_OPERATIONS = "git:operations"


# Role → Permission mapping
ROLE_PERMISSIONS: dict[Role, Set[Permission]] = {
    Role.GUEST: {
        Permission.USER_READ,  # Can read own user data
        Permission.SESSION_READ,  # Can read own session
    },
    Role.USER: {
        Permission.USER_READ,
        Permission.SESSION_CREATE,
        Permission.SESSION_READ,
        Permission.SESSION_DELETE,
        Permission.AGENT_RUN,
        Permission.FILE_READ,
        Permission.COMMAND_RUN,
        Permission.CONFIG_READ,
    },
    Role.MODERATOR: {
        Permission.USER_READ,
        Permission.USER_LIST,
        Permission.SESSION_CREATE,
        Permission.SESSION_READ,
        Permission.SESSION_DELETE,
        Permission.AGENT_RUN,
        Permission.AGENT_SPAWN,
        Permission.FILE_READ,
        Permission.FILE_WRITE,
        Permission.COMMAND_RUN,
        Permission.CONFIG_READ,
        Permission.BROWSER_USE,
        Permission.GIT_OPERATIONS,
    },
    Role.ADMIN: {
        Permission.USER_READ,
        Permission.USER_CREATE,
        Permission.USER_UPDATE,
        Permission.USER_DELETE,
        Permission.USER_LIST,
        Permission.SESSION_CREATE,
        Permission.SESSION_READ,
        Permission.SESSION_DELETE,
        Permission.AGENT_RUN,
        Permission.AGENT_SPAWN,
        Permission.AGENT_KILL,
        Permission.FILE_READ,
        Permission.FILE_WRITE,
        Permission.FILE_DELETE,
        Permission.COMMAND_RUN,
        Permission.COMMAND_PRIVILEGED,
        Permission.CONFIG_READ,
        Permission.CONFIG_WRITE,
        Permission.BROWSER_USE,
        Permission.GIT_OPERATIONS,
    },
    Role.SYSTEM: set(),  # System role has special handling
}


@dataclass
class User:
    """User account with credentials and metadata."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    username: str = ""
    email: str = ""
    password_hash: str = ""  # argon2 hash
    role: Role = Role.USER
    is_active: bool = True
    is_verified: bool = False
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_login: Optional[datetime] = None
    metadata: dict = field(default_factory=dict)  # flexible storage

    def to_dict(self) -> dict:
        """Serialize user for storage (excludes password_hash)."""
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "role": self.role.value,
            "is_active": self.is_active,
            "is_verified": self.is_verified,
            "created_at": self.created_at.isoformat(),
            "last_login": self.last_login.isoformat() if self.last_login else None,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "User":
        """Deserialize user from storage."""
        user = cls(
            id=data["id"],
            username=data["username"],
            email=data["email"],
            password_hash=data["password_hash"],
            role=Role(data["role"]),
            is_active=data.get("is_active", True),
            is_verified=data.get("is_verified", False),
            metadata=data.get("metadata", {}),
        )
        if data.get("created_at"):
            user.created_at = datetime.fromisoformat(data["created_at"])
        if data.get("last_login"):
            user.last_login = datetime.fromisoformat(data["last_login"])
        return user


@dataclass
class Session:
    """Active user session with JWT token."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = ""
    token: str = ""  # JWT token
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime = field(default_factory=lambda: datetime.utcnow() + timedelta(days=7))
    is_active: bool = True
    last_used: Optional[datetime] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

    def is_expired(self) -> bool:
        """Check if session has expired."""
        return datetime.utcnow() > self.expires_at

    def to_dict(self) -> dict:
        """Serialize session for storage."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "token": self.token,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "is_active": self.is_active,
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        """Deserialize session from storage."""
        session = cls(
            id=data["id"],
            user_id=data["user_id"],
            token=data["token"],
            is_active=data.get("is_active", True),
            ip_address=data.get("ip_address"),
            user_agent=data.get("user_agent"),
        )
        if data.get("created_at"):
            session.created_at = datetime.fromisoformat(data["created_at"])
        if data.get("expires_at"):
            session.expires_at = datetime.fromisoformat(data["expires_at"])
        if data.get("last_used"):
            session.last_used = datetime.fromisoformat(data["last_used"])
        return session