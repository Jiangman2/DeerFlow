"""Reuse the offline real-Nginx harness with the new insertion planner."""
from pathlib import Path

HERE=Path(__file__).resolve().parent
source=(HERE.parent/'html-reader-20261114-r1/test_nginx_http.py').read_text(encoding='utf-8')
source=source.replace('import nginx_reader\nfrom activate_reader import SYNTAX_COMMAND',
'''import importlib.util
spec=importlib.util.spec_from_file_location('repair_reader_http',Path(__file__).with_name('nginx_reader.py'))
nginx_reader=importlib.util.module_from_spec(spec);sys.modules[spec.name]=nginx_reader;spec.loader.exec_module(nginx_reader)
operator_spec=importlib.util.spec_from_file_location('repair_reader_operator',Path(__file__).with_name('repair_operator.py'))
operator=importlib.util.module_from_spec(operator_spec);operator_spec.loader.exec_module(operator)
SYNTAX_COMMAND=operator.SYNTAX''')
source=source.replace("upstream='http://$gateway_upstream' if variable else 'http://gateway_upstream'",
                      "upstream='http://${gateway_upstream}' if variable == 2 else 'http://$gateway_upstream' if variable else 'http://gateway_upstream'")
old="nginx_reader.reader_block(upstream,'$scheme') + '\\nlocation /api/ { proxy_set_header Cookie $http_cookie; proxy_pass '+upstream+'; }\\nlocation / { return 404; } } }\\n'"
new="'\\nlocation ~ \\\"\\\\\\\\.html$\\\" { return 404; }\\nlocation /api/ { proxy_set_header Cookie $http_cookie; proxy_pass '+upstream+'; }\\nlocation / { return 404; } } }\\n'\n        config,_=nginx_reader.add_reader(config)"
assert source.count(old)==1
source=source.replace(old,new).replace('location /api/ { proxy_set_header Cookie','location ^~ /api/ { proxy_set_header Cookie')
source=source.replace('sys.exit(run_mode(False) or run_mode(True))','sys.exit(run_mode(False) or run_mode(True) or run_mode(2))')
compile(source,'test_nginx_http.py','exec')
(HERE/'test_nginx_http.py').write_bytes(source.encode())
print('OFFLINE_SERVING_READER_HTTP_TEST_PREPARED')

if __name__=='__main__':pass
