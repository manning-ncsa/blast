from __future__ import absolute_import, unicode_literals
from celery import shared_task
from .models import Transient
from .transient_name_server import get_transients_from_tns
from .transient_name_server import get_tns_credentials
from .ghost import run_ghost
import datetime


@shared_task
def ingest_recent_tns_data(interval_minutes=10):
    """
    Download and save recent transients from the transient name server.

    Args:
        interval_minutes (int) : Minutes in the past from when the function is
            called to search the transient name server for new transients.
    Returns:
        (None): Transients are saved to the database backend.
    """
    now = datetime.datetime.now()
    time_delta = datetime.timedelta(minutes=interval_minutes)
    tns_credentials = get_tns_credentials()
    recent_transients = get_transients_from_tns(now-time_delta,
                                                tns_credentials=tns_credentials)
    saved_transients = Transient.objects.all()

    for transient in recent_transients:
        try:
            saved_transients.get(tns_id__exact=transient.tns_id)
        except Transient.DoesNotExist:
            transient.save()

@shared_task
def match_transient_to_host():
    """
    Match a single transient in the database to a host galaxy.

    Returns:
        (None): Matches host to transient
    """
    unmatched = Transient.objects.filter(host_match_status__exact='not processed')

    if unmatched.exists():
        transient = unmatched.order_by('public_timestamp')[0]
        transient.host_match_status = 'processing'
        transient.save()
        host = run_ghost(transient)

        if host is not None:
            host.save()
            transient.host = host
            transient.host_match_status = 'processed'
            transient.save()
        else:
            transient.host_match_status = 'no match'
            transient.save()










