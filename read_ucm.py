#!/usr/bin/env python
"""
Read learned patterns from remoteroutingpattern table and write to CSV for further processing
"""
import csv
import logging
import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field, parse_obj_as

from ucmaxl import AXLHelper

CSV_OUTPUT = 'read_ucm.csv'


class LearnedPattern(BaseModel):
    remote_catalog_key_id: str = Field(alias='remotecatalogkey_id')
    pattern: str


def learned_patterns(axl: AXLHelper, with_numbers: bool = False) -> list[LearnedPattern]:
    """
    read learned patterns from remoteroutingpattern table
    :param axl:
    :return: list of learned patterns
    """
    """
    tk pattern usage:
    - 22   Uri Routing                           
    + 23   ILS Learned Enterprise Number         
    + 24   ILS Learned E164 Number               
    + 25   ILS Learned Enterprise Numeric Pattern
    + 26   ILS Learned E164 Numeric Pattern      
    + 27   Alternate Number                      
    - 28   ILS Learned URI                       
    - 29   ILS Learned PSTN Failover Rule        
    + 30   ILS Imported E164 Number    
    """
    usage = [25, 26]
    if with_numbers:
        usage.extend((23, 24, 25))
    usage = f'({",".join(str(u) for u in usage)})'
    patterns = axl.sql_query(
        f'select remotecatalogkey_id,pattern from remoteroutingpattern where tkpatternusage in {usage}')
    return parse_obj_as(list[LearnedPattern], patterns)


class RemoteCatalog(BaseModel):
    peer_id: str = Field(alias='peerid')
    route_string: str = Field(alias='routestring')


class RcKey(BaseModel):
    rc_key_id: str = Field(alias='remotecatalogkey_id')
    rc_catalog_peer_id: str = Field(alias='remoteclusteruricatalog_peerid')


def read_from_ucm():
    """
    Read learned patterns from UCM using thin AXL. Output is written to CSV for further processing
    """
    axl_host = os.getenv('AXL_HOST')
    axl_user = os.getenv('AXL_USER')
    axl_password = os.getenv('AXL_PASSWORD')
    if not all((axl_host, axl_user, axl_password)):
        raise KeyError('Environment variables AXL_HOST, AXL_USER, and AXL_PASSWORD need to be set or defined in .env '
                       'file.')
    print('Reading from UCM...')
    axl = AXLHelper(ucm_host=axl_host, auth=(axl_user, axl_password), verify=False)

    remote_catalogs = parse_obj_as(list[RemoteCatalog],
                                   axl.sql_query('select peerid,routestring from remoteclusteruricatalog'))
    rc_by_peer_id: dict[str, RemoteCatalog] = {rc.peer_id: rc for rc in remote_catalogs}

    rc_keys = parse_obj_as(
        list[RcKey],
        axl.sql_query('select remotecatalogkey_id,remoteclusteruricatalog_peerid from remotecatalogkey'))
    route_string_by_catalog_key: dict[str, str] = {rc.rc_key_id: rc_by_peer_id[rc.rc_catalog_peer_id].route_string
                                                   for rc in rc_keys}

    # read learned patterns
    learned = learned_patterns(axl)

    # write patterns to file with route string in remotecatalogkey_id column
    print(f'Writing patterns to "{CSV_OUTPUT}"')
    with open(CSV_OUTPUT, mode='w', newline='') as output:
        writer = csv.writer(output)
        writer.writerow(('remotecatalogkey_id', 'pattern'))
        for pattern in learned:
            writer.writerow((route_string_by_catalog_key[pattern.remote_catalog_key_id], pattern.pattern))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    logging.getLogger('zeep.wsdl.wsdl').setLevel(logging.INFO)
    logging.getLogger('zeep.xsd.schema').setLevel(logging.INFO)
    load_dotenv()
    read_from_ucm()
