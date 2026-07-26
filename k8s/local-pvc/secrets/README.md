# Secret management

This directory contains examples only. Do not commit real Secret manifests,
API keys, access tokens, database passwords, or rendered `kubectl get secret -o
yaml` output.

## Local Docker Desktop Kubernetes

Create the namespace once before creating Secrets:

```powershell
kubectl apply -f k8s/local-pvc/namespace.yaml
```

Create the application runtime Secret from your local `.env` file:

```powershell
kubectl create secret generic deer-flow-env `
  -n deer-flow `
  --from-env-file=.env `
  --dry-run=client -o yaml | kubectl apply -f -
```

Kubernetes sandbox execution requires two distinct, non-empty keys:
`PROVISIONER_API_KEY` protects the Provisioner control API and
`SANDBOX_API_KEY` protects code execution inside every generated Sandbox Pod.
Generate both values in `.env`, create `deer-flow-env` from that file, and
reference them from `config.yaml` as `sandbox.provisioner_api_key` and
`sandbox.sandbox_api_key`. Do not reuse one value for both boundaries.

Before restarting workloads, verify the Secret contains both keys without
printing their values:

```powershell
$secret = kubectl get secret deer-flow-env -n deer-flow -o json | ConvertFrom-Json
@("PROVISIONER_API_KEY", "SANDBOX_API_KEY") | ForEach-Object {
  if (-not $secret.data.PSObject.Properties.Name.Contains($_)) {
    throw "deer-flow-env is missing required key: $_"
  }
}
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

## CNOOC data-lake SSO

When DeerFlow is opened from the data-lake platform, the platform redirects to
`/oauth/cnooc/login?token=<platform-token>`. Gateway validates that token and
then sets the normal DeerFlow `access_token` cookie. Add these values to the
`.env` file used to create `deer-flow-env`:

```dotenv
CNOOC_OAUTH_BASE_URL=https://datalake.cnooc
CNOOC_OAUTH_APP_NAME=deerflow
CNOOC_OAUTH_APP_ID=replace-me
CNOOC_OAUTH_TOKEN_CHECK_API_TOKEN=replace-me
CNOOC_OAUTH_TOKEN_CHECK_LOGIN_USER_CODE=replace-me
CNOOC_OAUTH_USER_INFO_API_TOKEN=replace-me
CNOOC_OAUTH_TLS_VERIFY=true
NEXT_PUBLIC_AUTH_LOGIN_URL=https://datalake.cnooc/path/to/deerflow-login
```

`NEXT_PUBLIC_AUTH_LOGIN_URL` is the browser-facing data-lake login entry used
when an existing DeerFlow session expires. DeerFlow appends a `next` query
parameter so the platform can redirect back through
`/api/v1/auth/cnooc/login?token=<platform-token>&next=<path>`.

After changing these values, recreate `deer-flow-env` and restart Gateway:

```powershell
kubectl create secret generic deer-flow-env `
  -n deer-flow `
  --from-env-file=.env `
  --dry-run=client -o yaml | kubectl apply -f -

kubectl rollout restart deployment/gateway -n deer-flow
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
