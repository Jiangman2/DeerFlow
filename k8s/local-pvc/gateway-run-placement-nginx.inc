        # Merge these creation/owner sibling locations into the live server that handles
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

        # Owner-cancel 20261004 r1: metadata-only internal lookup.
        location = /_deerflow_resolve_gateway_owner {
            internal;
            proxy_pass http://gateway-run-dispatcher:8004/resolve;
            proxy_method GET;
            proxy_pass_request_body off;
            proxy_pass_request_headers off;
            proxy_set_header Content-Length "";
            proxy_set_header X-DeerFlow-Thread-ID $df_owner_thread_id;
            proxy_set_header X-DeerFlow-Run-ID $df_owner_run_id;
            proxy_set_header X-DeerFlow-Run-Operation $df_owner_operation;
            proxy_set_header X-DeerFlow-Run-Method $df_owner_method;
            proxy_set_header X-DeerFlow-Run-Action $df_owner_action;
            proxy_connect_timeout 2s;
            proxy_read_timeout 5s;
        }

        location ~ ^/api/langgraph/threads/(?<df_owner_thread_capture>[^/]+)/runs/(?<df_owner_run_capture>[^/]+)/(?<df_owner_operation_capture>cancel|stream)$ {
            # Snapshot before rewrite/auth subrequest changes URI/captures/method.
            set $df_owner_thread_id $df_owner_thread_capture;
            set $df_owner_run_id $df_owner_run_capture;
            set $df_owner_operation $df_owner_operation_capture;
            set $df_owner_method $request_method;
            # Presence, not the first value: FastAPI can use a later duplicate
            # action parameter. Match percent-encoded lowercase key letters too.
            # This set-only rewrite if does not alter URI, body, or method.
            set $df_owner_action "";
            if ($args ~ "(^|&)(a|%61)(c|%63)(t|%74)(i|%69)(o|%6[fF])(n|%6[eE])(=|&|$)") {
                set $df_owner_action present;
            }
            auth_request /_deerflow_resolve_gateway_owner;
            auth_request_set $df_owner_gateway_target $upstream_http_x_deerflow_gateway_target;
            proxy_next_upstream off;

            rewrite ^/api/langgraph/(.*) /api/$1 break;
            proxy_pass http://$df_owner_gateway_target;
            proxy_http_version 1.1;

            # Headers
            proxy_set_header Host $http_host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $forwarded_proto;
            proxy_set_header Connection '';

            # SSE/Streaming support
            proxy_set_header X-Accel-Buffering no;

            # Timeouts for long-running requests
            proxy_connect_timeout 600s;
            proxy_send_timeout 600s;
            proxy_read_timeout 600s;

            # Chunked transfer encoding
            chunked_transfer_encoding on;
                }
