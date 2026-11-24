"""Private Studio authentication for outbound Buck Lab connectors."""
import getpass
import re


def bridge_post(client, path, **kwargs):
    """Use the Studio API gateway when its browser entry requires login."""
    response = client.post(path, **kwargs)
    origin = client.base_url
    studio = (origin.scheme == 'https' and origin.port in (None, 443)
              and re.fullmatch(r'(?:[a-z0-9-]+\.ms\.show|studio-[a-z0-9-]+\.api-inference\.modelscope\.net)', origin.host))
    if response.status_code in (401, 403) and studio:
        try:
            body = response.json()
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        if body.get('detail') == 'Invalid connector credential':
            response.raise_for_status()
        error = body.get('error')
        error = error if isinstance(error, dict) else {}
        web_challenge = str(body.get('Code')) == '10011402001'
        api_challenge = (origin.host.endswith('.api-inference.modelscope.net')
                         and response.status_code == 401
                         and 'modelscope token' in str(error.get('message', '')).lower())
        if web_challenge or api_challenge:
            if 'Authorization' not in client.headers:
                if origin.host.endswith('.ms.show'):
                    slug = origin.host.removesuffix('.ms.show')
                    client.base_url = origin.copy_with(host=f'studio-{slug}.api-inference.modelscope.net')
                token = getpass.getpass(f'ModelScope SDK token for {client.base_url.host}: ').strip()
                if not token or not token.isascii() or any(c.isspace() or not c.isprintable() for c in token):
                    raise SystemExit('Enter a valid ModelScope SDK token to access the private Studio.')
                client.headers['Authorization'] = 'Bearer ' + token
                return bridge_post(client, path, **kwargs)
        if web_challenge or api_challenge or response.status_code == 403 or error:
            # Only expose gateway identifiers and known messages. Responses can
            # contain reflected credentials, HTML or other untrusted content.
            identifiers = []
            for name, value in [('code', body.get('Code') or error.get('code')),
                                ('request_id', body.get('RequestId') or error.get('request_id'))]:
                if value is not None and re.fullmatch(r'[0-9a-fA-F-]{1,64}', str(value)):
                    identifiers.append(f'{name}={value}')
            detail = ' '.join(identifiers)
            if web_challenge or api_challenge:
                reason = 'ModelScope rejected the SDK token. Check that it is current and belongs to an account with access to this Studio.'
            else:
                reason = 'Access denied. Check this account can open the private Studio and verify the API endpoint shown by ModelScope.'
            raise SystemExit(f'{reason} HTTP {response.status_code} at {origin.host}{path} {detail}'.rstrip())
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise SystemExit(f'Expected Buck Lab JSON at {path} (HTTP {response.status_code}). Check the backend app URL.')
    return data
