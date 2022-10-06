#!/usr/bin/env python
"""
Delete all dial plans mentioned in config file
"""
import asyncio
import logging

from wxc_sdk.as_api import AsWebexSimpleApi

from config import *

log = logging.getLogger(__name__)


async def delete_dialplans():
    """
    delete dial plans mentioned in config
    """
    config = Config.from_yml('config.yml')
    async with AsWebexSimpleApi(tokens=config.tokens.access_token) as api:
        dp_api = api.telephony.prem_pstn.dial_plan
        dialplans = {dp.name: dp for dp in await dp_api.list()}

        tasks = [dp_api.delete_dial_plan(dial_plan_id=wxc_dp.dial_plan_id)
                 for dialplan in config.dialplans
                 if (wxc_dp := dialplans.get(dialplan.name))]
        if tasks:
            await asyncio.gather(*tasks)
            print(f'deleted {len(tasks)} dial plans')


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    asyncio.run(delete_dialplans())
