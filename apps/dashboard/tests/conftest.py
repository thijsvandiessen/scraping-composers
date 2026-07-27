from typing import Any

import pytest
from django.test import Client


@pytest.fixture
def staff_client(db: None, django_user_model: Any) -> Client:
    user = django_user_model.objects.create_superuser(username="thijs", password="pw")
    client = Client()
    client.force_login(user)
    return client
