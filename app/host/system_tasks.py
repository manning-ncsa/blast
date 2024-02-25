import datetime
import glob
import shutil

from django.conf import settings
from django.utils import timezone

from .base_tasks import initialise_all_tasks_status
from .base_tasks import SystemTaskRunner
from .models import TaskRegister
from .models import TaskRegisterSnapshot
from .models import Transient
from .models import Status
from .transient_name_server import get_daily_tns_staging_csv
from .transient_name_server import get_tns_credentials
from .transient_name_server import get_transients_from_tns
from .transient_name_server import tns_staging_blast_transient
from .transient_name_server import tns_staging_file_date_name
from .transient_name_server import update_blast_transient


class TNSDataIngestion(SystemTaskRunner):
    
    def run_process(self, interval_minutes=200):
        print('TNS STARTED')
        now = timezone.now()
        time_delta = datetime.timedelta(minutes=interval_minutes)
        tns_credentials = get_tns_credentials()
        recent_transients = get_transients_from_tns(
            now - time_delta, tns_credentials=tns_credentials
        )
        print('TNS DONE')
        saved_transients = Transient.objects.all()
        count = 0
        for transient in recent_transients:
            try:
                saved_transient = saved_transients.get(name__exact=transient.name)

                # if there was *not* a redshift before and there *is* one now
                # then it would be safest to reprocess everything
                if not saved_transient.redshift and transient.redshift:
                    tasks = TaskRegister.objects.filter(transient=saved_transient)
                    for t in tasks:
                        t.status = Status.objects.get(message='not processed')
                        t.save()

                ### update info
                new_transient_dict = transient.__dict__
                if 'host_id' in new_transient_dict.keys(): del new_transient_dict['host_id']
                del new_transient_dict['_state']
                del new_transient_dict['id']
                saved_transients.filter(name__exact=transient.name).update(**new_transient_dict)

            except Transient.DoesNotExist:
                transient.save()
                count += 1
        print(f"Added {count} new transients")
        print('TNS UPLOADED')

    @property
    def task_name(self):
        return "TNS data ingestion"

    @property
    def task_frequency_seconds(self):
	    return 240
    
    @property
    def task_initially_enabled(self):
        return False


class InitializeTransientTasks(SystemTaskRunner):
    def run_process(self):
        """
        Initializes all task in the database to not processed for new transients.
        """

        uninitialized_transients = Transient.objects.filter(
            tasks_initialized__exact="False"
        )
        for transient in uninitialized_transients:
            initialise_all_tasks_status(transient)
            transient.tasks_initialized = "True"
            transient.save()

    @property
    def task_name(self):
        return "Initialize transient task"


class IngestMissedTNSTransients(SystemTaskRunner):
    def run_process(self):
        """
        Gets missed transients from tns and update them using the daily staging csv
        """
        yesterday = timezone.now() - datetime.timedelta(days=1)
        date_string = tns_staging_file_date_name(yesterday)
        data = get_daily_tns_staging_csv(
            date_string,
            tns_credentials=get_tns_credentials(),
            save_dir=settings.TNS_STAGING_ROOT,
        )
        saved_transients = Transient.objects.all()

        for _, transient in data.iterrows():
            # if transient exists update it
            try:
                blast_transient = saved_transients.get(name__exact=transient["name"])
                update_blast_transient(blast_transient, transient)
            # if transient does not exist add it
            except Transient.DoesNotExist:
                blast_transient = tns_staging_blast_transient(transient)
                blast_transient.save()

    @property
    def task_name(self):
        return "Ingest missed TNS transients"

    @property
    def task_initially_enabled(self):
        return False


class DeleteGHOSTFiles(SystemTaskRunner):
    def run_process(self):
        """
        Removes GHOST files
        """
        dir_list = glob.glob("transients_*/")

        for dir in dir_list:
            try:
                shutil.rmtree(dir)
            except OSError as e:
                print("Error: %s : %s" % (dir, e.strerror))

        dir_list = glob.glob("quiverMaps/")

        for dir in dir_list:
            try:
                shutil.rmtree(dir)
            except OSError as e:
                print("Error: %s : %s" % (dir, e.strerror))

    @property
    def task_name(self):
        return "Delete GHOST files"


class SnapshotTaskRegister(SystemTaskRunner):
    def run_process(self, interval_minutes=100):
        """
        Takes snapshot of task register for diagnostic purposes.
        """
        transients = Transient.objects.all()
        total, completed, waiting, not_completed = 0, 0, 0, 0

        for transient in transients:
            total += 1
            if transient.progress == 100:
                completed += 1
            if transient.progress == 0:
                waiting += 1
            if transient.progress < 100 and transient.progress > 0:
                not_completed += 1

        now = timezone.now()

        for aggregate, label in zip(
            [not_completed, total, completed, waiting],
            ["not completed", "total", "completed", "waiting"],
        ):
            TaskRegisterSnapshot.objects.create(
                time=now, number_of_transients=aggregate, aggregate_type=label
            )

    @property
    def task_name(self):
        return "Snapshot task register"


class LogTransientProgress(SystemTaskRunner):
    def run_process(self):
        """
        Updates the processing status for all transients.
        """
        transients = Transient.objects.all()

        for transient in transients:
            tasks = TaskRegister.objects.filter(transient__name__exact=transient.name)
            processing_task_qs = TaskRegister.objects.filter(transient__name__exact=transient.name,
                                                             task__name=self.task_name)
            
            total_tasks = len(tasks)
            completed_tasks = len(
                [task for task in tasks if task.status.message == "processed"]
            )
            blocked = len([task for task in tasks if task.status.type == "error"])

            progress = "processing"

            if total_tasks == 0:
                progress = "processing"
            elif total_tasks == completed_tasks:
                progress = "completed"
            elif total_tasks < completed_tasks:
                progress = "processing"
            elif blocked > 0:
                progress = "blocked"

            # save task
            if len(processing_task_qs) == 1:
                processing_task = processing_task_qs[0]
                processing_task.status = \
                    Status.objects.get(message=progress if progress != 'completed' else 'processed')
                processing_task.save()

            # save transient progress
            transient.processing_status = progress
            transient.save()

    @property
    def task_name(self):
        return "Log transient processing status"
