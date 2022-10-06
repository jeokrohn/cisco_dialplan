"""
Configuration class for dial plan provisioning
"""
import json
import logging
import os
from typing import Optional, Union

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel
from wxc_sdk.common import RouteType
from wxc_sdk.integration import Integration
from wxc_sdk.tokens import Tokens

__all__ = ['DialplanConfig', 'Config']

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


class DialplanConfig(BaseModel):
    """
    Configuration of a single dial plan
    """
    name: str
    #: trunk or route group
    route_type: RouteType
    #: name of trunk or route group
    route_choice: str
    #: list of names of GDPR catalogs to be routed via this dial plan
    catalogs: list[str]


class Config(BaseModel):
    """
    Configuration for dial plan provisioning script
    """
    #: tokens to be used. Either a full Tokens intance ot a simple string with an access token
    #: if not existing then when instantiating the calls, new access and refresh tokens are obtained using the Wx
    #: integration defined in the .env file by initiating an OAuth flow redirecting to a local webserver
    tokens: Optional[Union[str, Tokens]]
    #: list of dial plan configurations
    dialplans: Optional[list[DialplanConfig]]
    #: cache for YML config file path the config is mapped to
    yml_path: Optional[str]

    def json(self) -> str:
        """
        JSON representation of the config; without the yml_path
        """
        return super().json(exclude={'yml_path'})

    @staticmethod
    def from_yml(path: str) -> 'Config':
        """
        Read config from YML file
        :param path: path of the YML file
        :return: instance of Config class
        """
        with open(path, mode='r') as f:
            config_dict = yaml.safe_load(f)
        config: Config = Config.parse_obj(config_dict)
        config.yml_path = path
        if isinstance(config.tokens, str):
            config.tokens = Tokens(access_token=config.tokens)
        # make sure we have a valid access token
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
            self.tokens = integration.get_tokens_from_oauth_flow()
            if self.tokens:
                self.write()
