# Archived production GLM build tools

These 45 historical files are restored byte-for-byte from
`99fb9464f8253bee7f3cfe059729a3df3ef553f2` for the accepted 2026-10-16
WeKnora backend image. This README is new archive guidance and is not a build input.

See [source, build sequence, receipt and rollback limits](../../../docs/weknora-native-glm-production-source-20261026.md)
and [the source lock](../../../deploy/enterprise/weknora-native-glm-source-lock-20261026.json).
Several original runbooks describe historical intermediate failures or point
to later deployment tools. They are retained as evidence, not current release
instructions. Do not rerun their fixed-host, fixed-state launchers on production.

Included: original offline context/input collection, base-image export and
verification, bounded build/OCI verification, SQLite and DWARF build repairs,
Jieba runtime completion, final qualification, and their synthetic tests.
The nine shared builder helpers already live under
`release/production-build-alignment-20260928/r4-build/`; their exact original
hashes are checked by the source lock.

Excluded: model activation, database/model-data changes, concurrency switches,
cluster preload/rollout/recovery commands and unrelated enrichment candidates.
The public AnyDoc source archive and original source/input inventories are
stored under `release/production-source-20261026/weknora-native-glm/`.
Large OCI archives, binaries, caches, credentials and private configuration are
not part of this source restoration.

On Linux, tests require root-owned temporary fixtures and the shared helper path:

```bash
sudo env PYTHONPATH="$PWD/release/production-build-alignment-20260928/r4-build" \
  python3 -B -m unittest discover \
  -s release/mineru-provider-switch-20261014/glm-native -p 'test_*.py' -v
```

These tests use synthetic local data; they do not run a production build or rollout.
