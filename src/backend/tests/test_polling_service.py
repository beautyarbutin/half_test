import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from database import Base
from models import Project, Task, ProjectPlan
from services.git_service import RepoSyncStatus
from services.polling_service import _task_usage_path, poll_project


class PollingServiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def _seed_running_task(
        self,
        expected_output_path: str,
        status: str = "running",
        *,
        dispatched_minutes_ago: int = 11,
    ) -> tuple[Project, Task]:
        db = self.SessionLocal()
        self.addCleanup(db.close)

        project = Project(
            id=7,
            name="Demo",
            git_repo_url="git@github.com:example-org/example-repo.git",
            collaboration_dir="outputs/proj-7-7b145d",
            status="executing",
        )
        plan = ProjectPlan(
            id=8,
            project_id=7,
            status="final",
        )
        task = Task(
            id=1,
            project_id=7,
            plan_id=8,
            task_code="TASK-001",
            task_name="需求梳理与功能清单",
            status=status,
            expected_output_path=expected_output_path,
            dispatched_at=datetime.now(timezone.utc) - timedelta(minutes=dispatched_minutes_ago),
            timeout_minutes=10,
        )
        db.add_all([project, plan, task])
        db.commit()
        db.refresh(project)
        db.refresh(task)
        return project, task

    def test_poll_project_marks_markdown_output_as_completed_when_file_exists(self):
        project, task = self._seed_running_task("outputs/proj-7-7b145d/TASK-001/requirements.md")

        with patch(
            "services.polling_service.git_service.ensure_repo_sync",
            return_value=RepoSyncStatus(repo_dir="/tmp/repo", fetched=True, pulled=True, remote_ready=True),
        ), patch(
            "services.polling_service.git_service.file_exists",
            side_effect=lambda project_id, relative_path, git_repo_url=None, prefer_remote=False: relative_path == "outputs/proj-7-7b145d/TASK-001/requirements.md",
        ), patch(
            "services.polling_service.git_service.read_json",
            return_value=None,
        ):
            poll_project(self.SessionLocal(), project)

        verify_db = self.SessionLocal()
        self.addCleanup(verify_db.close)
        refreshed = verify_db.query(Task).filter(Task.id == task.id).first()
        self.assertEqual(refreshed.status, "completed")
        self.assertEqual(refreshed.result_file_path, "outputs/proj-7-7b145d/TASK-001/requirements.md")
        self.assertIsNotNone(refreshed.completed_at)

    def test_poll_project_still_requires_task_code_for_json_result(self):
        project, task = self._seed_running_task("outputs/proj-7-7b145d/TASK-001/result.json")

        with patch(
            "services.polling_service.git_service.ensure_repo_sync",
            return_value=RepoSyncStatus(repo_dir="/tmp/repo", fetched=True, pulled=True, remote_ready=True),
        ), patch(
            "services.polling_service.git_service.read_json",
            return_value={"task_code": "TASK-999"},
        ), patch(
            "services.polling_service.git_service.file_exists",
            return_value=True,
        ):
            poll_project(self.SessionLocal(), project)

        verify_db = self.SessionLocal()
        self.addCleanup(verify_db.close)
        refreshed = verify_db.query(Task).filter(Task.id == task.id).first()
        self.assertEqual(refreshed.status, "needs_attention")
        self.assertIsNone(refreshed.result_file_path)
        self.assertIn("Timeout: result not found", refreshed.last_error)

    def test_poll_project_marks_invalid_expected_output_path_as_needs_attention(self):
        project, task = self._seed_running_task("outputs/proj-7-7b145d/代码变更提交")

        with patch(
            "services.polling_service.git_service.ensure_repo_sync",
            return_value=RepoSyncStatus(repo_dir="/tmp/repo", fetched=True, pulled=True, remote_ready=True),
        ):
            poll_project(self.SessionLocal(), project)

        verify_db = self.SessionLocal()
        self.addCleanup(verify_db.close)
        refreshed = verify_db.query(Task).filter(Task.id == task.id).first()
        self.assertEqual(refreshed.status, "needs_attention")
        self.assertIn("Invalid expected_output_path", refreshed.last_error)
        self.assertIn("action phrase", refreshed.last_error)

    def test_poll_project_reuses_result_file_path_before_fuzzy_matching(self):
        project, task = self._seed_running_task("outputs/proj-7-7b145d/TASK-001/result")
        db = self.SessionLocal()
        stored = db.query(Task).filter(Task.id == task.id).first()
        stored.result_file_path = "outputs/proj-7-7b145d/TASK-001/result.md"
        db.commit()
        db.close()

        with patch(
            "services.polling_service.git_service.ensure_repo_sync",
            return_value=RepoSyncStatus(repo_dir="/tmp/repo", fetched=True, pulled=True, remote_ready=True),
        ), patch(
            "services.polling_service.git_service.file_exists",
            side_effect=lambda project_id, relative_path, git_repo_url=None, prefer_remote=False: relative_path == "outputs/proj-7-7b145d/TASK-001/result.md",
        ) as mock_exists, patch(
            "services.polling_service.git_service.list_dir",
            side_effect=AssertionError("fuzzy match should not run after direct hit"),
        ):
            poll_project(self.SessionLocal(), project)

        self.assertTrue(mock_exists.called)
        verify_db = self.SessionLocal()
        self.addCleanup(verify_db.close)
        refreshed = verify_db.query(Task).filter(Task.id == task.id).first()
        self.assertEqual(refreshed.status, "completed")
        self.assertEqual(refreshed.result_file_path, "outputs/proj-7-7b145d/TASK-001/result.md")

    def test_poll_project_directory_output_requires_sentinel(self):
        """A directory expected_output must contain `result.json` sentinel
        before the task is marked completed. See log/0409.md.
        """
        project, task = self._seed_running_task(
            "outputs/proj-7-7b145d/TASK-001-artifacts",
            dispatched_minutes_ago=1,
        )
        sentinel = "outputs/proj-7-7b145d/TASK-001-artifacts/result.json"

        # Case 1: sentinel absent → task must stay running, even if the
        # directory already has content (pre-fix behavior would mark it done).
        with patch(
            "services.polling_service.git_service.ensure_repo_sync",
            return_value=RepoSyncStatus(repo_dir="/tmp/repo", fetched=True, pulled=True, remote_ready=True),
        ), patch(
            "services.polling_service.git_service.file_exists",
            return_value=False,
        ), patch(
            "services.polling_service.git_service.read_json",
            return_value=None,
        ), patch(
            "services.polling_service.git_service.list_dir",
            return_value=[],
        ):
            poll_project(self.SessionLocal(), project)

        verify_db = self.SessionLocal()
        self.addCleanup(verify_db.close)
        refreshed = verify_db.query(Task).filter(Task.id == task.id).first()
        self.assertEqual(refreshed.status, "running")

        # Case 2: sentinel present → task becomes completed and result_file_path
        # is the sentinel file itself.
        def _file_exists_only_sentinel(_project_id, path, **_kwargs):
            return path == sentinel

        with patch(
            "services.polling_service.git_service.ensure_repo_sync",
            return_value=RepoSyncStatus(repo_dir="/tmp/repo", fetched=True, pulled=True, remote_ready=True),
        ), patch(
            "services.polling_service.git_service.file_exists",
            side_effect=_file_exists_only_sentinel,
        ), patch(
            "services.polling_service.git_service.read_json",
            return_value=None,
        ), patch(
            "services.polling_service.git_service.list_dir",
            return_value=[],
        ):
            poll_project(self.SessionLocal(), project)

        verify_db2 = self.SessionLocal()
        self.addCleanup(verify_db2.close)
        refreshed2 = verify_db2.query(Task).filter(Task.id == task.id).first()
        self.assertEqual(refreshed2.status, "completed")
        self.assertEqual(refreshed2.result_file_path, sentinel)

    def test_task_usage_path_for_directory_expected_output_stays_inside_directory(self):
        project, task = self._seed_running_task(
            "outputs/proj-7-7b145d/TASK-001-artifacts",
            dispatched_minutes_ago=1,
        )

        usage_path = _task_usage_path(project, task)

        self.assertEqual(usage_path, "outputs/proj-7-7b145d/TASK-001-artifacts/usage.json")

    def test_poll_project_records_git_sync_failure_without_timing_out(self):
        project, task = self._seed_running_task("outputs/proj-7-7b145d/TASK-001/result.json")

        with patch(
            "services.polling_service.git_service.ensure_repo_sync",
            return_value=RepoSyncStatus(repo_dir="/tmp/repo", remote_ready=False, error="git fetch origin failed: network is unreachable"),
        ):
            poll_project(self.SessionLocal(), project)

        verify_db = self.SessionLocal()
        self.addCleanup(verify_db.close)
        refreshed = verify_db.query(Task).filter(Task.id == task.id).first()
        self.assertEqual(refreshed.status, "running")
        self.assertIn("Git sync failed", refreshed.last_error)
        self.assertNotIn("Timeout: result not found", refreshed.last_error)

    def test_poll_project_records_git_sync_warning_and_keeps_running(self):
        project, task = self._seed_running_task("outputs/proj-7-7b145d/TASK-001/result.md")

        with patch(
            "services.polling_service.git_service.ensure_repo_sync",
            return_value=RepoSyncStatus(
                repo_dir="/tmp/repo",
                fetched=True,
                pulled=False,
                remote_ready=True,
                warnings=["git pull --ff-only failed: working tree contains unstaged changes"],
            ),
        ), patch(
            "services.polling_service.git_service.file_exists",
            return_value=False,
        ), patch(
            "services.polling_service.git_service.read_json",
            return_value=None,
        ):
            poll_project(self.SessionLocal(), project)

        verify_db = self.SessionLocal()
        self.addCleanup(verify_db.close)
        refreshed = verify_db.query(Task).filter(Task.id == task.id).first()
        self.assertEqual(refreshed.status, "running")
        self.assertIn("Git sync warning", refreshed.last_error)
        self.assertNotIn("Timeout: result not found", refreshed.last_error)


if __name__ == "__main__":
    unittest.main()
