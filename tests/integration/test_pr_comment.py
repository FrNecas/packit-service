# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json
from typing import List

import pytest
from celery.canvas import Signature
from flexmock import flexmock
from github import Github
from packit_service.worker.monitoring import Pushgateway

from ogr.services.github import GithubProject

from packit.config import JobConfigTriggerType
from packit.local_project import LocalProject
from packit_service.config import ServiceConfig
from packit_service.constants import (
    SANDCASTLE_WORK_DIR,
    TASK_ACCEPTED,
    PG_COPR_BUILD_STATUS_SUCCESS,
)
from packit_service.models import PullRequestModel, JobTriggerModelType, JobTriggerModel
from packit_service.service.db_triggers import AddPullRequestDbTrigger
from packit_service.worker.build import copr_build
from packit_service.worker.build.copr_build import CoprBuildJobHelper
from packit_service.worker.build.koji_build import KojiBuildJobHelper
from packit_service.worker.jobs import SteveJobs, get_packit_commands_from_comment
from packit_service.worker.result import TaskResults
from packit_service.worker.tasks import (
    run_copr_build_handler,
    run_koji_build_handler,
    run_testing_farm_handler,
)
from packit_service.worker.testing_farm import TestingFarmJobHelper
from packit_service.worker.allowlist import Allowlist
from packit_service.worker.reporting import BaseCommitStatus
from tests.spellbook import DATA_DIR, first_dict_value, get_parameters_from_results


@pytest.fixture(scope="module")
def pr_copr_build_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github" / "pr_comment_copr_build.json").read_text()
    )


@pytest.fixture(scope="module")
def pr_build_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github" / "pr_comment_build.json").read_text()
    )


@pytest.fixture(scope="module")
def pr_production_build_comment_event():
    return json.loads(
        (
            DATA_DIR / "webhooks" / "github" / "pr_comment_production_build.json"
        ).read_text()
    )


@pytest.fixture(scope="module")
def pr_embedded_command_comment_event():
    return json.loads(
        (
            DATA_DIR / "webhooks" / "github" / "pr_comment_embedded_command.json"
        ).read_text()
    )


@pytest.fixture(scope="module")
def pr_empty_comment_event():
    return json.loads(
        (DATA_DIR / "webhooks" / "github" / "pr_comment_empty.json").read_text()
    )


@pytest.fixture(scope="module")
def pr_packit_only_comment_event():
    return json.loads(
        (
            DATA_DIR / "webhooks" / "github" / "issue_comment_packit_only.json"
        ).read_text()
    )


@pytest.fixture(scope="module")
def pr_wrong_packit_comment_event():
    return json.loads(
        (
            DATA_DIR / "webhooks" / "github" / "issue_comment_wrong_packit_command.json"
        ).read_text()
    )


@pytest.fixture(
    params=[
        [
            {
                "trigger": "pull_request",
                "job": "copr_build",
                "metadata": {"targets": "fedora-rawhide-x86_64"},
            }
        ],
        [
            {
                "trigger": "pull_request",
                "job": "tests",
                "metadata": {"targets": "fedora-rawhide-x86_64"},
            }
        ],
        [
            {
                "trigger": "pull_request",
                "job": "copr_build",
                "metadata": {"targets": "fedora-rawhide-x86_64"},
            },
            {
                "trigger": "pull_request",
                "job": "tests",
                "metadata": {"targets": "fedora-rawhide-x86_64"},
            },
        ],
    ]
)
def mock_pr_comment_functionality(request):
    packit_yaml = (
        "{'specfile_path': 'the-specfile.spec', 'synced_files': [], 'jobs': "
        + str(request.param)
        + "}"
    )

    flexmock(
        GithubProject,
        full_repo_name="packit-service/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)

    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)
    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request, id=123
    )
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(123).and_return(
        trigger
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)


def one_job_finished_with_msg(results: List[TaskResults], msg: str):
    for value in results:
        assert value["success"]
        if value["details"]["msg"] == msg:
            break
    else:
        raise AssertionError(f"None of the jobs finished with {msg!r}")


