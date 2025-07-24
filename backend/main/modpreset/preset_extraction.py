import re


def extract_links(file_path: str, log_callback: callable = None) -> list[str]:
    """Extract links from an HTML file.

    Args:
        file_path (str): The name of the HTML file to extract links from.
        log_callback (callable, optional): A callback function for logging. Defaults to None.

    Returns:
        list[str]: A list of extracted links.
    """
    with open(file_path, 'r') as file:
        links = re.findall(r'<a href="(https?://[^"]+)"', file.read())
    if log_callback:
        log_callback(f'Extracted links from html file: {links}')
    return links

def extract_workshop_ids(links: list[str], log_callback: callable = None) -> list[str]:
    """Extract workshop IDs from a list of links.

    Args:
        links (list[str]): A list of links to extract workshop IDs from.
        log_callback (callable, optional): A callback function for logging. Defaults to None.

    Returns:
        list[str]: A list of extracted workshop IDs.
    """
    workshop_ids = []
    for link in links:
        match = re.search(r'\?id=(\d+)', link)
        if match:
            workshop_ids.append(match.group(1))
    if log_callback:
        log_callback(f'Extracted workshop ids from links: {workshop_ids}')
    return workshop_ids

def preset_parser(file_path: str, log_callback: callable = None) -> list[str]:
    """Parse a preset HTML file and extract workshop IDs.

    Args:
        file_path (str): The name of the HTML file to parse.
        log_callback (callable, optional): The logging callback function. Defaults to None.

    Returns:
        list[str]: A list of extracted workshop IDs.
    """
    if log_callback:
        log_callback(f'Parsing preset file: {file_path}')
    if not file_path.endswith('.html'):
        if log_callback:
            log_callback('File is not an HTML file, returning empty list.')
        return []
    links = extract_links(file_path, log_callback=log_callback)
    workshop_ids = extract_workshop_ids(links, log_callback=log_callback)
    return workshop_ids