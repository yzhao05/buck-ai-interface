"""Show a configuration status page until the required Studio secret is set."""
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def token_problem(token):
    if token is None:
        return '容器没有收到 APP_ACCESS_TOKEN。'
    if not token.strip():
        return '容器收到了 APP_ACCESS_TOKEN，但值为空或只有空格。'
    if len(token) < 20:
        return '容器收到了 APP_ACCESS_TOKEN，但不足 20 个字符。'
    return None


def main():
    problem = token_problem(os.environ.get('APP_ACCESS_TOKEN'))
    if problem is None:
        print('Buck Lab: required app token present; starting backend.', flush=True)
        os.execv(sys.executable, [sys.executable, '-m', 'uvicorn', 'backend.main:app',
                                  '--host', '0.0.0.0', '--port', '7860', '--workers', '1'])

    print('Buck Lab 配置检查：' + problem, flush=True)
    page = ('<!doctype html><meta charset="utf-8"><title>Buck Lab 配置检查</title>'
            '<h1>Buck Lab 配置检查</h1><p>' + problem + '</p>'
            '<p>请在部署设置的密文变量中重新填写访问口令，再确认并部署。实验后端尚未启动。</p>').encode()

    class StatusPage(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split('?', 1)[0] != '/':
                self.send_error(503, 'Backend not configured')
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(('0.0.0.0', 7860), StatusPage) as server:
        server.serve_forever()


if __name__ == '__main__':
    main()
