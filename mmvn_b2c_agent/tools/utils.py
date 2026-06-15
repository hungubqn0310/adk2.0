import asyncio
import time
import logging

import aiohttp
import requests
from typing import Any

from google.adk.tools import ToolContext
from requests import Response

logger = logging.getLogger(__name__)
API_CONNECT_TIMEOUT = (3, 10)  # (connect timeout, read timeout)


def exponential_backoff_request(*args,
                                retries: int = 3,
                                initial_delay: float = 0.25,
                                max_delay: float = 2.0,
                                **kwargs) -> Response | None:
    """
    Make a request with exponential backoff in case of failures.

    Args:
        max_delay:
        retries (int): Number of retry attempts.
        initial_delay (float): Initial delay between retries in seconds.
        *args: Positional arguments for requests.request.
        **kwargs: Keyword arguments for requests.request.

    Returns:
        requests.Response: The response from the request.
    """
    if 'timeout' not in kwargs:
        kwargs['timeout'] = API_CONNECT_TIMEOUT
    for attempt in range(1, retries + 1):
        try:
            response = requests.request(*args, **kwargs)
            response.raise_for_status()  # Raise an error for bad responses
            return response
        except requests.RequestException as e:
            if attempt == retries:
                logger.error(f"Request failed after {attempt} retries")
                raise  # Re-raise the last exception if out of retries
            backoff_time = min(initial_delay * 2 ** (attempt - 1), max_delay)
            logger.info(f"Request failed: {e}.\nRetrying in {backoff_time} seconds...")
            time.sleep(backoff_time)
    return None


async def make_graphql_request_async(query: str, variables: dict[str, Any],
                                     base_url: str = "https://mmpro.vn",
                                     store_id: str = 'mm_10010_vi',
                                     max_retries: int = 3,
                                     initial_retry_delay: float = 0.25,
                                     max_retry_delay: float = 2.0,
                                     auth_token: str | None = None,
                                     ) -> dict | list | None:
    session_timeout = aiohttp.ClientTimeout(connect=API_CONNECT_TIMEOUT[0], total=30)
    payload = {
        "query": query,
        "variables": variables
    }
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:139.0) Gecko/20100101 Firefox/139.0',
        'Accept': '*/*',
        'Accept-Encoding': 'gzip, deflate, br',
        'content-type': 'application/json',
        'store': store_id,
        'Connection': 'keep-alive',
    }
    if auth_token:
        headers['Authorization'] = f"Bearer {auth_token}"
    url = f"{base_url.rstrip('/')}/graphql"
    for retries in range(max_retries):
        try:
            async with aiohttp.ClientSession(timeout=session_timeout) as session:
                async with session.post(url, json=payload, headers=headers) as response:
                    response.raise_for_status()  # Raise an error for bad responses
                    res = await response.json()
                    return res
        except (aiohttp.ClientError, asyncio.TimeoutError, Exception) as e:
            if retries >= max_retries - 1:
                logger.error(f"Async GraphQL request failed after {max_retries} retries: {e}")
                return None  # Return None instead of raising
            backoff_time = min(initial_retry_delay * 2 ** retries, max_retry_delay)
            logger.info(f"Async GraphQL request failed: {e}.\nRetrying in {backoff_time} seconds...")
            await asyncio.sleep(backoff_time)
    return None


def make_graphql_request(query: str, variables: dict[str, Any],
                         base_url: str = "https://mmpro.vn",
                         store_id: str = 'mm_10010_vi',
                         max_retries: int = 3,
                         initial_retry_delay: float = 0.25,
                         max_retry_delay: float = 2.0,
                         auth_token: str | None = None,
                         ) -> requests.Response:
    """
    Make a GraphQL request to the specified host.

    Args:
        query (str): The GraphQL query string.
        variables (dict[str, Any]): The variables for the GraphQL query.
        base_url (str): The host URL for the GraphQL endpoint. Defaults to "https://mmpro.vn".
        store_id (str): The store identifier to include in the headers. Defaults to 'mm_10010_vi'.
        max_retries (int): Number of retry attempts.
        initial_retry_delay (float): Initial delay between retries in seconds.
        max_retry_delay (float): Maximum delay between retries in seconds.

    Returns:
        requests.Response: The response from the GraphQL request.
    """
    payload = {
        "query": query,
        "variables": variables
    }
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:139.0) Gecko/20100101 Firefox/139.0',
        'Accept': '*/*',
        'Accept-Encoding': 'gzip, deflate, br',
        'content-type': 'application/json',
        'store': store_id,
        'Connection': 'keep-alive',
    }
    if auth_token:
        headers['Authorization'] = f"Bearer {auth_token}"
    url = f"{base_url.rstrip('/')}/graphql"
    response = exponential_backoff_request("POST", url, headers=headers, json=payload,
                                           retries=max_retries, initial_delay=initial_retry_delay,
                                           max_delay=max_retry_delay)
    response.raise_for_status()  # Raise an error for bad responses
    return response


async def convert_currency(from_amount: float, from_currency: str, to_currency: str) -> float:
    """
    Convert an amount of money from one currency to another using the ExchangeRate API.

    Args:
        from_amount (float): The amount of money to convert.
        from_currency (str): The currency code to convert from.
        to_currency (str): The currency code to convert to.
    Returns:
        float: The exchange rate from `from_currency` to `to_currency`.
    """
    from_currency = from_currency.strip().upper()
    to_currency = to_currency.strip().upper()

    url = f"https://api.exchangerate-api.com/v4/latest/{from_currency}"

    payload = {}
    headers = {}

    response = requests.request("GET", url, headers=headers, data=payload)
    response.raise_for_status()  # Raise an error for bad responses
    data = response.json()
    if not data.get('rates') or to_currency not in data['rates']:
        raise ValueError(f"No exchange rates found for {from_currency}")
    rate = data['rates'][to_currency]
    converted_amount = from_amount * rate
    return converted_amount


def set_session_language(user_language: str, tool_context: ToolContext):
    """Set the session's langauge to the last user's question langauge."""
    if 'state' not in tool_context.state:
        tool_context.state['state'] = {}
    tool_context.state['lang'] = user_language


def get_session_language(tool_context: ToolContext):
    return tool_context.state.get('state', {}).get('lang', 'vi')
