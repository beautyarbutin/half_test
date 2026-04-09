import asyncio
import json
import logging
import random
from pathlib import PurePosixPath
from datetime import datetime, timezone, timedelta

from sqlalchemy.orm import Session

from database import SessionLocal
from models import Agent, Project, ProjectPlan, Task, TaskEvent
from services import git_service
from services.path_service import ExpectedOutputPathError, normalize_expected_output_path
from services.polling_config_service import (
    get_project_polling_settings,
)

logger = logging.getLogger("half.poller")


def _normalize_collab_dir(project: Project) -> str:
    return (project.collaboration_dir or "").strip("/")


def _plan_source_path(project: Project, plan: ProjectPlan) -> str:
    if plan.source_path:
        return plan.source_path.lstrip("/")
    base = _normalize_collab_dir(project)
    if base:
        return f"{base}/plan.json"
    return "plan.json"


def _task_result_path(project: Project, task: Task) -> str:
    """Return the repo-root-relative path where the task result file is expected.

    Honors task.expected_output_path (set by plan finalize, already prefixed
    with collaboration_dir if applicable). Falls back to default convention
    relative to collaboration_dir for legacy tasks. Always strips leading
    slashes so the result can be safely os.path.join'd with repo_dir.
    """
    base = _normalize_collab_dir(project)
    return normalize_expected_output_path(
        task.expected_output_path,
        default_path=f"outputs/{task.task_code}/result.json",
        collaboration_dir=base,
        strict=True,
    )


def _task_usage_path(project: Project, task: Task) -> str:
    """Return the usage.json path, derived from the result path's directory."""
    result_path = _task_result_path(project, task)
    if "." not in PurePosixPath(result_path).name:
        return f"{result_path.rstrip('/')}/usage.json"
    # Replace the filename portion with usage.json
    if "/" in result_path:
        return result_path.rsplit("/", 1)[0] + "/usage.json"
    return "usage.json"


def _is_json_result_path(result_path: str) -> bool:
    return PurePosixPath(result_path).suffix.casefold() == ".json"


_RESULT_FALLBACK_SUFFIXES = (".md", ".json", ".txt", ".yaml", ".yml")


def _detect_task_result(project: Project, task: Task) -> tuple[bool, str | None]:
    """Detect a task result via multiple strategies.

    Strategies, in order (T1-ANALYZE F-P1-06):
      1. Exact result_path (JSON path matches by task_code; other paths by existence)
      2. Common-suffix completion when expected_output is given without an extension
      3. Sibling files in the same directory whose stem starts with the expected stem
      4. Directory existence with at least one non-empty file inside
    Hits return the actually-matched path so it can be persisted to result_file_path.
    """
    expected_result_path = _task_result_path(project, task)
    result_path = task.result_file_path or expected_result_path

    # Strategy 1: exact path
    if _is_json_result_path(result_path):
        result_data = git_service.read_json(
            project.id,
            result_path,
            git_repo_url=project.git_repo_url,
            prefer_remote=True,
        )
        if result_data and result_data.get("task_code") == task.task_code:
            return True, result_path
    else:
        if git_service.file_exists(
            project.id,
            result_path,
            git_repo_url=project.git_repo_url,
            prefer_remote=True,
        ):
            return True, result_path

    # Strategy 2: suffix completion
    if "." not in PurePosixPath(result_path).name:
        for suffix in _RESULT_FALLBACK_SUFFIXES:
            candidate = result_path + suffix
            if git_service.file_exists(
                project.id,
                candidate,
                git_repo_url=project.git_repo_url,
                prefer_remote=True,
            ):
                return True, candidate

    # Strategy 3: same-directory prefix match (handles "auto-renamed" / truncated outputs)
    parent = str(PurePosixPath(result_path).parent)
    stem = PurePosixPath(result_path).stem
    if stem and parent and parent != ".":
        try:
            entries = git_service.list_dir(
                project.id,
                parent,
                git_repo_url=project.git_repo_url,
                prefer_remote=True,
            )
        except Exception:
            entries = []
        for entry in entries or []:
            entry_path = f"{parent}/{entry}"
            if entry == PurePosixPath(result_path).name:
                continue
            entry_stem = PurePosixPath(entry).stem
            if entry_stem.startswith(stem) or stem.startswith(entry_stem):
                if git_service.file_exists(
                    project.id,
                    entry_path,
                    git_repo_url=project.git_repo_url,
                    prefer_remote=True,
                ):
                    return True, entry_path

    # Strategy 4: directory-as-output with sentinel completion marker.
    #
    # Historically this strategy returned True as soon as the directory contained
    # any non-empty file (`dir_has_content`). That is unsafe for long-running
    # tasks (e.g. frontend/backend scaffolds) that create the output directory
    # and start writing files long before the task is actually done — the task
    # would be marked completed mid-flight. See log/0409.md for the incident.
    #
    # New contract: when expected_output_path points at a directory, the agent
    # must write a sentinel file `result.json` into that directory as the
    # *last* step of the task. The poller only declares completion when this
    # sentinel exists.
    # Only treat suffixless expected_output values as directory-style outputs.
    # This prevents JSON/file outputs from accidentally falling through to the
    # sentinel strategy when their exact-path validation fails.
    if "." not in PurePosixPath(expected_result_path).name:
        sentinel_path = f"{expected_result_path.rstrip('/')}/result.json"
        if git_service.file_exists(
            project.id,
            sentinel_path,
            git_repo_url=project.git_repo_url,
            prefer_remote=True,
        ):
            return True, sentinel_path

    return False, result_path