def test_pr_comment_copr_build_handler(
    mock_pr_comment_functionality, pr_copr_build_comment_event
):
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(
        flexmock(id=9, job_config_trigger_type=JobConfigTriggerType.pull_request)
    )
    flexmock(CoprBuildJobHelper).should_receive("run_copr_build").and_return(
        TaskResults(success=True, details={})
    ).once()
    flexmock(GithubProject).should_receive("get_files").and_return(["foo.spec"])
    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/the-namespace/the-repo"
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(set())
    flexmock(CoprBuildJobHelper).should_receive("report_status_to_all").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
    ).once()
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").twice().and_return()
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args("+1").once()

    processing_results = SteveJobs().process_message(pr_copr_build_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    results = run_copr_build_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


def test_pr_comment_build_handler(
    mock_pr_comment_functionality, pr_build_comment_event
):
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(
        flexmock(id=9, job_config_trigger_type=JobConfigTriggerType.pull_request)
    )
    flexmock(CoprBuildJobHelper).should_receive("run_copr_build").and_return(
        TaskResults(success=True, details={})
    )
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(set())
    flexmock(CoprBuildJobHelper).should_receive("report_status_to_all").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
    ).once()
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").twice().and_return()
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args("+1").once()

    processing_results = SteveJobs().process_message(pr_build_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    results = run_copr_build_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


def test_pr_comment_production_build_handler(pr_production_build_comment_event):
    packit_yaml = str(
        {
            "specfile_path": "the-specfile.spec",
            "synced_files": [],
            "jobs": [
                {
                    "trigger": "pull_request",
                    "job": "production_build",
                    "metadata": {"targets": "fedora-rawhide-x86_64", "scratch": "true"},
                }
            ],
        }
    )
    comment = flexmock(add_reaction=lambda reaction: None)
    flexmock(
        GithubProject,
        full_repo_name="packit-service/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
        get_pr=lambda pr_id: flexmock(
            head_commit="12345", get_comment=lambda comment_id: comment
        ),
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)

    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)
    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request, id=123
    )
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(123).and_return(
        trigger
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)

    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(
        flexmock(id=9, job_config_trigger_type=JobConfigTriggerType.pull_request)
    )
    flexmock(KojiBuildJobHelper).should_receive("run_koji_build").and_return(
        TaskResults(success=True, details={})
    )
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(KojiBuildJobHelper).should_receive("report_status_to_all").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
    ).once()
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").twice().and_return()
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args("+1").once()

    processing_results = SteveJobs().process_message(pr_production_build_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    results = run_koji_build_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "comment",
    (
        "",
        " ",
        "   ",
        "some unrelated",
        "some\nmore\nunrelated\ntext",
        "even\nsome → unicode",
        " stuff",
        " \n ",
        "x ",
        """comment with embedded /packit build not recognized
        unless /packit command is on line by itself""",
        "\n2nd line\n\n4th line",
        "1st line\n\t\n\t\t\n4th line\n",
    ),
)
def test_pr_comment_invalid(comment):
    commands = get_packit_commands_from_comment(comment)
    assert len(commands) == 0


@pytest.mark.parametrize(
    "comments_list",
    (
        "/packit build",
        "/packit build ",
        "/packit  build ",
        " /packit build",
        " /packit build ",
        "asd\n/packit build\n",
        "asd\n /packit build \n",
        "Should be fixed now, let's\n /packit build\n it.",
    ),
)
def test_pr_embedded_command_handler(
    mock_pr_comment_functionality, pr_embedded_command_comment_event, comments_list
):
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(
        flexmock(id=9, job_config_trigger_type=JobConfigTriggerType.pull_request)
    )
    pr_embedded_command_comment_event["comment"]["body"] = comments_list
    flexmock(CoprBuildJobHelper).should_receive("run_copr_build").and_return(
        TaskResults(success=True, details={})
    )
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args("+1").once()
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(set())
    flexmock(CoprBuildJobHelper).should_receive("report_status_to_all").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
    ).once()
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").twice().and_return()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    results = run_copr_build_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


def test_pr_comment_empty_handler(
    mock_pr_comment_functionality, pr_empty_comment_event
):
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("can_merge_pr").and_return(True)
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)

    results = SteveJobs().process_message(pr_empty_comment_event)
    assert results == []


def test_pr_comment_packit_only_handler(
    mock_pr_comment_functionality, pr_packit_only_comment_event
):
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("can_merge_pr").and_return(True)
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)

    results = SteveJobs().process_message(pr_packit_only_comment_event)
    assert results == []


