"""ORM model registration entry point.

Importing this module ensures all ORM models are registered with
``Base.metadata`` so Alembic autogenerate detects every table.

The actual ORM classes have moved to entity-specific subpackages:
- ``deerflow.persistence.thread_meta``
- ``deerflow.persistence.run``
- ``deerflow.persistence.feedback``
- ``deerflow.persistence.user``

``RunEventRow`` remains in ``deerflow.persistence.models.run_event`` because
its storage implementation lives in ``deerflow.runtime.events.store.db`` and
there is no matching entity directory.
"""

from deerflow.persistence.agents.model import AgentRow
from deerflow.persistence.announcement.model import AnnouncementRow
from deerflow.persistence.channel_connections.model import (
    ChannelConnectionRow,
    ChannelConversationRow,
    ChannelCredentialRow,
    ChannelOAuthStateRow,
)
from deerflow.persistence.dashboard_rollup.model import (
    DashboardDailyRollupRow,
    DashboardHourlyRollupRow,
    DashboardRollupStateRow,
)
from deerflow.persistence.data_lake_auth.model import DataLakeAuthChallengeRow
from deerflow.persistence.feedback.model import FeedbackRow
from deerflow.persistence.models.run_event import RunEventRow
from deerflow.persistence.oa_policy_flow_jobs.model import OAPolicyFlowJobRow
from deerflow.persistence.observability.model import AuditLogRow, OperationDispositionRow
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.scheduled_task_runs.model import ScheduledTaskRunRow
from deerflow.persistence.scheduled_tasks.model import ScheduledTaskRow
from deerflow.persistence.thread_meta.model import ThreadCreationRow, ThreadMetaRow
from deerflow.persistence.user.model import UserRow
from deerflow.persistence.work.model import WorkRecordRow

__all__ = [
    "WorkRecordRow",
    "AgentRow",
    "AnnouncementRow",
    "ChannelConnectionRow",
    "ChannelConversationRow",
    "ChannelCredentialRow",
    "ChannelOAuthStateRow",
    "DataLakeAuthChallengeRow",
    "DashboardDailyRollupRow",
    "DashboardHourlyRollupRow",
    "DashboardRollupStateRow",
    "FeedbackRow",
    "AuditLogRow",
    "OperationDispositionRow",
    "OAPolicyFlowJobRow",
    "RunEventRow",
    "RunRow",
    "ScheduledTaskRow",
    "ScheduledTaskRunRow",
    "ThreadCreationRow",
    "ThreadMetaRow",
    "UserRow",
]
