#!/usr/bin/env python
"""
Configure dial plans according to config file
"""
import logging
import os
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from csv import DictReader
from dataclasses import dataclass

from config import *
from cpapi_helper import *

log = logging.getLogger(__name__)


@dataclass()
class Catalog:
    name: str
    patterns: list[str]


def read_patterns(*, csv_file: str) -> dict[str, Catalog]:
    log.info(f'reading patterns from {csv_file}')
    with open(csv_file, mode='r') as f:
        reader = DictReader(f, fieldnames=['catalog', 'pattern'])
        records = [r for r in reader]
    patterns_by_catalog = defaultdict(list)
    for r in records:
        patterns_by_catalog[r['catalog']].append(r['pattern'])
    return {name: Catalog(name=name, patterns=patterns)
            for name, patterns in patterns_by_catalog.items()}


def configure_wxc(*, csv_file:str):
    """
    Configure dial plans and patterns based on YML config
    """
    config = Config.from_yml('config.yml')
    catalogs = read_patterns(csv_file=csv_file)
    api = CPAPIHelper(access_token=config.tokens.access_token)
    trunks = {trunk.name: trunk for trunk in api.trunks_list()}
    route_groups = {rg.name: rg for rg in api.routegroups_list()}
    dialplans = {dp.name: dp for dp in api.dialplans_list()}

    def configure_dialplan(dialplan: DialplanConfig,
                           delete_only: bool):
        # check existence of trunk or route group
        if dialplan.route_type == RouteTypeConfig.trunk:
            route_choice = trunks.get(dialplan.route_choice)
        else:
            route_choice = route_groups.get(dialplan.route_choice)
        if route_choice is None:
            log.error(f'{dialplan.name}: unknown route choice:'
                      f'{dialplan.route_choice}({dialplan.route_type.value})"')
            return

        if dialplan.name not in dialplans:
            if delete_only:
                return
            # dialplan needs to be created
            api.dialplan_create(name=dialplan.name,
                                route_identity=route_choice.id,
                                route_identity_type=dialplan.route_type.value)
            log.info(f'{dialplan.name}: created')
        else:
            # check if the route choice has changed
            wxc_dialplan = dialplans[dialplan.name]
            wxc_dialplan: DialPlan
            if dialplan.route_type.value != wxc_dialplan.route_identity_type or \
                    route_choice.id != wxc_dialplan.route_identity_id:
                if not delete_only:
                    # route choice needs to be updated
                    api.dial_plan_update_routing(dialplan_id=wxc_dialplan.dialplan_id,
                                                 route_identity=route_choice.id,
                                                 route_identity_type=dialplan.route_type.value)
                    log.info(f'{dialplan.name}: Updated route choice: '
                             f'{dialplan.route_choice}({dialplan.route_type.value})"')

        patterns = []
        for catalog_name in dialplan.catalogs:
            if not (catalog := catalogs.get(catalog_name)):
                log.error(f'{dialplan.name}: invalid catalog name "{catalog_name}" in dial plan')
                continue
            patterns.extend(catalog.patterns)

        # make sure to only add unique patterns
        patterns = list(set(patterns))
        patterns.sort()

        api.dialplan_bulk_update(dialplan_name=dialplan.name,
                                 patterns=patterns,
                                 delete_only=delete_only)
        log.info(f'{dialplan.name}: bulk updated with {len(patterns)} patterns')
        return

    # we go through the dial plans twice:
    # * 1st round: we only delete patterns
    # * 2nd round: add patterns
    # goal: avoid conflicts if a catalog moves between dial plans
    for delete_only in [True, False]:
        with ThreadPoolExecutor() as pool:
            list(pool.map(lambda dialplan: configure_dialplan(dialplan=dialplan, delete_only=delete_only),
                          config.dialplans))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 2:
        print(f'usage: {os.path.basename(sys.argv[0])} csvfile')
        exit(1)
    configure_wxc(csv_file=sys.argv[1])
