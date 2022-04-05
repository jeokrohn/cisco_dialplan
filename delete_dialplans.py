#!/usr/bin/env python
"""
Delete all dial plans mentioned in config file
"""
import logging

from config import *
from cpapi_helper import *

log = logging.getLogger(__name__)


def delete_dialplans():
    """
    delete dial plans mentioned in config
    """
    config = Config.from_yml('config.yml')
    api = CPAPIHelper(access_token=config.tokens.access_token)
    dialplans = {dp.name: dp for dp in api.dialplans_list()}

    for dialplan in config.dialplans:
        wxc_dp = dialplans.get(dialplan.name)
        if wxc_dp is None:
            continue
        wxc_dp: DialPlan
        api.dialplan_delete(dialplan_id=wxc_dp.dialplan_id)


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    delete_dialplans()
