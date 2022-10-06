#!/usr/bin/env python
"""
Configure dial plans according to config file
"""
import asyncio
import logging
import os
import sys
from collections import defaultdict
from collections.abc import Iterable, Generator
from csv import DictReader
from dataclasses import dataclass
from functools import reduce

from wxc_sdk.as_api import AsWebexSimpleApi
from wxc_sdk.common import RouteType, PatternAction
from wxc_sdk.telephony.prem_pstn.dial_plan import DialPlan, PatternAndAction
from wxc_sdk.telephony.prem_pstn.route_group import RouteGroup
from wxc_sdk.telephony.prem_pstn.trunk import Trunk

from config import *

log = logging.getLogger(__name__)


@dataclass()
class Catalog:
    name: str
    patterns: list[str]


def read_patterns(*, csv_file: str) -> dict[str, Catalog]:
    """
    Read patterns from CSV file.

    CSV file is expected to have two columns
        * catalog: catalog identifier; referenced in config.yml
        * pattern: pattern

    :param csv_file:
    :return: Dictionary of patterns per catalog identifier
    """
    log.info(f'reading patterns from {csv_file}')
    with open(csv_file, mode='r') as f:
        reader = DictReader(f, fieldnames=['catalog', 'pattern'])
        records = [r for r in reader]
    # Now create a dictionary that has one
    patterns_by_catalog = reduce(lambda pbc, record: pbc[record['catalog']].append(record['pattern']) or pbc,
                                 records,
                                 defaultdict(list))
    return {name: Catalog(name=name, patterns=patterns)
            for name, patterns in patterns_by_catalog.items()}


async def configure_wxc(*, csv_file: str):
    """
    Configure dial plans and patterns. Patterns are read from the CSV file passed as argument. The mapping of
    catalogs to dial plans and the dial plan configuration is read from a YML config file.
    """
    # instantiate config from configuration YML file
    config = Config.from_yml('config.yml')

    # read catalogs with their respective patterns from CSV file
    catalogs = read_patterns(csv_file=csv_file)

    # instantiate an API instance to be used
    async with AsWebexSimpleApi(tokens=config.tokens.access_token) as api:

        # shortcut to premises PSTN endpoints
        prem_pstn = api.telephony.prem_pstn

        # get list of trunks, list of route groups, list of dial plans
        trunk_list, rg_list, dp_list = await asyncio.gather(prem_pstn.trunk.list(),
                                                            prem_pstn.route_group.list(),
                                                            prem_pstn.dial_plan.list())

        # dict of existing trunks by name
        trunks: dict[str, Trunk] = {trunk.name: trunk for trunk in trunk_list}

        # dict of existing route groups by name
        route_groups: dict[str, RouteGroup] = {rg.name: rg for rg in rg_list}

        # dict of dial plans by name
        dialplans: dict[str, DialPlan] = {dp.name: dp for dp in dp_list}

        async def configure_dialplan(dialplan: DialplanConfig,
                                     delete_only: bool):
            """
            Apply configuration for one dial plan to Webex Calling
            :param dialplan:
            :param delete_only:
            :return:
            """
            # check existence of trunk or route group
            route_id = None
            if dialplan.route_type == RouteType.trunk:
                route_choice = trunks.get(dialplan.route_choice)
                if route_choice is not None:
                    route_id = route_choice.trunk_id
            else:
                route_choice = route_groups.get(dialplan.route_choice)
                if route_choice is not None:
                    route_id = route_choice.rg_id
            if route_choice is None:
                log.error(f'{dialplan.name}: unknown route choice:'
                          f'{dialplan.route_choice}({dialplan.route_type.value})"')
                return

            if (wxc_dialplan := dialplans.get(dialplan.name)) is None:
                if delete_only:
                    return
                # dialplan needs to be created
                response = await prem_pstn.dial_plan.create(name=dialplan.name,
                                                            route_id=route_id,
                                                            route_type=dialplan.route_type.value)
                wxc_dialplan = await prem_pstn.dial_plan.details(dial_plan_id=response.dial_plan_id)
                log.info(f'{dialplan.name}: created')
            elif not delete_only:
                # check if the route choice has changed
                if dialplan.route_type != wxc_dialplan.route_type or route_id != wxc_dialplan.route_id:
                    # route choice needs to be updated
                    update = wxc_dialplan.copy(deep=True)
                    update.route_id = route_id
                    update.route_type = dialplan.route_type
                    await api.telephony.prem_pstn.dial_plan.update(update=update)
                    log.info(f'{dialplan.name}: Updated route choice: '
                             f'{dialplan.route_choice}({dialplan.route_type.value})"')

            # get combined list of patterns of all catalogs configured for this dialplan
            patterns = []
            for catalog_name in dialplan.catalogs:
                if (catalog := catalogs.get(catalog_name)) is None:
                    log.error(f'{dialplan.name}: invalid catalog name "{catalog_name}" in dial plan')
                    continue
                patterns.extend(catalog.patterns)

            # make sure to only add unique patterns
            patterns = list(set(patterns))
            patterns.sort()

            # get configured patterns
            curr_patterns = set(await prem_pstn.dial_plan.patterns(dial_plan_id=wxc_dialplan.dial_plan_id))

            to_add = set(patterns) - curr_patterns
            to_delete = curr_patterns - set(patterns)

            # remove patterns not needed any more
            async def modify_patterns(*, dp_id: str, action: PatternAction, patterns: Iterable[str]):
                """
                Modify patterns of current dial plan.

                The modifications are applied in batches of 200

                :param dp_id: dial plan id
                :param action: add or delete
                :param patterns: patterns to be added or deleteed
                """
                if not patterns:
                    # nothing to do
                    return

                def batches(batch_size: int) -> Generator[str, None, None]:
                    """
                    Yield pattern batches

                    :param batch_size:
                    """
                    pattern_list = list(patterns)
                    pattern_list.sort()
                    for i in range(0, len(pattern_list), batch_size):
                        yield pattern_list[i:i + batch_size]
                    return

                # schedule/run dial plan pattern updates in batches of 200
                await asyncio.gather(*[prem_pstn.dial_plan.modify_patterns(dial_plan_id=dp_id,
                                                                           dial_patterns=[
                                                                               PatternAndAction(dial_pattern=pattern,
                                                                                                action=action)
                                                                               for pattern in batch])
                                       for batch in batches(200)])
                return

            if delete_only:
                if to_delete:
                    await modify_patterns(dp_id=wxc_dialplan.dial_plan_id,
                                          action=PatternAction.delete,
                                          patterns=to_delete)
                    log.info(f'{dialplan.name}: bulk deleted {len(to_delete)} patterns')
                return
            else:
                if to_add:
                    await modify_patterns(dp_id=wxc_dialplan.dial_plan_id,
                                          action=PatternAction.add,
                                          patterns=to_add)
                    log.info(f'{dialplan.name}: bulk added {len(to_add)} patterns')

            log.info(f'{dialplan.name}: bulk updated with {len(patterns)} patterns')
            return

        # we go through the dial plans twice:
        # * 1st round: we only delete patterns
        # * 2nd round: add patterns
        # goal: avoid conflicts if a catalog moves between dial plans
        for delete_only in [True, False]:
            await asyncio.gather(*[configure_dialplan(dialplan=dialplan, delete_only=delete_only)
                                   for dialplan in config.dialplans])
    return


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 2:
        print(f'usage: {os.path.basename(sys.argv[0])} csvfile')
        exit(1)
    asyncio.run(configure_wxc(csv_file=sys.argv[1]))
