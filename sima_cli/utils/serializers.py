
def app_info_to_dict(app):
    d = {}
    for name in ("name", "pid", "archive_id", "status"):
        if hasattr(app, name):
            d[name] = getattr(app, name)
    if not d:
        d["repr"] = repr(app)
    return d

def device_to_dict(dv):
    return {"repr": repr(dv)}
