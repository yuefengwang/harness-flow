"""sw_lib.web.templating — Jinja2 rendering helper.

Work around a starlette 1.3.x regression: ``Jinja2Templates.get_template``
feeds the per-request context dict into jinja2's LRUCache key, which raises
``TypeError: unhashable type: 'dict'`` (a dict is not hashable). We render via
``env.get_template`` directly so the cache key stays a plain template name.
"""
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse


class SafeJinja2Templates(Jinja2Templates):
    """Jinja2Templates that avoids the starlette context-as-cache-key bug."""

    def TemplateResponse(self, name, context=None, status_code=200,
                         headers=None, media_type=None, background=None):
        context = dict(context or {})
        template = self.env.get_template(name)
        return HTMLResponse(
            content=template.render(**context),
            status_code=status_code,
            headers=headers,
            media_type=media_type or "text/html",
            background=background,
        )
