import json
from enum import Enum
from typing import Optional, Union
from wxc_sdk.tokens import Tokens
from wxc_sdk.integration import Integration
import concurrent.futures
import os

import http.server
import urllib.parse
import threading
import webbrowser
import uuid
import socketserver
import requests

import logging

import yaml
from pydantic import BaseModel
from dotenv import load_dotenv

__all__ = ['RouteTypeConfig', 'DialplanConfig', 'Config']

load_dotenv()

log = logging.getLogger(__name__)



def build_integration() -> Integration:
    """
    read integration parameters from environment variables and create an integration
    :return: :class:`wxc_sdk.integration.Integration` instance
    """
    client_id = os.getenv('TOKEN_INTEGRATION_CLIENT_ID')
    client_secret = os.getenv('TOKEN_INTEGRATION_CLIENT_SECRET')
    scopes = os.getenv('TOKEN_INTEGRATION_CLIENT_SCOPES')
    redirect_url = 'http://localhost:6001/redirect'
    if not all((client_id, client_secret, scopes)):
        raise ValueError('failed to get integration parameters from environment')
    return Integration(client_id=client_id, client_secret=client_secret, scopes=scopes,
                       redirect_url=redirect_url)


def get_tokens_from_oauth_flow(integration: Integration) -> Optional[Tokens]:
    """
    Initiate an OAuth flow to obtain new tokens.

    start a local webserver on port 6001 o serve the last step in the OAuth flow

    :param integration: Integration to use for the flow
    :type: :class:`wxc_sdk.integration.Integration`
    :return: set of new tokens if successful, else None
    :rtype: :class:`wxc_sdk.tokens.Tokens`
    """

    def serve_redirect():
        """
        Temporarily start a web server to serve the redirect URI at http://localhost:6001/redirect'
        :return: parses query of the GET on the redirect URI
        """

        # mutable to hold the query result
        oauth_response = dict()

        class RedirectRequestHandler(http.server.BaseHTTPRequestHandler):
            # handle the GET request on the redirect URI

            # noinspection PyPep8Naming
            def do_GET(self):
                # serve exactly one GET on the redirect URI and then we are done

                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == '/redirect':
                    log.debug('serve_redirect: got GET on /redirect')
                    query = urllib.parse.parse_qs(parsed.query)
                    oauth_response['query'] = query
                    # we are done
                    self.shutdown(self.server)
                self.send_response(200)
                self.flush_headers()

            @staticmethod
            def shutdown(server: socketserver.BaseServer):
                log.debug('serve_redirect: shutdown of local web server requested')
                threading.Thread(target=server.shutdown, daemon=True).start()

        httpd = http.server.HTTPServer(server_address=('', 6001),
                                       RequestHandlerClass=RedirectRequestHandler)
        log.debug('serve_redirect: starting local web server for redirect URI')
        httpd.serve_forever()
        httpd.server_close()
        log.debug(f'serve_redirect: server terminated, result {oauth_response["query"]}')
        return oauth_response['query']

    state = str(uuid.uuid4())
    auth_url = integration.auth_url(state=state)
    with concurrent.futures.ThreadPoolExecutor() as executor:
        # start web server
        fut = executor.submit(serve_redirect)

        # open authentication URL in local webbrowser
        webbrowser.open(auth_url)
        # wait for GET on redirect URI and get the result (parsed query of redirect URI)
        try:
            result = fut.result(timeout=120)
        except concurrent.futures.TimeoutError:
            try:
                # post a dummy response to the redirect URI to stop the server
                with requests.Session() as session:
                    session.get(integration.redirect_url, params={'code': 'foo'})
            except Exception:
                pass
            log.warning('Authorization did not finish in time (60 seconds)')
            return

    code = result['code'][0]
    response_state = result['state'][0]
    assert response_state == state

    # get access tokens
    new_tokens = integration.tokens_from_code(code=code)
    if new_tokens is None:
        log.error('Failed to obtain tokens')
        return None
    return new_tokens


class RouteTypeConfig(str, Enum):
    trunk = 'TRUNK'
    route_group = 'ROUTE_GROUP'


class DialplanConfig(BaseModel):
    name: str
    route_type: RouteTypeConfig
    route_choice: str
    catalogs: list[str]

class Config(BaseModel):
    # can be a string
    tokens: Optional[Union[str, Tokens]]
    dialplans: Optional[list[DialplanConfig]]
    yml_path: Optional[str]

    def json(self) -> str:
        return super().json(exclude={'yml_path'})

    @staticmethod
    def from_yml(path: str) -> 'Config':
        """
        Read config from YML file
        :param path:
        :return:
        """
        with open(path, mode='r') as f:
            config_dict = yaml.safe_load(f)
        config: Config = Config.parse_obj(config_dict)
        config.yml_path = path
        if isinstance(config.tokens, str):
            config.tokens = Tokens(access_token=config.tokens)
        config.assert_access_token()
        return config

    def write(self):
        """
        write config back to YML file
        """
        data = json.loads(self.json())
        with open(self.yml_path, mode='w') as f:
            yaml.dump(data, f, default_flow_style=False)

    def assert_access_token(self):
        """
        try to assert a valid access token.
        """
        if self.tokens and not self.tokens.refresh_token:
            # not a "full" token; we can't do anything
            return
        integration = build_integration()
        if self.tokens:
            # validate tokens
            tokens: Tokens
            changed = integration.validate_tokens(tokens=self.tokens)
            if not self.tokens.access_token:
                self.tokens = None
            elif changed:
                self.write()
        if not self.tokens:
            # get new tokens via integration if needed
            self.tokens = get_tokens_from_oauth_flow(integration=integration)
            if self.tokens:
                self.tokens.set_expiration()
                self.write()
