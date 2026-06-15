import html2text
from typing import Optional

import requests
from google.adk.tools import ToolContext

from mmvn_b2c_agent.shared.constants import DEFAULT_MMVN_STORE_ID, DEFAULT_MMVN_STORE_URL
from mmvn_b2c_agent.tools.utils import make_graphql_request


def get_mm_store_locations(tool_context: ToolContext):
    """
        Fetches all store information from the GraphQL API.

        Returns:
            dict: A dictionary containing all store information.
    """
    magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
    store_id = magento_session_data.get('store_id', DEFAULT_MMVN_STORE_ID)
    base_url = magento_session_data.get('base_url', DEFAULT_MMVN_STORE_URL).rstrip('/')
    query = """
        query {
            storeList{
                name
                code
            }
        }
        """
    response = make_graphql_request(query, {}, store_id=store_id, base_url=base_url)
    try:
        store_data = response.json()
    except requests.exceptions.JSONDecodeError:
        return f"MM Magento API return error"
    if 'data' not in store_data or 'storeList' not in store_data['data']:
        raise ValueError("Invalid response structure: 'storeList' not found in response data.")
    return store_data['data']['storeList']


def get_all_faqs(tool_context: ToolContext):
    """
    Fetches store FAQs from the GraphQL API.
    Args:
        store_view_code (Optional[str]): The store view code to fetch FAQs for. Defaults to "mm_10010_vi".

    Returns:
        dict: A dictionary containing all FAQs.
        :param tool_context:
    """
    magento_session_data = tool_context.state.get('state', {}).get("magento_session_data", {})
    store_id = magento_session_data.get('store_id', DEFAULT_MMVN_STORE_ID)
    base_url = magento_session_data.get('base_url', DEFAULT_MMVN_STORE_URL).rstrip('/')
    query = """
    query GetStoreFAQ($store_view_code: String!){
        faqs(store_view_code: $store_view_code){
            is_active
            name
            faqs{
                question
                answer
                is_active
            }
        }
    }
    """
    variables = {
        "store_view_code": store_id
    }
    response = make_graphql_request(query, variables, store_id=store_id, base_url=base_url)
    faq_data = response.json()
    if 'data' not in faq_data or 'faqs' not in faq_data['data']:
        raise ValueError("Invalid response structure: 'faqs' not found in response data.")
    # filter out inactive FAQs
    converter = html2text.HTML2Text()
    faqs = []
    for faq in faq_data['data']['faqs']:
        if faq['is_active']:
            current_faqs = faq
            current_faqs['faqs'] = [f for f in current_faqs['faqs'] if f.pop('is_active', True)]
            faqs.append(current_faqs)
    for faq in faqs:
        for f in faq['faqs']:
            f['question'] = converter.handle(f['question']).strip()
            f['answer'] = converter.handle(f['answer']).strip()

    return faqs
