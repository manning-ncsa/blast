"""
This modules contains the django code used to create tables in the database
backend.
"""
import os

import pandas as pd
from astropy import units as u
from astropy.coordinates import SkyCoord
from django.conf import settings
from django.db import models
from django_celery_beat.models import PeriodicTask
from django.contrib.auth.models import User
from photutils.aperture import SkyEllipticalAperture
from sedpy import observate

from .managers import ApertureManager
from .managers import CatalogManager
from .managers import CutoutManager
from .managers import FilterManager
from .managers import HostManager
from .managers import StatusManager
from .managers import SurveyManager
from .managers import TaskManager
from .managers import TransientManager


class SkyObject(models.Model):
    """
    Abstract base model to represent an astrophysical object with an on-sky
    position.

    Attributes:
        ra_deg (django.db.model.FloatField): Right Ascension (ICRS) in decimal
            degrees of the host
        deg_deg (django.db.model.FloatField): Declination (ICRS) in decimal degrees
            of the host
    """

    ra_deg = models.FloatField()
    dec_deg = models.FloatField()

    class Meta:
        abstract = True

    @property
    def sky_coord(self):
        """
        SkyCoordinate of the object's position.
        """
        return SkyCoord(ra=self.ra_deg, dec=self.dec_deg, unit="deg")

    @property
    def ra(self):
        """
        String representation of Right ascension.
        """
        return self.sky_coord.ra.to_string(unit=u.hour, precision=2)

    @property
    def dec(self):
        """
        String representation of Declination.
        """
        return self.sky_coord.dec.to_string(precision=2)


class Host(SkyObject):
    """
    Model to represent a host galaxy.

    Attributes:
        name (django.db.model.CharField): Name of the host galaxy, character
            limit = 20.
        ra_deg (django.db.model.FloatField): Right Ascension (ICRS) in decimal
            degrees of the host
        deg_deg (django.db.model.FloatField): Declination (ICRS) in decimal degrees
            of the host
    """

    name = models.CharField(max_length=100, blank=True, null=True)
    redshift = models.FloatField(null=True, blank=True)
    photometric_redshift = models.FloatField(null=True, blank=True)
    milkyway_dust_reddening = models.FloatField(null=True, blank=True)
    objects = HostManager()


class Transient(SkyObject):
    """
    Model to represent a transient.

    Attributes:
        name (django.db.model.CharField): Transient Name
            Server name of the transient, character limit = 20.
        tns_id (models.IntegerField): Transient Name Server ID.
        tns_prefix (models.CharField): Transient Name Server name
            prefix, character limit = 20.
        ra_deg (django.db.model.FloatField): Right Ascension (ICRS) in decimal
            degrees of the transient.
        deg_deg (django.db.model.FloatField): Declination (ICRS) in decimal
            degrees of the transient.
        host (django.db.model.ForeignKey): ForeignKey pointing to a :class:Host
            representing a transient's host galaxy.
        public_timestamp (django.db.model.DateTimeField): Transient name server
            public timestamp for the transient. Field can be null or blank. On
            Delete is set to cascade.
    """

    name = models.CharField(max_length=20)
    tns_id = models.IntegerField()
    tns_prefix = models.CharField(max_length=20)
    public_timestamp = models.DateTimeField(null=True, blank=True)
    host = models.ForeignKey(Host, on_delete=models.CASCADE, null=True, blank=True)
    objects = TransientManager()
    tasks_initialized = models.CharField(max_length=20, default="False")
    redshift = models.FloatField(null=True, blank=True)
    spectroscopic_class = models.CharField(max_length=20, null=True, blank=True)
    photometric_class = models.CharField(max_length=20, null=True, blank=True)
    milkyway_dust_reddening = models.FloatField(null=True, blank=True)
    processing_status = models.CharField(max_length=20, default="processing")
    added_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE)
    
    @property
    def progress(self):
        tasks = TaskRegister.objects.filter(transient__name__exact=self.name)
        total_tasks = len(tasks)
        completed_tasks = len(
            [task for task in tasks if task.status.message == "processed"]
        )
        progress = 100 * (completed_tasks / total_tasks) if total_tasks > 0 else 0
        return int(round(progress, 0))

    @property
    def best_redshift(self):
        """get the best redshift for a transient"""
        if self.host is not None and self.host.redshift is not None:
            if self.redshift is not None and abs(self.host.redshift-self.redshift) < 0.02:
                z = self.host.redshift
            elif self.redshift is None:
                z = self.host.redshift
            else:
                z = self.redshift
        elif self.redshift is not None:
            z = self.redshift
        elif self.host is not None and self.host.photometric_redshift is not None:
            z = self.host.photometric_redshift
        else:
            z = None
        return z