def test_pr_comment_wrong_packit_command_handler(
    mock_pr_comment_functionality, pr_wrong_packit_comment_event
):
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(GithubProject).should_receive("can_merge_pr").and_return(True)
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)

    results = SteveJobs().process_message(pr_wrong_packit_comment_event)
    assert results == []


def test_pr_test_command_handler(pr_embedded_command_comment_event):
    jobs = [
        {
            "trigger": "pull_request",
            "job": "tests",
            "metadata": {"targets": "fedora-rawhide-x86_64"},
        }
    ]
    packit_yaml = (
        "{'specfile_path': 'the-specfile.spec', 'synced_files': [], 'jobs': "
        + str(jobs)
        + "}"
    )
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args("+1").once()
    flexmock(
        GithubProject,
        full_repo_name="packit-service/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)

    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)
    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request, id=123
    )
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(123).and_return(
        trigger
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(
        flexmock(id=9, job_config_trigger_type=JobConfigTriggerType.pull_request)
    )
    pr_embedded_command_comment_event["comment"]["body"] = "/packit test"
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(copr_build).should_receive("get_valid_build_targets").twice().and_return(
        {"test-target"}
    )
    flexmock(TestingFarmJobHelper).should_receive("get_latest_copr_build").and_return(
        flexmock(status=PG_COPR_BUILD_STATUS_SUCCESS)
    )
    flexmock(TestingFarmJobHelper).should_receive("run_testing_farm").once().and_return(
        TaskResults(success=True, details={})
    )
    flexmock(Pushgateway).should_receive("push").twice().and_return()
    flexmock(CoprBuildJobHelper).should_receive("report_status_to_tests").with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
    ).once()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )


def test_pr_test_command_handler_missing_build(pr_embedded_command_comment_event):
    jobs = [
        {
            "trigger": "pull_request",
            "job": "tests",
            "metadata": {"targets": "fedora-rawhide-x86_64"},
        }
    ]
    packit_yaml = (
        "{'specfile_path': 'the-specfile.spec', 'synced_files': [], 'jobs': "
        + str(jobs)
        + "}"
    )
    pr = flexmock(head_commit="12345")
    flexmock(GithubProject).should_receive("get_pr").and_return(pr)
    comment = flexmock()
    flexmock(pr).should_receive("get_comment").and_return(comment)
    flexmock(comment).should_receive("add_reaction").with_args("+1").once()
    flexmock(
        GithubProject,
        full_repo_name="packit-service/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)

    config = ServiceConfig()
    config.command_handler_work_dir = SANDCASTLE_WORK_DIR
    flexmock(ServiceConfig).should_receive("get_service_config").and_return(config)
    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request, id=123
    )
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(123).and_return(
        trigger
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(Allowlist, check_and_report=True)
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=9,
        namespace="packit-service",
        repo_name="hello-world",
        project_url="https://github.com/packit-service/hello-world",
    ).and_return(
        flexmock(
            id=9,
            job_config_trigger_type=JobConfigTriggerType.pull_request,
            job_trigger_model_type=JobTriggerModelType.pull_request,
        )
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").with_args(
        type=JobTriggerModelType.pull_request, trigger_id=9
    ).and_return(trigger)

    pr_embedded_command_comment_event["comment"]["body"] = "/packit test"
    flexmock(GithubProject, get_files="foo.spec")
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(Signature).should_receive("apply_async").twice()
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(
        {"test-target", "test-target-without-build"}
    )
    flexmock(TestingFarmJobHelper).should_receive("get_latest_copr_build").and_return(
        flexmock(status=PG_COPR_BUILD_STATUS_SUCCESS)
    ).and_return()

    flexmock(TestingFarmJobHelper).should_receive("job_owner").and_return("owner")
    flexmock(TestingFarmJobHelper).should_receive("job_project").and_return("project")
    flexmock(CoprBuildJobHelper).should_receive("report_status_to_tests").once()
    flexmock(CoprBuildJobHelper).should_receive(
        "report_status_to_test_for_chroot"
    ).once()
    flexmock(TestingFarmJobHelper).should_receive("run_testing_farm").once().and_return(
        TaskResults(success=False, details={})
    )
    flexmock(Pushgateway).should_receive("push").twice().and_return()

    processing_results = SteveJobs().process_message(pr_embedded_command_comment_event)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)

    run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
