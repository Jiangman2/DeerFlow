# Wave 1 isolated frontend canary

- This package may create and delete only one uniquely labeled temporary Pod and
  ClusterIP Service. It must not patch production workloads, scale, uncordon,
  change node labels, import images or read Secret data.
- Reuse the unchanged preparation reader. Bind the canary to fresh cluster and
  preload evidence. Keep scheduler resource checks via nodeSelector and a narrow
  unschedulable taint toleration; never use nodeName to bypass scheduling.
- Bound HTTP reads, subprocesses and Pod lifetime. Cleanup requires unique labels,
  recorded identity and API UID preconditions, including uncertain create results.
- Copy references from the live frontend only in memory; never save raw manifests,
  environment values, authentication material, response bodies or application logs.
- Separate local synthetic tests from real-node canary results. No capacity claim
  follows from a single successful canary. Preserve Python 3.6 stdlib compatibility
  and LF/no-BOM artifacts. Do not mutate the already delivered preparation package.
