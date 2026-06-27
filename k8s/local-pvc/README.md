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

For local Docker Desktop Kubernetes, tag freshly built images with explicit
local version tags and keep those tags in `kustomization.yaml`:

```powershell
docker tag deer-flow-gateway:latest deer-flow-gateway:dev-YYYYMMDD-N
docker tag deer-flow-provisioner:latest deer-flow-provisioner:dev-YYYYMMDD-N
docker tag deer-flow-frontend:latest deer-flow-frontend:dev-YYYYMMDD-N

kubectl kustomize k8s/local-pvc
kubectl apply -k k8s/local-pvc
```

The current local `kustomization.yaml` pins the business images separately from
the base YAML. When building a new local image, update only the matching
`images[*].newTag` value in `kustomization.yaml`, then apply the kustomization.

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
kubectl delete configmap -n deer-flow deer-flow-nginx-config --ignore-not-found
kubectl -n deer-flow create configmap deer-flow-config --from-file=config.yaml=config.yaml
kubectl -n deer-flow create configmap deer-flow-extensions-config --from-file=extensions_config.json=extensions_config.json
kubectl -n deer-flow create configmap deer-flow-nginx-config --from-file=nginx.conf=docker/nginx/nginx.conf
kubectl apply -k k8s/local-pvc
```

Avoid relying on `latest` in this mode because the Kubernetes image cache can
continue using an older local digest after a rebuild.
Also avoid piping generated ConfigMap YAML through Windows PowerShell when the
source files contain non-ASCII text; it can turn Chinese display names into
`????`.

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
