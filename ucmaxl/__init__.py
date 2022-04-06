"""
AXL helper to wrap SOAP methods created based on the UCM AXL WSDL
"""
import logging
import os
import re
import tempfile
import zipfile

import requests
import urllib3
import zeep
from zeep.plugins import HistoryPlugin

log = logging.getLogger(__name__)

__all__ = ['AXLHelper']


class AXLHelper:
    # noinspection SpellCheckingInspection
    def __init__(self, ucm_host, auth, version=None, verify=None, timeout=60):
        """
        :param ucm_host: IP/FQDN of host to direct AXL requests to, optional with port spec
        :param auth: passed to requests.Session object. For basic authentication simply pass a (user/password) tuple
        :param version: String of WSDL version to use. For example: '12.0'
        :param verify: set to False to disable SSL key validation
        :param timeout: zeep timeout
        """
        self.ucm_host = ucm_host
        if ':' not in ucm_host:
            ucm_host += ':8443'
        self.axl_url = f'https://{ucm_host}/axl/'

        self.session = requests.Session()
        self.session.auth = auth
        if verify is not None:
            self.session.verify = verify
            if not verify:
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        version = version or self._get_version()

        wsdl_version = version

        # noinspection SpellCheckingInspection
        # try to find the WSDL file within the local directory
        self.wsdl = os.path.join(os.path.dirname(__file__), 'WSDL', wsdl_version, 'AXLAPI.wsdl')
        temp_dir = None
        if not os.path.isfile(self.wsdl):
            log.debug(f'__init__: WSDL not found: {self.wsdl}')
            # we need to download the wsdl from UCM
            temp_dir = tempfile.TemporaryDirectory()
            temp_zip_file_name = os.path.join(temp_dir.name, 'axlsqltoolkit.zip')
            r = self.session.get(f'https://{self.ucm_host}/plugins/axlsqltoolkit.zip')
            with open(temp_zip_file_name, 'wb') as f:
                f.write(r.content)
            log.debug(f'__init__: downloaded {temp_zip_file_name}')
            with zipfile.ZipFile(temp_zip_file_name, 'r') as zip_handle:
                zip_handle.extractall(path=temp_dir.name)
            log.debug(f'__init__: extracted {temp_zip_file_name}')
            self.wsdl = os.path.join(temp_dir.name, 'schema', 'current', 'AXLAPI.wsdl')
            log.debug(f'__init__: using {self.wsdl}')
        self.history = HistoryPlugin()
        self.client = zeep.Client(wsdl=self.wsdl,
                                  transport=zeep.Transport(timeout=timeout,
                                                           operation_timeout=timeout,
                                                           session=self.session),
                                  plugins=[self.history])
        self.service = self.client.create_service('{http://www.cisco.com/AXLAPIService/}AXLAPIBinding',
                                                  self.axl_url)
        if temp_dir:
            # remove temporary WSDL directory and temp files
            log.debug(f'__init__: cleaning up temp dir {temp_dir.name}')
            temp_dir.cleanup()
        return

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        if self.session:
            self.session.close()
            self.session = None

    # noinspection SpellCheckingInspection
    def _get_version(self):
        """
        Get UCM version w/o using zeep.
        Used to determine UCM version if no version is given on initialization
        :return: UCM version
        """
        # try for a number of UCM versions
        for major_version in [14, 12, 11, 10, 9, 8]:
            soap_envelope = (f'<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
                             f'xmlns:ns="http://www.cisco.com/AXL/API/{major_version}.0"><soapenv:Header/>'
                             f'<soapenv:Body><ns:getCCMVersion></ns:getCCMVersion></soapenv:Body></soapenv:Envelope>')
            headers = {'Content-Type': 'text/xml',
                       'SOAPAction': f'CUCM:DB ver={major_version}.0 getCCMVersion'}
            r = self.session.post(self.axl_url, data=soap_envelope, headers=headers)
            if r.status_code == 599:
                continue
            r.raise_for_status()
            log.debug(f'_get_version: reply from UCM: {r.text}')
            m = re.search(r'<version>(\d+)\.(\d+)\..+</version>', r.text)
            version = f'{m.group(1)}.{m.group(2)}'
            log.debug(f'_get_version: assuming version {version}')
            return version
        return ''

    def __getattr__(self, item):
        """
        unknown attributes are mapped to attributes of the zeep session.
        :param item:
        :return:
        """
        return self.service[item]

    def sql_query(self, query) -> list[dict]:
        """
        execute an SQL query
        :param query: SQL query
        :return: list of dict; each dict representing one record
        """
        r = self.service.executeSQLQuery(sql=query)

        if r['return'] is None:
            return []

        return [{t.tag: t.text for t in row} for row in r['return']['row']]
