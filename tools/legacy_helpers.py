"""
Small Notion/OpenAI helpers used by the automation functions.

Extracted from the old Slack-era base_scripts.py — only the helpers that
automation_functions.py still depends on live here.
"""

import os

from notion_client import Client
from openai import OpenAI


def get_notion_pages(notion_client: Client, database_id: str, filter: dict = None, sorts: list = []):
    """Retrieve pages from a Notion database with optional filtering.
    Args:
        notion_client (Client): An instance of the Notion client.
        database_id (str): The ID of the Notion database to query.
        filter (dict, optional): A filter object to apply to the query. Defaults to None.
        sorts (list, optional): A list of sort objects to apply to the query. Defaults to None.
    Returns:
        list: A list of pages matching the query.
    """
    query = {
        "filter": filter,
        "sorts": sorts
    }
    response = notion_client.databases.query(database_id, **{k: v for k, v in query.items() if v})
    return response.get("results", [])


def ask_openai(
    prompt: str,
    model: str = "",
    temperature: float = 0.7,
    system_message: str = "You are a helpful assistant.",
) -> str:
    """Send a prompt to the OpenAI API and return the response.
    Args:
        prompt (str): The prompt to send to the OpenAI API.
        model (str, optional): Model to use. Defaults to ASSISTANT_LLM_MODEL (gpt-4o-mini).
        temperature (float, optional): The temperature for the completion. Defaults to 0.7.
        system_message (str, optional): System message for the completion.
    Returns:
        str: The response from the OpenAI API.
    """
    client = OpenAI()
    response = client.chat.completions.create(
        model=model or os.getenv("ASSISTANT_LLM_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt}
        ],
        temperature=temperature
    )
    return response.choices[0].message.content


def notion_response_simplifier(entries: list, exclude: list = []) -> str:
    """Takes a notion response, and simplifies it to a more readable format."""
    simplified = []
    for entry in entries:
        simplified_entry = {}
        props = entry['properties']
        for prop_name, prop_content in props.items():
            if prop_name in exclude:
                continue
            if prop_content.get("type") == "relation":
                simplified_entry[prop_name] = prop_content.get("relation")
            elif prop_content.get("type") == "select" and prop_content.get("select") is not None:
                simplified_entry[prop_name] = prop_content.get("select", {}).get("name")
            elif prop_content.get("type") == "multi_select":
                simplified_entry[prop_name] = [item['name'] for item in prop_content.get("multi_select", [])]
            elif prop_content.get("type") == "title":
                title_parts = prop_content.get("title", [])
                title_text = " ".join(part.get("plain_text", "") for part in title_parts)
                simplified_entry[prop_name] = title_text
            elif prop_content.get("type") == "rich_text":
                text_parts = prop_content.get("rich_text", [])
                text_content = " ".join(part.get("plain_text", "") for part in text_parts)
                simplified_entry[prop_name] = text_content
            elif prop_content.get("type") == "number":
                simplified_entry[prop_name] = prop_content.get("number")
            elif prop_content.get("type") == "date":
                simplified_entry[prop_name] = prop_content.get("date", {}).get("start")
            elif prop_content.get("type") == "checkbox":
                simplified_entry[prop_name] = prop_content.get("checkbox")
            elif prop_content.get("type") == "files":
                file_urls = []
                for file_item in prop_content.get("files", []):
                    if file_item.get("type") == "external":
                        file_urls.append(file_item.get("external", {}).get("url"))
                    elif file_item.get("type") == "file":
                        file_urls.append(file_item.get("file", {}).get("url"))
                simplified_entry[prop_name] = file_urls
        simplified.append(simplified_entry)
    return simplified
