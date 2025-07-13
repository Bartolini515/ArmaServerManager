
# TODO: Refractor and integrate
def extract_links(file_name):
    with open(file_name, 'r') as file:
        links = re.findall(r'<a href="(https?://[^"]+)"', file.read())
    log(f'Extracted links from html file: {links}', False)
    return links

def extract_workshop_ids(links):
    workshop_ids = []
    for link in links:
        match = re.search(r'\?id=(\d+)', link)
        if match:
            workshop_ids.append(match.group(1))
    log(f'Extracted workshop ids from links: {workshop_ids}', False)
    return workshop_ids