"""
Django 5.0 readiness rules — APIs removed across Django 4.0→5.0 and deprecations
active in 4.2/5.0. Safe renames (the encoding/translation helpers, url→re_path)
are auto; structural changes (NullBooleanField, index_together, USE_L10N) are
detection + guidance.

Sources: Django's "Features removed" release notes for 4.0 and 5.0.
"""

from __future__ import annotations

from oneport_upgrade.rules.schema import DEPRECATED, REMOVED, Migration, Rule

_RULES = [
    # django.utils.encoding renames (removed in 4.0)
    Rule(id="dj-force-text", title="force_text() removed", kind="call", match="force_text",
         module="django.utils.encoding", severity=REMOVED, replacement="force_str", auto=True),
    Rule(id="dj-smart-text", title="smart_text() removed", kind="call", match="smart_text",
         module="django.utils.encoding", severity=REMOVED, replacement="smart_str", auto=True),
    # translation renames (removed in 4.0)
    Rule(id="dj-ugettext", title="ugettext() removed", kind="call", match="ugettext",
         module="django.utils.translation", severity=REMOVED, replacement="gettext", auto=True),
    Rule(id="dj-ugettext-lazy", title="ugettext_lazy() removed", kind="call", match="ugettext_lazy",
         module="django.utils.translation", severity=REMOVED, replacement="gettext_lazy", auto=True),
    Rule(id="dj-ungettext", title="ungettext() removed", kind="call", match="ungettext",
         module="django.utils.translation", severity=REMOVED, replacement="ngettext", auto=True),
    Rule(id="dj-ungettext-lazy", title="ungettext_lazy() removed", kind="call", match="ungettext_lazy",
         module="django.utils.translation", severity=REMOVED, replacement="ngettext_lazy", auto=True),
    # url() removed in 4.0 → re_path() (drop-in for regex patterns)
    Rule(id="dj-url", title="django.conf.urls.url() removed", kind="call", match="url",
         module="django.conf.urls", severity=REMOVED, replacement="re_path", auto=True,
         hint="re_path() is a drop-in for regex URLs; for simple paths prefer path()."),
    # The same renames at the IMPORT site, so `from ... import ugettext as _`
    # rewrites the imported name (preserving the alias) — not just call sites.
    Rule(id="dj-force-text-import", title="import of removed force_text", kind="from_import",
         module="django.utils.encoding", match="force_text", severity=REMOVED,
         replacement="force_str", auto=True),
    Rule(id="dj-smart-text-import", title="import of removed smart_text", kind="from_import",
         module="django.utils.encoding", match="smart_text", severity=REMOVED,
         replacement="smart_str", auto=True),
    Rule(id="dj-ugettext-import", title="import of removed ugettext", kind="from_import",
         module="django.utils.translation", match="ugettext", severity=REMOVED,
         replacement="gettext", auto=True),
    Rule(id="dj-ugettext-lazy-import", title="import of removed ugettext_lazy", kind="from_import",
         module="django.utils.translation", match="ugettext_lazy", severity=REMOVED,
         replacement="gettext_lazy", auto=True),
    Rule(id="dj-ungettext-import", title="import of removed ungettext", kind="from_import",
         module="django.utils.translation", match="ungettext", severity=REMOVED,
         replacement="ngettext", auto=True),
    Rule(id="dj-ungettext-lazy-import", title="import of removed ungettext_lazy", kind="from_import",
         module="django.utils.translation", match="ungettext_lazy", severity=REMOVED,
         replacement="ngettext_lazy", auto=True),
    Rule(id="dj-url-import", title="import of removed url()", kind="from_import",
         module="django.conf.urls", match="url", severity=REMOVED,
         replacement="re_path", auto=True,
         hint="re_path() is a drop-in for regex URLs; for simple paths prefer path()."),
    # request.is_ajax() removed in 4.0
    Rule(id="dj-is-ajax", title="HttpRequest.is_ajax() removed", kind="call", match="is_ajax",
         severity=REMOVED, auto=False,
         hint="Check request.headers.get('x-requested-with') == 'XMLHttpRequest'."),
    # NullBooleanField removed in 4.0
    Rule(id="dj-nullbooleanfield", title="NullBooleanField removed", kind="attr", match="NullBooleanField",
         severity=REMOVED, auto=False, hint="Use BooleanField(null=True)."),
    # index_together deprecated (4.2), removed 5.1
    Rule(id="dj-index-together", title="index_together is deprecated", kind="name", match="index_together",
         severity=DEPRECATED, auto=False, hint="Use Meta.indexes with models.Index."),
    # USE_L10N removed in 5.0
    Rule(id="dj-use-l10n", title="USE_L10N setting removed in 5.0", kind="name", match="USE_L10N",
         severity=REMOVED, auto=False, hint="Localization is always on; remove USE_L10N."),
    # django.utils.timezone.utc deprecated in 5.0
    Rule(id="dj-timezone-utc", title="django.utils.timezone.utc is deprecated", kind="attr", match="utc",
         module="timezone", owner_from="django.utils", severity=DEPRECATED, auto=False,
         hint="Use datetime.timezone.utc from the stdlib."),
    # STATICFILES_STORAGE / DEFAULT_FILE_STORAGE deprecated in 4.2 → STORAGES
    Rule(id="dj-staticfiles-storage", title="STATICFILES_STORAGE deprecated", kind="name",
         match="STATICFILES_STORAGE", severity=DEPRECATED, auto=False,
         hint="Use the unified STORAGES setting."),
    Rule(id="dj-default-file-storage", title="DEFAULT_FILE_STORAGE deprecated", kind="name",
         match="DEFAULT_FILE_STORAGE", severity=DEPRECATED, auto=False,
         hint="Use the unified STORAGES setting."),
]

DJANGO_50 = Migration(
    id="django:5.0",
    title="Django 5.0 readiness",
    summary="APIs removed across Django 4.0→5.0 and active deprecations.",
    rules=_RULES,
)
