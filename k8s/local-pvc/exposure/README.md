# Exposure options

The default `k8s/local-pvc` kustomization keeps every Service internal and uses
`kubectl port-forward` for local access. Pick one of these optional exposure
manifests when you are ready to expose the app outside the cluster.

## Local or Cloud LoadBalancer

Use this when your Kubernetes environment supports `type: LoadBalancer`.

```powershell
kubectl apply -f k8s/local-pvc/exposure/nginx-loadbalancer.yaml
kubectl -n deer-flow get svc nginx-public -w
```

The Service exposes the existing in-cluster nginx proxy:

- `80 -> nginx:2026`
- `2026 -> nginx:2026`

For production, prefer the platform's standard LoadBalancer settings and
restrict source ranges when possible.

## Ingress with TLS

Use this when an Ingress Controller is installed. The template assumes
`ingressClassName: nginx`.

Before applying:

1. Replace `deer-flow.example.com` with the real domain.
2. Create the TLS Secret named `deer-flow-tls` in namespace `deer-flow`.
3. Confirm an IngressClass exists:

```powershell
kubectl get ingressclass
kubectl -n deer-flow create secret tls deer-flow-tls --cert=path/to/tls.crt --key=path/to/tls.key
kubectl apply -f k8s/local-pvc/exposure/ingress.yaml
```

If your platform uses cert-manager, replace the TLS Secret workflow with the
cluster's standard `cert-manager.io/cluster-issuer` annotation and Certificate
automation.
