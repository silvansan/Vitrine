"""Organizations (2/3): move existing data into organization "Main".

Each agent gets a location, each service (event) goes to the first location,
and guests with the same name become one person. Their other links keep
working as aliases. Kept apart from the schema changes so PostgreSQL doesn't
mix data updates and ALTER TABLE in one transaction.
"""
from django.conf import settings
from django.db import migrations


def forwards(apps, schema_editor):
    Organization = apps.get_model("guestmedia", "Organization")
    Location = apps.get_model("guestmedia", "Location")
    Person = apps.get_model("guestmedia", "Person")
    PersonAlias = apps.get_model("guestmedia", "PersonAlias")
    Agent = apps.get_model("guestmedia", "Agent")
    Service = apps.get_model("guestmedia", "Service")
    Guest = apps.get_model("guestmedia", "Guest")
    MediaAsset = apps.get_model("guestmedia", "MediaAsset")
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))

    has_data = Service.objects.exists() or Agent.objects.exists() or MediaAsset.objects.exists()
    if not has_data:
        return
    org = Organization.objects.create(name="Main")
    org.staff.set(User.objects.filter(is_staff=True))
    first = None
    for agent in Agent.objects.order_by("id"):
        loc = Location.objects.create(organization=org, name=agent.name, agent=agent)
        first = first or loc
    if first is None:
        first = Location.objects.create(organization=org, name="Main")
    Service.objects.update(location=first)

    people = {}
    for guest in Guest.objects.order_by("created", "id"):
        key = guest.name.strip().lower()
        person = people.get(key)
        if person is None:
            person = Person.objects.create(
                organization=org, name=guest.name, token=guest.token, can_upload=guest.can_upload,
                can_browse=guest.can_browse, expires_at=guest.expires_at,
            )
            people[key] = person
        else:
            PersonAlias.objects.create(person=person, token=guest.token)
        guest.person = person
        guest.save(update_fields=["person"])

    for asset in MediaAsset.objects.all():
        asset.organization = org
        if asset.uploaded_by_id:
            asset.uploader = Guest.objects.get(pk=asset.uploaded_by_id).person
        asset.save(update_fields=["organization", "uploader"])

    # Two guest rows of the same person in one event can't stay: keep the first, move its set.
    seen = set()
    for guest in Guest.objects.order_by("id"):
        key = (guest.service_id, guest.person_id)
        if key in seen:
            guest.delete()
        else:
            seen.add(key)



class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("guestmedia", "0003_organizations"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
