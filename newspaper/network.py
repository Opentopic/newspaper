# -*- coding: utf-8 -*-
"""
All code involving requests and responses over the http network
must be abstracted in this file.
"""
import os
import subprocess
from contextlib import closing
from http.client import HTTPException
from urllib.parse import urljoin

from requests import RequestException
from requests.packages.urllib3.exceptions import HTTPError

from . import CASPERJS_PATH

__title__ = 'newspaper'
__author__ = 'Lucas Ou-Yang'
__license__ = 'MIT'
__copyright__ = 'Copyright 2014, Lucas Ou-Yang'

import logging
import requests

from .configuration import Configuration
from .mthreading import ThreadPool
from .settings import cj

log = logging.getLogger()


def get_request_kwargs(timeout, useragent):
    """This Wrapper method exists b/c some values in req_kwargs dict
    are methods which need to be called every time we make a request
    """
    return {
        'headers': {'User-Agent': useragent},
        'cookies': cj(),
        'timeout': timeout,
        'allow_redirects': True
    }


class NetworkError(Exception):
    pass


def get_html(url, config=None, response=None):
    """Retrieves the html for either a url or a response object. All html
    extractions MUST come from this method due to some intricies in the
    requests module. To get the encoding, requests only uses the HTTP header
    encoding declaration requests.utils.get_encoding_from_headers() and reverts
    to ISO-8859-1 if it doesn't find one. This results in incorrect character
    encoding in a lot of cases.
    """
    FAIL_ENCODING = 'ISO-8859-1'
    config = config or Configuration()
    useragent = config.browser_user_agent
    timeout = config.request_timeout
    size_limit = config.size_limit
    invalid_types = config.invalid_content_types

    if response is not None:
        if response.encoding != FAIL_ENCODING:
            return response.text
        return response.content

    def _get_using_requests():
        result = None
        try:
            with closing(requests.get(url=url, stream=True, **get_request_kwargs(timeout, useragent))) as _response:
                response_code = _response.status_code
                if not (200 <= response_code <= 299):
                    raise NetworkError('Invalid status code: {}'.format(response_code))
                length = _response.headers.get('content-length')
                type = _response.headers.get('content-type')
                if length is not None and int(length) >= size_limit:
                    log.warning('Requests response is too big, aborting', extra={
                        'url': url,
                        'length': length,
                    })
                elif type is not None and any(filter(lambda x: type.startswith(x[:-1]) if x[-1] == '*' else type == x,
                                                     invalid_types)):
                    log.warning('Requests response has invalid content type, aborting', extra={
                        'url': url,
                        'content_type': type,
                    })
                else:
                    log.info('Url: {} got response from Requests'.format(url))
                    result = _response.text if _response.encoding != FAIL_ENCODING else _response.content
        except (RequestException, ConnectionResetError, ConnectionError, HTTPException, HTTPError) as e:
            raise NetworkError('Network error') from e
        return result or ''

    if config.content_strategy['name'] == 'casperjs':
        command_formula = '{casperjs} {script} {url}'

        base_dir = os.path.abspath(os.path.dirname(__file__))
        command = command_formula.format(
            casperjs=CASPERJS_PATH,
            script=os.path.join(base_dir, 'casperjs/get_page_content.js'),
            url=url)
        try:
            p = subprocess.Popen(
                command.split(), stdout=subprocess.PIPE,
                stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            output, err = p.communicate(timeout=timeout)
            response_code_part = str(output).split('\\n')[0]
            response_code = int(''.join(filter(lambda x: x.isdigit(), response_code_part)))
            if not (200 <= response_code <= 299):
                raise NetworkError('Invalid status code: {}'.format(response_code))
            log.info('Url: {} got response from CasperJS'.format(url))
        except subprocess.TimeoutExpired as e:
            log.info('Url: {} got timeout from CasperJS'.format(url))
            return _get_using_requests()

        return output

    elif config.content_strategy['name'] == 'selenium':
        from selenium import webdriver
        from selenium.webdriver.firefox.firefox_binary import FirefoxBinary
        import pyvirtualdisplay

        log_file = config.content_strategy['kwargs'].get('log_file')

        with pyvirtualdisplay.Display():
            browser = webdriver.Firefox(firefox_binary=FirefoxBinary(
                log_file=log_file))
            browser.get(url)
            log.info('Url: {} got response from Selenium'.format(url))
            # webdriver does not support returning HTTP status code
            html = browser.page_source
            browser.quit()
            del browser
        return html

    elif config.content_strategy['name'] == 'splash':
        assert 'host' in config.content_strategy['kwargs'], \
            'If you want to use `Splash` as content strategy, you must ' \
            'define in config the `host` key in content_strategy[\'kwargs\']'
        payload = {
            'url': url,
            'timeout': timeout,
            'wait': 0.5
        }
        endpoint = urljoin(
            config.content_strategy['kwargs']['host'], '/render.html')
        resp = requests.get(endpoint, params=payload)
        response_code = resp.status_code
        if not (200 <= response_code <= 299):
            raise NetworkError('Invalid status code: {}'.format(response_code))
        log.info('Url: {} got response from Splash'.format(url))
        return resp.content

    return _get_using_requests()


class MRequest(object):
    """Wrapper for request object for multithreading. If the domain we are
    crawling is under heavy load, the self.resp will be left as None.
    If this is the case, we still want to report the url which has failed
    so (perhaps) we can try again later.
    """
    def __init__(self, url, config=None):
        self.url = url
        self.config = config
        config = config or Configuration()
        self.useragent = config.browser_user_agent
        self.timeout = config.request_timeout
        self.resp = None

    def send(self):
        try:
            self.resp = requests.get(self.url, **get_request_kwargs(
                                     self.timeout, self.useragent))
            if self.config.http_success_only:
                self.resp.raise_for_status()
        except RequestException as e:
            log.critical('[REQUEST FAILED] ' + str(e))


def multithread_request(urls, config=None):
    """Request multiple urls via mthreading, order of urls & requests is stable
    returns same requests but with response variables filled.
    """
    config = config or Configuration()
    num_threads = config.number_threads
    timeout = config.thread_timeout_seconds

    pool = ThreadPool(num_threads, timeout)

    m_requests = []
    for url in urls:
        m_requests.append(MRequest(url, config))

    for req in m_requests:
        pool.add_task(req.send)

    pool.wait_completion()
    return m_requests

# def async_request(urls, timeout=7):
#    """receives a list of requests and sends them all
#    asynchronously at once"""
#
#    rs = (grequests.request('GET', url,
#          **get_request_kwargs(timeout)) for url in urls)
#    responses = grequests.map(rs, size=10)
#
#    return responses


# def sync_request(urls_or_url, config=None):
#    """
#    Wrapper for a regular request, no asyn nor multithread.
#    """
#    # TODO config = default_config if not config else config
#    useragent = config.browser_user_agent
#    timeout = config.request_timeout
#    if isinstance(urls_or_url, list):
#        resps = [requests.get(url, **get_request_kwargs(timeout, useragent))
#                                                for url in urls_or_url]
#        return resps
#    else:
#        return requests.get(urls_or_url,
#                            **get_request_kwargs(timeout, useragent))