def _set_task_runtime_error(db: Session, task: Task, now: datetime, message: str, *, needs_attention: bool) -> None:
    task.last_error = message
    task.updated_at = now
    if needs_attention:
        task.status = "needs_attention"
    db.add(TaskEvent(
        task_id=task.id,
        event_type="error",
        detail=message,
    ))


def _set_plan_runtime_error(plan: ProjectPlan, now: datetime, message: str, *, needs_attention: bool) -> None:
    plan.last_error = message
    plan.updated_at = now
    if needs_attention:
        plan.status = "needs_attention"


def poll_project(db: Session, project: Project) -> None:
    if not project.git_repo_url:
        return

    all_tasks = db.query(Task).filter(Task.project_id == project.id).all()
    running_tasks = [task for task in all_tasks if task.status == "running"]

    now = datetime.now(timezone.utc)

    # Get effective polling delay for this project (project-level overrides global)
    polling_settings = get_project_polling_settings(db, project)
    delay_seconds = (
        polling_settings["polling_start_delay_minutes"] * 60
        + polling_settings["polling_start_delay_seconds"]
    )
    delay_threshold = timedelta(seconds=delay_seconds)

    def _delay_satisfied(dispatched_at) -> bool:
        """Return True if enough time has passed since dispatch to start polling."""
        if dispatched_at is None or delay_seconds <= 0:
            return True
        elapsed = now - dispatched_at.replace(tzinfo=timezone.utc)
        return elapsed >= delay_threshold

    running_plans = db.query(ProjectPlan).filter(
        ProjectPlan.project_id == project.id,
        ProjectPlan.status == "running",
    ).all()
    sync_status = git_service.ensure_repo_sync(project.id, project.git_repo_url)
    if sync_status.error:
        sync_message = (
            f"Git sync failed while polling project {project.id}: {sync_status.error}. "
            "HALF will retry automatically; this is not treated as 'result not found'."
        )
        logger.error(sync_message)
        for plan in running_plans:
            if _delay_satisfied(plan.dispatched_at):
                _set_plan_runtime_error(plan, now, sync_message, needs_attention=False)
        for task in running_tasks:
            if _delay_satisfied(task.dispatched_at):
                _set_task_runtime_error(db, task, now, sync_message, needs_attention=False)
        db.commit()
        return

    sync_warning = None
    if sync_status.warnings:
        sync_warning = (
            "Git sync warning: "
            + " | ".join(sync_status.warnings)
            + ". HALF used the latest reachable remote snapshot for detection."
        )
        logger.warning("Project %s polling sync warning: %s", project.id, sync_warning)

    for plan in running_plans:
        # Skip polling this plan if start delay has not elapsed yet
        if not _delay_satisfied(plan.dispatched_at):
            logger.debug(
                "Project %s plan %s polling delayed (waiting %ss after dispatch)",
                project.id, plan.id, delay_seconds,
            )
            continue
        source_path = _plan_source_path(project, plan)
        plan_data = git_service.read_json(
            project.id,
            source_path,
            git_repo_url=project.git_repo_url,
            prefer_remote=True,
        )

        if isinstance(plan_data, dict) and isinstance(plan_data.get("tasks"), list) and plan_data.get("tasks"):
            plan.plan_json = json.dumps(plan_data, ensure_ascii=False, indent=2)
            plan.status = "completed"
            plan.detected_at = now
            plan.last_error = None
            plan.source_path = source_path
            plan.updated_at = now
        elif sync_warning:
            _set_plan_runtime_error(plan, now, sync_warning, needs_attention=False)
        elif plan.dispatched_at:
            elapsed_minutes = (now - plan.dispatched_at.replace(tzinfo=timezone.utc)).total_seconds() / 60
            if elapsed_minutes > 30:
                plan.status = "needs_attention"
                plan.last_error = f"Plan JSON not found at {source_path} after {elapsed_minutes:.1f} minutes"
                plan.updated_at = now

    for task in running_tasks:
        # Skip polling this task if start delay has not elapsed yet
        if not _delay_satisfied(task.dispatched_at):
            logger.debug(
                "Project %s task %s polling delayed (waiting %ss after dispatch)",
                project.id, task.task_code, delay_seconds,
            )
            continue
        try:
            result_detected, result_path = _detect_task_result(project, task)
        except ExpectedOutputPathError as exc:
            _set_task_runtime_error(
                db,
                task,
                now,
                f"Invalid expected_output_path for task {task.task_code}: {exc}",
                needs_attention=True,
            )
            continue

        if result_detected:
            task.status = "completed"
            task.completed_at = now
            task.result_file_path = result_path
            task.last_error = None
            task.updated_at = now
            db.add(TaskEvent(
                task_id=task.id,
                event_type="completed",
                detail=f"Result detected at {result_path}",
            ))
        elif sync_warning:
            _set_task_runtime_error(db, task, now, sync_warning, needs_attention=False)
        elif task.dispatched_at:
            elapsed_minutes = (now - task.dispatched_at.replace(tzinfo=timezone.utc)).total_seconds() / 60
            if elapsed_minutes > (task.timeout_minutes or 10):
                task.status = "needs_attention"
                task.last_error = f"Timeout: result not found at {result_path} after {elapsed_minutes:.1f} minutes"
                task.updated_at = now
                db.add(TaskEvent(
                    task_id=task.id,
                    event_type="timeout",
                    detail=f"Timeout after {elapsed_minutes:.1f} minutes",
                ))

        # Check usage.json
        try:
            usage_path = _task_usage_path(project, task)
        except ExpectedOutputPathError:
            usage_path = None
        if usage_path and git_service.file_exists(
            project.id,
            usage_path,
            git_repo_url=project.git_repo_url,
            prefer_remote=True,
        ):
            task.usage_file_path = usage_path
            if task.assignee_agent_id:
                agent = db.query(Agent).filter(Agent.id == task.assignee_agent_id).first()
                if agent:
                    agent.last_usage_update_at = now
                    agent.updated_at = now

    # Check if all tasks in executing project are completed
    if project.status == "executing":
        if all_tasks and all(t.status in ("completed", "abandoned") for t in all_tasks):
            project.status = "completed"
            project.updated_at = now
    elif project.status == "planning":
        if any(plan.status in ("completed", "final") for plan in db.query(ProjectPlan).filter(ProjectPlan.project_id == project.id).all()):
            project.updated_at = now

    db.commit()


