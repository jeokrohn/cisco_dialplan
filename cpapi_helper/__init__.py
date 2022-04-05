import logging
from collections.abc import Generator, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from itertools import zip_longest, chain
from typing import Type, List, Any, Callable

from pydantic import Field
from wxc_sdk import WebexSimpleApi
from wxc_sdk.base import webex_id_to_uuid, ApiModel, to_camel
from wxc_sdk.rest import RestSession
from wxc_sdk.tokens import Tokens

__all__ = ['DialPlan', 'DialPatternStatus', 'Trunk', 'RouteGroup', 'CPAPISession',
           'CPAPIHelper']

log = logging.getLogger(name=__name__)


class DialPlan(ApiModel):
    dialplan_id: str = Field(alias='id')
    name: str
    url: str
    route_identity: str
    route_identity_type: str
    route_identity_id: str


class DialPatternStatus(ApiModel):
    pattern: str = Field(alias='dialPattern')
    status: str = Field(alias='patternStatus')
    message: str


class Trunk(ApiModel):
    id: str
    name: str
    url: str
    location: Any
    in_use: bool


class RouteGroup(ApiModel):
    id: str
    name: str
    url: str
    in_use: bool


class CPAPISession(RestSession):

    def rest_patch(self, *args, **kwargs) -> dict:
        """
        PATCH request

        :param args:
        :param kwargs:
        """
        return self._request('PATCH', *args, **kwargs)

    def follow_pagination(self, *, url: str, model: Type[ApiModel] = None,
                          params: dict = None, item_key: str = None, **kwargs) -> Generator[Any, None, None]:
        if model is None:
            def parser(x):
                return x
        else:
            parser = model.parse_obj
        while url:
            log.debug(f'{self}.pagination: getting {url}')
            response, data = self._request_w_response('GET', url=url, params=params, **kwargs)
            # params only in first request. In subsequent requests we rely on the completeness of the 'next' URL
            params = None
            # try to get the next page (if present)
            try:
                url = data['paging']['next']
            except KeyError:
                url = None
            # return all items
            if item_key is None:
                if 'items' in data:
                    item_key = 'items'
                else:
                    # we go w/ the first return value that is a list
                    item_key = next((k for k, v in data.items()
                                     if isinstance(v, list)))
            items = data.get(item_key)
            for item in items:
                yield parser(item)


