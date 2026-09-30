        # Merge these two sibling locations into the live server that handles
        # port 2026, immediately before its /api/langgraph/ location. Do not
        # replace the whole live nginx.conf with this fragment.
        location = /_deerflow_select_gateway_run {
            internal;
            proxy_pass http://gateway-run-dispatcher:8004/select;
            proxy_method GET;
            proxy_pass_request_body off;
            proxy_pass_request_headers off;
            proxy_set_header Content-Length "";
            proxy_connect_timeout 2s;
            proxy_read_timeout 5s;
        }

        location ~ ^/api/langgraph/threads/[^/]+/runs/stream$ {
            auth_request /_deerflow_select_gateway_run;
            auth_request_set $run_gateway_target $upstream_http_x_deerflow_gateway_target;
            rewrite ^/api/langgraph/(.*) /api/$1 break;
            proxy_pass http://$run_gateway_target;
            proxy_http_version 1.1;
            proxy_set_header Host $http_host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $forwarded_proto;
            proxy_set_header Connection '';
            proxy_buffering off;
            proxy_connect_timeout 600s;
            proxy_send_timeout 600s;
            proxy_read_timeout 600s;
            chunked_transfer_encoding on;
        }
