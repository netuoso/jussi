# -*- coding: utf-8 -*-
import logging
from typing import Optional

from sanic import response
from sanic.exceptions import InvalidUsage

from jussi.typedefs import HTTPRequest
from jussi.typedefs import HTTPResponse

from .cache import cache_get
from .cache import jsonrpc_cache_key
from .cache import merge_cached_response
from .errors import InvalidRequest
from .errors import ParseError
from .errors import ServerError
from .errors import handle_middleware_exceptions
from .utils import async_exclude_methods
from .utils import is_batch_jsonrpc
from .utils import is_valid_jsonrpc_request
from .utils import method_urn
from .utils import sort_request

logger = logging.getLogger('sanic')


def setup_middlewares(app):
    logger = app.config.logger
    logger.info('before_server_start -> setup_middlewares')
    app.request_middleware.append(validate_jsonrpc_request)
    app.request_middleware.append(request_stats)
    app.request_middleware.append(caching_middleware)
    app.response_middleware.append(finalize_request_stats)
    return app


@handle_middleware_exceptions
@async_exclude_methods(exclude_http_methods=('GET', ))
async def validate_jsonrpc_request(
        request: HTTPRequest) -> Optional[HTTPResponse]:
    try:
        is_valid_jsonrpc_request(single_jsonrpc_request=request.json)
        request.parsed_json = sort_request(single_jsonrpc_request=request.json)
    except AssertionError as e:
        # invalid jsonrpc
        return response.json(
            InvalidRequest(sanic_request=request, exception=e).to_dict())
    except (InvalidUsage, ValueError) as e:
        return response.json(
            ParseError(sanic_request=request, exception=e).to_dict())
    except Exception as e:
        # json failed to parse
        return response.json(
            ServerError(sanic_request=request, exception=e).to_dict())


@handle_middleware_exceptions
@async_exclude_methods(exclude_http_methods=('GET', ))
async def request_stats(
        request: HTTPRequest) -> Optional[HTTPResponse]:
    stats = request.app.config.stats
    if is_batch_jsonrpc(sanic_http_request=request):
        stats.incr('jsonrpc.batch_requests')
        for req in request.json:
            urn = method_urn(req)
            stats.incr('jsonrpc.requests.%s' % urn)
        return
    urn = method_urn(request.json)
    stats.incr('jsonrpc.single_requests')
    stats.incr('jsonrpc.requests.%s' % urn)
    request['timer'] = stats.timer('jsonrpc.requests.%s' % urn)
    request['timer'].start()


@handle_middleware_exceptions
@async_exclude_methods(exclude_http_methods=('GET', ))
async def caching_middleware(request: HTTPRequest) -> None:
    if is_batch_jsonrpc(sanic_http_request=request):
        logger.debug('skipping cache for jsonrpc batch request')
        return
    key = jsonrpc_cache_key(request.json)
    logger.debug('caching_middleware cache_get %s', key)
    cached_response = await cache_get(request, request.json)

    if cached_response:
        logger.debug('caching_middleware hit for %s', key)
        cached_response = merge_cached_response(cached_response, request.json)
        return response.json(
            cached_response, headers={'x-jussi-cache-hit': key})

    logger.debug('caching_middleware no hit for %s', key)

# pylint: disable=unused-argument
@handle_middleware_exceptions
async def finalize_request_stats(
        request: HTTPRequest, response: HTTPResponse) -> None:
    if 'timer' in request:
        request['timer'].stop()
