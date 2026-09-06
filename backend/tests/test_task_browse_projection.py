"""Browse ships list fields, while task details and edits preserve the full record."""

from datetime import timedelta

BROWSE_FIELDS = {
    "id",
    "title",
    "status",
    "priority",
    "assignee",
    "due_date",
    "completed_at",
    "forge_url",
    "visibility",
    "crew_id",
}


def test_browse_projection_preserves_detail_and_inline_edit_fields(client, fresh_db):
    from app.services import users, work

    users.ensure_user("ava")
    description = "Detailed acceptance evidence. " * 100
    due = (fresh_db.today() + timedelta(days=1)).isoformat()
    task = work.create_task(
        "Review delivery", description=description, assignee="ava", due_date=due, actor="tester"
    )
    response = client.get("/api/tasks/browse")
    assert response.status_code == 200
    row = response.json()["open"][0]
    assert set(row) == BROWSE_FIELDS
    assert row["id"] == task["id"] and row["assignee"] == "ava" and row["due_date"] == due
    assert len(response.content) < len(client.get("/api/tasks").content) / 2

    details = client.get(f"/api/tasks/{task['id']}").json()
    assert details["description"] == description
    edited = client.patch(
        f"/api/tasks/{task['id']}",
        json={"title": "Review shipped delivery", "assignee": "ava", "due_date": due},
    )
    assert edited.status_code == 200
    assert client.get(f"/api/tasks/{task['id']}").json()["description"] == description

    work.update_task(task["id"], status="done", actor="tester")
    done = client.get("/api/tasks/browse").json()["done"][0]
    assert set(done) == BROWSE_FIELDS
    assert done["completed_at"] and done["status"] == "done"


def test_browse_projection_runs_after_visibility_and_workplace_policy(client, monkeypatch):
    from app.services import engagements, projection_policy, users, work

    users.ensure_user("ava")
    denied_project = engagements.create_engagement("Restricted project", project_class="regulated")
    work.create_task(
        "Hidden workspace policy",
        description="not for this caller",
        engagement_id=denied_project["id"],
        actor="tester",
    )
    work.create_task("Private task", actor="ava", visibility="private")
    visible = work.create_task("Permitted task", actor="tester")
    original = projection_policy.ProjectionPolicy.permits
    evaluated = []

    def permits(self, entity, entity_id, attributes):
        if entity == "task":
            evaluated.append(entity_id)
            if attributes.get("project_type") == "regulated":
                return False
        return original(self, entity, entity_id, attributes)

    monkeypatch.setattr(projection_policy.ProjectionPolicy, "permits", permits)
    response = client.get("/api/tasks/browse")
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["open"]] == [visible["id"]]
    assert visible["id"] in evaluated
    assert set(response.json()["open"][0]) == BROWSE_FIELDS
