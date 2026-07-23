# Local PVC deployment

This stack is for local Docker Desktop Kubernetes validation when sandbox
execution must use Kubernetes PVCs as the single storage source.

In this mode, Gateway runs inside Kubernetes and mounts:

- `deer-flow-userdata-pvc` at `/app/backend/.deer-flow` with subPath `deer-flow`
- `deer-flow-skills-pvc` at `/app/skills`

Sandbox Pods created by the provisioner mount the same PVCs:

- user-data subPath `deer-flow/users/{user_id}/threads/{thread_id}/user-data`
- skills PVC at `/mnt/skills`

Do not use the Docker Compose Gateway for this mode; a Compose Gateway writes to
host bind mounts, not to the Kubernetes PVC.

`storage.yaml` defines the local Docker Desktop PVCs used by Gateway,
provisioner-created sandboxes, and skills. These local PVCs use the Docker
Desktop `standard` storage class and `ReadWriteOnce`, which is suitable only for
single-node validation. For multi-node production clusters, create storage from
`storage.production.example.yaml` or an environment-specific equivalent before
deploying the workloads:

- `deer-flow-userdata-pvc`: prefer RWX storage because Gateway and sandbox Pods
  must see the same uploaded files and generated artifacts.
- `deer-flow-skills-pvc`: prefer RWX or ROX storage, or bake immutable skills
  into the runtime image.
- PostgreSQL data: keep RWO on durable block storage with snapshots/backups.
- Backup data: protect independently from the primary database volume.

PVC storage class and access mode are immutable after binding. If an existing
environment was created with the wrong storage class or access mode, migrate by
backing up data, creating new PVCs, restoring data, and then pointing workloads
at the new claims.

For local Docker Desktop Kubernetes, tag freshly built images with an explicit
local version and keep that version in `kustomization.yaml`. The skills image
seeds the shared skills PVC, including `ppt-master`; the PPT sandbox image adds
its native and Python dependencies.

```powershell
$tag = "dev-YYYYMMDD-N"

docker build -f backend/Dockerfile --build-arg UV_EXTRAS=postgres -t "deer-flow-gateway:$tag" .
docker build -f frontend/Dockerfile --target prod -t "deer-flow-frontend:$tag" .
docker build -f docker/provisioner/Dockerfile -t "deer-flow-provisioner:$tag" docker/provisioner
docker build -f docker/k8s-skills/Dockerfile -t "deer-flow-skills:$tag" .
docker build --build-context ppt-master-skill=skills/public/ppt-master -f docker/sandbox-ppt-master/Dockerfile -t "deer-flow-aio-sandbox:ppt-master-$tag" .

kubectl kustomize k8s/local-pvc
kubectl apply -k k8s/local-pvc
kubectl -n deer-flow wait --for=condition=complete job/skills-sync --timeout=300s
```

Build the skills image from the repository root exactly as shown. The
Dockerfile copies `skills/` explicitly and the sync Job validates both the
source and mounted target layout before reporting success. A nested layout such
as `/mnt/skills/skills/public` therefore fails deployment instead of surfacing
later as a missing skill during an agent run.

On a multi-node cluster, the absolute container paths stay the same on every
node. What must be shared is the underlying storage: use RWX storage (for
example NFS or CephFS) for both skills and user data when Gateway and sandbox
Pods can run on different nodes. A node-local RWO skills PVC constrains every
Pod that mounts it to that PV's node and is not a multi-node scaling design.
For zero-gap skills updates, seed a new versioned PVC and roll workloads to that
claim instead of rewriting a mounted PVC in place.

The current local `kustomization.yaml` pins the business images separately from
the base YAML. When building a new local image, update all matching
`images[*].newTag` values and the `SANDBOX_IMAGE` tag in
`deer-flow-pvc-stack.yaml`, then apply the kustomization.

For a code-only update, rebuild just the changed business image, give it a new
tag, update that one `newTag`, and run `kubectl apply -k`. For a skill or PPT
template update, rebuild `deer-flow-skills` and the PPT sandbox image, update
their tags, then recreate the sync Job before applying:

```powershell
kubectl -n deer-flow delete job skills-sync --ignore-not-found
kubectl apply -k k8s/local-pvc
kubectl -n deer-flow wait --for=condition=complete job/skills-sync --timeout=300s
kubectl -n deer-flow rollout restart deployment/gateway
```

For a real environment, push images to a registry and replace `newName`/`newTag`
with immutable registry references, for example:

```yaml
images:
  - name: deer-flow-gateway
    newName: registry.example.com/deer-flow-gateway
    newTag: 2026.06.26-a1b2c3d
```

Do not deploy production with mutable tags such as `latest` or `pvc-local`.
Sandbox images must also be pinned by digest through `SANDBOX_IMAGE`; otherwise
new sandbox Pods can silently change when an upstream `latest` tag moves.

Before applying, refresh the ConfigMaps from the current config files:

