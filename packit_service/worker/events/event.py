# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Generic/abstract event classes.
"""
import copy
from datetime import datetime, timezone
from logging import getLogger
from typing import Dict, Optional, Union, Set, List

from ogr.abstract import GitProject
from ogr.services.pagure import PagureProject
from packit.config import PackageConfig

from packit_service.config import PackageConfigGetter, ServiceConfig
from packit_service.models import (
    AbstractTriggerDbType,
    GitBranchModel,
    IssueModel,
    ProjectReleaseModel,
    PullRequestModel,
)

logger = getLogger(__name__)


class EventData:
    """
    Class to represent the data which are common for handlers and comes from the original event
    """

    def __init__(
        self,
        event_type: str,
        user_login: str,
        trigger_id: int,
        project_url: str,
        tag_name: Optional[str],
        git_ref: Optional[str],
        pr_id: Optional[int],
        commit_sha: Optional[str],
        identifier: Optional[str],
        event_dict: Optional[dict],
        issue_id: Optional[int],
        task_accepted_time: Optional[datetime],
        targets_override: Optional[List[str]],
    ):
        self.event_type = event_type
        self.user_login = user_login
        self.trigger_id = trigger_id
        self.project_url = project_url
        self.tag_name = tag_name
        self.git_ref = git_ref
        self.pr_id = pr_id
        self.commit_sha = commit_sha
        self.identifier = identifier
        self.event_dict = event_dict
        self.issue_id = issue_id
        self.task_accepted_time = task_accepted_time
        self.targets_override = set(targets_override) if targets_override else None

        # lazy attributes
        self._project = None
        self._db_trigger: Optional[AbstractTriggerDbType] = None

    @classmethod
    def from_event_dict(cls, event: dict):
        event_type = event.get("event_type")
        user_login = event.get("user_login")
        trigger_id = event.get("trigger_id")
        project_url = event.get("project_url")
        tag_name = event.get("tag_name")
        git_ref = event.get("git_ref")
        # event has _pr_id as the attribute while pr_id is a getter property
        pr_id = event.get("_pr_id") or event.get("pr_id")
        commit_sha = event.get("commit_sha")
        identifier = event.get("identifier")
        issue_id = event.get("issue_id")
        task_accepted_time = (
            datetime.fromtimestamp(event.get("task_accepted_time"), timezone.utc)
            if event.get("task_accepted_time")
            else None
        )
        targets_override = event.get("targets_override")

        return EventData(
            event_type=event_type,
            user_login=user_login,
            trigger_id=trigger_id,
            project_url=project_url,
            tag_name=tag_name,
            git_ref=git_ref,
            pr_id=pr_id,
            commit_sha=commit_sha,
            identifier=identifier,
            event_dict=event,
            issue_id=issue_id,
            task_accepted_time=task_accepted_time,
            targets_override=targets_override,
        )

    @property
    def project(self):
        if not self._project:
            self._project = self.get_project()
        return self._project

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        if not self._db_trigger:

            # TODO, do a better job
            # Probably, try to recreate original classes.
            if self.event_type in {
                "PullRequestGithubEvent",
                "PullRequestPagureEvent",
                "MergeRequestGitlabEvent",
                "PullRequestCommentGithubEvent",
                "MergeRequestCommentGitlabEvent",
                "PullRequestCommentPagureEvent",
                "CheckRerunPullRequestEvent",
            }:
                self._db_trigger = PullRequestModel.get_or_create(
                    pr_id=self.pr_id,
                    namespace=self.project.namespace,
                    repo_name=self.project.repo,
                    project_url=self.project_url,
                )
            elif self.event_type in {
                "PushGitHubEvent",
                "PushGitlabEvent",
                "PushPagureEvent",
                "CheckRerunCommitEvent",
            }:
                self._db_trigger = GitBranchModel.get_or_create(
                    branch_name=self.git_ref,
                    namespace=self.project.namespace,
                    repo_name=self.project.repo,
                    project_url=self.project_url,
                )

            elif self.event_type in {"ReleaseEvent", "CheckRerunReleaseEvent"}:
                self._db_trigger = ProjectReleaseModel.get_or_create(
                    tag_name=self.tag_name,
                    namespace=self.project.namespace,
                    repo_name=self.project.repo,
                    project_url=self.project_url,
                    commit_hash=self.commit_sha,
                )
            elif self.event_type in {
                "IssueCommentEvent",
                "IssueCommentGitlabEvent",
            }:
                self._db_trigger = IssueModel.get_or_create(
                    issue_id=self.issue_id,
                    namespace=self.project.namespace,
                    repo_name=self.project.repo,
                    project_url=self.project_url,
                )
            else:
                logger.warning(
                    "We don't know, what to search in the database for this event data."
                )

        return self._db_trigger

    def get_dict(self) -> dict:
        d = self.__dict__
        d = copy.deepcopy(d)
        task_accepted_time = d.get("task_accepted_time")
        d["task_accepted_time"] = (
            int(task_accepted_time.timestamp()) if task_accepted_time else None
        )
        targets_override = self.targets_override
        if targets_override:
            d["targets_override"] = list(targets_override)
        d.pop("_project", None)
        d.pop("_db_trigger", None)
        return d

    def get_project(self) -> Optional[GitProject]:
        if not self.project_url:
            return None
        return ServiceConfig.get_service_config().get_project(
            url=self.project_url or self.db_trigger.project.project_url
        )


class Event:
    task_accepted_time: Optional[datetime] = None

    def __init__(self, created_at: Union[int, float, str] = None):
        self.created_at: datetime
        if created_at:
            if isinstance(created_at, (int, float)):
                self.created_at = datetime.fromtimestamp(created_at, timezone.utc)
            elif isinstance(created_at, str):
                # https://stackoverflow.com/questions/127803/how-do-i-parse-an-iso-8601-formatted-date/49784038
                created_at = created_at.replace("Z", "+00:00")
                self.created_at = datetime.fromisoformat(created_at)
        else:
            self.created_at = datetime.now()

    @staticmethod
    def ts2str(event: dict):
        """
        Convert 'created_at' key from timestamp to iso 8601 time format.
        This would normally be in a from_dict(), but we don't have such method.
        In api/* we read events from db and directly serve them to clients.
        Deserialize (from_dict) and serialize (to_dict) every entry
        just to do this ts2str would be waste of resources.
        """
        created_at = event.get("created_at")
        if isinstance(created_at, int):
            event["created_at"] = datetime.fromtimestamp(created_at).isoformat()
        return event

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        d = default_dict or self.__dict__
        d = copy.deepcopy(d)
        # whole dict has to be JSON serializable because of redis
        d["event_type"] = self.__class__.__name__
        d["trigger_id"] = self.db_trigger.id if self.db_trigger else None
        d["created_at"] = int(d["created_at"].timestamp())
        task_accepted_time = d.get("task_accepted_time")
        d["task_accepted_time"] = (
            int(task_accepted_time.timestamp()) if task_accepted_time else None
        )
        d["project_url"] = d.get("project_url") or (
            self.db_trigger.project.project_url if self.db_trigger else None
        )
        targets_override = self.targets_override
        if targets_override:
            d["targets_override"] = list(targets_override)
        return d

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        return None

    @property
    def project(self):
        raise NotImplementedError("Please implement me!")

    @property
    def base_project(self):
        raise NotImplementedError("Please implement me!")

    @property
    def package_config(self):
        raise NotImplementedError("Please implement me!")

    @property
    def targets_override(self) -> Optional[Set[str]]:
        """
        Return the targets to use for building/testing instead of the all targets from config
        for the relevant events (e.g.rerunning of a single check).
        """
        return None

    def get_package_config(self):
        raise NotImplementedError("Please implement me!")

    def get_project(self) -> GitProject:
        raise NotImplementedError("Please implement me!")

    def pre_check(self) -> bool:
        """
        Implement this method for those events, where you want to check if event properties are
        correct. If this method returns False during runtime, execution of service code is skipped.

        :return: False if we can ignore the event
        """
        return True

    def __str__(self):
        return str(self.get_dict())

    def __repr__(self):
        return f"{self.__class__.__name__}({self.get_dict()})"


class AbstractForgeIndependentEvent(Event):
    commit_sha: Optional[str]
    project_url: str

    def __init__(
        self,
        created_at: Union[int, float, str] = None,
        project_url=None,
        pr_id: Optional[int] = None,
    ):
        super().__init__(created_at)
        self.project_url = project_url
        self._pr_id = pr_id

        # Lazy properties
        self._project: Optional[GitProject] = None
        self._base_project: Optional[GitProject] = None
        self._package_config: Optional[PackageConfig] = None

    @property
    def project(self):
        if not self._project:
            self._project = self.get_project()
        return self._project

    @property
    def base_project(self):
        if not self._base_project:
            self._base_project = self.get_base_project()
        return self._base_project

    @property
    def package_config(self):
        if not self._package_config:
            self._package_config = self.get_package_config()
        return self._package_config

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        raise NotImplementedError()

    @property
    def pr_id(self) -> Optional[int]:
        return self._pr_id

    def get_project(self) -> Optional[GitProject]:
        if not (self.project_url or self.db_trigger):
            return None

        return ServiceConfig.get_service_config().get_project(
            url=self.project_url or self.db_trigger.project.project_url
        )

    def get_base_project(self) -> Optional[GitProject]:
        """Reimplement in the PR events."""
        return None

    def get_package_config(self) -> Optional[PackageConfig]:
        logger.debug(
            f"Getting package_config:\n"
            f"\tproject: {self.project}\n"
            f"\tbase_project: {self.base_project}\n"
            f"\treference: {self.commit_sha}\n"
            f"\tpr_id: {self.pr_id}"
        )

        spec_path = None
        if isinstance(self.base_project, PagureProject):
            spec_path = f"SPECS/{self.project.repo}.spec"
            logger.debug(
                f"Getting package_config from Pagure. "
                f"(Spec-file is expected to be in {spec_path})"
            )
        package_config = PackageConfigGetter.get_package_config_from_repo(
            base_project=self.base_project,
            project=self.project,
            reference=self.commit_sha,
            pr_id=self.pr_id,
            fail_when_missing=False,
            spec_file_path=spec_path,
        )

        # job config change note:
        #   this is used in sync-from-downstream which is buggy - we don't need to change this
        if package_config:
            package_config.upstream_project_url = self.project_url
        return package_config

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        # so that it is JSON serializable (because of Celery tasks)
        result.pop("_project")
        result.pop("_base_project")
        result.pop("_package_config")
        return result