@dataclass(init=False)
class CPAPIHelper:
    """
    Helper class implementing CPAPI endpoints required for dial plan provisioning

    """
    session: CPAPISession
    base: str

    def endpoint(self, *, path: str = None) -> str:
        path = path and f'/{path}' or ''
        return f'{self.base}{path}'

    def __init__(self, *, access_token: str, org_id: str = None):
        """
        :param access_token:
        """
        tokens = Tokens(access_token=access_token)
        if not org_id:
            # Default: org of the token owner
            with WebexSimpleApi(tokens=tokens) as api:
                me = api.people.me()
                org_id = webex_id_to_uuid(me.org_id)

        # get service catalog
        session = CPAPISession(tokens=tokens, concurrent_requests=10)
        catalog = session.rest_get(f'https://u2c.wbx2.com/u2c/api/v1/org/{org_id}/catalog')

        # determine CPAPI base url
        cpapi = next((service for service in catalog['services']
                      if service['serviceName'] == 'cpapi'), None)
        if not cpapi:
            raise KeyError('CPAPI service not found')
        cpapi_url = cpapi['serviceUrls'][0]['baseUrl']

        self.base = f'{cpapi_url}/customers/{org_id}'
        self.session = session
        self.ci_org_id = org_id

    def dialplans_list(self, name: str = None) -> Generator[DialPlan, None, None]:
        """
        list dial plans

        :param name
        :return:
        """
        if name:
            params = {'dialPlanName': name}
        else:
            params = None
        url = self.endpoint(path=f'dialplans')
        # noinspection PyTypeChecker
        return self.session.follow_pagination(url=url, model=DialPlan, params=params)

    def dialplan_create(self, *, name: str, route_identity: str, route_identity_type: str) -> str:
        """

        :param name:
        :param route_identity:
        :param route_identity_type: TRUNK or ROUTE_GROUP
        :return dialplan id
        """
        body = {to_camel(param): value
                for index, (param, value) in enumerate(locals().items())
                if index}
        allowed = ['TRUNK', 'ROUTE_GROUP']
        if route_identity_type not in allowed:
            raise ValueError(f'route_identity_type needs to be one of {", ".join(allowed)}')
        url = self.endpoint(path=f'dialplans')
        data = self.session.rest_post(url=url, json=body)
        return data['id']

    def dialplan_delete(self, dialplan_id: str):
        """
        Delete a dialplan

        :param dialplan_id:
        """
        url = self.endpoint(path=f'dialplans/{dialplan_id}')
        self.session.rest_delete(url)

    def dial_plan_update_routing(self, dialplan_id: str, route_identity: str, route_identity_type: str):
        """
        Update the routing choice on a dial plan

        :param dialplan_id:
        :param route_identity:
        :param route_identity_type:
        :return:
        """
        body = {to_camel(param): value
                for index, (param, value) in enumerate(locals().items())
                if index > 1}
        allowed = ['TRUNK', 'ROUTE_GROUP']
        if route_identity_type not in allowed:
            raise ValueError(f'route_identity_type needs to be one of {", ".join(allowed)}')
        url = self.endpoint(path=f'dialplans/{dialplan_id}')
        self.session.rest_patch(url, json=body)

    def dialplan_patterns(self, dialplan_id: str) -> Generator[str, None, None]:
        """
        Get dial plan patterns in dial plan

        :param dialplan_id:
        :return:
        """
        url = self.endpoint(path=f'dialplans/{dialplan_id}/dialpatterns')
        return self.session.follow_pagination(url=url, model=None)

    def _dialplan_patterns_patch(self, dialplan_id: str, patterns: List[str],
                                 action: str) -> List[DialPatternStatus]:
        url = self.endpoint(path=f'dialplans/{dialplan_id}/dialpatterns')
        body = {'dialPatterns': [{'action': action,
                                  'dialPattern': pattern}
                                 for pattern in patterns]}

        data = self.session.rest_patch(url, json=body)
        if not data:
            return []
        result = [DialPatternStatus.parse_obj(o)
                  for o in data.get('dialPatternStatus', [])]
        return result

    def dialplan_patterns_delete(self, dialplan_id: str,
                                 patterns: List[str]) -> List[DialPatternStatus]:
        """
        Remove dial plan patterns from dial plan
        :param dialplan_id:
        :param patterns:
        :return:
        """
        return self._dialplan_patterns_patch(dialplan_id, patterns, action='DELETE')

    def dialplan_patterns_add(self, dialplan_id: str,
                              patterns: List[str]) -> List[DialPatternStatus]:
        """
        Add dial plan patterns to dial plan. If any pattern wasn't added then A status record is returned for each
        pattern which couldn't get added
        :param dialplan_id:
        :param patterns:
        :return:
        """
        return self._dialplan_patterns_patch(dialplan_id, patterns, action='ADD')

    def dialplan_bulk_update(self, *, dialplan_name: str, patterns: Iterable[str],
                             delete_only: bool = False) -> List[DialPatternStatus]:
        """
        bulk add/remove patterns to/from dial plan

        :param dialplan_name:
        :param patterns:
        :param delete_only:
        :return:
        """
        dialplan = next((dp for dp in self.dialplans_list(name=dialplan_name)
                         if dp.name == dialplan_name), None)
        if not dialplan:
            raise KeyError(f'Dialplan {dialplan_name} not found')

        existing_patterns = set(self.dialplan_patterns(dialplan_id=dialplan.dialplan_id))
        desired_patterns = set(patterns)
        to_add = [p for p in desired_patterns
                  if p not in existing_patterns]
        to_delete = [p for p in existing_patterns
                     if p not in desired_patterns]

        def batch_update(operation: Callable, patterns: Iterable[str]) -> list[DialPatternStatus]:
            # update patterns in batches of 200
            p_iter = iter(patterns)
            batch_iter = [p_iter] * 200
            batches = ([e for e in batch if e] for batch in zip_longest(*batch_iter, fillvalue=None))
            with ThreadPoolExecutor() as pool:
                batch_results = list(pool.map(lambda batch: operation(dialplan_id=dialplan.dialplan_id,
                                                                      patterns=batch),
                                              batches))
            return list(chain.from_iterable(batch_results))

        results = []
        if to_delete:
            results.extend(batch_update(self.dialplan_patterns_delete, patterns=to_delete))
        if to_add and not delete_only:
            results.extend(batch_update(self.dialplan_patterns_add, patterns=to_add))
        return results

    def trunks_list(self, *, order: str = 'name',
                    name: str = None) -> Generator[Trunk, None, None]:
        """
        List trunks

        :param order:
        :param name: name of LGW
        :return:
        """
        params = {param: value
                  for index, (param, value) in enumerate(locals().items())
                  if index and value is not None}
        url = self.endpoint(path=f'localgateways')
        return self.session.follow_pagination(url=url, model=Trunk, params=params)

    def routegroups_list(self, order: str = 'name',
                         name: str = None) -> Generator[RouteGroup, None, None]:
        """
        List route groups

        :param order:
        :param name: name of route group
        :return:
        """
        params = {key: value
                  for index, (key, value) in enumerate(locals().items())
                  if index and value is not None}
        url = self.endpoint(path=f'routegroups')
        return self.session.follow_pagination(url=url, model=RouteGroup, params=params)
