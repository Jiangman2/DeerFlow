# Secret management

This directory contains examples only. Do not commit real Secret manifests,
API keys, access tokens, database passwords, or rendered `kubectl get secret -o
yaml` output.

## Local Docker Desktop Kubernetes

Create the application runtime Secret from your local `.env` file:

```powershell
kubectl create secret generic deer-flow-env `
  -n deer-flow `
  --from-env-file=.env `
  --dry-run=client -o yaml | kubectl apply -f -
```

For the `local-pvc` stack, `DATABASE_URL` is owned by the
`deer-flow-postgres` Secret. Remove `DATABASE_URL` from `deer-flow-env` so the
same application Secret can be reused without carrying the old Compose database
address:

```powershell
kubectl patch secret deer-flow-env `
  -n deer-flow `
  --type=json `
  -p='[{"op":"remove","path":"/data/DATABASE_URL"}]'
```

Create or rotate the PostgreSQL Secret with generated values:

```powershell
$dbPassword = -join ((48..57 + 65..90 + 97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
$databaseUrl = "postgresql://deerflow:$dbPassword@postgres:5432/deerflow"

kubectl create secret generic deer-flow-postgres `
  -n deer-flow `
  --from-literal=POSTGRES_DB=deerflow `
  --from-literal=POSTGRES_USER=deerflow `
  --from-literal=POSTGRES_PASSWORD=$dbPassword `
  --from-literal=DATABASE_URL=$databaseUrl `
  --dry-run=client -o yaml | kubectl apply -f -
```

Restart the affected workloads after rotating database or auth secrets:

```powershell
kubectl rollout restart statefulset/postgres -n deer-flow
kubectl rollout restart deployment/gateway deployment/frontend -n deer-flow
```

## Production

Use a real secret-management workflow instead of committed Secret YAML:

- External Secrets Operator with a cloud secret manager
- Sealed Secrets or SOPS-encrypted manifests
- CI/CD secret injection into Kubernetes

Required production rules:

- Keep application secrets in `deer-flow-env`.
- Keep PostgreSQL credentials and `DATABASE_URL` in `deer-flow-postgres`.
- Do not store `DATABASE_URL` in both Secrets.
- Rotate all local/default credentials before exposing the app.
- Treat base64 Secret data as plaintext, not encryption.
- Avoid printing Secret YAML in CI logs, screenshots, issue reports, or chat.

