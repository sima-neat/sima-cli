import paramiko


class DevkitFirstContactPolicy(paramiko.MissingHostKeyPolicy):
    """Accept unknown DevKit host keys for factory-reset first-contact flows."""

    def missing_host_key(self, client, hostname, key):
        client.get_host_keys().add(hostname, key.get_name(), key)


def create_devkit_ssh_client() -> paramiko.SSHClient:
    """Create an SSH client for first-contact DevKit workflows."""
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(DevkitFirstContactPolicy())
    return client
