"""Names reserved for local migration notices, never remote workflow aliases."""

RETIRED_DATALAKE_TOOL_NAMES = frozenset({"datalake_metrics_qa", "datalake_detail_qa", "datalake_metric_qa"})
RETIRED_DATALAKE_EXPORTS = frozenset(name + "_tool" for name in RETIRED_DATALAKE_TOOL_NAMES)
DATA_QA_USE = "deerflow.community.appforge.tools:datalake_data_qa_tool"


def is_retired_datalake_registration(name: str, use: str) -> bool:
    module, _, export = use.partition(":")
    return name in RETIRED_DATALAKE_TOOL_NAMES or (module in ("deerflow.community.appforge", "deerflow.community.appforge.tools") and export in RETIRED_DATALAKE_EXPORTS)
