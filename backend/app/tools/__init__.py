from .work import (
    create_milestone,
    update_milestone,
    list_milestones,
    create_task,
    update_task,
    list_tasks,
)
from .collab import (
    ask_question,
    answer_question,
    list_questions,
    record_decision,
    list_decisions,
    post_standup,
    list_standups,
    save_note,
    search_notes,
)
from .schedule import schedule_event, list_events, cancel_event

ALL_TOOLS = [
    create_milestone,
    update_milestone,
    list_milestones,
    create_task,
    update_task,
    list_tasks,
    ask_question,
    answer_question,
    list_questions,
    record_decision,
    list_decisions,
    post_standup,
    list_standups,
    save_note,
    search_notes,
    schedule_event,
    list_events,
    cancel_event,
]
