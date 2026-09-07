# 企业统一分支开发指导手册

本文规定企业定制版本的分支职责、日常开发、验证、合并和清理流程。目标是保持企业统一分支稳定，同时让每项功能都能够独立审查、测试和回退。

## 1. 分支职责

长期保留的分支分为三类：

| 分支                                      | 职责                       | 是否直接开发 |
| ----------------------------------------- | -------------------------- | ------------ |
| `main`                                    | 原有生产和历史代码线       | 否           |
| `upstream/main`                    | 官方上游同步基线           | 否           |
| `codex/enterprise-consolidation-20260716` | 当前企业版本的稳定集成基线 | 原则上否     |

`codex/upstream-compatible-20260713` 是仍包含独立兼容提交的历史分支。在完成差异审查前保留，不作为新功能开发基线。

企业统一分支用于接收已经完成、已经验证的功能，不应成为多个未完成需求共用的工作区。

## 2. 什么时候创建功能分支

以下情况必须创建独立功能分支：

- 新功能或跨多个文件的修改；
- 前后端联动；
- Docker、Nginx、Kubernetes 或数据库变更；
- 需要多轮调试、评审或产品确认的界面修改；
- 与其他需求可以独立发布或回退的修改；
- 多名开发者或多个 Codex 任务可能并行工作的修改。

只有范围明确、风险很低、可以立即完成并验证的微小修复，才可以直接提交到企业统一分支。即使是小改动，只要预计会继续迭代，也应创建功能分支。

## 3. 分支命名

Codex 创建的分支统一使用 `codex/` 前缀：

```text
codex/feature-<功能名称>
codex/fix-<问题名称>
codex/refactor-<模块名称>
codex/docs-<文档主题>
```

示例：

```text
codex/feature-enterprise-landing-polish
codex/feature-knowledge-search
codex/fix-human-input-resume
codex/fix-k8s-runtime-config
```

分支名称应表达业务目的，不使用 `temp`、`test2`、`new-final` 等无法追踪含义的名称。

## 4. 开始一项新工作

开始前先回到企业统一分支，并确认工作区干净：

```powershell
git switch codex/enterprise-consolidation-20260716
git status
```

如果 `git status` 显示未提交改动，必须先确认改动归属。不得为了切换分支而使用 `git reset --hard` 或覆盖他人的工作。

从最新企业统一分支创建功能分支：

```powershell
git switch -c codex/feature-enterprise-landing-polish
```

如果统一分支已推送远端，开始工作前先同步：

```powershell
git fetch origin
git switch codex/enterprise-consolidation-20260716
git pull --ff-only origin codex/enterprise-consolidation-20260716
git switch -c codex/feature-enterprise-landing-polish
```

`--ff-only` 可以避免拉取时意外生成没有明确目的的合并提交。

## 5. 开发与提交原则

### 5.1 一个分支只处理一个目标

不要在首页功能分支中顺手修改 K8s，也不要在后端修复分支中混入无关的品牌文案。发现新的独立需求时，应另建分支。

### 5.2 一个提交只表达一个完整意图

推荐的提交结构：

```text
feat: add enterprise knowledge search
fix: pause goal continuation for human input
style: refine enterprise landing hero
test: cover clarification resume behavior
docs: document enterprise branch workflow
```

前端、后端和部署变更如果可以独立回退，应拆成不同提交。测试通常与对应实现放在同一个提交中。

提交前检查暂存内容：

```powershell
git diff
git add <本次提交相关文件>
git diff --cached
git diff --cached --check
git commit -m "fix: describe the change"
```

不建议直接执行 `git add .`。如果必须批量暂存，应在提交前认真检查 `git diff --cached --stat` 和 `git diff --cached`。

### 5.3 不覆盖既有工作

- 不使用 `git reset --hard` 清理未知改动；
- 不使用 `git checkout -- <file>` 或 `git restore <file>` 丢弃未确认的修改；
- 不在脏工作区中进行大范围 merge 或 rebase；
- 不修改与当前任务无关的文件；
- 发现同一文件混合多个需求时，按 diff hunk 拆分暂存或先停止并确认归属。

## 6. 验证要求

验证范围应与改动风险相匹配。

### 前端

```powershell
cd frontend
pnpm check
pnpm test
```

界面修改还应在实际页面中检查桌面端和窄屏布局，重点观察溢出、遮挡、重复元素、加载状态和空状态。

### 后端

```powershell
cd backend
uv run pytest <相关测试文件> -q
uv run ruff check <相关文件>
```

较大后端变更应运行完整测试：

```powershell
cd backend
make test
make lint
```

### Docker 和 Kubernetes

Compose 覆盖文件应与基础文件组合验证：

```powershell
docker compose -f docker/docker-compose.yaml -f docker/docker-compose-local-prod.yaml config --quiet
kubectl kustomize k8s/local-pvc
```

环境变量缺失导致验证失败时，应提供仅用于本地校验的临时值，不得把密钥写入仓库。

## 7. 合并回企业统一分支

合并前确保功能分支：

