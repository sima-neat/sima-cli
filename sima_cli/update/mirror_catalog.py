"""Read the complete public build catalog without masking publication errors."""


def read_mirror_index(session, index_url, timeout):
    response = session.get(index_url, timeout=timeout)
    # A missing private-S3-origin object can be reported as either 403 or 404.
    if response.status_code in (403, 404):
        legacy_url = index_url.rsplit('/', 1)[0] + '/index.json'
        response = session.get(legacy_url, timeout=timeout)
    response.raise_for_status()
    return response.json()
