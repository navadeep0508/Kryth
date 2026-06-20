from typing import List, Optional
from datetime import datetime
from models import TodoCreate, TodoUpdate, TodoResponse

# In-memory storage
todos_db: List[dict] = []
next_id: int = 1


def create_todo(todo: TodoCreate) -> TodoResponse:
    """Create a new todo item."""
    global next_id
    now = datetime.utcnow()
    new_todo = {
        "id": next_id,
        "title": todo.title,
        "description": todo.description,
        "completed": todo.completed,
        "created_at": now,
        "updated_at": now
    }
    todos_db.append(new_todo)
    next_id += 1
    return TodoResponse(**new_todo)


def get_todos() -> List[TodoResponse]:
    """Get all todo items."""
    return [TodoResponse(**todo) for todo in todos_db]


def get_todo_by_id(todo_id: int) -> Optional[TodoResponse]:
    """Get a specific todo item by ID."""
    for todo in todos_db:
        if todo["id"] == todo_id:
            return TodoResponse(**todo)
    return None


def update_todo(todo_id: int, todo_update: TodoUpdate) -> Optional[TodoResponse]:
    """Update a todo item."""
    for todo in todos_db:
        if todo["id"] == todo_id:
            update_data = todo_update.model_dump(exclude_unset=True)
            for field, value in update_data.items():
                todo[field] = value
            todo["updated_at"] = datetime.utcnow()
            return TodoResponse(**todo)
    return None


def delete_todo(todo_id: int) -> bool:
    """Delete a todo item. Returns True if deleted, False if not found."""
    for i, todo in enumerate(todos_db):
        if todo["id"] == todo_id:
            todos_db.pop(i)
            return True
    return False
