"""Read-only REST endpoints for the dashboard. Mutations flow through the agent."""

from fastapi import APIRouter

from .. import db

router = APIRouter(prefix="/api")


@router.get("/milestones")
def milestones():
    return db.query("SELECT * FROM milestones ORDER BY due_date IS NULL, due_date, id")


@router.get("/tasks")
def tasks():
    return db.query(
        "SELECT t.*, m.title AS milestone_title FROM tasks t"
        " LEFT JOIN milestones m ON m.id = t.milestone_id"
        " ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, t.id"
    )


@router.get("/questions")
def questions():
    return db.query("SELECT * FROM questions ORDER BY status = 'answered', id DESC")


@router.get("/decisions")
def decisions():
    return db.query("SELECT * FROM decisions ORDER BY id DESC LIMIT 50")


@router.get("/standups")
def standups():
    return db.query("SELECT * FROM standups ORDER BY id DESC LIMIT 30")


@router.get("/events")
def events():
    return db.query("SELECT * FROM events ORDER BY starts_at LIMIT 50")


@router.get("/notes")
def notes():
    return db.query("SELECT * FROM notes ORDER BY id DESC LIMIT 50")


@router.get("/activity")
def activity():
    return db.query("SELECT * FROM activity ORDER BY id DESC LIMIT 50")
