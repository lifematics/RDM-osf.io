# -*- coding: utf-8 -*-
# Generated by Django 1.11.11 on 2018-04-18 22:22
from __future__ import unicode_literals

from django.db import migrations, models
import osf.utils.fields


class Migration(migrations.Migration):

    dependencies = [
        ('osf', '0097_merge_20180416_1533'),
    ]

    operations = [
        migrations.AddField(
            model_name='osfuser',
            name='change_password_last_attempt',
            field=osf.utils.fields.NonNaiveDateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='osfuser',
            name='old_password_invalid_attempts',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