def _compute_next_poll_time(db: Session, project: Project, now: datetime) -> datetime:
    """Compute the next polling time for a project based on its random interval config."""
    settings = get_project_polling_settings(db, project)
    min_interval = max(1, settings["polling_interval_min"])
    max_interval = max(min_interval, settings["polling_interval_max"])
    interval_seconds = random.randint(min_interval, max_interval)
    return now + timedelta(seconds=interval_seconds)


async def polling_loop(interval_seconds: int) -> None:
    """Per-project polling scheduler.

    Each project schedules its own next poll based on its (random) interval
    configured at project level, falling back to global defaults. The main
    loop wakes up frequently (every 2 seconds) and dispatches polling for any
    project whose next_poll_at has been reached.

    The legacy ``interval_seconds`` parameter is kept only for backward
    compatibility with the startup signature; it is no longer used as the
    actual interval, since each project now has its own random interval.
    """
    logger.info(
        "Per-project polling loop started (legacy interval_seconds=%s ignored; "
        "each project now uses its own random interval)",
        interval_seconds,
    )
    # Map project_id -> datetime when this project should be polled next.
    # Newly-discovered projects are polled immediately on the first tick.
    next_poll_at: dict[int, datetime] = {}

    while True:
        try:
            now = datetime.now(timezone.utc)
            db = SessionLocal()
            try:
                projects = db.query(Project).filter(
                    Project.status.in_(("planning", "executing"))
                ).all()
                active_ids = {p.id for p in projects}
                # Drop schedule entries for projects no longer active
                for stale_id in list(next_poll_at.keys()):
                    if stale_id not in active_ids:
                        next_poll_at.pop(stale_id, None)

                for project in projects:
                    scheduled = next_poll_at.get(project.id)
                    if scheduled is not None and scheduled > now:
                        continue  # Not yet time for this project
                    try:
                        poll_project(db, project)
                    except Exception as e:
                        logger.error(f"Error polling project {project.id}: {e}")
                    # Re-fetch settings each time so live config changes take effect
                    next_poll_at[project.id] = _compute_next_poll_time(db, project, now)
                    logger.debug(
                        "Project %s next poll at %s",
                        project.id, next_poll_at[project.id].isoformat(),
                    )
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Polling loop error: {e}")
        # Short tick so we can honor per-project random intervals as low as a few seconds.
        await asyncio.sleep(2)