class Status(models.Model):
    """
    Status of a given processing task
    """

    message = models.CharField(max_length=20)
    type = models.CharField(max_length=20)
    objects = StatusManager()

    @property
    def badge(self):
        """
        Returns the Boostrap badge class of the status
        """
        if self.type == "error":
            badge_class = "badge bg-danger"
        elif self.type == "warning":
            badge_class = "badge bg-warning"
        elif self.type == "success":
            badge_class = "badge bg-success"
        elif self.type == "blank":
            badge_class = "badge bg-secondary"
        else:
            badge_class = "badge bg-secondary"

        return badge_class

    def __str__(self):
        return f"{self.message}"

    def __repr__(self):
        return f"{self.message}"


class Task(models.Model):
    """
    A processing task that needs to be completed for a transient.
    """

    name = models.CharField(max_length=100)
    objects = TaskManager()

    def __str__(self):
        return self.name

    def __repr__(self):
        return f"{self.name}"


class TaskRegister(models.Model):
    """
    Keep track of the the various processing status of a transient.
    """

    task = models.ForeignKey(Task, on_delete=models.CASCADE)
    status = models.ForeignKey(Status, on_delete=models.CASCADE)
    transient = models.ForeignKey(Transient, on_delete=models.CASCADE)
    user_warning = models.BooleanField(default=False)
    last_modified = models.DateTimeField(blank=True, null=True)
    last_processing_time_seconds = models.FloatField(blank=True, null=True)

    def __repr__(self):
        return f" {self.transient.name} | {self.task.name} | {self.status.message}"


class ExternalResourceCall(models.Model):
    """
    A model to represent a call to a call to an external resource.

    Attributes:
        resource_name (models.CharField): Name of the external resource.
        response_status (models.CharField): Response status returned when the
            external resource was requested.
        request_time (models.DateTimeField): Time of request to the resource.
    """

    resource_name = models.CharField(max_length=20)
    response_status = models.CharField(max_length=20)
    request_time = models.DateTimeField(null=True, blank=True)


class Survey(models.Model):
    """
    Model to represent a survey
    """

    name = models.CharField(max_length=20, unique=True)
    objects = SurveyManager()


class Filter(models.Model):
    """
    Model to represent a survey filter
    """

    name = models.CharField(max_length=20, unique=True)
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE)
    kcorrect_name = models.CharField(max_length=100, null=True, blank=True)
    sedpy_id = models.CharField(max_length=20)
    hips_id = models.CharField(max_length=250)
    vosa_id = models.CharField(max_length=20)
    image_download_method = models.CharField(max_length=20)
    pixel_size_arcsec = models.FloatField()
    image_fwhm_arcsec = models.FloatField(null=True, blank=True)
    wavelength_eff_angstrom = models.FloatField()
    wavelength_min_angstrom = models.FloatField()
    wavelength_max_angstrom = models.FloatField()
    vega_zero_point_jansky = models.FloatField()
    magnitude_zero_point = models.FloatField(null=True, blank=True)
    ab_offset = models.FloatField(null=True, blank=True)
    magnitude_zero_point_keyword = models.CharField(null=True, blank=True, max_length=8)
    image_pixel_units = models.CharField(max_length=50, null=True, blank=True)

    objects = FilterManager()

    def __str__(self):
        return self.name

    def transmission_curve(self):
        """
        Returns the transmission curve of the filter
        """
        curve_name = f"{settings.TRANSMISSION_CURVES_ROOT}/{self.name}.txt"

        try:
            transmission_curve = pd.read_csv(
                curve_name, sep='\s+', header=None
            )
        except:
            raise ValueError(
                f"{self.name}: Problem loading filter transmission curve from {curve_name}"
            )

        wavelength = transmission_curve[0].to_numpy()
        transmission = transmission_curve[1].to_numpy()
        return observate.Filter(
            kname=self.name, nick=self.name, data=(wavelength, transmission)
        )

    def correlation_model(self):
        """
        Returns the model for correlated errors of the filter, if it exists
        """
        corr_model_name = (
            f"{settings.TRANSMISSION_CURVES_ROOT}/{self.name}_corrmodel.txt"
        )
        if not os.path.exists(corr_model_name):
            return None, None

        try:
            corr_model = pd.read_csv(
                corr_model_name, sep='\s+', header=None
            )
        except:
            raise ValueError(
                f"{self.name}: Problem loading filter transmission curve from {curve_name}"
            )

        app_radius = corr_model[0].to_numpy()
        error_adjust = corr_model[1].to_numpy() ** (1 / 2.0)
        return app_radius, error_adjust


