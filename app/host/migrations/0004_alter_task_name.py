# Generated by Django 3.2.9 on 2022-05-03 17:40

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('host', '0003_aperture'),
    ]

    operations = [
        migrations.AlterField(
            model_name='task',
            name='name',
            field=models.CharField(max_length=100),
        ),
    ]
