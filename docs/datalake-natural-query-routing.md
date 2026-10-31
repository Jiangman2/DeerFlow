# DataLake natural business queries

The active enterprise tools are `datalake_data_qa` (master data, production,
metrics, details) and `datalake_smart_qa` (narrative document evidence).
A user need not name the data store. The task-boundary classifier accepts
business objects with requested facts and query wording, including:

- 天津公司有多少气田 → data_qa
- 某油田上个月产量是多少 → data_qa
- 列出某平台的生产明细 → data_qa
- 查 NB27-1-1 的井史 → smart_qa
- 检索某油田的完井报告 → smart_qa

This is bounded rule-based intent recognition, not a guarantee of arbitrary
semantic understanding or of the model's chosen tool. Clarify ambiguous source,
object or field requirements without requiring a magic DataLake keyword.

Intent is evaluated from original human content; generated tool arguments and
attachment text cannot establish it. Effective `datalake_enabled` must still
be exactly true. Unauthorized, empty or failed enterprise queries cannot use
public web or arbitrary scripts as substitutes. Ordinary writing, local-file
analysis, quoted instructions, code, concepts and explicitly public questions
retain their boundaries. Current-run scope, delegated restrictions, subsequent
local processing and unrelated task switches remain governed by existing state.

No tool is added or activated, and no endpoint or credential changes. Release
requires a rebuilt Gateway image and installed-image QA. Mocked graph tests do
not establish live provider acceptance; validate both original natural user
questions and the disabled-authorization case after deployment.