class Catalog(models.Model):
    """
    Model to represent a photometric catalog
    """

    name = models.CharField(max_length=100, unique=True)
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE)
    vizier_id = models.CharField(max_length=20)
    id_column = models.CharField(max_length=20)
    ra_column = models.CharField(max_length=20)
    dec_column = models.CharField(max_length=20)

    objects = CatalogManager()


class CatalogPhotometry(models.Model):
    name = models.CharField(max_length=100, unique=True)
    mag_column = models.CharField(max_length=20)
    mag_error_column = models.CharField(max_length=20)
    filter = models.ForeignKey(Filter, on_delete=models.CASCADE)
    catalog = models.ForeignKey(Catalog, on_delete=models.CASCADE)


def fits_file_path(instance):
    """
    Constructs a file path for a fits image
    """
    return f"{instance.host}/{instance.filter.survey}/{instance.filter}.fits"


def hdf5_file_path(instance):
    """
    Constructs a file path for a HDF5 image
    """
    return f"{instance.transient.name}/{instance.transient.name}_{instance.aperture.type}.h5"

def npz_chains_file_path(instance):
    """
    Constructs a file path for a npz file
    """
    return f"{instance.transient.name}/{instance.transient.name}_{instance.aperture.type}_chain.npz"

def npz_percentiles_file_path(instance):
    """
    Constructs a file path for a npz file
    """
    return f"{instance.transient.name}/{instance.transient.name}_{instance.aperture.type}_perc.npz"

def npz_model_file_path(instance):
    """
    Constructs a file path for a npz file
    """
    return f"{instance.transient.name}/{instance.transient.name}_{instance.aperture.type}_modeldata.npz"


class Cutout(models.Model):
    """
    Model to represent a cutout image of a host galaxy
    """

    name = models.CharField(max_length=50, null=True, blank=True)
    filter = models.ForeignKey(Filter, on_delete=models.CASCADE)
    transient = models.ForeignKey(
        Transient, on_delete=models.CASCADE, null=True, blank=True
    )
    fits = models.FileField(upload_to=fits_file_path, null=True, blank=True)
    objects = CutoutManager()


class Aperture(SkyObject):
    """
    Model to represent a sky aperture
    """

    name = models.CharField(max_length=50, blank=True, null=True)
    cutout = models.ForeignKey(Cutout, on_delete=models.CASCADE, blank=True, null=True)
    transient = models.ForeignKey(
        Transient, on_delete=models.CASCADE, blank=True, null=True
    )
    orientation_deg = models.FloatField()
    semi_major_axis_arcsec = models.FloatField()
    semi_minor_axis_arcsec = models.FloatField()
    type = models.CharField(max_length=20)
    objects = ApertureManager()

    def __str__(self):
        return (
            f"Aperture(ra={self.ra_deg},dec={self.dec_deg}, "
            f'semi major axis={self.semi_major_axis_arcsec}", '
            f'semi_minor axis={self.semi_minor_axis_arcsec}")'
        )

    @property
    def sky_aperture(self):
        """Return photutils object"""
        return SkyEllipticalAperture(
            self.sky_coord,
            self.semi_major_axis_arcsec * u.arcsec,
            self.semi_minor_axis_arcsec * u.arcsec,
            theta=self.orientation_deg * u.degree,
        )

    @property
    def semi_major_axis(self):
        return round(self.semi_major_axis_arcsec, 2)

    @property
    def semi_minor_axis(self):
        return round(self.semi_minor_axis_arcsec, 2)

    @property
    def orientation_angle(self):
        return round(self.orientation_deg, 2)


