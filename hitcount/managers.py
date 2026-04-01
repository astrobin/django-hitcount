# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import logging
from datetime import timedelta

from django.db import models
from django.conf import settings
from django.db.models import Sum
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType

log = logging.getLogger(__name__)


class HitCountManager(models.Manager):

    def get_for_object(self, obj):
        from django.core.exceptions import MultipleObjectsReturned

        ctype = ContentType.objects.get_for_model(obj)
        try:
            hit_count, created = self.get_or_create(
                content_type=ctype, object_pk=obj.pk)
        except MultipleObjectsReturned:
            hit_count = self._merge_duplicates(ctype, obj.pk)
        return hit_count

    def _merge_duplicates(self, ctype, object_pk):
        """Merge duplicate HitCount rows: sum hits into the keeper, delete the rest."""
        from django.db import transaction
        try:
            with transaction.atomic():
                dupes = (
                    self.select_for_update()
                    .filter(content_type=ctype, object_pk=object_pk)
                    .order_by('pk')
                )
                total_hits = dupes.aggregate(total=Sum('hits'))['total'] or 0
                keeper = dupes.first()
                if keeper is None:
                    # Another thread already merged — re-fetch.
                    return self.get(content_type=ctype, object_pk=object_pk)
                deleted_count = dupes.exclude(pk=keeper.pk).delete()[0]
                if deleted_count > 0 and keeper.hits != total_hits:
                    keeper.hits = total_hits
                    keeper.save(update_fields=['hits'])
                if deleted_count > 0:
                    log.info(
                        "Merged %d duplicate HitCount rows for content_type=%d, "
                        "object_pk=%s. Kept pk=%d with %d hits.",
                        deleted_count, ctype.pk, object_pk, keeper.pk, total_hits,
                    )
            return keeper
        except Exception:
            # Last resort: if anything goes wrong, just return the first one.
            return self.filter(content_type=ctype, object_pk=object_pk).first()


class HitManager(models.Manager):

    def filter_active(self, *args, **kwargs):
        """
        Return only the 'active' hits.

        How you count a hit/view will depend on personal choice: Should the
        same user/visitor *ever* be counted twice?  After a week, or a month,
        or a year, should their view be counted again?

        The default is to consider a visitor's hit still 'active' if they
        return within a the last seven days..  After that the hit
        will be counted again.  So if one person visits once a week for a year,
        they will add 52 hits to a given object.

        Change how long the expiration is by adding to settings.py:

        HITCOUNT_KEEP_HIT_ACTIVE  = {'days' : 30, 'minutes' : 30}

        Accepts days, seconds, microseconds, milliseconds, minutes,
        hours, and weeks.  It's creating a datetime.timedelta object.

        """
        grace = getattr(settings, 'HITCOUNT_KEEP_HIT_ACTIVE', {'days': 7})
        period = timezone.now() - timedelta(**grace)
        return self.filter(created__gte=period).filter(*args, **kwargs)
