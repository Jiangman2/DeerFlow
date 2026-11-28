# Include inside the datax-01 HTTPS server shared with heai.cnooc.com.cn.
if ($host = cnoocclaw.cnooc.com.cn) {
    rewrite ^/join$ /__deerflow_invite_redirect last;
}

location = /__deerflow_invite_redirect {
    internal;
    absolute_redirect off;
    return 302 /platform/organizations?invite_code=$arg_code;
}
