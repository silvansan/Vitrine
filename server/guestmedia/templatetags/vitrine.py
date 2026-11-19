from django import template

register = template.Library()


@register.filter
def mmss(ms):
    """Milliseconds -> m:ss."""
    try:
        total = int(ms) // 1000
    except (TypeError, ValueError):
        return ""
    return f"{total // 60}:{total % 60:02d}"