class AperturePhotometry(models.Model):
    """Model to store the photometric data"""

    aperture = models.ForeignKey(Aperture, on_delete=models.CASCADE)
    filter = models.ForeignKey(Filter, on_delete=models.CASCADE)
    transient = models.ForeignKey(Transient, on_delete=models.CASCADE)
    flux = models.FloatField(blank=True, null=True)
    flux_error = models.FloatField(blank=True, null=True)
    magnitude = models.FloatField(blank=True, null=True)
    magnitude_error = models.FloatField(blank=True, null=True)
    is_validated = models.CharField(blank=True, null=True, max_length=40)

    @property
    def flux_rounded(self):
        return round(self.flux, 2)

    @property
    def flux_error_rounded(self):
        return round(self.flux_error, 2)


class SEDFittingResult(models.Model):
    """Model to store prospector results"""

    transient = models.ForeignKey(
        Transient, on_delete=models.CASCADE, null=True, blank=True
    )
    aperture = models.ForeignKey(
        Aperture, on_delete=models.CASCADE, null=True, blank=True
    )
    posterior = models.FileField(upload_to=hdf5_file_path, null=True, blank=True)
    log_mass_16 = models.FloatField(null=True, blank=True)
    log_mass_50 = models.FloatField(null=True, blank=True)
    log_mass_84 = models.FloatField(null=True, blank=True)

    # from Prospector, we need to save the ratio of
    # surviving stellar mass to the formed mass
    mass_surviving_ratio = models.FloatField(null=True, blank=True)

    log_sfr_16 = models.FloatField(null=True, blank=True)
    log_sfr_50 = models.FloatField(null=True, blank=True)
    log_sfr_84 = models.FloatField(null=True, blank=True)

    log_ssfr_16 = models.FloatField(null=True, blank=True)
    log_ssfr_50 = models.FloatField(null=True, blank=True)
    log_ssfr_84 = models.FloatField(null=True, blank=True)

    # SFR ~ exp(-age/tau)
    # https://prospect.readthedocs.io/en/latest/sfhs.html?highlight=tau#parametric-sfh
    log_age_16 = models.FloatField(null=True, blank=True)
    log_age_50 = models.FloatField(null=True, blank=True)
    log_age_84 = models.FloatField(null=True, blank=True)

    log_tau_16 = models.FloatField(null=True, blank=True)
    log_tau_50 = models.FloatField(null=True, blank=True)
    log_tau_84 = models.FloatField(null=True, blank=True)

    chains_file = models.FileField(upload_to=npz_chains_file_path, null=True, blank=True)
    percentiles_file = models.FileField(upload_to=npz_percentiles_file_path, null=True, blank=True)
    model_file = models.FileField(upload_to=npz_model_file_path, null=True, blank=True)

class TaskRegisterSnapshot(models.Model):
    """
    Model to keep track of how many unprocessed transients exist
    """

    time = models.DateTimeField()
    number_of_transients = models.IntegerField()
    aggregate_type = models.CharField(max_length=100)


class Acknowledgement(models.Model):
    """
    Model to keep track of other work blast uses and relies on.
    """

    name = models.CharField(max_length=100)
    description = models.CharField(max_length=1000, null=True, blank=True)
    repository_url = models.CharField(max_length=100, null=True, blank=True)
    website_url = models.CharField(max_length=100, null=True, blank=True)
    paper_url = models.CharField(max_length=100, null=True, blank=True)
    doi = models.CharField(max_length=1000, null=True, blank=True)
    acknowledgement_text = models.CharField(max_length=1000, null=True, blank=True)