- 工作区干净；
- 提交历史清晰；
- 相关检查通过；
- 没有混入无关文件；
- 已同步企业统一分支的最新变化。

先将统一分支的最新内容合入功能分支并解决冲突：

```powershell
git switch codex/feature-enterprise-landing-polish
git merge codex/enterprise-consolidation-20260716
```

重新验证后，切回统一分支并使用非快进合并：

```powershell
git switch codex/enterprise-consolidation-20260716
git merge --no-ff codex/feature-enterprise-landing-polish
```

`--no-ff` 会保留功能边界，便于后续审查和整体回退。合并后再次运行关键检查。

如果团队使用 Pull Request，则推送功能分支并以企业统一分支为目标创建 PR，不在本地直接合并：

```powershell
git push -u fork codex/feature-enterprise-landing-polish
```

## 8. 合并后的清理

确认功能已经进入统一分支后，安全删除本地功能分支：

```powershell
git branch -d codex/feature-enterprise-landing-polish
```

如果存在远端功能分支，并且团队确认不再需要：

```powershell
git push fork --delete codex/feature-enterprise-landing-polish
```

优先使用 `git branch -d`。只有确认提交已经由其他分支完整保留时，才使用 `git branch -D`。

定期检查分支：

```powershell
git branch -vv
git branch --merged codex/enterprise-consolidation-20260716
git worktree list
```

## 9. 旧 `main` 与上游同步原则

不要把旧 `main` 或兼容历史分支整体 merge 到企业统一分支。历史分支可能包含已被重新实现的品牌、知识库、Agent、部署和交互逻辑，整体合并容易产生重复实现和回归。

需要吸收历史修复时：

1. 明确缺失的具体能力；
2. 比较当前实现与历史提交；
3. 优先重新实现或选择性 `cherry-pick`；
4. 解决冲突后运行相关测试；
5. 单独记录该兼容性变更。

选择性引入示例：

```powershell
git switch -c codex/fix-port-missing-runtime-guard codex/enterprise-consolidation-20260716
git cherry-pick <明确需要的提交 SHA>
```

官方上游同步也应通过专门的同步分支完成，不直接在企业统一分支上操作：

```powershell
git switch -c codex/sync-upstream-YYYYMMDD codex/enterprise-consolidation-20260716
git fetch upstream
```

随后审查上游差异、解决冲突、完成全量验证，再合并回企业统一分支。

## 10. 紧急修复流程

生产紧急修复仍应创建短期分支：

```powershell
git switch codex/enterprise-consolidation-20260716
git switch -c codex/fix-<紧急问题>
```

修复应保持范围最小，完成针对性测试后合入统一分支。不要借紧急修复重构无关模块。

如果某个版本已经从不同提交发布，应从实际发布提交创建修复分支，修复生产版本后，再把同一修复合并或 cherry-pick 回企业统一分支。

## 11. 与 Codex 协作的约定

向 Codex 提交新开发任务时，应说明目标功能和验收标准。默认流程为：

1. 确认企业统一分支和工作区状态；
2. 从企业统一分支创建命名清晰的短期功能分支；
3. 只修改当前任务范围内的文件；
4. 添加或更新测试；
5. 完成格式、类型和针对性测试；
6. 按功能拆分提交；
7. 汇报变更、验证结果和未解决风险；
8. 经确认后合并回企业统一分支并清理短期分支。

如果用户明确要求仅分析、审查或诊断，Codex 不应创建分支、提交或合并代码。

## 12. 日常检查清单

开始开发前：

- [ ] 当前基线是 `codex/enterprise-consolidation-20260716`；
- [ ] 工作区干净；
- [ ] 新需求已有独立功能分支；
- [ ] 分支名称能够表达业务目标。

提交前：

- [ ] diff 中没有无关文件；
- [ ] 一个提交只表达一个意图；
- [ ] 测试与实现保持同步；
- [ ] 格式、类型和相关测试通过；
- [ ] 没有提交密钥、本地配置、数据库或生成产物。

合并前：

- [ ] 功能分支已同步统一分支；
- [ ] 冲突已按业务语义解决；
- [ ] 合并前验证通过；
- [ ] 合并后关键检查再次通过；
- [ ] 功能分支可以安全删除。

## 13. 标准流程速查

```powershell
# 1. 从稳定基线创建功能分支
git switch codex/enterprise-consolidation-20260716
git status
git switch -c codex/feature-example

# 2. 开发、检查并提交
git diff
git add <相关文件>
git diff --cached --check
git commit -m "feat: implement example"

# 3. 同步统一分支并验证
git merge codex/enterprise-consolidation-20260716
# 运行相关检查

# 4. 合并回统一分支
git switch codex/enterprise-consolidation-20260716
git merge --no-ff codex/feature-example

# 5. 清理
git branch -d codex/feature-example
git status
```

上游固定基线、共享补丁与金仓数据库验收流程见 [上游升级手册](upstream/upgrade-runbook.md)。当前远程名称以 origin 和 upstream 为准。
