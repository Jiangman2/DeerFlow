# Provisioner maintenance

Read README.md for node-pool configuration and offline test commands.

Keep SANDBOX_NODE_POOL operator-only and snapshot it at startup.
Preserve default-unset manifests, strict validation and both PVC guards.
Use required hostname affinity only; do not add fallback nodes, nodeName,
tolerations, migrations or unrelated lifecycle/resource changes.
Preserve ownership checks and bounded deletion/recreation handling.
Do not commit deployment-specific node pools or credentials.

Run test_node_pool.py with an explicit baseline and its SHA256.
Use real Kubernetes models, mock API operations and block network access.
Never enter application lifespan or load cluster credentials in tests.
Keep feature documentation and regression tests synchronized.
