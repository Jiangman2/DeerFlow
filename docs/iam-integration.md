# CNOOC IAM integration

DeerFlow's IAM integration has two independent paths. Keep them separate from
the existing CNOOC DataLake platform-token login: an IAM token is never written
to the `cnooc.platform_token` browser cookie.

## Browser sign-in

The login page discovers the `iam` provider from `auth.iam` and redirects to
`GET /api/v1/auth/iam/login`. Gateway stores a signed, short-lived state cookie
and uses the IAM authorization-code endpoints:

1. `/idp/oauth2/authorize`
2. `/idp/oauth2/getToken`
3. `/idp/oauth2/getUserInfo`

The callback is `GET /api/v1/auth/iam/callback`. It verifies state, obtains the
IAM UID and `spRoleList` application account, then resolves the account to
`<ad-account>@cnooc.com.cn`. Login is allowed only when that address already
belongs to an IAM-synchronized DeerFlow user. A local/non-IAM account using the
same corporate address is rejected instead of being adopted. The first browser
login binds the IAM UID in `oauth_id`; it then receives DeerFlow's normal
HttpOnly session cookie and CSRF cookie.

Configure the non-secret values in `config.yaml`:

```yaml
auth:
  iam:
    enabled: true
    base_url: https://iam.example.com
    client_id: deerflow
    client_secret: $IAM_CLIENT_SECRET
    redirect_uri: https://deerflow.example.com/api/v1/auth/iam/callback
    frontend_base_url: https://deerflow.example.com
```

`IAM_CLIENT_SECRET` must come from the runtime secret store. Register the exact
callback URL with IAM. Do not send the client secret, IAM access token, or IAM
user-info payload to the browser or application logs.

## BIM account synchronization

BIM calls Gateway directly at these routes:

| Operation | Route |
| --- | --- |
| Schema | `POST /api/v1/iam/account-sync` or `/SchemaService` |
| Create | `POST /api/v1/iam/account-sync/UserCreateService` |
| Update | `POST /api/v1/iam/account-sync/UserUpdateService` |
| Delete | `POST /api/v1/iam/account-sync/UserDeleteService` |

The request and successful response body are
`Base64(SM4-ECB-PKCS7(JSON))`. Gateway validates `bimRemoteUser`,
`bimRemotePwd`, and an SM3 signature over deterministic request fields. Deploy
the following values only as Gateway runtime secrets:

- `IAM_ACCOUNT_SYNC_SM4_KEY`
- `IAM_ACCOUNT_SYNC_REMOTE_USER`
- `IAM_ACCOUNT_SYNC_REMOTE_PASSWORD`

`__NAME__` is the required AD application account; `fullName`, `orgcode`, and
`__ENABLE__` are optional account properties. Older `oaAccount` and `state`
fields remain supported. A successful operation returns `resultCode: "0"` and
the stable DeerFlow UUID as `uid`. Disabling or deleting an account marks it
disabled and increments `token_version`, invalidating its active sessions.

Nginx must proxy both the exact base path and its slash-prefixed children to
Gateway before any restrictive authentication location:

```nginx
location = /api/v1/iam/account-sync { proxy_pass http://gateway; }
location ^~ /api/v1/iam/account-sync/ { proxy_pass http://gateway; }
```

The endpoint has no browser cookie or CSRF requirement because its encrypted,
credentialed envelope is the authentication boundary. That exemption does not
apply to any neighboring browser route.

## Migration and rollout

Older IAM accounts stored at `iam-account-<sha256>@iam-sso.example.com` are
migrated in place on their next synchronization or IAM login. Their DeerFlow
user IDs and conversations are preserved. For a pre-existing enterprise user,
set `oauth_provider` to `iam`, leave `oauth_id` empty until the first browser
login, and increment `token_version`; never reassign a corporate email that is
owned by a non-IAM local account.
