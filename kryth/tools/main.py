from fastapi import FastAPI, HTTPException, status
from typing import List
from models import TodoCreate, TodoUpdate, TodoResponse
from crud import create_todo, get_todos, get_todo_by_id, update_todo, delete_todo

app = FastAPI(title="Todo API", description="CRUD operations for Todo items")


@app.post("/todos", response_model=TodoResponse, status_code=status.HTTP_201_CREATED)
def create_todo_endpoint(todo: TodoCreate):
    """Create a new todo item."""
    return create_todo(todo)


@app.get("/todos", response_model=List[TodoResponse], status_code=status.HTTP_200_OK)
def get_todos_endpoint():
    """Get all todo items."""
    return get_todos()


@app.get("/todos/{todo_id}", response_model=TodoResponse, status_code=status.HTTP_200_OK)
def get_todo_endpoint(todo_id: int):
    """Get a specific todo item by ID."""
    todo = get_todo_by_id(todo_id)
    if not todo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Todo with id {todo_id} not found"
        )
    return todo


@app.put("/todos/{todo_id}", response_model=TodoResponse, status_code=status.HTTP_200_OK)
def update_todo_endpoint(todo_id: int, todo_update: TodoUpdate):
    """Update a todo item."""
    todo = update_todo(todo_id, todo_update)
    if not todo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Todo with id {todo_id} not found"
        )
    return todo


@app.delete("/todos/{todo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_todo_endpoint(todo_id: int):
    """Delete a todo item."""
    deleted = delete_todo(todo_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Todo with id {todo_id} not found"
        )
    return None
