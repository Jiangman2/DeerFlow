# Exposure options

The default `k8s/local-pvc` kustomization keeps every Service internal and uses
`kubectl port-forward` for local access. Pick one of these optional exposure
manifests when you are ready to expose the app outside the cluster.

## Production Recommendation

For a production or long-running intranet deployment, do not rely on
`kubectl port-forward`. Use one stable entrypoint:

1. **Ingress with TLS and a real domain**: recommended for production because it
   supports HTTPS certificates, host routing, proxy timeouts, and central access
   control.
2. **Cloud or platform LoadBalancer**: recommended when the Kubernetes platform
   provides a managed external load balancer.
3. **Fixed NodePort**: acceptable for bare-metal or isolated intranet clusters
   when an external reverse proxy, firewall, or load balancer forwards traffic
   to the node port.

Docker Desktop may leave `type: LoadBalancer` Services in `<pending>` and may
not expose NodePorts on Windows `127.0.0.1`. That is a local platform limitation,
not an application requirement. In production, install an Ingress controller or
LoadBalancer implementation such as the platform cloud controller or MetalLB.

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

## Fixed NodePort

Use this for an internal network or bare-metal deployment when each Kubernetes
node is reachable from clients or from an external reverse proxy:

```powershell
kubectl apply -f k8s/local-pvc/exposure/nginx-nodeport.yaml
kubectl -n deer-flow get svc nginx-nodeport
```

The fixed node port is:

- `nodeIP:32026 -> nginx:2026`

For production, put a reverse proxy or load balancer in front of this port and
terminate TLS there. Avoid exposing NodePorts directly to the public Internet.

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
