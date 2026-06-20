from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional


class TodoCreate(BaseModel):
    """Model for creating a new todo item."""
    title: str = Field(..., min_length=1, max_length=200, description="Todo title")
    description: Optional[str] = Field(None, max_length=1000, description="Todo description")
    completed: bool = Field(default=False, description="Completion status")


class TodoUpdate(BaseModel):
    """Model for updating an existing todo item."""
    title: Optional[str] = Field(None, min_length=1, max_length=200, description="Todo title")
    description: Optional[str] = Field(None, max_length=1000, description="Todo description")
    completed: Optional[bool] = Field(None, description="Completion status")


class TodoResponse(BaseModel):
    """Model for todo item responses."""
    id: int = Field(..., description="Unique todo identifier")
    title: str = Field(..., description="Todo title")
    description: Optional[str] = Field(None, description="Todo description")
    completed: bool = Field(..., description="Completion status")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