```powershell
kubectl delete configmap -n deer-flow deer-flow-config --ignore-not-found
kubectl delete configmap -n deer-flow deer-flow-extensions-config --ignore-not-found
kubectl -n deer-flow create configmap deer-flow-config --from-file=config.yaml=config.yaml
kubectl -n deer-flow create configmap deer-flow-extensions-config --from-file=extensions_config.json=extensions_config.json
kubectl apply -k k8s/local-pvc
```

`deer-flow-nginx-config` is generated by `kustomization.yaml` from the checked-in
`k8s/local-pvc/nginx.conf`; do not create it separately. When the shared Nginx
configuration changes, update that local K8S copy in the same change.

The K8S sandbox configuration in `config.yaml` must use the in-cluster
Provisioner and the same key stored in `deer-flow-env`:

```yaml
sandbox:
  use: deerflow.community.aio_sandbox:AioSandboxProvider
  provisioner_url: http://provisioner:8002
  provisioner_api_key: $PROVISIONER_API_KEY
  sandbox_api_key: $SANDBOX_API_KEY
```

`PROVISIONER_API_KEY` protects the Provisioner control API, while the separate
`SANDBOX_API_KEY` protects code-execution APIs on every generated Sandbox Pod.
Store both values in `deer-flow-env`. Sandbox health probes read the key inside
the container and never place its value in the Pod specification.

Avoid relying on `latest` in this mode because the Kubernetes image cache can
continue using an older local digest after a rebuild.
Also avoid piping generated ConfigMap YAML through Windows PowerShell when the
source files contain non-ASCII text; it can turn Chinese display names into
`????`.

The Provisioner deployment explicitly configures generated Sandbox Pods with
500m/512Mi requests, 2 CPU/2Gi limits, and a liveness window of about one
minute. For heavier multi-node workloads, tune the
`SANDBOX_*_PROBE_*` and `SANDBOX_{CPU,MEMORY}_{REQUEST,LIMIT}` environment
variables on the Provisioner Deployment; these settings apply to newly created
Sandbox Pods, while existing Pods retain their original specification.

## Secrets

`deer-flow-env` stores application secrets such as auth signing keys and
provider API keys. `deer-flow-postgres` stores PostgreSQL credentials and the
Kubernetes database URL. In this deployment mode, do not put `DATABASE_URL` in
`deer-flow-env`; the Gateway receives it explicitly from `deer-flow-postgres`.

The default kustomization intentionally does not create real Secrets. Create
`deer-flow-env` and `deer-flow-postgres` before applying the stack. Production
deployments must create both Secrets from a secret manager, Sealed Secrets,
SOPS, or CI/CD secret injection and must rotate all local/default values before
exposure.

See `k8s/local-pvc/secrets/` for sanitized examples and local commands. Never
commit real Secret manifests or share `kubectl get secret -o yaml` output.

The Kustomization includes `namespace.yaml`, but Secrets must be created before
the workload bundle. On a fresh cluster, create the namespace first:

```powershell
kubectl apply -f k8s/local-pvc/namespace.yaml
```

## PostgreSQL migration

This stack runs PostgreSQL inside Kubernetes as a `StatefulSet` with its own
PVC and points Gateway at `postgres:5432` through the `deer-flow-postgres`
Secret. The local Compose PostgreSQL container is no longer part of the target
runtime once the dump has been restored.

Backup is handled by `postgres-backup`, which writes custom-format `pg_dump`
files to `deer-flow-postgres-backups-pvc`. To test a backup immediately:

```powershell
kubectl create job -n deer-flow --from=cronjob/postgres-backup postgres-backup-manual
kubectl -n deer-flow wait --for=condition=complete job/postgres-backup-manual --timeout=180s
```

To validate a restore without touching the live database, edit
`k8s/local-pvc/postgres-restore-job.yaml`, set `RESTORE_FILE` to a dump file
present in the backups PVC, keep `RESTORE_DATABASE=deerflow_restore_check`, and
apply it. The job drops/recreates only that scratch database and prints core
table counts.

Restoring over the live `POSTGRES_DB` is intentionally blocked unless
`RESTORE_DATABASE` is set to the live database name and
`ALLOW_PRODUCTION_RESTORE=true` is set in the job.

To use `http://127.0.0.1:2026` with this stack, stop the Docker Compose nginx
that binds port 2026, then port-forward the Kubernetes nginx service:

```powershell
docker compose --env-file .env -p deer-flow -f docker/docker-compose.yaml -f docker/docker-compose-local-prod.yaml stop nginx frontend gateway provisioner
kubectl -n deer-flow port-forward svc/nginx 2026:2026
```

## Exposing the app

The default stack keeps `nginx` as an internal `ClusterIP` Service. This is
intentional for local validation.

For a real environment, use one of the optional manifests in
`k8s/local-pvc/exposure/`:

- `nginx-loadbalancer.yaml` for clusters that support `type: LoadBalancer`
- `ingress.yaml` for environments with an Ingress Controller and TLS

Do not rely on `kubectl port-forward` for production traffic.
