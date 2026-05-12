
import click

def device_opts(f):
    f = click.option("--device-model", "device_model", type=int, required=True, help="DeviceModel enum value")(f)
    f = click.option("--ip", required=True)(f)
    f = click.option("--user", required=True)(f)
    f = click.option("--password", required=True)(f)
    return f
